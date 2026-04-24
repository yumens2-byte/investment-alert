from __future__ import annotations

from dataclasses import dataclass

from .audit import AuditRecord, InMemoryAuditStore
from .models import AlertEvent, AlertLevel
from .policy import SourcePolicy, SourcePolicyGate
from .quota import QuotaManager, QuotaMode
from .routing import ChannelRouter


@dataclass(frozen=True)
class PilotOutcome:
    run_id: str
    sent: bool
    blocked_reasons: tuple[str, ...]
    channels: tuple[str, ...]
    quota_mode: QuotaMode


class PilotExecutor:
    """Small orchestration helper for pilot validation scenarios."""

    def __init__(self) -> None:
        self.policy_gate = SourcePolicyGate()
        self.quota_manager = QuotaManager()
        self.router = ChannelRouter()
        self.audit_store = InMemoryAuditStore()

    def run(
        self,
        run_id: str,
        event: AlertEvent,
        policies: list[SourcePolicy],
        quota_usage_percent: float,
    ) -> PilotOutcome:
        policy_ok, blocked = self.policy_gate.validate_for_deploy(policies)
        quota_mode = self.quota_manager.mode_for_usage(quota_usage_percent)
        level = event.level()
        channels = self.router.channels_for(level)

        dedupe_key = f"{event.symbol}:{level}"
        sent = policy_ok and self.router.can_send(dedupe_key)
        if quota_mode == QuotaMode.CORE_ONLY and level != AlertLevel.L1:
            sent = False

        if sent:
            self.audit_store.save(
                AuditRecord(
                    alert_id=event.alert_id,
                    score_total=event.scores.total,
                    evidence_sources=tuple(policy.source_name for policy in policies),
                    delivery_results={channel: "ok" for channel in channels},
                )
            )

        return PilotOutcome(
            run_id=run_id,
            sent=sent,
            blocked_reasons=tuple(blocked),
            channels=channels,
            quota_mode=quota_mode,
        )
