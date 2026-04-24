from __future__ import annotations

from enum import StrEnum


class QuotaMode(StrEnum):
    NORMAL = "normal"
    STOP_EXPERIMENTAL = "stop_experimental"
    LOW_FREQ_BACKUP = "low_freq_backup"
    CORE_ONLY = "core_only"


class QuotaManager:
    def mode_for_usage(self, usage_percent: float) -> QuotaMode:
        if usage_percent >= 95:
            return QuotaMode.CORE_ONLY
        if usage_percent >= 90:
            return QuotaMode.LOW_FREQ_BACKUP
        if usage_percent >= 80:
            return QuotaMode.STOP_EXPERIMENTAL
        return QuotaMode.NORMAL
