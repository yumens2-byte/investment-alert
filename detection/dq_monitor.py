"""
제목: 데이터 품질 모니터 (Phase 1)
내용: 수집 사이클 종료 직후 4개 지표를 계산하여 degraded 여부 판정.
      MacroNewsLayer.detect()에 주입되어 SYSTEM_DEGRADED 단락 평가에 사용.

지표:
  1. source_success_rate  — 등록된 소스 중 수집 성공한 소스의 비율
  2. fresh_event_ratio    — 수집 이벤트 중 1시간 이내 발행 비율
  3. lag_seconds_p95      — 사이클 소요시간 (단일 사이클은 단일 측정값으로 사용)
  4. volume_zscore        — 수집량의 baseline 대비 z-score

판정:
  degraded = ANY(임계 위반)

주요 클래스:
  - DataQualityState: 평가 결과 dataclass
  - DataQualityMonitor: 평가 수행 클래스

설계 근거:
  - 02 프로세스 설계서 §2.1
  - 04 수정 리스트 M-01 (macro_news_layer.py 주입 지점)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime

from collectors.base import CollectorEvent
from core.logger import get_logger

VERSION = "1.0.0"

logger = get_logger(__name__)


@dataclass
class DataQualityState:
    """
    제목: 데이터 품질 평가 결과
    내용: DataQualityMonitor.evaluate() 반환값.
          ia_data_quality_state 테이블 1로우와 1:1 매핑.
    """

    fresh_event_ratio: float
    source_success_rate: float
    lag_seconds_p95: float
    volume_zscore: float | None
    degraded_flag: bool
    degraded_reasons: list[str] = field(default_factory=list)
    cycle_started_at: datetime | None = None
    cycle_finished_at: datetime | None = None
    source_results: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """
        제목: Supabase 적재용 dict 변환
        내용: ia_data_quality_state 컬럼 이름과 정확히 일치하는 키로 반환.
              datetime은 ISO 문자열로 변환 (Supabase JSONB/TIMESTAMPTZ 호환).
        """
        return {
            "fresh_event_ratio": round(self.fresh_event_ratio, 3),
            "source_success_rate": round(self.source_success_rate, 3),
            "lag_seconds_p95": round(self.lag_seconds_p95, 2),
            "volume_zscore": (
                round(self.volume_zscore, 3) if self.volume_zscore is not None else None
            ),
            "degraded_flag": self.degraded_flag,
            "degraded_reasons": list(self.degraded_reasons),
            "source_results": dict(self.source_results),
            "cycle_started_at": (
                self.cycle_started_at.isoformat() if self.cycle_started_at else None
            ),
            "cycle_finished_at": (
                self.cycle_finished_at.isoformat() if self.cycle_finished_at else None
            ),
        }


class DataQualityMonitor:
    """
    제목: 데이터 품질 모니터
    내용: 수집 사이클당 1회 호출. 4개 지표를 계산하고 임계 위반 시
          degraded_flag=True로 표기.

    책임:
      - 4개 지표 계산
      - 임계 비교 → degraded 판정
      - 환경변수 override 지원

    환경변수 (선택):
      DQ_SOURCE_SUCCESS_MIN  — 기본 0.50
      DQ_FRESH_RATIO_MIN     — 기본 0.10
      DQ_VOLUME_ZSCORE_MIN   — 기본 -2.0
      DQ_LAG_P95_MAX         — 기본 90.0 (초)
    """

    DEFAULT_THRESHOLDS: dict[str, float] = {
        "source_success_rate_min": 0.50,
        "fresh_event_ratio_min": 0.10,
        "volume_zscore_min": -2.0,
        "lag_seconds_p95_max": 90.0,
    }

    # 1시간 이내 이벤트를 fresh로 정의
    FRESH_WINDOW_SECONDS: int = 3600

    # baseline 미제공 시 zscore 기본값 (degraded 트리거하지 않음)
    NO_BASELINE_ZSCORE: float = 0.0

    # baseline 표준편차 추정 계수 (평균의 30%)
    # — 정식 표준편차 누적은 Phase 2 weekly_tracker로 이관
    STD_ESTIMATION_RATIO: float = 0.30

    def __init__(self, thresholds: dict[str, float] | None = None) -> None:
        """
        제목: DataQualityMonitor 초기화
        내용: 환경변수 우선, 명시 인자 후순위로 임계값을 합성.

        Args:
            thresholds: 임계값 override (env보다 우선 적용)
        """
        env_thresholds = {
            "source_success_rate_min": float(
                os.getenv(
                    "DQ_SOURCE_SUCCESS_MIN",
                    self.DEFAULT_THRESHOLDS["source_success_rate_min"],
                )
            ),
            "fresh_event_ratio_min": float(
                os.getenv(
                    "DQ_FRESH_RATIO_MIN",
                    self.DEFAULT_THRESHOLDS["fresh_event_ratio_min"],
                )
            ),
            "volume_zscore_min": float(
                os.getenv(
                    "DQ_VOLUME_ZSCORE_MIN",
                    self.DEFAULT_THRESHOLDS["volume_zscore_min"],
                )
            ),
            "lag_seconds_p95_max": float(
                os.getenv(
                    "DQ_LAG_P95_MAX",
                    self.DEFAULT_THRESHOLDS["lag_seconds_p95_max"],
                )
            ),
        }
        # 명시 thresholds가 env보다 우선
        self.thresholds: dict[str, float] = {**env_thresholds, **(thresholds or {})}
        logger.info(
            f"[DQMonitor] v{VERSION} 초기화 thresholds={self.thresholds}"
        )

    def evaluate(
        self,
        cycle_started_at: datetime,
        cycle_finished_at: datetime,
        source_results: dict[str, bool],
        news_events: list[CollectorEvent],
        youtube_events: list[CollectorEvent],
        baseline_volume_avg: float | None = None,
    ) -> DataQualityState:
        """
        제목: 데이터 품질 평가 실행
        내용: 4개 지표 계산 후 임계 비교하여 degraded 여부 판정.

        처리 플로우:
          1. source_success_rate 계산
          2. fresh_event_ratio 계산
          3. lag_seconds_p95 계산 (현 사이클은 단일 측정)
          4. volume_zscore 계산 (baseline 없으면 0)
          5. 임계 위반 사유 수집
          6. DataQualityState 반환

        Args:
            cycle_started_at: 사이클 시작 시각 (UTC timezone-aware)
            cycle_finished_at: 사이클 종료 시각 (UTC timezone-aware)
            source_results: {source_name: success_bool}
            news_events: 수집된 뉴스 이벤트 리스트
            youtube_events: 수집된 YouTube 이벤트 리스트
            baseline_volume_avg: 7일 이동평균 수집량. None이면 zscore 계산 스킵.

        Returns:
            DataQualityState: 평가 결과 (degraded_flag, reasons 포함)
        """
        # 1) source_success_rate
        success_rate = self._compute_source_success_rate(source_results)

        # 2) fresh_event_ratio
        fresh_ratio = self._compute_fresh_event_ratio(news_events, youtube_events)

        # 3) lag_seconds_p95
        # 단일 사이클이므로 사이클 소요시간 자체가 측정값
        # 음수 방어: cycle_finished_at < cycle_started_at 시 0으로 클램핑
        lag_seconds = max(
            (cycle_finished_at - cycle_started_at).total_seconds(),
            0.0,
        )

        # 4) volume_zscore
        cur_volume = float(len(news_events) + len(youtube_events))
        zscore = self._compute_volume_zscore(cur_volume, baseline_volume_avg)

        # 5) 임계 위반 사유 수집
        reasons = self._collect_degraded_reasons(
            success_rate=success_rate,
            fresh_ratio=fresh_ratio,
            lag_seconds=lag_seconds,
            zscore=zscore,
        )

        degraded = len(reasons) > 0
        if degraded:
            logger.warning(
                f"[DQMonitor] DEGRADED 감지 — reasons={reasons} "
                f"(success={success_rate:.2f}, fresh={fresh_ratio:.2f}, "
                f"lag={lag_seconds:.1f}s, zscore={zscore})"
            )
        else:
            logger.info(
                f"[DQMonitor] 정상 — success={success_rate:.2f} fresh={fresh_ratio:.2f} "
                f"lag={lag_seconds:.1f}s zscore={zscore}"
            )

        return DataQualityState(
            fresh_event_ratio=fresh_ratio,
            source_success_rate=success_rate,
            lag_seconds_p95=lag_seconds,
            volume_zscore=zscore,
            degraded_flag=degraded,
            degraded_reasons=reasons,
            cycle_started_at=cycle_started_at,
            cycle_finished_at=cycle_finished_at,
            source_results=dict(source_results),
        )

    # ── 내부 계산 메서드 ──────────────────────────────────────────

    def _compute_source_success_rate(
        self, source_results: dict[str, bool]
    ) -> float:
        """등록된 소스 중 success=True 비율. 빈 dict면 0.0 반환."""
        total = len(source_results)
        if total == 0:
            return 0.0
        ok = sum(1 for v in source_results.values() if v)
        return ok / total

    def _compute_fresh_event_ratio(
        self,
        news_events: list[CollectorEvent],
        youtube_events: list[CollectorEvent],
    ) -> float:
        """전체 이벤트 중 FRESH_WINDOW_SECONDS 이내 발행 이벤트 비율."""
        all_events = list(news_events) + list(youtube_events)
        if not all_events:
            return 0.0

        now = datetime.now(UTC)
        fresh_count = 0
        for e in all_events:
            pub = getattr(e, "published_at", None)
            if not isinstance(pub, datetime):
                continue
            # tz-naive 방어 (CollectorEvent는 UTC tz-aware 보장이지만 안전책)
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=UTC)
            try:
                age_seconds = (now - pub).total_seconds()
            except (TypeError, ValueError):
                continue
            if 0 <= age_seconds <= self.FRESH_WINDOW_SECONDS:
                fresh_count += 1

        return fresh_count / len(all_events)

    def _compute_volume_zscore(
        self, cur_volume: float, baseline_avg: float | None
    ) -> float | None:
        """
        제목: 수집량 z-score
        내용: baseline 미제공 또는 0이면 None 반환 (zscore 평가 스킵).
              표준편차는 baseline의 30%로 추정 (Phase 1 임시).
        """
        if baseline_avg is None or baseline_avg <= 0:
            return None
        std = max(baseline_avg * self.STD_ESTIMATION_RATIO, 1.0)
        return (cur_volume - baseline_avg) / std

    def _collect_degraded_reasons(
        self,
        success_rate: float,
        fresh_ratio: float,
        lag_seconds: float,
        zscore: float | None,
    ) -> list[str]:
        """
        제목: 임계 위반 사유 수집
        내용: 각 임계 위반은 사람이 읽을 수 있는 짧은 reason 문자열로 기록.
              zscore가 None이면 zscore 평가는 스킵 (degraded 트리거 안 함).
        """
        reasons: list[str] = []

        if success_rate < self.thresholds["source_success_rate_min"]:
            reasons.append(
                f"source_success_rate={success_rate:.2f}<"
                f"{self.thresholds['source_success_rate_min']:.2f}"
            )

        if fresh_ratio < self.thresholds["fresh_event_ratio_min"]:
            reasons.append(
                f"fresh_event_ratio={fresh_ratio:.2f}<"
                f"{self.thresholds['fresh_event_ratio_min']:.2f}"
            )

        if zscore is not None and zscore < self.thresholds["volume_zscore_min"]:
            reasons.append(
                f"volume_zscore={zscore:.2f}<"
                f"{self.thresholds['volume_zscore_min']:.2f}"
            )

        if lag_seconds > self.thresholds["lag_seconds_p95_max"]:
            reasons.append(
                f"lag_seconds_p95={lag_seconds:.1f}>"
                f"{self.thresholds['lag_seconds_p95_max']:.1f}"
            )

        return reasons
