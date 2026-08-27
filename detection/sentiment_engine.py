"""
제목: EDT 심리지표 엔진 (v1.0.0)
내용: 수집된 5개 지표 원값을 0~100 점수로 정규화(밴드 선형보간)하고
      EDT 3축(E: Emotion / D: Divergence / T: Trend)을 산출한다.
      기존 alert_engine / sector_alert_engine과 완전 독립 — import 없음.

축 정의:
  - E (Emotion): 5지표 가중 종합점수 (0=극단공포, 100=극단탐욕)
  - D (Divergence): Fast Fear(파생: VIX구조+PCR) − Slow Fear(신용·실체: HY+Breadth)
                    점수 기반 차이 (−100 ~ +100). 음수 = 파생이 더 공포.
  - T (Trend): E(오늘) − E(5영업일 전). 과거 E는 store에서 조회해 파라미터로 주입.

결측 정책 (default-deny):
  - 지표 1개 결측 → 잔여 가중치 재정규화
  - MAX_MISSING_INDICATORS(2) 이상 결측 → level=DEGRADED (발행 스킵 대상)
  - D축은 구성 지표 중 하나라도 결측이면 None
  - T축은 과거 E 부재(콜드스타트) 시 None

주요 클래스:
  - SentimentResult: 산출 결과 dataclass
  - SentimentEngine: compute() 순수 계산 (외부 I/O 없음 — 테스트 용이성)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from config.sentiment_settings import (
    BAND_BREADTH,
    BAND_HY_OAS,
    BAND_PCR,
    BAND_VIX_RATIO,
    D_DIVERGENCE_THRESHOLD,
    D_FAST_WEIGHTS,
    D_SLOW_WEIGHTS,
    LEVEL_BOUNDS,
    MAX_MISSING_INDICATORS,
    T_TREND_THRESHOLD,
    WEIGHTS,
)
from core.logger import get_logger

VERSION = "1.0.0"

logger = get_logger(__name__)

LEVEL_DEGRADED = "DEGRADED"

# T축 방향 라벨
TREND_UP = "IMPROVING"
TREND_FLAT = "FLAT"
TREND_DOWN = "WORSENING"

# 상태 라벨 (E 레벨 × T 방향 = 15종) — 발행 문구의 뼈대
STATE_LABELS: dict[tuple[str, str], str] = {
    ("EXTREME_FEAR", TREND_DOWN): "투매 가속",
    ("EXTREME_FEAR", TREND_FLAT): "공포 바닥 다지기",
    ("EXTREME_FEAR", TREND_UP): "바닥 반등 시도",
    ("FEAR", TREND_DOWN): "공포 심화",
    ("FEAR", TREND_FLAT): "경계 지속",
    ("FEAR", TREND_UP): "공포 완화",
    ("NEUTRAL", TREND_DOWN): "온기 식는 중",
    ("NEUTRAL", TREND_FLAT): "방향 탐색",
    ("NEUTRAL", TREND_UP): "회복 조짐",
    ("GREED", TREND_DOWN): "탐욕 흔들림",
    ("GREED", TREND_FLAT): "온기 유지",
    ("GREED", TREND_UP): "탐욕 가속",
    ("EXTREME_GREED", TREND_DOWN): "과열 식히기",
    ("EXTREME_GREED", TREND_FLAT): "과열 지속",
    ("EXTREME_GREED", TREND_UP): "폭주 구간",
}


@dataclass
class SentimentResult:
    """
    제목: EDT 산출 결과
    내용: 원값/점수/3축/레벨/결측 정보를 모두 보존 (백테스트용 전체 적재).
    """

    raw: dict[str, dict | None] = field(default_factory=dict)
    scores: dict[str, float | None] = field(default_factory=dict)
    e_score: float | None = None
    level: str = LEVEL_DEGRADED
    fast_fear: float | None = None
    slow_fear: float | None = None
    d_score: float | None = None
    t_score: float | None = None
    trend: str | None = None
    state_label: str | None = None
    missing: list[str] = field(default_factory=list)

    @property
    def is_degraded(self) -> bool:
        """제목: SYSTEM_DEGRADED 여부 (발행 스킵 판정)"""
        return self.level == LEVEL_DEGRADED


def interpolate_band(value: float, band: list[tuple[float, float]]) -> float:
    """
    제목: 밴드 선형보간
    내용: (원값, 점수) 브레이크포인트 리스트에서 선형보간. 범위 밖은 clamp.

    Args:
        value: 지표 원값
        band: 원값 오름차순 (원값, 점수) 쌍

    Returns:
        float: 0~100 점수 (소수 2자리)
    """
    if value <= band[0][0]:
        return round(band[0][1], 2)
    if value >= band[-1][0]:
        return round(band[-1][1], 2)
    for (x1, y1), (x2, y2) in zip(band, band[1:], strict=False):
        if x1 <= value <= x2:
            if x2 == x1:  # 방어 — 동일 x 브레이크포인트
                return round(y2, 2)
            ratio = (value - x1) / (x2 - x1)
            return round(y1 + (y2 - y1) * ratio, 2)
    # 도달 불가 경로 방어
    return round(band[-1][1], 2)


class SentimentEngine:
    """
    제목: EDT 3축 산출 엔진
    내용: 순수 계산 클래스 — 외부 I/O 없음. 과거 E 점수는 파라미터 주입.
    """

    def compute(
        self,
        collected: dict[str, dict | None],
        prev_e_score: float | None = None,
    ) -> SentimentResult:
        """
        제목: EDT 전체 산출
        내용: 점수화 → E(재정규화 가중합) → D → T → 레벨/상태 라벨.

        Args:
            collected: SentimentCollector.collect_all() 반환값
            prev_e_score: T_LOOKBACK_DAYS 영업일 전 E 점수 (없으면 None → T 결측)

        Returns:
            SentimentResult
        """
        result = SentimentResult(raw=collected)
        result.scores = self._score_all(collected)
        result.missing = [k for k, v in result.scores.items() if v is None]

        if len(result.missing) >= MAX_MISSING_INDICATORS:
            logger.warning(
                f"[SentimentEngine] v{VERSION} 결측 {len(result.missing)}건 "
                f"({result.missing}) → DEGRADED"
            )
            result.level = LEVEL_DEGRADED
            return result

        result.e_score = self._weighted_composite(result.scores)
        result.level = self._resolve_level(result.e_score)

        result.fast_fear = self._axis_score(result.scores, D_FAST_WEIGHTS)
        result.slow_fear = self._axis_score(result.scores, D_SLOW_WEIGHTS)
        if result.fast_fear is not None and result.slow_fear is not None:
            result.d_score = round(result.fast_fear - result.slow_fear, 2)

        if prev_e_score is not None and result.e_score is not None:
            result.t_score = round(result.e_score - prev_e_score, 2)
            result.trend = self._resolve_trend(result.t_score)
        else:
            result.trend = None

        result.state_label = self._resolve_state_label(result.level, result.trend)

        logger.info(
            f"[SentimentEngine] v{VERSION} E={result.e_score} level={result.level} "
            f"D={result.d_score} T={result.t_score} state={result.state_label} "
            f"missing={result.missing}"
        )
        return result

    # ────────────────────────────────────────────────
    # 내부 계산
    # ────────────────────────────────────────────────
    @staticmethod
    def _score_all(collected: dict[str, dict | None]) -> dict[str, float | None]:
        """제목: 지표별 0~100 점수화 (결측은 None 유지)"""

        def _val(key: str) -> float | None:
            item = collected.get(key)
            if item is None or item.get("value") is None:
                return None
            return float(item["value"])

        scores: dict[str, float | None] = {}
        v = _val("vix_ratio")
        scores["vix_ratio"] = interpolate_band(v, BAND_VIX_RATIO) if v is not None else None
        v = _val("pcr")
        scores["pcr"] = interpolate_band(v, BAND_PCR) if v is not None else None
        v = _val("hy_oas")
        scores["hy_oas"] = interpolate_band(v, BAND_HY_OAS) if v is not None else None
        v = _val("breadth")
        scores["breadth"] = interpolate_band(v, BAND_BREADTH) if v is not None else None
        v = _val("crypto_fg")
        scores["crypto_fg"] = round(min(max(v, 0.0), 100.0), 2) if v is not None else None
        return scores

    @staticmethod
    def _weighted_composite(scores: dict[str, float | None]) -> float:
        """
        제목: E 점수 산출 — 결측 가중치 재정규화
        내용: 유효 지표의 가중치 합으로 나누어 재정규화.
        """
        total_weight = 0.0
        acc = 0.0
        for key, weight in WEIGHTS.items():
            score = scores.get(key)
            if score is None:
                continue
            acc += score * weight
            total_weight += weight
        return round(acc / total_weight, 2)

    @staticmethod
    def _axis_score(
        scores: dict[str, float | None], axis_weights: dict[str, float]
    ) -> float | None:
        """
        제목: D축 구성점수 (Fast/Slow Fear)
        내용: 구성 지표 중 하나라도 결측이면 None (부분 산출 금지 — 왜곡 방지).
        """
        acc = 0.0
        for key, weight in axis_weights.items():
            score = scores.get(key)
            if score is None:
                return None
            acc += score * weight
        return round(acc, 2)

    @staticmethod
    def _resolve_level(e_score: float) -> str:
        """제목: E 점수 → 레벨 (하한 포함)"""
        for bound, level in LEVEL_BOUNDS:
            if e_score >= bound:
                return level
        return "EXTREME_FEAR"

    @staticmethod
    def _resolve_trend(t_score: float) -> str:
        """제목: T 점수 → 방향 라벨"""
        if t_score >= T_TREND_THRESHOLD:
            return TREND_UP
        if t_score <= -T_TREND_THRESHOLD:
            return TREND_DOWN
        return TREND_FLAT

    @staticmethod
    def _resolve_state_label(level: str, trend: str | None) -> str | None:
        """
        제목: 상태 라벨 (레벨×방향 15종)
        내용: DEGRADED 또는 T 결측(콜드스타트) 시 None.
        """
        if level == LEVEL_DEGRADED or trend is None:
            return None
        return STATE_LABELS.get((level, trend))

    @staticmethod
    def divergence_note(d_score: float | None) -> str | None:
        """
        제목: D축 해석 문구 키 반환
        내용: 임계 초과 방향에 따라 "FAST"/"SLOW"/"ALIGNED". D 결측 시 None.
        """
        if d_score is None:
            return None
        if d_score <= -D_DIVERGENCE_THRESHOLD:
            return "FAST"  # 파생 단독 공포
        if d_score >= D_DIVERGENCE_THRESHOLD:
            return "SLOW"  # 신용이 더 공포
        return "ALIGNED"
