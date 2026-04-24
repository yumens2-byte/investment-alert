from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .models import AlertLevel


@dataclass
class ChannelRouter:
    cooldown_seconds: int = 300
    sent_at: dict[str, datetime] = field(default_factory=dict)

    def channels_for(self, level: AlertLevel) -> tuple[str, ...]:
        if level == AlertLevel.L1:
            return ("telegram_free", "telegram_paid", "x")
        return ("telegram_free",)

    def can_send(self, dedupe_key: str, now: datetime | None = None) -> bool:
        now = now or datetime.now(tz=timezone.utc)
        last = self.sent_at.get(dedupe_key)
        if last and now - last < timedelta(seconds=self.cooldown_seconds):
            return False
        self.sent_at[dedupe_key] = now
        return True
