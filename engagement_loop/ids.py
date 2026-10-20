"""Deterministic identifiers for engagement-loop retries."""

from __future__ import annotations

from datetime import date

from engagement_loop.models import Slot


def build_loop_id(day_kst: date) -> str:
    """Build the ISO-week loop identifier for a KST calendar date."""

    iso_year, iso_week, _ = day_kst.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def build_content_id(loop_id: str, slot: Slot, revision: int = 1) -> str:
    """Build a stable content identifier; revisions start at one."""

    if not loop_id.strip():
        raise ValueError("loop_id is required")
    if revision < 1:
        raise ValueError("revision must be at least 1")
    return f"{loop_id}:{slot.value}:v{revision}"
