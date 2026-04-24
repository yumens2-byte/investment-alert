from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ReviewStatus(StrEnum):
    APPROVED = "approved"
    RESTRICTED = "restricted"
    NOT_REVIEWED = "not_reviewed"


@dataclass(frozen=True)
class SourcePolicy:
    source_name: str
    review_status: ReviewStatus
    allow_redistribution: bool


class SourcePolicyGate:
    def validate_for_deploy(self, policies: list[SourcePolicy]) -> tuple[bool, list[str]]:
        blocked: list[str] = []
        for policy in policies:
            if policy.review_status == ReviewStatus.NOT_REVIEWED:
                blocked.append(f"{policy.source_name}: not reviewed")
            elif not policy.allow_redistribution:
                blocked.append(f"{policy.source_name}: redistribution blocked")
        return len(blocked) == 0, blocked
