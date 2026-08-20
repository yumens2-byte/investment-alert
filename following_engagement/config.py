from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .models import ActionType, ExecutionMode

DEFAULT_TOPICS = (
    "ai",
    "artificial intelligence",
    "openai",
    "nvidia",
    "semiconductor",
    "data center",
    "stock",
    "market",
    "nasdaq",
    "s&p",
    "federal reserve",
    "inflation",
    "cpi",
    "ppi",
    "treasury",
    "interest rate",
    "energy",
    "oil",
    "defense",
    "economy",
)


def _csv(name: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    raw = os.getenv(name)
    return tuple(x.strip().lower() for x in raw.split(",") if x.strip()) if raw else default


@dataclass(frozen=True)
class FollowingConfig:
    enabled: bool = False
    execution_mode: ExecutionMode = ExecutionMode.DRY_RUN
    user_id: str = ""
    x_api_key: str = ""
    x_api_secret: str = ""
    access_token: str = ""
    access_token_secret: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    max_fetch_count: int = 300
    min_text_length: int = 30
    include_topics: tuple[str, ...] = DEFAULT_TOPICS
    exclude_topics: tuple[str, ...] = ("giveaway", "promotion", "discount")
    blocked_authors: tuple[str, ...] = ()
    priority_authors: tuple[str, ...] = ()
    priority_author_bonus: int = 10
    min_relevance_score: int = 85
    min_content_value: int = 80
    min_engagement_value: int = 75
    max_actions_per_run: int = 2
    max_actions_per_day: int = 5
    same_author_cooldown_hours: int = 24
    duplicate_similarity_threshold: float = 0.85
    live_allowlist: frozenset[ActionType] = field(
        default_factory=lambda: frozenset({ActionType.POST, ActionType.QUOTE})
    )
    state_path: Path = Path("state/following_agent.sqlite3")

    @classmethod
    def from_env(cls) -> FollowingConfig:
        mode = ExecutionMode.from_env(os.getenv("EXECUTION_MODE"))
        allowlist = frozenset(
            ActionType(x.upper())
            for x in _csv("LIVE_ACTION_ALLOWLIST", ("post", "quote"))
            if x.upper() in {a.value for a in ActionType}
        )
        return cls(
            enabled=os.getenv("FOLLOWING_ENABLED", "false").lower() in {"1", "true", "yes", "on"},
            execution_mode=mode,
            user_id=os.getenv("X_USER_ID", ""),
            x_api_key=os.getenv("X_API_KEY", ""),
            x_api_secret=os.getenv("X_API_SECRET", ""),
            access_token=os.getenv("X_ACCESS_TOKEN", ""),
            access_token_secret=os.getenv("X_ACCESS_TOKEN_SECRET", ""),
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            max_fetch_count=int(os.getenv("MAX_FETCH_COUNT", "300")),
            min_text_length=int(os.getenv("MIN_TEXT_LENGTH", "30")),
            include_topics=_csv("FOLLOWING_INCLUDE_TOPICS", DEFAULT_TOPICS),
            exclude_topics=_csv("FOLLOWING_EXCLUDE_TOPICS", ("giveaway", "promotion", "discount")),
            blocked_authors=_csv("FOLLOWING_BLOCKED_AUTHORS"),
            priority_authors=_csv("FOLLOWING_PRIORITY_AUTHORS"),
            max_actions_per_run=int(os.getenv("MAX_ACTIONS_PER_RUN", "2")),
            max_actions_per_day=int(os.getenv("MAX_ACTIONS_PER_DAY", "5")),
            same_author_cooldown_hours=int(os.getenv("SAME_AUTHOR_COOLDOWN_HOURS", "24")),
            state_path=Path(os.getenv("FOLLOWING_STATE_PATH", "state/following_agent.sqlite3")),
            live_allowlist=allowlist,
        )
