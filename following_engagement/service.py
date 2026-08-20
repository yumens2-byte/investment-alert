from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field

from .clients import OpenAiAnalyzer, XTimelineClient
from .config import FollowingConfig
from .execution import ExecutionModeRouter
from .models import ExecutionMode
from .pipeline import DecisionEngine, PostPreFilter
from .state import StateRepository

logger = logging.getLogger(__name__)


@dataclass
class RunSummary:
    mode: str
    fetched: int = 0
    prefiltered: int = 0
    analyzed: int = 0
    candidates: int = 0
    would_execute: int = 0
    actual_writes: int = 0
    errors: int = 0
    skips: Counter[str] = field(default_factory=Counter)

    def markdown(self) -> str:
        rows = [
            "## X Following Engagement",
            "",
            "| Metric | Value |",
            "|---|---:|",
            f"| Execution Mode | {self.mode} |",
            f"| Fetch Count | {self.fetched} |",
            f"| Pre-filtered | {self.prefiltered} |",
            f"| AI Call Count | {self.analyzed} |",
            f"| Candidate Count | {self.candidates} |",
            f"| Would Execute | {self.would_execute} |",
            f"| Actual Write Count | {self.actual_writes} |",
            f"| Error Count | {self.errors} |",
        ]
        if self.skips:
            rows += ["", "### Skip reasons"] + [
                f"- `{key}`: {value}" for key, value in sorted(self.skips.items())
            ]
        return "\n".join(rows)


class FollowingService:
    def __init__(
        self,
        config: FollowingConfig,
        timeline: XTimelineClient,
        analyzer: OpenAiAnalyzer,
        state: StateRepository,
        router: ExecutionModeRouter,
    ) -> None:
        self.config, self.timeline, self.analyzer = config, timeline, analyzer
        self.state, self.router = state, router

    def run(self) -> RunSummary:
        summary = RunSummary(self.config.execution_mode.value)
        logger.info("FOLLOWING_FETCH_STARTED mode=%s", summary.mode)
        posts = self.timeline.fetch(
            self.config.user_id, self.state.checkpoint(), self.config.max_fetch_count
        )
        summary.fetched = len(posts)
        logger.info("FOLLOWING_FETCH_COMPLETED count=%d", len(posts))
        prefilter, decision = (
            PostPreFilter(self.config, self.state),
            DecisionEngine(self.config, self.state),
        )
        for post in posts:
            reason = prefilter.reason(post)
            if reason:
                summary.prefiltered += 1
                summary.skips[reason] += 1
                logger.info("POST_FILTERED postId=%s reason=%s", post.post_id, reason)
                continue
            try:
                logger.info("POST_ANALYSIS_STARTED postId=%s", post.post_id)
                analysis = self.analyzer.analyze(post)
                summary.analyzed += 1
                logger.info(
                    "POST_ANALYZED postId=%s relevanceScore=%d",
                    post.post_id,
                    analysis.relevance_score,
                )
                candidate, reason = decision.decide(post, analysis)
                if candidate is None:
                    summary.skips[reason or "DECISION_SKIP"] += 1
                    logger.info("ACTION_SKIPPED postId=%s reason=%s", post.post_id, reason)
                    if self.config.execution_mode in {ExecutionMode.SHADOW, ExecutionMode.LIVE}:
                        self.state.record_skipped(
                            post, analysis, self.config.execution_mode, reason or "DECISION_SKIP"
                        )
                    continue
                summary.candidates += 1
                logger.info(
                    "ACTION_SELECTED postId=%s action=%s", post.post_id, candidate.action_type.value
                )
                result = self.router.execute(candidate)
                summary.would_execute += int(
                    result.status.value in {"DRY_RUN_COMPLETED", "SHADOW_COMPLETED", "EXECUTED"}
                )
                summary.actual_writes += int(result.write_executed)
                logger.info(
                    "ACTION_%s postId=%s action=%s relevanceScore=%d contentValue=%d "
                    "generatedText=%r writeExecuted=%s",
                    self.config.execution_mode.value,
                    post.post_id,
                    candidate.action_type.value,
                    candidate.relevance_score,
                    candidate.content_value,
                    candidate.generated_text,
                    result.write_executed,
                )
            except Exception as exc:
                summary.errors += 1
                summary.skips["ANALYSIS_ERROR"] += 1
                logger.error("ACTION_FAILED postId=%s error=%s", post.post_id, type(exc).__name__)
        # Do not advance beyond an analysis/API failure: the next run must be able
        # to replay the complete range rather than silently losing a post.
        if posts and summary.errors == 0:
            latest = max((p.post_id for p in posts), key=lambda value: (len(value), value))
            self.state.update_checkpoint(latest, self.config.execution_mode, len(posts))
            logger.info("CHECKPOINT_UPDATED postId=%s processedCount=%d", latest, len(posts))
        return summary
