from __future__ import annotations

from dataclasses import dataclass

from .models import AlertEvent
from .pilot import PilotExecutor
from .policy import SourcePolicy
from .state import SentAlertRegistry


@dataclass(frozen=True)
class DispatchResult:
    alert_id: str
    sent: bool
    reason: str


class AlertRunner:
    """Production-facing orchestration entry to avoid duplicate announcements."""

    def __init__(self, state_path: str = ".state/sent_alerts.json") -> None:
        self.executor = PilotExecutor()
        self.registry = SentAlertRegistry(path=state_path)

    def dispatch_once(
        self,
        run_id: str,
        event: AlertEvent,
        policies: list[SourcePolicy],
        quota_usage_percent: float,
    ) -> DispatchResult:
        dedupe_key = f"{event.alert_id}:{event.symbol}:{event.level()}"
        if self.registry.already_sent(dedupe_key):
            return DispatchResult(event.alert_id, False, "already_sent")

        outcome = self.executor.run(
            run_id=run_id,
            event=event,
            policies=policies,
            quota_usage_percent=quota_usage_percent,
        )
        if outcome.sent:
            self.registry.mark_sent(dedupe_key)
            return DispatchResult(event.alert_id, True, "sent")
        return DispatchResult(event.alert_id, False, "blocked")
