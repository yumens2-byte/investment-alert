"""Domain models for the isolated US-market engagement loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class Slot(StrEnum):
    """Supported recurring-content slots."""

    THREE_NUMBERS = "three_numbers"
    SCENARIO_POLL = "scenario_poll"
    SCORECARD = "scorecard"
    NEXT_WEEK_CALENDAR = "next_week_calendar"


class LoopStatus(StrEnum):
    """Lifecycle states persisted in Supabase."""

    PLANNED = "planned"
    OPEN = "open"
    POLLING = "polling"
    READY_TO_SCORE = "ready_to_score"
    CLOSED = "closed"
    SKIPPED_INPUT = "skipped_input"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Fact:
    """A sourced market value used by a loop."""

    key: str
    value: Decimal
    unit: str
    as_of: datetime
    source_url: str
    source_name: str
    retrieved_at: datetime

    def __post_init__(self) -> None:
        if not self.key.strip() or not self.unit.strip() or not self.source_name.strip():
            raise ValueError("Fact key, unit, and source_name are required")
        if self.as_of.tzinfo is None or self.retrieved_at.tzinfo is None:
            raise ValueError("Fact timestamps must be timezone-aware")
        if not self.source_url.startswith("https://"):
            raise ValueError("Fact source_url must use HTTPS")
        if not self.value.is_finite():
            raise ValueError("Fact value must be finite")
        if self.as_of > self.retrieved_at:
            raise ValueError("Fact as_of cannot be later than retrieved_at")

    def to_row(self, loop_id: str, phase: str) -> dict[str, Any]:
        """Return a Supabase-compatible row without float precision loss."""

        return {
            "loop_id": loop_id,
            "phase": phase,
            "fact_key": self.key,
            "value": str(self.value),
            "unit": self.unit,
            "as_of": self.as_of.isoformat(),
            "source_url": self.source_url,
            "source_name": self.source_name,
            "retrieved_at": self.retrieved_at.isoformat(),
        }


@dataclass(frozen=True)
class Criterion:
    """An immutable, pre-announced scoring criterion."""

    fact_key: str
    operator: str
    thresholds: tuple[Decimal, ...]
    interpretation: str

    def __post_init__(self) -> None:
        valid_operators = {"gt", "gte", "lt", "lte", "between"}
        if self.operator not in valid_operators:
            raise ValueError(f"Unsupported criterion operator: {self.operator}")
        expected = 2 if self.operator == "between" else 1
        if len(self.thresholds) != expected:
            raise ValueError(f"{self.operator} requires {expected} threshold value(s)")
        if not self.fact_key.strip() or not self.interpretation.strip():
            raise ValueError("Criterion fact_key and interpretation are required")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""

        return {
            "fact_key": self.fact_key,
            "operator": self.operator,
            "thresholds": [str(value) for value in self.thresholds],
            "interpretation": self.interpretation,
        }


@dataclass(frozen=True)
class EngagementLoop:
    """Aggregate persisted as one weekly engagement loop."""

    loop_id: str
    week_start_kst: date
    status: LoopStatus = LoopStatus.PLANNED
    criteria: tuple[Criterion, ...] = field(default_factory=tuple)
    schema_version: str = "1.0"

    def __post_init__(self) -> None:
        if not self.loop_id.strip():
            raise ValueError("loop_id is required")
        if self.week_start_kst.weekday() != 0:
            raise ValueError("week_start_kst must be a Monday")
        fact_keys = [criterion.fact_key for criterion in self.criteria]
        if len(fact_keys) != len(set(fact_keys)):
            raise ValueError("criteria fact_key values must be unique")
        active_statuses = {
            LoopStatus.OPEN,
            LoopStatus.POLLING,
            LoopStatus.READY_TO_SCORE,
            LoopStatus.CLOSED,
        }
        if self.status in active_statuses and len(self.criteria) != 3:
            raise ValueError("Active engagement loops require exactly three criteria")

    def to_row(self) -> dict[str, Any]:
        """Return the row stored in ``ia_engagement_loops``."""

        return {
            "loop_id": self.loop_id,
            "schema_version": self.schema_version,
            "week_start_kst": self.week_start_kst.isoformat(),
            "status": self.status.value,
            "criteria": [criterion.to_dict() for criterion in self.criteria],
        }
