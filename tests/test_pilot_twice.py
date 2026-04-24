from pathlib import Path

from investment_alert.models import AlertEvent, LayerScores
from investment_alert.policy import ReviewStatus, SourcePolicy
from investment_alert.runner import AlertRunner


def test_pilot_twice_first_send_second_dedupe(tmp_path: Path) -> None:
    runner = AlertRunner(state_path=str(tmp_path / "pilot_twice.json"))
    event = AlertEvent(
        alert_id="pilot-twice-btc",
        symbol="BTC",
        scores=LayerScores(price=30, derivatives=30, macro_news=25),
        quality_ok=True,
    )
    policies = [SourcePolicy("fred", ReviewStatus.APPROVED, True)]

    first = runner.dispatch_once("pilot-1", event, policies, 70)
    second = runner.dispatch_once("pilot-2", event, policies, 70)

    assert first.sent is True
    assert first.reason == "sent"
    assert second.sent is False
    assert second.reason == "already_sent"
