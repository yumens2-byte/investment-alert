"""
제목: ReasoningBuilder — reasoning v1.0 스키마 조립 (Phase 1)
내용: AlertLevel 판정 결과를 (1) 텍스트 reasoning + (2) JSONB reasoning_json으로 조립.
      ia_alert_history.reasoning(text) + reasoning_json(JSONB) 동시 적재 (dual-write).

설계 근거:
  - 02 프로세스 설계서 §2.3, §3.4 (스키마 v1.0)
  - 04 수정 리스트 M-01 (macro_news_layer.py 주입 지점)
  - reasoning_json 스키마: docs/reasoning_v1.json (N-22)

핵심 책임:
  - reasoning_text 생성 (사람이 읽는 1줄, 기존 형식과 호환)
  - reasoning_json 생성 (감사·회귀분석용 구조화 데이터)
  - max_factors 제한 (5KB 이벤트당 제약 충족)
  - SYSTEM_DEGRADED 특수 케이스 처리
"""

from __future__ import annotations

from typing import Literal

from core.logger import get_logger
from detection.dq_monitor import DataQualityState

VERSION = "1.0.0"
SCHEMA_VERSION = "1.0"

logger = get_logger(__name__)

# AlertLevel — macro_news_layer.py와 동일하게 정의 (순환 import 방지)
# Phase 1 M-01 적용 후엔 detection.macro_news_layer 에서 import 해도 무방
AlertLevel = Literal["L1", "L2", "L3", "NONE", "SYSTEM_DEGRADED"]


class ReasoningBuilder:
    """
    제목: 표준화 reasoning 조립기
    내용: 판정 결과 + 부수 데이터를 받아 (text, json) 튜플로 반환.

    호출 예 (M-01 적용 후 macro_news_layer.detect()):
        builder = ReasoningBuilder()
        text, json_obj = builder.build(
            level="L1",
            score=8.2,
            news_score=7.0,
            yt_bonus=0.78,
            semantic_bonus=0.0,
            thresholds_used={"l1": 6.5, "l2": 4.0, "health_l1": 0.85},
            market_profile="intraday",
            contributing_factors=[{"factor": "tier_s_auto_l1", ...}],
            health_components={"diversity": 1.0, "recency": 0.7, ...},
            dq_state=None,
            policy_version="v1.0.0",
        )
    """

    DEFAULT_MAX_FACTORS: int = 10

    def __init__(self, max_factors: int = DEFAULT_MAX_FACTORS) -> None:
        """
        제목: ReasoningBuilder 초기화

        Args:
            max_factors: contributing_factors 배열 최대 길이 (NFR-04 5KB 제약 보호)
        """
        self.max_factors = max_factors
        logger.info(
            f"[ReasoningBuilder] v{VERSION} schema={SCHEMA_VERSION} "
            f"max_factors={self.max_factors}"
        )

    def build(
        self,
        level: AlertLevel,
        score: float,
        news_score: float,
        yt_bonus: float,
        semantic_bonus: float,
        thresholds_used: dict[str, float],
        market_profile: str,
        contributing_factors: list[dict],
        health_components: dict[str, float],
        dq_state: DataQualityState | None,
        policy_version: str,
    ) -> tuple[str, dict]:
        """
        제목: reasoning 조립
        내용: 텍스트 reasoning(1줄)과 JSON reasoning(스키마 v1.0)을 동시 반환.

        처리 플로우:
          1. dq_state 존재 여부 확인 (degraded_signals 추출)
          2. JSON 본체 조립 (스키마 v1.0)
          3. contributing_factors 절단 (max_factors 제한)
          4. score_breakdown 반올림 (3자리)
          5. text reasoning 생성
             - SYSTEM_DEGRADED → DQ 사유 요약
             - 기타 → level + score + market_profile + top_factor

        Args:
            level: 판정 레벨
            score: 최종 Macro-News Score
            news_score: 뉴스 부분 점수
            yt_bonus: YouTube 보너스
            semantic_bonus: Phase 2 임베딩 보너스 (Phase 1은 0.0)
            thresholds_used: 적용된 임계값 (l1, l2, health_l1 등)
            market_profile: 'intraday' | 'extended' | 'holiday'
            contributing_factors: [{factor, weight, ...}, ...]
            health_components: {diversity, recency, cross_val, dedup, ...}
            dq_state: DataQualityMonitor 결과 (None 가능)
            policy_version: 적용된 정책 버전 (semver)

        Returns:
            tuple[str, dict]: (reasoning_text, reasoning_json)
        """
        # 1) degraded_signals 추출
        degraded_signals = self._extract_degraded_signals(dq_state)

        # 2) factors 절단 (NFR-04 보호)
        truncated_factors = (
            list(contributing_factors)[: self.max_factors]
            if contributing_factors
            else []
        )

        # 3) JSON 조립 (스키마 v1.0)
        reasoning_json: dict = {
            "version": SCHEMA_VERSION,
            "policy_version": policy_version,
            "market_profile": market_profile,
            "score_breakdown": {
                "news_score": self._round(news_score),
                "yt_bonus": self._round(yt_bonus),
                "semantic_bonus": self._round(semantic_bonus),
            },
            "thresholds_used": dict(thresholds_used) if thresholds_used else {},
            "contributing_factors": truncated_factors,
            "health_components": (
                {k: self._round(v) for k, v in health_components.items()}
                if health_components
                else {}
            ),
            "degraded_signals": degraded_signals,
        }

        # 4) text 생성
        reasoning_text = self._build_text(
            level=level,
            score=score,
            market_profile=market_profile,
            contributing_factors=truncated_factors,
            dq_state=dq_state,
        )

        return reasoning_text, reasoning_json

    # ── 내부 메서드 ──────────────────────────────────────────────

    @staticmethod
    def _extract_degraded_signals(dq_state: DataQualityState | None) -> list[str]:
        """
        제목: degraded_signals 추출
        내용: dq_state가 있고 degraded_flag=True이면 reasons 반환, 아니면 빈 배열.
        """
        if dq_state is None:
            return []
        if not dq_state.degraded_flag:
            return []
        return list(dq_state.degraded_reasons)

    @staticmethod
    def _round(value: float | None) -> float | None:
        """소수점 3자리 반올림. None은 그대로 반환."""
        if value is None:
            return None
        try:
            return round(float(value), 3)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _build_text(
        level: AlertLevel,
        score: float,
        market_profile: str,
        contributing_factors: list[dict],
        dq_state: DataQualityState | None,
    ) -> str:
        """
        제목: 사람이 읽는 reasoning 텍스트 생성
        내용:
          - SYSTEM_DEGRADED  → 'SYSTEM_DEGRADED: <reasons>'
          - 그 외           → '<level> 판정 (score=X.XX, profile=<P>, top_factor=<F>)'
        """
        # SYSTEM_DEGRADED 케이스 — DQ 사유 요약
        if level == "SYSTEM_DEGRADED":
            if dq_state and dq_state.degraded_reasons:
                top_reasons = ", ".join(dq_state.degraded_reasons[:3])
                return f"SYSTEM_DEGRADED: {top_reasons}"
            return "SYSTEM_DEGRADED: data_quality_unknown"

        # 일반 케이스 — top_factor 추출
        top_factor = "score_threshold"
        if contributing_factors:
            first = contributing_factors[0]
            if isinstance(first, dict) and "factor" in first:
                factor_value = first.get("factor")
                if isinstance(factor_value, str) and factor_value:
                    top_factor = factor_value

        return (
            f"{level} 판정 (score={score:.2f}, profile={market_profile}, "
            f"top_factor={top_factor})"
        )
