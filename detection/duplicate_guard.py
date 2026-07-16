"""
제목: Alert Pipeline X 중복 발행 억제 가드
내용: 동일/유사 시장 이슈가 짧은 시간 내 X에 반복 발행되는 것을 막기 위해
      topic 단위 상태와 최종 본문 fingerprint를 함께 평가합니다.

주요 클래스:
  - DuplicateDecision: 중복 판단 결과
  - DuplicateGuard: topic/content 기반 X 발행 의사결정기
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlparse

from core.logger import get_logger
from detection.alert_engine import AlertSignal
from detection.macro_news_layer import MacroNewsResult

VERSION = "0.1.0"

logger = get_logger(__name__)

DuplicateAction = Literal[
    "publish_new",
    "suppress_duplicate",
    "publish_update",
    "publish_escalation",
    "guard_unavailable",
]

_TOKEN_PATTERN = re.compile(r"[a-z0-9가-힣]+")
_TEXT_CLEAN_PATTERN = re.compile(r"[^a-z0-9가-힣]+")
_DEFAULT_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "into", "over",
    "after", "before", "amid", "about", "says", "said", "will", "could",
    "would", "market", "markets", "news", "update", "breaking", "속보", "관련",
}


def _get_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _get_int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _get_float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass
class DuplicateDecision:
    """X 중복 발행 판단 결과."""

    action: DuplicateAction
    topic_key: str | None
    content_fingerprint: str | None
    reason: str
    suppress_x: bool
    update_suffix: str | None = None
    similarity_score: float | None = None
    previous_alert_id: str | None = None


class DuplicateGuard:
    """
    제목: X 중복 발행 억제 가드
    내용: topic state 기반 pre-format 판단과 최종 X 본문 기반 post-format 판단을 제공한다.
          DB/가드 장애 시 기본적으로 fail-open 하여 중요 알림 발행을 막지 않는다.
    """

    def __init__(
        self,
        alert_store: object | None,
        enabled: bool | None = None,
        topic_window_minutes: int | None = None,
        content_window_minutes: int | None = None,
        update_min_interval_minutes: int | None = None,
        score_delta_threshold: float | None = None,
        fail_open: bool | None = None,
    ) -> None:
        self.alert_store = alert_store
        self.enabled = _get_bool_env("DUP_GUARD_ENABLED", True) if enabled is None else enabled
        self.topic_window_minutes = topic_window_minutes or _get_int_env("DUP_TOPIC_WINDOW_MINUTES", 180)
        self.content_window_minutes = content_window_minutes or _get_int_env("DUP_CONTENT_WINDOW_MINUTES", 1440)
        self.update_min_interval_minutes = (
            update_min_interval_minutes
            or _get_int_env("DUP_UPDATE_MIN_INTERVAL_MINUTES", 45)
        )
        self.score_delta_threshold = (
            score_delta_threshold
            if score_delta_threshold is not None
            else _get_float_env("DUP_SCORE_DELTA_THRESHOLD", 0.15)
        )
        self.fail_open = _get_bool_env("DUP_FAIL_OPEN", True) if fail_open is None else fail_open
        logger.info(
            f"[DuplicateGuard] v{VERSION} 초기화 "
            f"(enabled={self.enabled}, topic_window={self.topic_window_minutes}m)"
        )

    def evaluate_pre_format(
        self,
        signal: AlertSignal,
        result: MacroNewsResult,
    ) -> DuplicateDecision:
        """topic state만으로 X 발행 필요 여부를 1차 판단한다."""
        topic_key = self.compute_topic_key(result)
        if not self.enabled or not signal.publish_x:
            return DuplicateDecision(
                action="publish_new",
                topic_key=topic_key,
                content_fingerprint=None,
                reason="guard_disabled_or_x_not_targeted",
                suppress_x=False,
            )
        if not topic_key or self.alert_store is None:
            return DuplicateDecision(
                action="publish_new",
                topic_key=topic_key,
                content_fingerprint=None,
                reason="topic_or_store_unavailable",
                suppress_x=False,
            )

        try:
            state = self.alert_store.get_topic_state(topic_key)  # type: ignore[attr-defined]
            if not state:
                return DuplicateDecision(
                    action="publish_new",
                    topic_key=topic_key,
                    content_fingerprint=None,
                    reason="new_topic",
                    suppress_x=False,
                )

            previous_alert_id = str(state.get("last_alert_id") or "") or None
            last_x_published_at = self._parse_dt(state.get("last_x_published_at"))
            last_level = str(state.get("last_level") or "")
            last_score = self._safe_float(state.get("last_score"), 0.0)
            now = datetime.now(UTC)
            in_topic_window = (
                last_x_published_at is not None
                and last_x_published_at + timedelta(minutes=self.topic_window_minutes) > now
            )
            in_update_min_interval = (
                last_x_published_at is not None
                and last_x_published_at + timedelta(minutes=self.update_min_interval_minutes) > now
            )

            if self._is_escalation(last_level, signal.level):
                return DuplicateDecision(
                    action="publish_escalation",
                    topic_key=topic_key,
                    content_fingerprint=None,
                    reason=f"level_escalation:{last_level}->{signal.level}",
                    suppress_x=False,
                    previous_alert_id=previous_alert_id,
                    update_suffix="레벨 상향 업데이트",
                )

            has_new_source = self._has_new_source_urls(result, state)
            score_delta = signal.score - last_score
            if in_topic_window and not in_update_min_interval and (
                has_new_source or score_delta >= self.score_delta_threshold
            ):
                reason_bits = []
                if has_new_source:
                    reason_bits.append("new_source")
                if score_delta >= self.score_delta_threshold:
                    reason_bits.append(f"score_delta={score_delta:.2f}")
                return DuplicateDecision(
                    action="publish_update",
                    topic_key=topic_key,
                    content_fingerprint=None,
                    reason=";".join(reason_bits),
                    suppress_x=False,
                    previous_alert_id=previous_alert_id,
                    update_suffix="추가 업데이트",
                )

            if in_topic_window:
                return DuplicateDecision(
                    action="suppress_duplicate",
                    topic_key=topic_key,
                    content_fingerprint=None,
                    reason="same_topic_within_suppress_window",
                    suppress_x=True,
                    previous_alert_id=previous_alert_id,
                )

            return DuplicateDecision(
                action="publish_new",
                topic_key=topic_key,
                content_fingerprint=None,
                reason="topic_window_expired",
                suppress_x=False,
                previous_alert_id=previous_alert_id,
            )
        except Exception as e:
            logger.warning(f"[DuplicateGuard] pre-format 평가 실패: {type(e).__name__}: {e}")
            return DuplicateDecision(
                action="guard_unavailable",
                topic_key=topic_key,
                content_fingerprint=None,
                reason=f"pre_format_error:{type(e).__name__}",
                suppress_x=not self.fail_open,
            )

    def evaluate_post_format(
        self,
        signal: AlertSignal,
        x_text: str,
        topic_key: str | None,
    ) -> DuplicateDecision:
        """최종 X 본문 fingerprint로 발행 직전 중복을 2차 판단한다."""
        normalized_text = self.normalize_text(x_text)
        fingerprint = self.compute_content_fingerprint(x_text)
        if not self.enabled or not signal.publish_x or self.alert_store is None:
            return DuplicateDecision(
                action="publish_new",
                topic_key=topic_key,
                content_fingerprint=fingerprint,
                reason="post_guard_disabled_or_unavailable",
                suppress_x=False,
            )

        try:
            recent = self.alert_store.get_recent_x_fingerprints(  # type: ignore[attr-defined]
                window_minutes=self.content_window_minutes,
                limit=20,
            )
            for row in recent:
                previous = str(row.get("normalized_text") or "")
                similarity = self._jaccard_similarity(normalized_text, previous)
                same_fingerprint = fingerprint == row.get("content_fingerprint")
                same_topic = bool(topic_key and topic_key == row.get("topic_key"))
                if same_fingerprint or similarity >= 0.92 or (same_topic and similarity >= 0.85):
                    return DuplicateDecision(
                        action="suppress_duplicate",
                        topic_key=topic_key,
                        content_fingerprint=fingerprint,
                        reason="content_fingerprint_collision",
                        suppress_x=True,
                        similarity_score=similarity,
                        previous_alert_id=row.get("alert_id"),
                    )

            return DuplicateDecision(
                action="publish_new",
                topic_key=topic_key,
                content_fingerprint=fingerprint,
                reason="content_unique",
                suppress_x=False,
            )
        except Exception as e:
            logger.warning(f"[DuplicateGuard] post-format 평가 실패: {type(e).__name__}: {e}")
            return DuplicateDecision(
                action="guard_unavailable",
                topic_key=topic_key,
                content_fingerprint=fingerprint,
                reason=f"post_format_error:{type(e).__name__}",
                suppress_x=not self.fail_open,
            )

    def record_topic_observation(
        self,
        signal: AlertSignal,
        result: MacroNewsResult,
        decision: DuplicateDecision,
        x_published: bool,
    ) -> None:
        """topic state와 decision log를 best-effort로 저장한다."""
        if not self.enabled or self.alert_store is None or not decision.topic_key:
            return
        try:
            self.alert_store.upsert_topic_state(  # type: ignore[attr-defined]
                topic_key=decision.topic_key,
                alert_id=signal.alert_id,
                level=signal.level,
                score=signal.score,
                canonical_title=self._canonical_title(result),
                keywords=self._keywords(result),
                source_urls=self._source_urls(result),
                x_published=x_published,
            )
            self.alert_store.save_duplicate_decision(  # type: ignore[attr-defined]
                alert_id=signal.alert_id,
                channel="x",
                topic_key=decision.topic_key,
                action=decision.action,
                reason=decision.reason,
                similarity_score=decision.similarity_score,
                previous_alert_id=decision.previous_alert_id,
            )
        except Exception as e:
            logger.warning(f"[DuplicateGuard] topic observation 저장 실패: {e}")

    def record_x_fingerprint(
        self,
        alert_id: str,
        topic_key: str | None,
        x_text: str,
        tweet_id: str | None,
    ) -> None:
        """X 발행 성공 후 최종 본문 fingerprint를 저장한다."""
        if not self.enabled or self.alert_store is None or not x_text:
            return
        try:
            self.alert_store.save_x_fingerprint(  # type: ignore[attr-defined]
                alert_id=alert_id,
                topic_key=topic_key,
                content_fingerprint=self.compute_content_fingerprint(x_text),
                normalized_text=self.normalize_text(x_text),
                tweet_id=tweet_id,
            )
        except Exception as e:
            logger.warning(f"[DuplicateGuard] X fingerprint 저장 실패: {e}")

    def compute_topic_key(self, result: MacroNewsResult) -> str | None:
        """URL/제목/키워드/카테고리성 토큰을 결합해 안정적인 topic key를 만든다."""
        events = result.top_news or result.news_events or result.top_youtube or result.youtube_events
        if not events:
            return None

        url_tokens: list[str] = []
        title_tokens: list[str] = []
        keyword_tokens: list[str] = []
        for event in events[:3]:
            url_tokens.extend(self._url_tokens(event.url))
            title_tokens.extend(self._tokenize(event.title))
            keyword_tokens.extend(self._tokenize(" ".join(event.matched_keywords)))
            if event.topic_hash:
                keyword_tokens.append(event.topic_hash.lower())

        seed_tokens = self._select_tokens(url_tokens, 4) + self._select_tokens(title_tokens, 8) + self._select_tokens(keyword_tokens, 8)
        if not seed_tokens:
            return None
        seed = "|".join(sorted(set(seed_tokens)))[:500]
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]

    def compute_content_fingerprint(self, text: str) -> str:
        """최종 발행 본문의 정규화 fingerprint를 산출한다."""
        normalized = self.normalize_text(text)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    def normalize_text(self, text: str) -> str:
        """본문 비교용 정규화 문자열."""
        lowered = (text or "").lower()
        cleaned = _TEXT_CLEAN_PATTERN.sub(" ", lowered)
        tokens = [t for t in cleaned.split() if t not in _DEFAULT_STOPWORDS]
        return " ".join(tokens)[:500]

    def _canonical_title(self, result: MacroNewsResult) -> str:
        events = result.top_news or result.news_events or result.top_youtube or result.youtube_events
        return events[0].title if events else ""

    def _keywords(self, result: MacroNewsResult) -> list[str]:
        keywords: list[str] = []
        for event in (result.top_news or result.news_events)[:3]:
            keywords.extend(event.matched_keywords[:5])
        return sorted(set(keywords))[:20]

    def _source_urls(self, result: MacroNewsResult) -> list[str]:
        urls = [event.url for event in (result.top_news or result.news_events)[:5] if event.url]
        return sorted(set(urls))

    def _has_new_source_urls(self, result: MacroNewsResult, state: dict) -> bool:
        old_urls = set(state.get("source_urls") or [])
        new_urls = set(self._source_urls(result))
        return bool(new_urls - old_urls)

    def _url_tokens(self, url: str) -> list[str]:
        if not url:
            return []
        parsed = urlparse(url)
        path = parsed.path.replace("-", " ").replace("_", " ")
        return self._tokenize(f"{parsed.netloc} {path}")

    def _tokenize(self, text: str) -> list[str]:
        tokens = _TOKEN_PATTERN.findall((text or "").lower())
        return [t for t in tokens if len(t) >= 3 and t not in _DEFAULT_STOPWORDS]

    def _select_tokens(self, tokens: list[str], limit: int) -> list[str]:
        return sorted(set(tokens))[:limit]

    def _is_escalation(self, previous: str, current: str) -> bool:
        rank = {"NONE": 0, "L3": 1, "L2": 2, "L1": 3, "SYSTEM_DEGRADED": 0}
        return rank.get(current, 0) > rank.get(previous, 0)

    def _parse_dt(self, value: object) -> datetime | None:
        if not value:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=UTC)
        try:
            from dateutil.parser import parse as dateutil_parse
            parsed = dateutil_parse(str(value))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except Exception:
            return None

    def _safe_float(self, value: object, default: float) -> float:
        try:
            return float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default

    def _jaccard_similarity(self, left: str, right: str) -> float:
        left_tokens = set(left.split())
        right_tokens = set(right.split())
        if not left_tokens or not right_tokens:
            return 0.0
        return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
