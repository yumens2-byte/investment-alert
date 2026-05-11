"""
제목: 섹터 로테이션 변화 감지 레이어
내용: SectorFlowStore에서 최근 5일치 데이터를 읽어 defensive vs cyclical 그룹의
      spread를 계산하고 변화 수준(L1/L2/NONE)을 판정한다.

변경 사유 (v1.0.0 → v1.1.0):
  - 5일 데이터 충분성 가드 추가 (MIN_ROWS_FOR_5D=24)
  - rows_used < 24 시 NONE 강제 → DB 누적 전 false alarm 방지
  - 첫 1주(평일 5회 cron 누적) 동안 자동 NONE 보장

주요 클래스:
  - SectorRotationResult: 감지 결과 dataclass
  - SectorFlowLayer: 변화 감지 엔진

주요 함수:
  - SectorFlowLayer.detect(): 5일치 데이터 → SectorRotationResult
  - SectorFlowLayer._group_averages(ticker_map): 그룹별 평균 등락률
  - SectorFlowLayer._judge_level(spread_1d, spread_5d): 레벨 + rotation_type 판정
  - SectorFlowLayer._compute_health_score(rows): 데이터 건강도
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from config.market_calendar import PROFILE_HOLIDAY, get_market_profile
from config.sector_groups import POLICY_VERSION_SECTOR, SECTOR_GROUPS_US
from config.settings import get_env_float
from core.logger import get_logger
from db.sector_flow_store import SectorFlowStore

VERSION = "1.1.0"

logger = get_logger(__name__)

# 임계값 (환경변수 오버라이드 가능)
ROTATION_THR_1D = get_env_float("SECTOR_ROTATION_THR_1D", 1.0)  # %p
ROTATION_THR_5D = get_env_float("SECTOR_ROTATION_THR_5D", 1.5)  # %p

# 그룹별 최소 ticker 수 (계산 가능 조건)
MIN_TICKERS_PER_GROUP = 2

# v1.1.0: 5일 누적 spread 계산 가능 최소 row 수 (5일 × 6 ticker × 80% = 24)
# 이보다 부족하면 1d_spread == 5d_spread 수렴되어 misleading 위험 → NONE 강제
MIN_ROWS_FOR_5D = 24


@dataclass
class SectorRotationResult:
    """
    제목: 섹터 로테이션 감지 결과
    내용: SectorFlowLayer.detect() 반환 타입.

    책임:
      - level/rotation_type 결과 보관
      - 1일/5일 spread 및 그룹 평균 수치 보관
      - 감사 추적용 reasoning + reasoning_json
      - 데이터 건강도(health_score) 및 rows_used
    """
    level: str  # 'L1' | 'L2' | 'NONE'
    rotation_type: str  # 'DEFENSIVE_ROTATION' | 'RISK_ON_ROTATION' | 'ROTATION_WATCH_DEF' | 'ROTATION_WATCH_RISK' | 'NONE'
    spread_1d: float | None
    spread_5d: float | None
    def_avg_1d: float | None
    cyc_avg_1d: float | None
    def_avg_5d: float | None
    cyc_avg_5d: float | None
    reasoning: str
    policy_version: str
    health_score: float  # 0~1
    rows_used: int
    reasoning_json: dict = field(default_factory=dict)


class SectorFlowLayer:
    """
    제목: 섹터 로테이션 변화 감지 레이어
    내용: SectorFlowStore에서 5일치 데이터를 읽어 그룹 spread 계산 + 레벨 판정.

    책임:
      - 휴장일 스킵 (config.market_calendar.PROFILE_HOLIDAY 활용)
      - (date → ticker → chg_pct) 형태로 pivot
      - 그룹별 평균 (1일 / 5일 누적) 산출
      - L1/L2/NONE 판정 (1차 안: 5일 우선, 1일은 L1 보조 조건)
    """

    def __init__(self, sector_store: SectorFlowStore) -> None:
        """
        제목: SectorFlowLayer 초기화

        Args:
            sector_store: SectorFlowStore 인스턴스
        """
        self.store = sector_store
        logger.info(f"[SectorFlowLayer] v{VERSION} 초기화")

    def detect(self) -> SectorRotationResult:
        """
        제목: 전체 감지 파이프라인
        내용:
          1. 휴장일 체크 → NONE
          2. 최근 5일 row 조회
          3. (date → ticker → chg_pct) pivot
          4. 그룹별 평균 (1일/5일)
          5. spread 계산 + 레벨 판정
        """
        # Step 1: 휴장일 체크
        profile = get_market_profile()
        if profile == PROFILE_HOLIDAY:
            return self._build_none_result(
                reason="휴장일/주말 — 감지 스킵",
                rows_used=0,
                health_score=1.0,
            )

        # Step 2: 5일치 row 조회
        rows = self.store.fetch_latest_n_days(n=5)
        if not rows:
            return self._build_none_result(
                reason="데이터 0건 — DB 조회 실패 또는 미적재",
                rows_used=0,
                health_score=0.0,
            )

        # Step 2-1 (v1.1.0): 5일 누적 데이터 충분성 가드
        # 1일치 데이터만 있으면 1d_spread == 5d_spread 수렴 → "5일 누적" 메시지가 misleading.
        # rows_used가 MIN_ROWS_FOR_5D 미달이면 임계 돌파해도 NONE 강제하여 false alarm 방지.
        if len(rows) < MIN_ROWS_FOR_5D:
            health_score = self._compute_health_score(rows, expected_per_day=6)
            return self._build_none_result(
                reason=(
                    f"5일 데이터 누적 중 (rows_used={len(rows)} < "
                    f"{MIN_ROWS_FOR_5D}) — 알람 보류"
                ),
                rows_used=len(rows),
                health_score=health_score,
            )

        # Step 3: pivot (date → ticker → chg_pct)
        by_date: dict[str, dict[str, float | None]] = defaultdict(dict)
        for r in rows:
            d = str(r["snapshot_date"])
            t = str(r["ticker"])
            chg = r.get("chg_pct")
            by_date[d][t] = float(chg) if chg is not None else None

        sorted_dates = sorted(by_date.keys(), reverse=True)
        rows_used = len(rows)
        health_score = self._compute_health_score(rows, expected_per_day=6)

        # Step 4: 그룹별 평균 — 최신 1일
        latest_date = sorted_dates[0] if sorted_dates else None
        if latest_date is None:
            return self._build_none_result(
                reason="유효 날짜 0건",
                rows_used=rows_used,
                health_score=health_score,
            )

        def_avg_1d, cyc_avg_1d = self._group_averages(by_date[latest_date])

        # 5일 누적: 각 일자의 그룹 평균을 산출 후 합산
        def_sums: list[float] = []
        cyc_sums: list[float] = []
        for d in sorted_dates[:5]:
            d_avg, c_avg = self._group_averages(by_date[d])
            if d_avg is not None:
                def_sums.append(d_avg)
            if c_avg is not None:
                cyc_sums.append(c_avg)

        def_avg_5d = sum(def_sums) if def_sums else None
        cyc_avg_5d = sum(cyc_sums) if cyc_sums else None

        # Step 5: spread 계산
        spread_1d = (
            def_avg_1d - cyc_avg_1d
            if (def_avg_1d is not None and cyc_avg_1d is not None)
            else None
        )
        spread_5d = (
            def_avg_5d - cyc_avg_5d
            if (def_avg_5d is not None and cyc_avg_5d is not None)
            else None
        )

        # Step 6: 레벨 판정
        level, rotation_type = self._judge_level(spread_1d, spread_5d)

        reasoning = self._build_reasoning(
            level=level,
            rotation_type=rotation_type,
            spread_1d=spread_1d,
            spread_5d=spread_5d,
            def_avg_1d=def_avg_1d,
            cyc_avg_1d=cyc_avg_1d,
            def_avg_5d=def_avg_5d,
            cyc_avg_5d=cyc_avg_5d,
            rows_used=rows_used,
            health_score=health_score,
        )

        reasoning_json = {
            "schema_version": "sector-1.0",
            "level": level,
            "rotation_type": rotation_type,
            "spread_1d": spread_1d,
            "spread_5d": spread_5d,
            "thr_1d": ROTATION_THR_1D,
            "thr_5d": ROTATION_THR_5D,
            "def_avg_1d": def_avg_1d,
            "cyc_avg_1d": cyc_avg_1d,
            "def_avg_5d": def_avg_5d,
            "cyc_avg_5d": cyc_avg_5d,
            "rows_used": rows_used,
            "health_score": health_score,
            "policy_version": POLICY_VERSION_SECTOR,
            "evaluated_at": datetime.now(UTC).isoformat(),
        }

        return SectorRotationResult(
            level=level,
            rotation_type=rotation_type,
            spread_1d=spread_1d,
            spread_5d=spread_5d,
            def_avg_1d=def_avg_1d,
            cyc_avg_1d=cyc_avg_1d,
            def_avg_5d=def_avg_5d,
            cyc_avg_5d=cyc_avg_5d,
            reasoning=reasoning,
            policy_version=POLICY_VERSION_SECTOR,
            health_score=health_score,
            rows_used=rows_used,
            reasoning_json=reasoning_json,
        )

    @staticmethod
    def _group_averages(
        ticker_map: dict[str, float | None],
    ) -> tuple[float | None, float | None]:
        """
        제목: 그룹별 평균 등락률 산출
        내용: None 제외, 최소 MIN_TICKERS_PER_GROUP 개 필요.

        Args:
            ticker_map: {ticker: chg_pct | None}

        Returns:
            (defensive_avg, cyclical_avg) — 둘 다 None 가능
        """
        def _avg(tickers: list[str]) -> float | None:
            vals = [ticker_map.get(t) for t in tickers if ticker_map.get(t) is not None]
            vals = [v for v in vals if v is not None]
            if len(vals) < MIN_TICKERS_PER_GROUP:
                return None
            return sum(vals) / len(vals)

        return (
            _avg(SECTOR_GROUPS_US["defensive"]),
            _avg(SECTOR_GROUPS_US["cyclical"]),
        )

    @staticmethod
    def _judge_level(
        spread_1d: float | None,
        spread_5d: float | None,
    ) -> tuple[str, str]:
        """
        제목: 레벨 + rotation_type 판정
        내용:
          - L2 — 5일 누적 |spread| >= ROTATION_THR_5D
          - L1 — 1일 + 5일 동시에 |spread| >= ROTATION_THR_1D
          - NONE — 그 외

        Args:
            spread_1d: 1일 spread (def - cyc)
            spread_5d: 5일 누적 spread

        Returns:
            (level, rotation_type)
        """
        if spread_5d is None or spread_1d is None:
            return "NONE", "NONE"

        # L2 — 5일 누적 임계
        if abs(spread_5d) >= ROTATION_THR_5D:
            if spread_5d > 0:
                return "L2", "DEFENSIVE_ROTATION"
            return "L2", "RISK_ON_ROTATION"

        # L1 — 1일 + 5일 동시 ROTATION_THR_1D 돌파 (단발 노이즈 회피)
        if (
            abs(spread_1d) >= ROTATION_THR_1D
            and abs(spread_5d) >= ROTATION_THR_1D
        ):
            if spread_1d > 0:
                return "L1", "ROTATION_WATCH_DEF"
            return "L1", "ROTATION_WATCH_RISK"

        return "NONE", "NONE"

    @staticmethod
    def _compute_health_score(rows: list[dict], expected_per_day: int = 6) -> float:
        """
        제목: 데이터 건강도 계산
        내용: chg_pct가 NULL인 row는 0.5 가중치. 전체를 max_expected로 정규화.

        Args:
            rows: DB 조회 결과
            expected_per_day: 일별 기대 ticker 수 (US=6)

        Returns:
            float: 0.0 ~ 1.0
        """
        if not rows:
            return 0.0
        max_expected = 5 * expected_per_day
        weighted = sum(1.0 if r.get("chg_pct") is not None else 0.5 for r in rows)
        return round(min(weighted / max_expected, 1.0), 3)

    def _build_reasoning(
        self,
        level: str,
        rotation_type: str,
        spread_1d: float | None,
        spread_5d: float | None,
        def_avg_1d: float | None,
        cyc_avg_1d: float | None,
        def_avg_5d: float | None,
        cyc_avg_5d: float | None,
        rows_used: int,
        health_score: float,
    ) -> str:
        """제목: 사람이 읽는 reasoning 문자열 생성"""
        if level == "NONE":
            return (
                f"NONE — 임계값 미달 또는 데이터 부족 "
                f"(rows_used={rows_used}, health={health_score:.2f})"
            )

        def _fmt(v: float | None) -> str:
            return f"{v:+.2f}" if v is not None else "—"

        return (
            f"{level}/{rotation_type} — "
            f"5일 spread={_fmt(spread_5d)}p (thr {ROTATION_THR_5D:.1f}p), "
            f"1일 spread={_fmt(spread_1d)}p (thr {ROTATION_THR_1D:.1f}p) | "
            f"방어 1d/5d={_fmt(def_avg_1d)}/{_fmt(def_avg_5d)} "
            f"경기민감 1d/5d={_fmt(cyc_avg_1d)}/{_fmt(cyc_avg_5d)} | "
            f"rows={rows_used}, health={health_score:.2f}"
        )

    def _build_none_result(
        self,
        reason: str,
        rows_used: int,
        health_score: float,
    ) -> SectorRotationResult:
        """제목: NONE 결과 빌더"""
        return SectorRotationResult(
            level="NONE",
            rotation_type="NONE",
            spread_1d=None,
            spread_5d=None,
            def_avg_1d=None,
            cyc_avg_1d=None,
            def_avg_5d=None,
            cyc_avg_5d=None,
            reasoning=reason,
            policy_version=POLICY_VERSION_SECTOR,
            health_score=health_score,
            rows_used=rows_used,
            reasoning_json={
                "schema_version": "sector-1.0",
                "level": "NONE",
                "reason": reason,
                "rows_used": rows_used,
                "health_score": health_score,
                "policy_version": POLICY_VERSION_SECTOR,
            },
        )
