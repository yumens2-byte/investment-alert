from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class AuditRecord:
    alert_id: str
    score_total: float
    evidence_sources: tuple[str, ...]
    delivery_results: dict[str, str]
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class InMemoryAuditStore:
    def __init__(self) -> None:
        self._records: dict[str, AuditRecord] = {}

    def save(self, record: AuditRecord) -> None:
        self._records[record.alert_id] = record

    def get(self, alert_id: str) -> AuditRecord | None:
        return self._records.get(alert_id)
