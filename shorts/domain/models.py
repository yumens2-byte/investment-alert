"""Shorts 파이프라인의 provider 독립 도메인 모델."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class SlotName(StrEnum):
    MORNING = "morning"
    NIGHT = "night"


class ContentMode(StrEnum):
    MARKET_DAY = "market_day"
    EVERGREEN = "evergreen"


class JobState(StrEnum):
    PLANNED = "planned"
    SOURCED = "sourced"
    SCRIPTED = "scripted"
    STORYBOARDED = "storyboarded"
    MEDIA_READY = "media_ready"
    RENDERED = "rendered"
    VALIDATED = "validated"
    READY_TO_PUBLISH = "ready_to_publish"
    UPLOADING = "uploading"
    PUBLISHED = "published"
    QUARANTINED = "quarantined"
    SKIPPED = "skipped"
    UPLOAD_UNKNOWN = "upload_unknown"


@dataclass(frozen=True)
class Evidence:
    id: str
    claim: str
    source_url: str
    observed_at: datetime
    source_tier: str = "OFFICIAL"
    value: float | None = None
    unit: str | None = None


@dataclass(frozen=True)
class FactPack:
    schema_version: str
    as_of: datetime
    market_status: str
    topic_key: str
    facts: tuple[Evidence, ...]
    forbidden_inferences: tuple[str, ...] = ()


@dataclass(frozen=True)
class Scene:
    index: int
    duration_ms: int
    narration: str
    subtitle: str
    visual_prompt: str
    claim_type: str
    evidence_ids: tuple[str, ...] = ()
    company_asset_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Script:
    title: str
    hook: str
    scenes: tuple[Scene, ...]
    description: str
    hashtags: tuple[str, ...]

    @property
    def duration_ms(self) -> int:
        return sum(scene.duration_ms for scene in self.scenes)


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str


@dataclass(frozen=True)
class ValidationResult:
    passed: bool
    issues: tuple[ValidationIssue, ...] = ()


@dataclass
class PilotManifest:
    content_id: str
    slot: SlotName
    mode: ContentMode
    state: JobState
    fact_pack: FactPack
    script: Script
    validation: ValidationResult
    video_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """datetime/enum을 포함한 manifest를 JSON 직렬화 가능한 값으로 변환한다."""

        def normalize(value: Any) -> Any:
            if isinstance(value, datetime):
                return value.isoformat()
            if isinstance(value, StrEnum):
                return str(value)
            if isinstance(value, tuple):
                return [normalize(item) for item in value]
            if isinstance(value, list):
                return [normalize(item) for item in value]
            if isinstance(value, dict):
                return {key: normalize(item) for key, item in value.items()}
            return value

        return normalize(asdict(self))
