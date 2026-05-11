"""
제목: SectorAlertEngine 단위 테스트
내용: SHADOW_MODE 분기, 쿨다운 prefix, audit_persisted, B5 fallback.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from detection.sector_alert_engine import (
    COOLDOWN_LEVEL_PREFIX,
    SECTOR_PUBLISH_POLICY,
    SectorAlertEngine,
)
from detection.sector_flow_layer import SectorRotationResult


def _make_result(level: str = "L2", rotation: str = "DEFENSIVE_ROTATION") -> SectorRotationResult:
    """제목: 테스트용 SectorRotationResult 생성기"""
    return SectorRotationResult(
        level=level,
        rotation_type=rotation,
        spread_1d=0.5,
        spread_5d=1.8,
        def_avg_1d=0.4,
        cyc_avg_1d=-0.1,
        def_avg_5d=0.9,
        cyc_avg_5d=-0.9,
        reasoning="test reasoning",
        policy_version="sector-v1.0.0",
        health_score=0.95,
        rows_used=30,
        reasoning_json={"schema_version": "sector-1.0", "level": level},
    )


def test_shadow_mode_blocks_free_and_paid(monkeypatch) -> None:
    """제목: SHADOW_MODE=true — tg_free/tg_paid 강제 False, tg_internal만 True"""
    monkeypatch.setenv("SECTOR_SHADOW_MODE", "true")

    mock_store = MagicMock()
    mock_store.is_cooldown_active.return_value = False
    mock_store.save_alert.return_value = True

    engine = SectorAlertEngine(alert_store=mock_store)
    signal = engine.process(_make_result(level="L2"))

    assert signal.publish_tg_free is False
    assert signal.publish_tg_paid is False
    assert signal.publish_tg_internal is True
    assert signal.shadow_mode is True


def test_production_mode_enables_free_paid(monkeypatch) -> None:
    """제목: SHADOW_MODE=false — L2면 tg_free/tg_paid 활성화"""
    monkeypatch.setenv("SECTOR_SHADOW_MODE", "false")

    mock_store = MagicMock()
    mock_store.is_cooldown_active.return_value = False
    mock_store.save_alert.return_value = True

    engine = SectorAlertEngine(alert_store=mock_store)
    signal = engine.process(_make_result(level="L2"))

    assert signal.publish_tg_free is True
    assert signal.publish_tg_paid is True
    assert signal.publish_tg_internal is True
    assert signal.shadow_mode is False


def test_cooldown_active_blocks_all_publish(monkeypatch) -> None:
    """제목: 쿨다운 활성 — 모든 채널 False"""
    monkeypatch.setenv("SECTOR_SHADOW_MODE", "false")

    mock_store = MagicMock()
    mock_store.is_cooldown_active.return_value = True
    mock_store.save_alert.return_value = True

    engine = SectorAlertEngine(alert_store=mock_store)
    signal = engine.process(_make_result(level="L2"))

    assert signal.is_cooldown_active is True
    assert signal.publish_tg_free is False
    assert signal.publish_tg_paid is False
    assert signal.publish_tg_internal is False
    assert signal.should_publish is False


def test_cooldown_prefix_applied(monkeypatch) -> None:
    """제목: 쿨다운 조회 시 'sector:L2' prefix가 전달되는지"""
    monkeypatch.setenv("SECTOR_SHADOW_MODE", "true")

    mock_store = MagicMock()
    mock_store.is_cooldown_active.return_value = False
    mock_store.save_alert.return_value = True

    engine = SectorAlertEngine(alert_store=mock_store)
    engine.process(_make_result(level="L2"))

    mock_store.is_cooldown_active.assert_called_with(f"{COOLDOWN_LEVEL_PREFIX}L2")


def test_none_level_no_publish_no_save(monkeypatch) -> None:
    """제목: NONE 레벨 — 모든 채널 False + save_alert 호출 안 함"""
    monkeypatch.setenv("SECTOR_SHADOW_MODE", "true")

    mock_store = MagicMock()
    mock_store.is_cooldown_active.return_value = False

    engine = SectorAlertEngine(alert_store=mock_store)
    signal = engine.process(_make_result(level="NONE", rotation="NONE"))

    assert signal.publish_tg_internal is False
    mock_store.save_alert.assert_not_called()


def test_save_alert_failure_triggers_audit_fallback(monkeypatch, tmp_path) -> None:
    """제목: save_alert 실패 시 audit_persisted=False + audit_fallback 호출"""
    monkeypatch.setenv("SECTOR_SHADOW_MODE", "true")

    mock_store = MagicMock()
    mock_store.is_cooldown_active.return_value = False
    mock_store.save_alert.return_value = False  # 실패

    # audit_fallback 파일 경로를 tmp_path로 우회
    tmp_path / "audit.jsonl"
    monkeypatch.chdir(tmp_path)

    engine = SectorAlertEngine(alert_store=mock_store)
    signal = engine.process(_make_result(level="L2"))

    assert signal.audit_persisted is False
    # logs/alert_audit_fallback.jsonl 파일 생성 확인
    expected_fallback = tmp_path / "logs" / "alert_audit_fallback.jsonl"
    assert expected_fallback.exists()


def test_publish_policy_definition() -> None:
    """제목: SECTOR_PUBLISH_POLICY 표준 — L1=internal only, L2=internal+free+paid, NONE=all false"""
    assert SECTOR_PUBLISH_POLICY["L1"]["tg_internal"] is True
    assert SECTOR_PUBLISH_POLICY["L1"]["tg_free"] is False
    assert SECTOR_PUBLISH_POLICY["L2"]["tg_internal"] is True
    assert SECTOR_PUBLISH_POLICY["L2"]["tg_free"] is True
    assert SECTOR_PUBLISH_POLICY["NONE"]["tg_internal"] is False
