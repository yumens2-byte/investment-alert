"""
제목: ReasoningBuilder 단위 테스트 (Phase 1 / N-07)
내용: detection/reasoning_builder.py의 8개 케이스를 검증한다.

테스트 매트릭스:
  1. L1 reasoning에 top_factor가 포함됨
  2. SYSTEM_DEGRADED 텍스트에 degraded reasons 포함
  3. JSON version은 항상 '1.0'
  4. policy_version 그대로 전달
  5. factors가 max_factors로 잘림
  6. dq_state=None이면 degraded_signals=[]
  7. score_breakdown은 3자리 반올림
  8. health_components 그대로 패스스루 (3자리 반올림)
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from detection.dq_monitor import DataQualityState
from detection.reasoning_builder import (
    SCHEMA_VERSION,
    ReasoningBuilder,
)

# ── helpers ──────────────────────────────────────────────────────


def _build_dq_degraded(reasons: list[str]) -> DataQualityState:
    """degraded=True인 DataQualityState 생성"""
    return DataQualityState(
        fresh_event_ratio=0.0,
        source_success_rate=0.25,
        lag_seconds_p95=12.0,
        volume_zscore=None,
        degraded_flag=True,
        degraded_reasons=list(reasons),
        cycle_started_at=datetime.now(UTC),
        cycle_finished_at=datetime.now(UTC),
        source_results={},
    )


def _default_kwargs() -> dict:
    """build() 기본 인자 (테스트마다 일부 override)"""
    return dict(
        level="L1",
        score=8.2,
        news_score=7.0,
        yt_bonus=0.78,
        semantic_bonus=0.0,
        thresholds_used={"l1": 6.5, "l2": 4.0, "health_l1": 0.85},
        market_profile="intraday",
        contributing_factors=[
            {"factor": "tier_s_auto_l1", "weight": None, "matched_source": "fed_rss"}
        ],
        health_components={"diversity": 1.0, "recency": 0.7},
        dq_state=None,
        policy_version="v1.0.0",
    )


# ── tests ────────────────────────────────────────────────────────


def test_l1_reasoning_includes_top_factor() -> None:
    """1. L1 텍스트에 contributing_factors[0].factor가 포함된다"""
    b = ReasoningBuilder()
    text, _ = b.build(**_default_kwargs())
    assert "L1" in text
    assert "tier_s_auto_l1" in text
    assert "score=8.20" in text


def test_system_degraded_text_includes_reasons() -> None:
    """2. SYSTEM_DEGRADED 텍스트에 degraded_reasons가 ', '로 결합되어 포함된다"""
    b = ReasoningBuilder()
    kwargs = _default_kwargs()
    kwargs["level"] = "SYSTEM_DEGRADED"
    kwargs["score"] = 0.0
    kwargs["dq_state"] = _build_dq_degraded(
        ["source_success_rate=0.25<0.50", "fresh_event_ratio=0.00<0.10"]
    )
    kwargs["contributing_factors"] = []

    text, j = b.build(**kwargs)
    assert text.startswith("SYSTEM_DEGRADED:")
    assert "source_success_rate" in text
    assert "fresh_event_ratio" in text
    # degraded_signals JSON 배열도 확인
    assert len(j["degraded_signals"]) == 2


def test_json_version_is_1_0() -> None:
    """3. reasoning_json.version은 항상 '1.0' (스키마 고정)"""
    b = ReasoningBuilder()
    _, j = b.build(**_default_kwargs())
    assert j["version"] == SCHEMA_VERSION
    assert j["version"] == "1.0"


def test_policy_version_passed_through() -> None:
    """4. policy_version은 변형 없이 그대로 전달"""
    b = ReasoningBuilder()
    kwargs = _default_kwargs()
    kwargs["policy_version"] = "v2.5.7"
    _, j = b.build(**kwargs)
    assert j["policy_version"] == "v2.5.7"


def test_factors_truncated_to_max() -> None:
    """5. contributing_factors는 max_factors까지만 유지된다"""
    b = ReasoningBuilder(max_factors=3)
    kwargs = _default_kwargs()
    kwargs["contributing_factors"] = [
        {"factor": f"factor_{i}", "weight": float(i)} for i in range(10)
    ]
    _, j = b.build(**kwargs)
    assert len(j["contributing_factors"]) == 3
    # 앞에서부터 절단 (factor_0, factor_1, factor_2)
    assert j["contributing_factors"][0]["factor"] == "factor_0"
    assert j["contributing_factors"][2]["factor"] == "factor_2"


def test_dq_none_yields_empty_degraded_signals() -> None:
    """6. dq_state=None이면 degraded_signals=[]"""
    b = ReasoningBuilder()
    _, j = b.build(**_default_kwargs())
    assert j["degraded_signals"] == []


def test_score_breakdown_rounded_to_3_digits() -> None:
    """7. score_breakdown 모든 값은 소수점 3자리로 반올림"""
    b = ReasoningBuilder()
    kwargs = _default_kwargs()
    kwargs["news_score"] = 7.123456789
    kwargs["yt_bonus"] = 0.7891234
    kwargs["semantic_bonus"] = 0.4445555
    _, j = b.build(**kwargs)
    sb = j["score_breakdown"]
    assert sb["news_score"] == 7.123
    assert sb["yt_bonus"] == 0.789
    # 0.4445555 → 0.445 (Python round half to even: 0.4445 → 0.444이지만 0.4445555는 0.445)
    assert sb["semantic_bonus"] == pytest.approx(0.445, abs=0.001)


def test_health_components_passthrough_with_rounding() -> None:
    """8. health_components는 그대로 패스스루하되 값은 3자리 반올림"""
    b = ReasoningBuilder()
    kwargs = _default_kwargs()
    kwargs["health_components"] = {
        "diversity": 1.0,
        "recency": 0.7777777,
        "cross_val": 0.6,
        "dedup": 0.85,
    }
    _, j = b.build(**kwargs)
    hc = j["health_components"]
    assert hc["diversity"] == 1.0
    assert hc["recency"] == 0.778
    assert hc["cross_val"] == 0.6
    assert hc["dedup"] == 0.85
    # 키 모두 보존
    assert set(hc.keys()) == {"diversity", "recency", "cross_val", "dedup"}
