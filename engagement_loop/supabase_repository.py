"""Supabase persistence for the isolated engagement-loop pipeline."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from core.logger import get_logger
from engagement_loop.models import EngagementLoop, Fact

TABLE_LOOPS = "ia_engagement_loops"
TABLE_FACTS = "ia_engagement_facts"
TABLE_EVENTS = "ia_engagement_events"

logger = get_logger(__name__)


class RepositoryUnavailableError(RuntimeError):
    """Raised when Supabase availability cannot be distinguished from absence."""


class EngagementLoopRepository:
    """Store engagement-loop state using a server-only Supabase credential.

    This repository deliberately does not fall back to the existing ``SUPABASE_KEY``.
    The new pipeline must receive a dedicated service-role secret through
    ``ENGAGEMENT_LOOP_SUPABASE_SERVICE_KEY``.
    """

    def __init__(
        self,
        supabase_url: str | None = None,
        service_key: str | None = None,
        client_factory: Callable[[str, str], Any] | None = None,
    ) -> None:
        raw_url = supabase_url or os.getenv("ENGAGEMENT_LOOP_SUPABASE_URL", "")
        self._url = raw_url.rstrip("/")
        self._service_key = service_key or os.getenv("ENGAGEMENT_LOOP_SUPABASE_SERVICE_KEY", "")
        self._client_factory = client_factory
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self._url or not self._service_key:
            raise RuntimeError(
                "ENGAGEMENT_LOOP_SUPABASE_URL/ENGAGEMENT_LOOP_SUPABASE_SERVICE_KEY 미설정"
            )
        if self._client_factory is None:
            from supabase import create_client  # type: ignore[import]

            self._client_factory = create_client
        self._client = self._client_factory(self._url, self._service_key)
        return self._client

    def upsert_loop(self, loop: EngagementLoop) -> bool:
        """Create or update a loop without overwriting immutable criteria in SQL."""

        try:
            (
                self._get_client()
                .table(TABLE_LOOPS)
                .upsert(loop.to_row(), on_conflict="loop_id")
                .execute()
            )
            return True
        except Exception as exc:
            logger.error("[EngagementLoopRepository] loop 저장 실패: %s", type(exc).__name__)
            return False

    def get_loop(self, loop_id: str) -> dict[str, Any] | None:
        """Return one loop projection, or ``None`` only when it does not exist."""

        try:
            response = (
                self._get_client()
                .table(TABLE_LOOPS)
                .select("*")
                .eq("loop_id", loop_id)
                .limit(1)
                .execute()
            )
            return response.data[0] if response.data else None
        except Exception as exc:
            logger.error("[EngagementLoopRepository] loop 조회 실패: %s", type(exc).__name__)
            raise RepositoryUnavailableError("engagement loop 조회 실패") from exc

    def save_facts(self, loop_id: str, phase: str, facts: list[Fact]) -> bool:
        """Upsert a phase snapshot of sourced market facts."""

        if phase not in {"monday", "friday", "calendar"}:
            raise ValueError(f"Unsupported fact phase: {phase}")
        if not facts:
            raise ValueError("At least one fact is required")
        rows = [fact.to_row(loop_id, phase) for fact in facts]
        try:
            (
                self._get_client()
                .table(TABLE_FACTS)
                .upsert(rows, on_conflict="loop_id,phase,fact_key")
                .execute()
            )
            return True
        except Exception as exc:
            logger.error("[EngagementLoopRepository] fact 저장 실패: %s", type(exc).__name__)
            return False

    def append_event(self, event: dict[str, Any]) -> bool:
        """Append an audit event; ``event_id`` provides retry idempotency."""

        required = {"event_id", "loop_id", "event_type", "occurred_at"}
        missing = required.difference(event)
        if missing:
            raise ValueError(f"Missing event fields: {', '.join(sorted(missing))}")
        try:
            (
                self._get_client()
                .table(TABLE_EVENTS)
                .upsert(event, on_conflict="event_id", ignore_duplicates=True)
                .execute()
            )
            return True
        except Exception as exc:
            logger.error("[EngagementLoopRepository] event 저장 실패: %s", type(exc).__name__)
            return False
