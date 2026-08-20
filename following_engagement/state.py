from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .models import (
    ActionCandidate,
    ActionStatus,
    Analysis,
    ExecutionMode,
    ExecutionResult,
    TimelinePost,
)


class StateRepository:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS following_checkpoint (
                  singleton INTEGER PRIMARY KEY CHECK(singleton=1), last_post_id TEXT,
                  last_execution_at TEXT, execution_mode TEXT, processed_count INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS x_action_shadow (
                  id INTEGER PRIMARY KEY, source_type TEXT NOT NULL, post_id TEXT NOT NULL,
                  author_id TEXT, author_username TEXT, post_text TEXT, action_type TEXT,
                  generated_text TEXT, relevance_score INTEGER, content_value INTEGER,
                  engagement_value INTEGER, execution_mode TEXT, would_execute INTEGER,
                  skip_reason TEXT, created_at TEXT, executed_at TEXT, actual_x_post_id TEXT,
                  error_code TEXT, error_message TEXT);
                CREATE INDEX IF NOT EXISTS idx_action_post ON x_action_shadow(post_id);
            """)

    def checkpoint(self) -> str | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT last_post_id FROM following_checkpoint WHERE singleton=1"
            ).fetchone()
        return row[0] if row else None

    def update_checkpoint(self, post_id: str, mode: ExecutionMode, count: int) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO following_checkpoint VALUES(1, ?, ?, ?, ?)
              ON CONFLICT(singleton) DO UPDATE SET last_post_id=excluded.last_post_id,
              last_execution_at=excluded.last_execution_at, execution_mode=excluded.execution_mode,
              processed_count=excluded.processed_count""",
                (post_id, datetime.now(UTC).isoformat(), mode.value, count),
            )

    def has_post(self, post_id: str) -> bool:
        with self._connect() as db:
            return (
                db.execute("SELECT 1 FROM x_action_shadow WHERE post_id=?", (post_id,)).fetchone()
                is not None
            )

    def daily_count(self) -> int:
        cutoff = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        with self._connect() as db:
            return int(
                db.execute(
                    "SELECT count(*) FROM x_action_shadow WHERE would_execute=1 AND created_at>=?",
                    (cutoff,),
                ).fetchone()[0]
            )

    def author_in_cooldown(self, author_id: str, hours: int) -> bool:
        cutoff = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
        with self._connect() as db:
            return (
                db.execute(
                    "SELECT 1 FROM x_action_shadow WHERE author_id=? AND would_execute=1 AND created_at>=? LIMIT 1",
                    (author_id, cutoff),
                ).fetchone()
                is not None
            )

    def recent_texts(self, limit: int = 100) -> list[str]:
        with self._connect() as db:
            return [
                r[0]
                for r in db.execute(
                    "SELECT generated_text FROM x_action_shadow WHERE generated_text IS NOT NULL ORDER BY id DESC LIMIT ?",
                    (limit,),
                )
            ]

    def audit_count(self, would_execute: bool | None = None) -> int:
        query = "SELECT count(*) FROM x_action_shadow"
        parameters: tuple[int, ...] = ()
        if would_execute is not None:
            query += " WHERE would_execute=?"
            parameters = (int(would_execute),)
        with self._connect() as db:
            return int(db.execute(query, parameters).fetchone()[0])

    def record(
        self,
        candidate: ActionCandidate,
        mode: ExecutionMode,
        result: ExecutionResult,
        would_execute: bool,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO x_action_shadow(source_type,post_id,author_id,author_username,
              post_text,action_type,generated_text,relevance_score,content_value,engagement_value,
              execution_mode,would_execute,skip_reason,created_at,executed_at,actual_x_post_id)
              VALUES('FOLLOWING_ENGAGEMENT',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    candidate.post_id,
                    candidate.author_id,
                    candidate.author_username,
                    candidate.post_text,
                    candidate.action_type.value,
                    candidate.generated_text,
                    candidate.relevance_score,
                    candidate.content_value,
                    candidate.engagement_value,
                    mode.value,
                    int(would_execute),
                    result.reason,
                    candidate.created_at.isoformat(),
                    datetime.now(UTC).isoformat() if result.write_executed else None,
                    result.actual_x_post_id,
                ),
            )

    def record_skipped(
        self,
        post: TimelinePost,
        analysis: Analysis,
        mode: ExecutionMode,
        reason: str,
    ) -> None:
        """Persist a non-executable analyzed decision for SHADOW/LIVE audit replay."""
        candidate = ActionCandidate(
            post.post_id,
            post.author_id,
            post.author_username,
            post.text,
            analysis.recommended_action,
            analysis.relevance_score,
            analysis.content_value,
            analysis.engagement_value,
            analysis.generated_text,
        )
        self.record(
            candidate,
            mode,
            ExecutionResult(ActionStatus.SKIPPED, False, reason=reason),
            False,
        )
