from __future__ import annotations

from datetime import UTC, datetime

import pytest

from shorts.domain.models import ContentMode, SlotName
from shorts.scheduling.dispatcher import due_slot


def test_summer_morning_slot_uses_edt() -> None:
    claim = due_slot(datetime(2026, 7, 24, 12, 5, tzinfo=UTC))
    assert claim is not None
    assert claim.local_time.hour == 8
    assert claim.slot == SlotName.MORNING
    assert claim.mode == ContentMode.MARKET_DAY


def test_winter_morning_slot_uses_est_without_clock_drift() -> None:
    claim = due_slot(datetime(2026, 12, 7, 13, 5, tzinfo=UTC))
    assert claim is not None
    assert claim.local_time.hour == 8
    assert claim.slot == SlotName.MORNING


def test_summer_night_slot() -> None:
    claim = due_slot(datetime(2026, 7, 25, 2, 10, tzinfo=UTC))
    assert claim is not None
    assert claim.local_time.date().isoformat() == "2026-07-24"
    assert claim.slot == SlotName.NIGHT


def test_weekend_and_market_holiday_use_evergreen() -> None:
    weekend = due_slot(datetime(2026, 7, 25, 12, 0, tzinfo=UTC))
    holiday = due_slot(datetime(2026, 12, 25, 13, 0, tzinfo=UTC))
    assert weekend is not None and weekend.mode == ContentMode.EVERGREEN
    assert holiday is not None and holiday.mode == ContentMode.EVERGREEN


def test_outside_window_is_not_due() -> None:
    assert due_slot(datetime(2026, 7, 24, 13, 0, tzinfo=UTC)) is None


def test_content_id_is_stable_within_window() -> None:
    first = due_slot(datetime(2026, 7, 24, 12, 1, tzinfo=UTC))
    second = due_slot(datetime(2026, 7, 24, 12, 29, tzinfo=UTC))
    assert first is not None and second is not None
    assert first.content_id == second.content_id == "2026-07-24:morning"


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(ValueError):
        due_slot(datetime(2026, 7, 24, 12, 0))
