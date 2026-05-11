"""
제목: run_sector_alert.py 통합 테스트
내용: 휴장일 분기, DRY_RUN, SHADOW 발행 흐름을 Mock 기반으로 검증.
      실제 외부 호출(Yahoo, Supabase, Telegram) 없음.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def env_dry_run_shadow(monkeypatch):
    """제목: DRY_RUN=true + SHADOW_MODE=true 환경"""
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("SECTOR_SHADOW_MODE", "true")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "test_key")


def test_holiday_skips_pipeline(monkeypatch, env_dry_run_shadow, tmp_path) -> None:
    """제목: 휴장일 — Step 1에서 조기 종료 (수집기 호출 안 함)"""
    monkeypatch.chdir(tmp_path)

    import run_sector_alert

    with patch.object(run_sector_alert, "get_market_profile", return_value="holiday"):
        with patch.object(run_sector_alert, "SectorCollector") as MockCollector:
            with pytest.raises(SystemExit) as exc:
                run_sector_alert.main()
            assert exc.value.code == 0
            # 수집기는 인스턴스화되지 않음
            MockCollector.assert_not_called()


def test_dry_run_l2_flow_publishes_internal_only(monkeypatch, env_dry_run_shadow, tmp_path) -> None:
    """제목: SHADOW_MODE=true + L2 감지 — TG Internal만 호출, Free/Paid는 호출 안 함"""
    monkeypatch.chdir(tmp_path)

    import run_sector_alert
    from detection.sector_flow_layer import SectorRotationResult

    # 정상 감지 결과 (L2 DEFENSIVE_ROTATION)
    mock_result = SectorRotationResult(
        level="L2",
        rotation_type="DEFENSIVE_ROTATION",
        spread_1d=0.5,
        spread_5d=1.8,
        def_avg_1d=0.4,
        cyc_avg_1d=-0.1,
        def_avg_5d=0.9,
        cyc_avg_5d=-0.9,
        reasoning="test",
        policy_version="sector-v1.0.0",
        health_score=0.95,
        rows_used=30,
        reasoning_json={"schema_version": "sector-1.0"},
    )

    mock_tg = MagicMock()
    mock_collector = MagicMock()
    mock_collector.collect_sector_changes.return_value = {
        "XLV": [{"date": "2026-05-09", "chg_pct": 0.4}],
        "XLU": [{"date": "2026-05-09", "chg_pct": 0.4}],
        "XLP": [{"date": "2026-05-09", "chg_pct": 0.4}],
        "XLI": [{"date": "2026-05-09", "chg_pct": -0.4}],
        "XLRE": [{"date": "2026-05-09", "chg_pct": -0.4}],
        "XLB": [{"date": "2026-05-09", "chg_pct": -0.4}],
    }
    mock_layer = MagicMock()
    mock_layer.detect.return_value = mock_result
    mock_store = MagicMock()
    mock_store.upsert_daily_rows.return_value = True

    mock_alert_store = MagicMock()
    mock_alert_store.is_cooldown_active.return_value = False
    mock_alert_store.save_alert.return_value = True

    with patch.object(run_sector_alert, "get_market_profile", return_value="extended"), \
         patch.object(run_sector_alert, "SectorCollector", return_value=mock_collector), \
         patch.object(run_sector_alert, "SectorFlowStore", return_value=mock_store), \
         patch.object(run_sector_alert, "SectorFlowLayer", return_value=mock_layer), \
         patch.object(run_sector_alert, "AlertStore", return_value=mock_alert_store), \
         patch.object(run_sector_alert, "TelegramPublisher", return_value=mock_tg), \
         patch.object(run_sector_alert, "_publish_jitter"):
        run_sector_alert.main()

    # TG Internal만 호출, Free/Paid는 호출 안 됨
    mock_tg.publish_internal.assert_called_once()
    mock_tg.publish_free.assert_not_called()
    mock_tg.publish_paid.assert_not_called()
    # 쿨다운은 sector:L2 prefix로 설정됨
    mock_alert_store.set_cooldown.assert_called_once()
    cooldown_args = mock_alert_store.set_cooldown.call_args
    assert "sector:L2" in str(cooldown_args)


def test_none_level_skips_publish(monkeypatch, env_dry_run_shadow, tmp_path) -> None:
    """제목: NONE 레벨 — Step 7에서 조기 종료, 어떤 publish도 호출 안 함"""
    monkeypatch.chdir(tmp_path)

    import run_sector_alert
    from detection.sector_flow_layer import SectorRotationResult

    none_result = SectorRotationResult(
        level="NONE", rotation_type="NONE",
        spread_1d=0.1, spread_5d=0.3,
        def_avg_1d=0.0, cyc_avg_1d=-0.1,
        def_avg_5d=0.0, cyc_avg_5d=-0.3,
        reasoning="NONE — 임계 미달",
        policy_version="sector-v1.0.0",
        health_score=1.0,
        rows_used=30,
        reasoning_json={},
    )

    mock_tg = MagicMock()
    mock_collector = MagicMock()
    mock_collector.collect_sector_changes.return_value = {
        t: [{"date": "2026-05-09", "chg_pct": 0.0}] for t in
        ["XLV", "XLU", "XLP", "XLI", "XLRE", "XLB"]
    }
    mock_layer = MagicMock()
    mock_layer.detect.return_value = none_result
    mock_store = MagicMock()
    mock_store.upsert_daily_rows.return_value = True
    mock_alert_store = MagicMock()

    with patch.object(run_sector_alert, "get_market_profile", return_value="extended"), \
         patch.object(run_sector_alert, "SectorCollector", return_value=mock_collector), \
         patch.object(run_sector_alert, "SectorFlowStore", return_value=mock_store), \
         patch.object(run_sector_alert, "SectorFlowLayer", return_value=mock_layer), \
         patch.object(run_sector_alert, "AlertStore", return_value=mock_alert_store), \
         patch.object(run_sector_alert, "TelegramPublisher", return_value=mock_tg):
        with pytest.raises(SystemExit) as exc:
            run_sector_alert.main()
        assert exc.value.code == 0

    mock_tg.publish_internal.assert_not_called()
    mock_alert_store.set_cooldown.assert_not_called()


def test_upsert_failure_continues_to_detect(monkeypatch, env_dry_run_shadow, tmp_path) -> None:
    """제목: 적재 실패 — 감지 단계는 계속 진행 (격리)"""
    monkeypatch.chdir(tmp_path)

    import run_sector_alert
    from detection.sector_flow_layer import SectorRotationResult

    mock_collector = MagicMock()
    mock_collector.collect_sector_changes.return_value = {
        t: [{"date": "2026-05-09", "chg_pct": 0.0}] for t in
        ["XLV", "XLU", "XLP", "XLI", "XLRE", "XLB"]
    }
    mock_store = MagicMock()
    mock_store.upsert_daily_rows.return_value = False  # 적재 실패
    mock_layer = MagicMock()
    mock_layer.detect.return_value = SectorRotationResult(
        level="NONE", rotation_type="NONE",
        spread_1d=0.0, spread_5d=0.0,
        def_avg_1d=0.0, cyc_avg_1d=0.0,
        def_avg_5d=0.0, cyc_avg_5d=0.0,
        reasoning="NONE", policy_version="sector-v1.0.0",
        health_score=0.5, rows_used=6, reasoning_json={},
    )
    mock_alert_store = MagicMock()
    mock_tg = MagicMock()

    with patch.object(run_sector_alert, "get_market_profile", return_value="extended"), \
         patch.object(run_sector_alert, "SectorCollector", return_value=mock_collector), \
         patch.object(run_sector_alert, "SectorFlowStore", return_value=mock_store), \
         patch.object(run_sector_alert, "SectorFlowLayer", return_value=mock_layer), \
         patch.object(run_sector_alert, "AlertStore", return_value=mock_alert_store), \
         patch.object(run_sector_alert, "TelegramPublisher", return_value=mock_tg):
        with pytest.raises(SystemExit):
            run_sector_alert.main()

    # detect는 호출됨 (적재 실패 격리)
    mock_layer.detect.assert_called_once()
