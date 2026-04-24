from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass(frozen=True)
class SentRecord:
    digest: str
    sent_at: datetime


class SentAlertRegistry:
    """Persistent dedupe registry to prevent re-sending previously announced alerts."""

    def __init__(self, path: str = ".state/sent_alerts.json", retention_days: int = 30) -> None:
        self.path = Path(path)
        self.retention = timedelta(days=retention_days)

    def _digest(self, key: str) -> str:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _atomic_save(self, data: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(temp_path, self.path)
        os.chmod(self.path, 0o600)

    def prune(self, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        data = self._load()
        kept: dict[str, str] = {}
        for digest, sent_at in data.items():
            ts = datetime.fromisoformat(sent_at)
            if now - ts <= self.retention:
                kept[digest] = sent_at
        self._atomic_save(kept)

    def already_sent(self, key: str) -> bool:
        data = self._load()
        return self._digest(key) in data

    def mark_sent(self, key: str, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        data = self._load()
        data[self._digest(key)] = now.isoformat()
        self._atomic_save(data)
