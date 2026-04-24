from pathlib import Path

from investment_alert.models import AlertEvent, LayerScores
from investment_alert.policy import ReviewStatus, SourcePolicy
from investment_alert.runner import AlertRunner
from investment_alert.state import SentAlertRegistry


def _event() -> AlertEvent:
    return AlertEvent(
        alert_id="R-1",
        symbol="BTC",
        scores=LayerScores(price=30, derivatives=30, macro_news=25),
        quality_ok=True,
    )


def _policies() -> list[SourcePolicy]:
    return [SourcePolicy("fred", ReviewStatus.APPROVED, True)]


def test_runner_blocks_previous_announcement(tmp_path: Path) -> None:
    state_path = tmp_path / "sent.json"
    runner = AlertRunner(state_path=str(state_path))

    first = runner.dispatch_once("run-1", _event(), _policies(), 70)
    second = runner.dispatch_once("run-2", _event(), _policies(), 70)

    assert first.sent is True
    assert second.sent is False
    assert second.reason == "already_sent"


def test_registry_persists_hashed_keys(tmp_path: Path) -> None:
    state_path = tmp_path / "sent.json"
    registry = SentAlertRegistry(path=str(state_path))
    raw_key = "A-100:BTC:L1"

    registry.mark_sent(raw_key)
    content = state_path.read_text(encoding="utf-8")

    assert raw_key not in content
    assert registry.already_sent(raw_key) is True
