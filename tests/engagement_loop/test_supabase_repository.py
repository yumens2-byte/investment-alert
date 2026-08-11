"""Mocked Supabase repository tests; no network or production DB is used."""

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from engagement_loop.models import EngagementLoop, Fact
from engagement_loop.supabase_repository import (
    EngagementLoopRepository,
    RepositoryUnavailableError,
)


def _client(data: list[dict] | None = None) -> MagicMock:
    client = MagicMock()
    client.table.return_value = client
    client.upsert.return_value = client
    client.select.return_value = client
    client.eq.return_value = client
    client.limit.return_value = client
    client.execute.return_value = SimpleNamespace(data=data or [])
    return client


def _fact() -> Fact:
    return Fact(
        key="vix",
        value=Decimal("18.2"),
        unit="index",
        as_of=datetime(2026, 8, 10, tzinfo=UTC),
        source_url="https://example.com/vix",
        source_name="Example",
        retrieved_at=datetime(2026, 8, 10, 0, 1, tzinfo=UTC),
    )


def test_credentials_are_isolated_from_existing_supabase_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://legacy.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "legacy-key")
    repository = EngagementLoopRepository()
    with pytest.raises(RuntimeError, match="ENGAGEMENT_LOOP_SUPABASE"):
        repository._get_client()


def test_upsert_loop_uses_expected_conflict_key() -> None:
    client = _client()
    repository = EngagementLoopRepository(
        "https://test.supabase.co", "service-key", client_factory=lambda *_: client
    )
    assert repository.upsert_loop(EngagementLoop("2026-W33", date(2026, 8, 10))) is True
    client.upsert.assert_called_once()
    assert client.upsert.call_args.kwargs["on_conflict"] == "loop_id"


def test_client_factory_is_lazy_and_cached() -> None:
    client = _client()
    factory = MagicMock(return_value=client)
    repository = EngagementLoopRepository(
        "https://test.supabase.co/", "service-key", client_factory=factory
    )
    assert repository._get_client() is client
    assert repository._get_client() is client
    factory.assert_called_once_with("https://test.supabase.co", "service-key")


def test_get_loop_returns_first_row() -> None:
    client = _client([{"loop_id": "2026-W33", "status": "planned"}])
    repository = EngagementLoopRepository(
        "https://test.supabase.co", "service-key", client_factory=lambda *_: client
    )
    assert repository.get_loop("2026-W33") == {
        "loop_id": "2026-W33",
        "status": "planned",
    }


def test_get_loop_raises_on_failure_instead_of_looking_absent() -> None:
    client = _client()
    client.execute.side_effect = RuntimeError("database unavailable")
    repository = EngagementLoopRepository(
        "https://test.supabase.co", "service-key", client_factory=lambda *_: client
    )
    with pytest.raises(RepositoryUnavailableError):
        repository.get_loop("2026-W33")


def test_save_facts_rejects_unknown_phase() -> None:
    repository = EngagementLoopRepository("https://test.supabase.co", "service-key")
    with pytest.raises(ValueError, match="Unsupported fact phase"):
        repository.save_facts("2026-W33", "tuesday", [_fact()])


def test_save_facts_upserts_snapshot() -> None:
    client = _client()
    repository = EngagementLoopRepository(
        "https://test.supabase.co", "service-key", client_factory=lambda *_: client
    )
    assert repository.save_facts("2026-W33", "monday", [_fact()]) is True
    assert client.upsert.call_args.kwargs["on_conflict"] == "loop_id,phase,fact_key"


def test_save_facts_requires_rows_and_handles_failure() -> None:
    repository = EngagementLoopRepository("https://test.supabase.co", "service-key")
    with pytest.raises(ValueError, match="At least one"):
        repository.save_facts("2026-W33", "monday", [])

    client = _client()
    client.execute.side_effect = RuntimeError("database unavailable")
    repository = EngagementLoopRepository(
        "https://test.supabase.co", "service-key", client_factory=lambda *_: client
    )
    assert repository.save_facts("2026-W33", "monday", [_fact()]) is False


def test_append_event_requires_audit_fields() -> None:
    repository = EngagementLoopRepository("https://test.supabase.co", "service-key")
    with pytest.raises(ValueError, match="event_id"):
        repository.append_event({"loop_id": "2026-W33"})


def test_append_event_is_idempotent_and_handles_failure() -> None:
    event = {
        "event_id": "00000000-0000-0000-0000-000000000001",
        "loop_id": "2026-W33",
        "event_type": "loop_planned",
        "occurred_at": "2026-08-10T00:00:00+00:00",
    }
    client = _client()
    repository = EngagementLoopRepository(
        "https://test.supabase.co", "service-key", client_factory=lambda *_: client
    )
    assert repository.append_event(event) is True
    assert client.upsert.call_args.kwargs["on_conflict"] == "event_id"
    assert client.upsert.call_args.kwargs["ignore_duplicates"] is True

    client.execute.side_effect = RuntimeError("database unavailable")
    assert repository.append_event(event) is False


def test_repository_failure_does_not_log_secret() -> None:
    client = _client()
    client.execute.side_effect = RuntimeError("database unavailable")
    repository = EngagementLoopRepository(
        "https://test.supabase.co", "very-secret", client_factory=lambda *_: client
    )
    assert repository.upsert_loop(EngagementLoop("2026-W33", date(2026, 8, 10))) is False
