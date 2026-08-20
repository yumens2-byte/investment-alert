from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class ExecutionMode(StrEnum):
    DRY_RUN = "DRY_RUN"
    SHADOW = "SHADOW"
    LIVE = "LIVE"

    @classmethod
    def from_env(cls, value: str | None) -> ExecutionMode:
        try:
            return cls((value or cls.DRY_RUN).strip().upper())
        except ValueError:
            return cls.DRY_RUN


class ActionType(StrEnum):
    SKIP = "SKIP"
    QUOTE = "QUOTE"
    POST = "POST"
    PERMITTED_REPLY = "PERMITTED_REPLY"
    REVIEW_ONLY = "REVIEW_ONLY"


class ActionStatus(StrEnum):
    READY = "READY"
    DRY_RUN_COMPLETED = "DRY_RUN_COMPLETED"
    SHADOW_COMPLETED = "SHADOW_COMPLETED"
    EXECUTED = "EXECUTED"
    SKIPPED = "SKIPPED"
    SKIPPED_POLICY = "SKIPPED_POLICY"
    FAILED = "FAILED"


@dataclass(frozen=True)
class TimelinePost:
    post_id: str
    author_id: str
    author_username: str
    text: str
    metrics: dict[str, int] = field(default_factory=dict)
    created_at: str | None = None
    is_reply: bool = False
    is_repost: bool = False


@dataclass(frozen=True)
class Analysis:
    relevant: bool
    category: str
    relevance_score: int
    importance_score: int
    engagement_value: int
    content_value: int
    summary: str
    recommended_action: ActionType
    reason: str
    generated_text: str


@dataclass(frozen=True)
class ActionCandidate:
    post_id: str
    author_id: str
    author_username: str
    post_text: str
    action_type: ActionType
    relevance_score: int
    content_value: int
    engagement_value: int
    generated_text: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    execute_after: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class ExecutionResult:
    status: ActionStatus
    write_executed: bool
    actual_x_post_id: str | None = None
    reason: str | None = None
