from __future__ import annotations

from typing import Protocol

from .config import FollowingConfig
from .models import ActionCandidate, ActionStatus, ExecutionMode, ExecutionResult
from .state import StateRepository


class Writer(Protocol):
    def write(self, action: object, text: str, post_id: str) -> str: ...


class DryRunActionExecutor:
    def execute(self, candidate: ActionCandidate) -> ExecutionResult:
        return ExecutionResult(ActionStatus.DRY_RUN_COMPLETED, False)


class ShadowActionExecutor:
    def __init__(self, state: StateRepository) -> None:
        self.state = state

    def execute(self, candidate: ActionCandidate) -> ExecutionResult:
        result = ExecutionResult(ActionStatus.SHADOW_COMPLETED, False)
        self.state.record(candidate, ExecutionMode.SHADOW, result, True)
        return result


class LiveSafetyGuard:
    def __init__(self, config: FollowingConfig, state: StateRepository) -> None:
        self.config, self.state = config, state

    def validate(self, candidate: ActionCandidate) -> str | None:
        if self.config.execution_mode != ExecutionMode.LIVE:
            return "NOT_LIVE"
        if candidate.action_type not in self.config.live_allowlist:
            return "ACTION_NOT_ALLOWED"
        if not candidate.generated_text.strip():
            return "BLANK_TEXT"
        if self.state.has_post(candidate.post_id):
            return "DUPLICATE_POST"
        if self.state.daily_count() >= self.config.max_actions_per_day:
            return "DAILY_LIMIT"
        if self.state.author_in_cooldown(
            candidate.author_id, self.config.same_author_cooldown_hours
        ):
            return "AUTHOR_COOLDOWN"
        return None


class LiveActionExecutor:
    def __init__(self, state: StateRepository, guard: LiveSafetyGuard, writer: Writer) -> None:
        self.state, self.guard, self.writer = state, guard, writer

    def execute(self, candidate: ActionCandidate) -> ExecutionResult:
        reason = self.guard.validate(candidate)
        if reason:
            result = ExecutionResult(ActionStatus.SKIPPED_POLICY, False, reason=reason)
            self.state.record(candidate, ExecutionMode.LIVE, result, False)
            return result
        try:
            post_id = self.writer.write(
                candidate.action_type, candidate.generated_text, candidate.post_id
            )
            result = ExecutionResult(ActionStatus.EXECUTED, True, post_id)
            self.state.record(candidate, ExecutionMode.LIVE, result, True)
            return result
        except Exception as exc:
            result = ExecutionResult(ActionStatus.FAILED, False, reason=type(exc).__name__)
            self.state.record(candidate, ExecutionMode.LIVE, result, False)
            return result


class ExecutionModeRouter:
    def __init__(
        self,
        mode: ExecutionMode,
        dry: DryRunActionExecutor,
        shadow: ShadowActionExecutor,
        live: LiveActionExecutor | None = None,
    ) -> None:
        self.mode, self.dry, self.shadow, self.live = mode, dry, shadow, live

    def execute(self, candidate: ActionCandidate) -> ExecutionResult:
        if self.mode == ExecutionMode.DRY_RUN:
            return self.dry.execute(candidate)
        if self.mode == ExecutionMode.SHADOW:
            return self.shadow.execute(candidate)
        if self.live is None:
            return ExecutionResult(ActionStatus.SKIPPED_POLICY, False, reason="LIVE_NOT_CONFIGURED")
        return self.live.execute(candidate)
