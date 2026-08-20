from __future__ import annotations

from difflib import SequenceMatcher

from .config import FollowingConfig
from .models import ActionCandidate, ActionType, Analysis, TimelinePost
from .state import StateRepository


class PostPreFilter:
    def __init__(self, config: FollowingConfig, state: StateRepository) -> None:
        self.config, self.state = config, state

    def reason(self, post: TimelinePost) -> str | None:
        text, author = post.text.strip(), post.author_username.lower()
        if post.author_id == self.config.user_id:
            return "OWN_POST"
        if self.state.has_post(post.post_id):
            return "DUPLICATE_POST"
        if post.is_repost or post.is_reply:
            return "REPOST_OR_REPLY"
        if len(text) < self.config.min_text_length:
            return "TEXT_TOO_SHORT"
        if author in self.config.blocked_authors:
            return "BLOCKED_AUTHOR"
        lowered = text.lower()
        if any(word in lowered for word in self.config.exclude_topics):
            return "EXCLUDED_OR_PROMOTIONAL"
        if self.config.include_topics and not any(
            word in lowered for word in self.config.include_topics
        ):
            return "TOPIC_NOT_INCLUDED"
        return None


class DecisionEngine:
    def __init__(self, config: FollowingConfig, state: StateRepository) -> None:
        self.config, self.state, self.selected = config, state, 0

    def decide(
        self, post: TimelinePost, analysis: Analysis
    ) -> tuple[ActionCandidate | None, str | None]:
        action = analysis.recommended_action
        if not analysis.relevant:
            return None, "NOT_RELEVANT"
        if action in {ActionType.SKIP, ActionType.PERMITTED_REPLY}:
            return None, "ACTION_POLICY"
        if self.state.has_post(post.post_id):
            return None, "DUPLICATE_POST"
        if self.state.daily_count() >= self.config.max_actions_per_day:
            return None, "DAILY_LIMIT"
        if self.selected >= self.config.max_actions_per_run:
            return None, "PER_RUN_LIMIT"
        if self.state.author_in_cooldown(post.author_id, self.config.same_author_cooldown_hours):
            return None, "AUTHOR_COOLDOWN"
        bonus = (
            self.config.priority_author_bonus
            if post.author_username.lower() in self.config.priority_authors
            else 0
        )
        if min(100, analysis.relevance_score + bonus) < self.config.min_relevance_score:
            return None, "LOW_RELEVANCE"
        if analysis.content_value < self.config.min_content_value:
            return None, "LOW_CONTENT_VALUE"
        if analysis.engagement_value < self.config.min_engagement_value:
            return None, "LOW_ENGAGEMENT_VALUE"
        if any(
            SequenceMatcher(None, analysis.generated_text, prior).ratio()
            >= self.config.duplicate_similarity_threshold
            for prior in self.state.recent_texts()
        ):
            return None, "SIMILAR_CONTENT"
        if not analysis.generated_text.strip() or action == ActionType.REVIEW_ONLY:
            return None, "REVIEW_ONLY"
        self.selected += 1
        return ActionCandidate(
            post.post_id,
            post.author_id,
            post.author_username,
            post.text,
            action,
            min(100, analysis.relevance_score + bonus),
            analysis.content_value,
            analysis.engagement_value,
            analysis.generated_text,
        ), None
