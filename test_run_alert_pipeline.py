from unittest.mock import MagicMock

import run_alert
from detection.macro_news_layer import MacroNewsResult


def _result(level: str = "NONE") -> MacroNewsResult:
    return MacroNewsResult(
        score=0.0,
        level=level,
        news_events=[],
        youtube_events=[],
        news_score=0.0,
        youtube_bonus=0.0,
        reasoning="test",
        health_score=1.0,
    )


def test_canonical_run_uses_repository_collectors(monkeypatch) -> None:
    detect = MagicMock(return_value=_result())
    monkeypatch.setattr("detection.macro_news_layer.MacroNewsLayer.detect", detect)

    summary = run_alert.run()

    assert detect.call_count == 1
    assert summary["level"] == "NONE"
    assert summary["alerts_detected"] == 0
    assert summary["alerts_sent"] == 0


def test_canonical_run_no_longer_imports_legacy_modules(monkeypatch) -> None:
    detect = MagicMock(return_value=_result())
    monkeypatch.setattr("detection.macro_news_layer.MacroNewsLayer.detect", detect)

    original_import = __import__

    def guarded_import(name, *args, **kwargs):
        assert name not in {"collectors.news_rss", "collectors.yahoo_finance", "engines.alert_engine"}
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", guarded_import)
    run_alert.run()


def test_canonical_run_isolates_channel_publish_failure(monkeypatch) -> None:
    result = _result("L2")
    signal = MagicMock(
        level="L2", score=5.0, reasoning="test", health_score=1.0,
        alert_id="alert-id", top_news_titles=[], top_youtube_titles=[],
        publish_tg_free=True, publish_tg_paid=True, publish_tg_internal=False,
        publish_x=False, should_publish=True, dq_state_dict=None,
    )
    monkeypatch.setattr("detection.macro_news_layer.MacroNewsLayer.detect", lambda self: result)
    monkeypatch.setattr("detection.alert_engine.AlertEngine.process", lambda self, value: signal)
    monkeypatch.setattr(
        "publishers.telegram_publisher.TelegramPublisher.publish_free",
        MagicMock(side_effect=RuntimeError("free failed")),
    )
    paid = MagicMock(return_value="DRY_RUN")
    monkeypatch.setattr("publishers.telegram_publisher.TelegramPublisher.publish_paid", paid)

    summary = run_alert.run()

    assert summary["alerts_sent"] == 1
    assert summary["published"]["tg_free"] is False
    assert summary["published"]["tg_paid"] is True
    assert "free failed" in summary["errors"]["tg_free"]
    paid.assert_called_once()
