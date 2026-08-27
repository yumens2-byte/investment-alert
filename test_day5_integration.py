"""
제목: Day 5 통합 테스트 — M-02~M-06 + audit_fallback 동작 검증
내용: PUBLISH_POLICY 5x4, tg_internal 발행, save_alert 실패 시 fallback,
      SYSTEM_DEGRADED 흐름을 end-to-end로 검증한다.

테스트 매트릭스:
  1. PUBLISH_POLICY는 5레벨 × 4채널 모두 정의됨
  2. L3 레벨에서 tg_internal만 발행 (FR-04)
  3. SYSTEM_DEGRADED 레벨에서 tg_internal만 발행
  4. NONE 레벨에서 모든 채널 false (회귀 보호)
  5. save_alert 성공 시 audit_persisted=True
  6. save_alert 실패 시 audit_persisted=False + fallback JSONL 생성
  7. AlertSignal.dq_state_dict가 result.dq_state.to_dict() 결과를 보유
  8. update_publish_result에 tg_internal 인자 전달됨
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from detection.alert_engine import (
    PUBLISH_POLICY,
    AlertEngine,
)
from detection.macro_news_layer import MacroNewsResult

# ── helpers ─────────────────────────────────────────────


def _build_macro_result(
    level: str = "L1",
    score: float = 8.0,
    reasoning_json: dict | None = None,
    dq_state=None,
) -> MacroNewsResult:
    """간단한 MacroNewsResult 인스턴스 생성"""
    return MacroNewsResult(
        score=score,
        level=level,  # type: ignore[arg-type]
        news_events=[],
        youtube_events=[],
        news_score=0.0,
        youtube_bonus=0.0,
        top_news=[],
        top_youtube=[],
        reasoning="test reasoning",
        health_score=0.9,
        reasoning_json=reasoning_json or {"version": "1.0", "policy_version": "v1.0.0"},
        policy_version="v1.0.0",
        dq_state=dq_state,
    )


# ── tests ───────────────────────────────────────────────


def test_publish_policy_has_5_levels_x_4_channels() -> None:
    """1. PUBLISH_POLICY는 5레벨 × 4채널 (FR-03 + FR-04)"""
    expected_levels = {"L1", "L2", "L3", "SYSTEM_DEGRADED", "NONE"}
    expected_channels = {"x", "tg_free", "tg_paid", "tg_internal"}
    assert set(PUBLISH_POLICY.keys()) == expected_levels
    for level, channels in PUBLISH_POLICY.items():
        assert set(channels.keys()) == expected_channels, f"{level} 채널 누락"


def test_l3_publishes_only_tg_internal() -> None:
    """2. L3 레벨에서는 tg_internal만 True (FR-04 핵심 변경)"""
    p = PUBLISH_POLICY["L3"]
    assert p["x"] is False
    assert p["tg_free"] is False
    assert p["tg_paid"] is False
    assert p["tg_internal"] is True


def test_system_degraded_publishes_only_tg_internal() -> None:
    """3. SYSTEM_DEGRADED 레벨에서도 tg_internal만 True"""
    p = PUBLISH_POLICY["SYSTEM_DEGRADED"]
    assert p["x"] is False
    assert p["tg_free"] is False
    assert p["tg_paid"] is False
    assert p["tg_internal"] is True


def test_none_publishes_no_channel() -> None:
    """4. NONE 레벨은 모든 채널 false (회귀 보호)"""
    p = PUBLISH_POLICY["NONE"]
    assert all(v is False for v in p.values())


def test_save_alert_success_sets_audit_persisted_true() -> None:
    """5. save_alert가 True 반환 시 signal.audit_persisted=True"""
    mock_store = MagicMock()
    mock_store.save_alert.return_value = True
    mock_store.is_cooldown_active.return_value = False
    mock_store.is_topic_cooldown_active.return_value = False

    engine = AlertEngine(alert_store=mock_store)
    result = _build_macro_result(level="L2", score=5.5)
    signal = engine.process(result)

    assert signal.audit_persisted is True


def test_save_alert_failure_creates_fallback_jsonl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """6. save_alert 예외 시 audit_persisted=False + fallback JSONL 생성"""
    # fallback 경로를 임시 디렉토리로 변경
    fallback_file = tmp_path / "audit_fallback.jsonl"
    monkeypatch.setattr(
        "core.audit_fallback.DEFAULT_FALLBACK_FILE",
        fallback_file,
    )

    mock_store = MagicMock()
    mock_store.save_alert.side_effect = RuntimeError("Supabase down")
    mock_store.is_cooldown_active.return_value = False
    mock_store.is_topic_cooldown_active.return_value = False

    engine = AlertEngine(alert_store=mock_store)
    result = _build_macro_result(level="L1", score=8.0)
    signal = engine.process(result)

    assert signal.audit_persisted is False
    # fallback 파일이 생성되었는가
    assert fallback_file.exists()
    line = fallback_file.read_text(encoding="utf-8").strip()
    assert "save_alert" in line
    assert signal.alert_id in line


def test_dq_state_dict_propagates_from_macro_result() -> None:
    """7. result.dq_state가 있으면 signal.dq_state_dict가 그 to_dict() 결과를 가짐"""
    from detection.dq_monitor import DataQualityState

    mock_store = MagicMock()
    mock_store.save_alert.return_value = True
    mock_store.is_cooldown_active.return_value = False
    mock_store.is_topic_cooldown_active.return_value = False

    dq_state = DataQualityState(
        fresh_event_ratio=0.0,
        source_success_rate=0.25,
        lag_seconds_p95=12.0,
        volume_zscore=None,
        degraded_flag=True,
        degraded_reasons=["source_success_rate=0.25<0.50"],
        cycle_started_at=datetime.now(UTC),
        cycle_finished_at=datetime.now(UTC),
        source_results={"fed_rss": False},
    )

    engine = AlertEngine(alert_store=mock_store)
    result = _build_macro_result(level="SYSTEM_DEGRADED", score=0.0, dq_state=dq_state)
    signal = engine.process(result)

    assert signal.dq_state_dict is not None
    assert signal.dq_state_dict["degraded_flag"] is True
    assert signal.dq_state_dict["source_success_rate"] == 0.25


def test_alert_signal_includes_publish_tg_internal_field() -> None:
    """8. AlertSignal 생성 시 publish_tg_internal 플래그가 정책에 따라 결정됨"""
    mock_store = MagicMock()
    mock_store.save_alert.return_value = True
    mock_store.is_cooldown_active.return_value = False
    mock_store.is_topic_cooldown_active.return_value = False

    engine = AlertEngine(alert_store=mock_store)
    # L3 → tg_internal=True
    result_l3 = _build_macro_result(level="L3", score=4.0)
    signal_l3 = engine.process(result_l3)
    assert signal_l3.publish_tg_internal is True
    assert signal_l3.publish_x is False
    assert signal_l3.publish_tg_free is False
