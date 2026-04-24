from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AlertLevel(StrEnum):
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


@dataclass(frozen=True)
class LayerScores:
    price: float
    derivatives: float
    macro_news: float

    @property
    def total(self) -> float:
        return self.price + self.derivatives + self.macro_news


@dataclass(frozen=True)
class AlertEvent:
    alert_id: str
    symbol: str
    scores: LayerScores
    quality_ok: bool = True

    def level(self) -> AlertLevel:
        score = self.scores.total
        if score >= 80:
            base = AlertLevel.L1
        elif score >= 50:
            base = AlertLevel.L2
        else:
            base = AlertLevel.L3

        if self.quality_ok:
            return base

        if base == AlertLevel.L1:
            return AlertLevel.L2
        if base == AlertLevel.L2:
            return AlertLevel.L3
        return AlertLevel.L3
