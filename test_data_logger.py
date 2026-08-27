"""
제목: DataLogger 단위 테스트
내용: MacroNewsResult 및 AlertSignal 로그 출력 메서드를 Mock 기반으로 테스트합니다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from collectors.base import CollectorEvent
from core.data_logger import DataLogger


def _make_event(
    source_type: str = "news",
    source_name: str = "reuters",
    title: str = "Market crisis plunge",
    tier: str | None = "A",
    keyword_score: float = 3.5,
    ai_score: float | None = 6.0,
    ai_reasoning: str | None = "High impact",
    matched_keywords: list | None = None,
    channel_weight: float = 1.0,
    source_count: int = 1,
) -> CollectorEvent:
    return CollectorEvent(
        source_type=source_type,
        source_name=source_name,
        event_id="abc123",
        title=title,
        summary="summary text",
        url="https://reuters.com/article/1",
        published_at=datetime.now(UTC),
        tier=tier,
        channel_weight=channel_weight,
        auto_l1=False,
        keyword_score=keyword_score,
        ai_score=ai_score,
        ai_reasoning=ai_reasoning,
        matched_keywords=matched_keywords or ["crisis"],
        source_count=source_count,
    )


def _make_result(
    level: str = "L2",
    score: float = 5.5,
    news_score: float = 5.0,
    youtube_bonus: float = 0.5,
    health_score: float = 0.85,
    reasoning: str = "L2: score=5.5",
    news_count: int = 2,
    yt_count: int = 1,
):
    news = [_make_event() for _ in range(news_count)]
    yt = [_make_event(source_type="youtube", tier=None, channel_weight=1.2) for _ in range(yt_count)]

    from detection.macro_news_layer import MacroNewsResult
    return MacroNewsResult(
        score=score,
        level=level,  # type: ignore[arg-type]
        news_events=news,
        youtube_events=yt,
        news_score=news_score,
        youtube_bonus=youtube_bonus,
        top_news=news[:3],
        top_youtube=yt[:3],
        reasoning=reasoning,
        health_score=health_score,
    )


def _make_signal(
    level: str = "L2",
    publish_x: bool = False,
    publish_tg_free: bool = True,
    publish_tg_paid: bool = True,
    is_cooldown: bool = False,
):
    from detection.alert_engine import AlertSignal
    return AlertSignal(
        alert_id="abcdef12-1234-5678-abcd-ef1234567890",
        level=level,  # type: ignore[arg-type]
        score=5.5,
        reasoning="L2 판정",
        health_score=0.85,
        created_at=datetime.now(UTC),
        top_news_titles=["News 1", "News 2"],
        top_youtube_titles=["YT 1"],
        publish_x=publish_x,
        publish_tg_free=publish_tg_free,
        publish_tg_paid=publish_tg_paid,
        is_cooldown_active=is_cooldown,
    )


@pytest.fixture
def data_logger() -> DataLogger:
    return DataLogger()


# ── log_news_events ───────────────────────────────────
@pytest.mark.unit
def test_log_news_events_with_events(data_logger: DataLogger, caplog) -> None:
    """뉴스 이벤트가 있으면 제목, 소스, 점수 로그 출력"""
    events = [_make_event(), _make_event(title="Flash crash event")]
    with patch.object(data_logger, "log_news_events", wraps=data_logger.log_news_events):
        data_logger.log_news_events(events)
    # 예외 없이 완료되면 OK


@pytest.mark.unit
def test_log_news_events_empty(data_logger: DataLogger) -> None:
    """뉴스 이벤트 없으면 [없음] 출력 (예외 없음)"""
    data_logger.log_news_events([])  # 예외 없어야 함


@pytest.mark.unit
def test_log_news_events_long_title(data_logger: DataLogger) -> None:
    """제목 80자 초과 시 말줄임 처리 (예외 없음)"""
    event = _make_event(title="A" * 150)
    data_logger.log_news_events([event])  # 예외 없어야 함


@pytest.mark.unit
def test_log_news_events_no_ai_reasoning(data_logger: DataLogger) -> None:
    """ai_reasoning 없어도 예외 없음"""
    event = _make_event(ai_reasoning=None)
    data_logger.log_news_events([event])


@pytest.mark.unit
def test_log_news_events_multi_source_bonus(data_logger: DataLogger) -> None:
    """source_count > 1이면 복수소스 표시 (예외 없음)"""
    event = _make_event(source_count=3)
    data_logger.log_news_events([event])


# ── log_youtube_events ────────────────────────────────
@pytest.mark.unit
def test_log_youtube_events_with_events(data_logger: DataLogger) -> None:
    """YouTube 이벤트 있으면 채널, 가중치, 점수 출력 (예외 없음)"""
    events = [
        _make_event(source_type="youtube", tier=None, channel_weight=1.3),
        _make_event(source_type="youtube", tier=None, channel_weight=1.1),
    ]
    data_logger.log_youtube_events(events)


@pytest.mark.unit
def test_log_youtube_events_empty(data_logger: DataLogger) -> None:
    """YouTube 이벤트 없으면 [없음] 출력 (예외 없음)"""
    data_logger.log_youtube_events([])


# ── log_score_breakdown ───────────────────────────────
@pytest.mark.unit
def test_log_score_breakdown(data_logger: DataLogger) -> None:
    """Score 분해 출력 — 예외 없음"""
    result = _make_result(score=5.5, news_score=5.0, youtube_bonus=0.5)
    data_logger.log_score_breakdown(result)


# ── log_alert_signal ──────────────────────────────────
@pytest.mark.unit
def test_log_alert_signal_l1(data_logger: DataLogger) -> None:
    """L1 AlertSignal 로그 출력 — 예외 없음"""
    signal = _make_signal(level="L1", publish_x=True)
    data_logger.log_alert_signal(signal)


@pytest.mark.unit
def test_log_alert_signal_cooldown_active(data_logger: DataLogger) -> None:
    """쿨다운 활성 AlertSignal 로그 출력 — 예외 없음"""
    signal = _make_signal(is_cooldown=True)
    data_logger.log_alert_signal(signal)


# ── log_all ───────────────────────────────────────────
@pytest.mark.unit
def test_log_all_with_signal(data_logger: DataLogger) -> None:
    """log_all — result + signal 전체 출력 (예외 없음)"""
    result = _make_result()
    signal = _make_signal()
    data_logger.log_all(result=result, signal=signal)


@pytest.mark.unit
def test_log_all_without_signal(data_logger: DataLogger) -> None:
    """log_all — signal=None이면 AlertSignal 섹션 생략 (예외 없음)"""
    result = _make_result()
    data_logger.log_all(result=result, signal=None)


@pytest.mark.unit
def test_log_all_empty_events(data_logger: DataLogger) -> None:
    """log_all — 뉴스/YouTube 없어도 예외 없음"""
    result = _make_result(news_count=0, yt_count=0)
    data_logger.log_all(result=result, signal=None)
