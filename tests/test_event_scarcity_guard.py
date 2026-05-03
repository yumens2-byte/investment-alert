from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from collectors.base import CollectorEvent
from detection.macro_news_layer import MacroNewsLayer


def _make_event(name: str) -> CollectorEvent:
    now = datetime.now(UTC)
    return CollectorEvent(
        source_type="news",
        source_name=name,
        event_id=hashlib.sha256(name.encode()).hexdigest(),
        title=f"{name} title",
        summary="summary",
        url="https://example.com",
        published_at=now - timedelta(minutes=20),
        tier="A",
        channel_weight=1.0,
        auto_l1=False,
        keyword_score=0.0,
        matched_keywords=[],
    )


def test_event_scarcity_warning_when_raw_present_but_filtered_empty() -> None:
    news_mock = MagicMock()
    news_mock.collect.return_value = []
    news_mock.last_raw_events = [_make_event("raw_news_1"), _make_event("raw_news_2")]

    yt_mock = MagicMock()
    yt_mock.collect.return_value = []
    yt_mock.last_raw_events = [_make_event("raw_yt_1")]

    layer = MacroNewsLayer(news_collector=news_mock, youtube_collector=yt_mock)
    result = layer.detect()

    assert result.ops_warnings
    assert "event_scarcity" in result.ops_warnings[0]


def test_event_scarcity_warning_absent_when_filtered_events_exist() -> None:
    event = _make_event("filtered_news_1")

    news_mock = MagicMock()
    news_mock.collect.return_value = [event]
    news_mock.last_raw_events = [event]

    yt_mock = MagicMock()
    yt_mock.collect.return_value = []
    yt_mock.last_raw_events = []

    layer = MacroNewsLayer(news_collector=news_mock, youtube_collector=yt_mock)
    result = layer.detect()

    assert result.ops_warnings == []


def test_event_scarcity_low_signal_on_holiday_is_info(monkeypatch) -> None:
    news_mock = MagicMock()
    news_mock.collect.return_value = []
    news_mock.last_raw_events = [_make_event("raw_news_1"), _make_event("raw_news_2")]

    yt_mock = MagicMock()
    yt_mock.collect.return_value = []
    yt_mock.last_raw_events = []

    monkeypatch.setattr("detection.macro_news_layer.get_market_profile", lambda: "holiday")

    layer = MacroNewsLayer(news_collector=news_mock, youtube_collector=yt_mock)
    result = layer.detect()

    assert "event_scarcity[info]" in result.ops_warnings[0]


def test_event_scarcity_warn_on_intraday(monkeypatch) -> None:
    news_mock = MagicMock()
    news_mock.collect.return_value = []
    news_mock.last_raw_events = [_make_event(f"raw_news_{i}") for i in range(6)]

    yt_mock = MagicMock()
    yt_mock.collect.return_value = []
    yt_mock.last_raw_events = []

    monkeypatch.setattr("detection.macro_news_layer.get_market_profile", lambda: "intraday")

    layer = MacroNewsLayer(news_collector=news_mock, youtube_collector=yt_mock)
    result = layer.detect()

    assert "event_scarcity[warn]" in result.ops_warnings[0]

