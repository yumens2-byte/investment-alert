"""
제목: NewsCollector 단위 테스트
내용: RSS 수집 로직(feedparser mock), 키워드 필터, AI 분석, 교차검증을
      외부 API 없이 Mock 기반으로 테스트합니다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from collectors.base import CollectorEvent
from collectors.news_collector import (
    AI_SCORE_MIN_KEYWORD,
    KEYWORD_THRESHOLD,
    NewsCollector,
)


# ────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────
@pytest.fixture
def collector() -> NewsCollector:
    """AI 클라이언트 없는 기본 NewsCollector"""
    return NewsCollector(ai_client=None)


@pytest.fixture
def collector_with_ai() -> tuple[NewsCollector, MagicMock]:
    """Mock AI 클라이언트가 주입된 NewsCollector"""
    ai_mock = MagicMock()
    ai_mock.generate.return_value = json.dumps({"score": 7.5, "reasoning": "High impact"})
    collector = NewsCollector(ai_client=ai_mock)
    return collector, ai_mock


def make_event(
    source_name: str = "reuters_markets",
    tier: str = "A",
    title: str = "Market crisis plunge",
    url: str = "https://reuters.com/1",
    auto_l1: bool = False,
    keyword_score: float = 0.0,
    matched_keywords: list | None = None,
    source_count: int = 1,
    published_at: datetime | None = None,
) -> CollectorEvent:
    if published_at is None:
        published_at = datetime.now(UTC) - timedelta(hours=1)

    return CollectorEvent(
        source_type="news",
        source_name=source_name,
        event_id=CollectorEvent.compute_event_id(source_name, url, title),
        title=title,
        summary="",
        url=url,
        published_at=published_at,
        tier=tier,
        auto_l1=auto_l1,
        keyword_score=keyword_score,
        matched_keywords=matched_keywords or [],
        source_count=source_count,
    )


# ────────────────────────────────────────────────────────
# 초기화
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_init_defaults(collector: NewsCollector) -> None:
    """기본 초기화 값 확인"""
    assert collector.source_name == "news_collector"
    assert collector.ai_client is None
    assert collector.window_hours == 24


@pytest.mark.unit
def test_init_with_ai_client(collector_with_ai: tuple) -> None:
    """AI 클라이언트 주입 확인"""
    c, mock = collector_with_ai
    assert c.ai_client is mock


# ────────────────────────────────────────────────────────
# 키워드 필터
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_filter_keywords_l1_match(collector: NewsCollector) -> None:
    """L1 키워드(crisis, 4.5점) 포함 시 통과"""
    event = make_event(title="Emergency rate hike crisis announced")
    result = collector._filter_by_keywords([event])
    assert len(result) == 1
    assert result[0].keyword_score >= KEYWORD_THRESHOLD


@pytest.mark.unit
def test_filter_keywords_below_threshold(collector: NewsCollector) -> None:
    """키워드 점수 임계값 미달 시 제외"""
    event = make_event(title="Regular market update today")
    result = collector._filter_by_keywords([event])
    assert len(result) == 0


@pytest.mark.unit
def test_filter_keywords_auto_l1_bypasses(collector: NewsCollector) -> None:
    """auto_l1 이벤트는 키워드 필터 우회"""
    event = make_event(
        title="Routine Fed statement",
        auto_l1=True,
        keyword_score=5.0,
        matched_keywords=["auto_l1"],
    )
    result = collector._filter_by_keywords([event])
    assert len(result) == 1


@pytest.mark.unit
def test_filter_keywords_multiple_keywords_summed(collector: NewsCollector) -> None:
    """복수 키워드 점수 합산 확인"""
    event = make_event(title="Market plunge crisis unprecedented sell-off")
    result = collector._filter_by_keywords([event])
    assert len(result) == 1
    assert result[0].keyword_score > 5.0  # plunge(3.0) + crisis(4.5) + unprecedented(4.0)


# ────────────────────────────────────────────────────────
# AI 분석
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_ai_scoring_success(collector_with_ai: tuple) -> None:
    """AI 분석 성공 시 ai_score 적용"""
    c, _ = collector_with_ai
    event = make_event(keyword_score=3.0)
    result = c._apply_ai_scoring([event])
    assert result[0].ai_score == 7.5
    assert result[0].ai_reasoning == "High impact"


@pytest.mark.unit
def test_ai_scoring_fallback_on_error(collector_with_ai: tuple) -> None:
    """AI 호출 실패 시 keyword_score로 fallback"""
    c, mock = collector_with_ai
    mock.generate.side_effect = RuntimeError("API 오류")
    event = make_event(keyword_score=3.0)
    result = c._apply_ai_scoring([event])
    assert result[0].ai_score == 3.0  # keyword_score fallback
    assert "fallback" in (result[0].ai_reasoning or "")


@pytest.mark.unit
def test_ai_scoring_skip_low_keyword(collector_with_ai: tuple) -> None:
    """keyword_score < AI_SCORE_MIN_KEYWORD이면 AI 호출 안 함"""
    c, mock = collector_with_ai
    event = make_event(keyword_score=AI_SCORE_MIN_KEYWORD - 0.5)
    c._apply_ai_scoring([event])
    mock.generate.assert_not_called()


@pytest.mark.unit
def test_ai_scoring_no_client_returns_unchanged(collector: NewsCollector) -> None:
    """AI 클라이언트 없으면 이벤트 그대로 반환"""
    event = make_event(keyword_score=3.0)
    result = collector._apply_ai_scoring([event])
    assert result[0].ai_score is None  # 변경 없음


# ────────────────────────────────────────────────────────
# 교차검증
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_cross_validate_same_topic_increases_source_count(collector: NewsCollector) -> None:
    """동일 주제 2소스 → source_count=2"""
    title = "Fed emergency rate hike"
    e1 = make_event(source_name="reuters_markets", title=title, url="https://r.com/1")
    e2 = make_event(source_name="wsj_markets", title=title, url="https://w.com/1")
    result = collector._apply_cross_validation([e1, e2])
    assert result[0].source_count == 2
    assert result[1].source_count == 2


@pytest.mark.unit
def test_cross_validate_different_topics_no_bonus(collector: NewsCollector) -> None:
    """주제가 다른 이벤트는 source_count=1 유지"""
    e1 = make_event(title="Fed emergency rate hike", url="https://r.com/1")
    e2 = make_event(title="Oil price surge record high", url="https://r.com/2")
    result = collector._apply_cross_validation([e1, e2])
    assert result[0].source_count == 1
    assert result[1].source_count == 1


# ────────────────────────────────────────────────────────
# 주제 해시
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_compute_topic_hash_deterministic(collector: NewsCollector) -> None:
    """동일 제목 → 동일 해시 (결정론적)"""
    h1 = collector._compute_topic_hash("Fed announces emergency rate cut")
    h2 = collector._compute_topic_hash("Fed announces emergency rate cut")
    assert h1 == h2


@pytest.mark.unit
def test_compute_topic_hash_length(collector: NewsCollector) -> None:
    """해시 길이는 8자리"""
    h = collector._compute_topic_hash("Market crash crisis")
    assert len(h) == 8
