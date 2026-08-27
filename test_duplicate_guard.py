"""DuplicateGuard 파일럿 테스트."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from collectors.base import CollectorEvent
from detection.alert_engine import AlertSignal
from detection.duplicate_guard import DuplicateGuard
from detection.macro_news_layer import MacroNewsResult


def _event(title: str, url: str = "https://reuters.com/markets/fed-cpi-hot") -> CollectorEvent:
    return CollectorEvent(
        source_type="news",
        source_name="reuters",
        event_id="e1",
        title=title,
        summary="",
        url=url,
        published_at=datetime.now(UTC),
        matched_keywords=["fed", "cpi", "rate cut"],
    )


def _result(level: str = "L1", score: float = 8.1, url: str = "https://reuters.com/markets/fed-cpi-hot") -> MacroNewsResult:
    news = [_event("Fed rate-cut hopes fade as CPI comes in hot", url=url)]
    return MacroNewsResult(
        score=score,
        level=level,  # type: ignore[arg-type]
        news_events=news,
        youtube_events=[],
        news_score=score,
        youtube_bonus=0,
        top_news=news,
        top_youtube=[],
        reasoning="L1",
        health_score=1.0,
    )


def _signal(level: str = "L1", score: float = 8.1, publish_x: bool = True) -> AlertSignal:
    return AlertSignal(
        alert_id="alert-1",
        level=level,  # type: ignore[arg-type]
        score=score,
        reasoning="L1",
        health_score=1.0,
        created_at=datetime.now(UTC),
        publish_x=publish_x,
    )


@pytest.mark.unit
def test_compute_topic_key_is_stable_for_same_topic() -> None:
    guard = DuplicateGuard(alert_store=None)
    assert guard.compute_topic_key(_result()) == guard.compute_topic_key(_result())


@pytest.mark.unit
def test_pre_format_new_topic_allows_publish() -> None:
    store = MagicMock()
    store.get_topic_state.return_value = None
    guard = DuplicateGuard(alert_store=store)

    decision = guard.evaluate_pre_format(_signal(), _result())

    assert decision.action == "publish_new"
    assert decision.suppress_x is False


@pytest.mark.unit
def test_pre_format_same_topic_within_window_suppresses_x() -> None:
    store = MagicMock()
    store.get_topic_state.return_value = {
        "last_x_published_at": (datetime.now(UTC) - timedelta(minutes=30)).isoformat(),
        "last_level": "L1",
        "last_score": 8.0,
        "source_urls": ["https://reuters.com/markets/fed-cpi-hot"],
        "last_alert_id": "prev-alert",
    }
    guard = DuplicateGuard(alert_store=store, topic_window_minutes=180)

    decision = guard.evaluate_pre_format(_signal(score=8.05), _result(score=8.05))

    assert decision.action == "suppress_duplicate"
    assert decision.suppress_x is True
    assert decision.previous_alert_id == "prev-alert"


@pytest.mark.unit
def test_pre_format_escalation_allows_publish() -> None:
    store = MagicMock()
    store.get_topic_state.return_value = {
        "last_x_published_at": (datetime.now(UTC) - timedelta(minutes=30)).isoformat(),
        "last_level": "L2",
        "last_score": 6.0,
        "source_urls": ["https://reuters.com/markets/fed-cpi-hot"],
    }
    guard = DuplicateGuard(alert_store=store)

    decision = guard.evaluate_pre_format(_signal(level="L1", score=8.2), _result(level="L1", score=8.2))

    assert decision.action == "publish_escalation"
    assert decision.suppress_x is False


@pytest.mark.unit
def test_pre_format_score_delta_update_allows_publish_after_min_interval() -> None:
    store = MagicMock()
    store.get_topic_state.return_value = {
        "last_x_published_at": (datetime.now(UTC) - timedelta(minutes=70)).isoformat(),
        "last_level": "L1",
        "last_score": 7.8,
        "source_urls": ["https://reuters.com/markets/fed-cpi-hot"],
    }
    guard = DuplicateGuard(alert_store=store, update_min_interval_minutes=45, score_delta_threshold=0.15)

    decision = guard.evaluate_pre_format(_signal(score=8.1), _result(score=8.1))

    assert decision.action == "publish_update"
    assert decision.suppress_x is False


@pytest.mark.unit
def test_post_format_similar_text_suppresses() -> None:
    store = MagicMock()
    store.get_recent_x_fingerprints.return_value = [
        {
            "alert_id": "prev-alert",
            "topic_key": "topic-1",
            "content_fingerprint": "different",
            "normalized_text": "fed cpi hot rate cut hopes fade",
        }
    ]
    guard = DuplicateGuard(alert_store=store)

    decision = guard.evaluate_post_format(
        signal=_signal(),
        x_text="Fed CPI hot: rate cut hopes fade",
        topic_key="topic-1",
    )

    assert decision.action == "suppress_duplicate"
    assert decision.suppress_x is True


@pytest.mark.unit
def test_guard_error_fail_open_allows_publish() -> None:
    store = MagicMock()
    store.get_topic_state.side_effect = RuntimeError("db down")
    guard = DuplicateGuard(alert_store=store, fail_open=True)

    decision = guard.evaluate_pre_format(_signal(), _result())

    assert decision.action == "guard_unavailable"
    assert decision.suppress_x is False
