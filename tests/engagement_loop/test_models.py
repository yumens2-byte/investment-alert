"""Domain-model and deterministic-ID tests for engagement_loop."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from engagement_loop.ids import build_content_id, build_loop_id
from engagement_loop.models import Criterion, EngagementLoop, Fact, LoopStatus, Slot


def test_build_loop_id_uses_iso_week_year_boundary() -> None:
    assert build_loop_id(date(2027, 1, 1)) == "2026-W53"


def test_build_content_id_is_deterministic() -> None:
    assert build_content_id("2026-W33", Slot.THREE_NUMBERS) == ("2026-W33:three_numbers:v1")


def test_build_content_id_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError, match="loop_id"):
        build_content_id("", Slot.SCORECARD)
    with pytest.raises(ValueError, match="revision"):
        build_content_id("2026-W33", Slot.SCORECARD, revision=0)


def test_fact_requires_https_and_timezone() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        Fact(
            key="vix",
            value=Decimal("15.4"),
            unit="index",
            as_of=datetime.now(UTC),
            source_url="http://example.com/value",
            source_name="Example",
            retrieved_at=datetime.now(UTC),
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        Fact(
            key="vix",
            value=Decimal("15.4"),
            unit="index",
            as_of=datetime(2026, 8, 10),
            source_url="https://example.com/value",
            source_name="Example",
            retrieved_at=datetime.now(UTC),
        )


def test_fact_requires_descriptive_fields() -> None:
    with pytest.raises(ValueError, match="required"):
        Fact(
            key="",
            value=Decimal("15.4"),
            unit="index",
            as_of=datetime.now(UTC),
            source_url="https://example.com/value",
            source_name="Example",
            retrieved_at=datetime.now(UTC),
        )


def test_fact_requires_finite_non_future_value() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="finite"):
        Fact("vix", Decimal("NaN"), "index", now, "https://example.com", "Example", now)
    with pytest.raises(ValueError, match="later"):
        Fact(
            "vix",
            Decimal("10"),
            "index",
            datetime(2026, 8, 11, tzinfo=UTC),
            "https://example.com",
            "Example",
            datetime(2026, 8, 10, tzinfo=UTC),
        )


def test_fact_row_preserves_decimal_as_string() -> None:
    fact = Fact(
        key="treasury_10y",
        value=Decimal("4.123456789"),
        unit="percent",
        as_of=datetime(2026, 8, 10, 0, 0, tzinfo=UTC),
        source_url="https://example.com/value",
        source_name="Example",
        retrieved_at=datetime(2026, 8, 10, 0, 1, tzinfo=UTC),
    )
    assert fact.to_row("2026-W33", "monday")["value"] == "4.123456789"


def test_between_criterion_requires_two_thresholds() -> None:
    with pytest.raises(ValueError, match="requires 2"):
        Criterion("vix", "between", (Decimal("15"),), "range")


def test_criterion_rejects_unknown_operator_and_empty_fields() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        Criterion("vix", "equals", (Decimal("15"),), "same")
    with pytest.raises(ValueError, match="required"):
        Criterion("", "gte", (Decimal("15"),), "same")


def test_loop_requires_monday_and_unique_criteria() -> None:
    criterion = Criterion("vix", "gte", (Decimal("20"),), "risk rises")
    with pytest.raises(ValueError, match="Monday"):
        EngagementLoop("2026-W33", date(2026, 8, 11), criteria=(criterion,))
    with pytest.raises(ValueError, match="unique"):
        EngagementLoop("2026-W33", date(2026, 8, 10), criteria=(criterion, criterion))


def test_loop_requires_id_and_serializes_criteria() -> None:
    criterion = Criterion("vix", "gte", (Decimal("20"),), "risk rises")
    with pytest.raises(ValueError, match="loop_id"):
        EngagementLoop("", date(2026, 8, 10))
    row = EngagementLoop("2026-W33", date(2026, 8, 10), criteria=(criterion,)).to_row()
    assert row["criteria"] == [
        {
            "fact_key": "vix",
            "operator": "gte",
            "thresholds": ["20"],
            "interpretation": "risk rises",
        }
    ]


def test_active_loop_requires_exactly_three_criteria() -> None:
    one = Criterion("vix", "gte", (Decimal("20"),), "risk rises")
    with pytest.raises(ValueError, match="exactly three"):
        EngagementLoop("2026-W33", date(2026, 8, 10), LoopStatus.OPEN, (one,))

    criteria = (
        one,
        Criterion("yield", "gte", (Decimal("4"),), "rates rise"),
        Criterion("spx", "gte", (Decimal("6000"),), "index rises"),
    )
    loop = EngagementLoop("2026-W33", date(2026, 8, 10), LoopStatus.OPEN, criteria)
    assert len(loop.criteria) == 3
