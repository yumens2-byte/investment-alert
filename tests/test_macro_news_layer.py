"""
제목: MacroNewsLayer 단위 테스트
내용: Macro-News Score 산출, YouTube 보너스, 레벨 판정, 건강도 계산을
      Mock Collector 기반으로 테스트합니다. 외부 API 호출 없음.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from collectors.base import CollectorEvent
from detection.macro_news_layer import (
    YOUTUBE_SOLO_L2_MIN_SCORE,
    MacroNewsLayer,
    MacroNewsResult,
)


# ────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────
def make_news_event(
    source_name: str = "reuters_markets",
    tier: str = "A",
    title: str = "Market crisis",
    url: str = "https://r.com/1",
    ai_score: float | None = None,
    keyword_score: float = 3.0,
    source_count: int = 1,
    auto_l1: bool = False,
    matched_keywords: list | None = None,
) -> CollectorEvent:
    return CollectorEvent(
        source_type="news",
        source_name=source_name,
        event_id=CollectorEvent.compute_event_id(source_name, url, title),
        title=title,
        summary="",
        url=url,
        published_at=datetime.now(UTC) - timedelta(hours=1),
        tier=tier,
        auto_l1=auto_l1,
        keyword_score=keyword_score,
        ai_score=ai_score,
        source_count=source_count,
        matched_keywords=matched_keywords or ["crisis"],
    )


def make_yt_event(
    channel_name: str = "소수몽키",
    channel_weight: float = 1.0,
    keyword_score: float = 3.5,
    ai_score: float | None = None,
    matched_keywords: list | None = None,
) -> CollectorEvent:
    return CollectorEvent(
        source_type="youtube",
        source_name=channel_name,
        event_id=CollectorEvent.compute_event_id(channel_name, "https://yt.com/1", "title"),
        title="긴급속보 위기",
        summary="",
        url="https://yt.com/1",
        published_at=datetime.now(UTC) - timedelta(hours=2),
        tier=None,
        channel_weight=channel_weight,
        auto_l1=False,
        keyword_score=keyword_score,
        ai_score=ai_score,
        matched_keywords=matched_keywords or ["긴급", "위기"],
    )


def make_layer(
    news_events: list[CollectorEvent] | None = None,
    youtube_events: list[CollectorEvent] | None = None,
) -> MacroNewsLayer:
    """Mock Collector를 가진 MacroNewsLayer 생성 헬퍼"""
    news_mock = MagicMock()
    news_mock.collect.return_value = news_events or []
    yt_mock = MagicMock()
    yt_mock.collect.return_value = youtube_events or []
    return MacroNewsLayer(news_collector=news_mock, youtube_collector=yt_mock)


# ────────────────────────────────────────────────────────
# 뉴스 점수 산출
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_compute_news_score_single_tier_s(make_layer=make_layer) -> None:
    """Tier S × 1건: ai_score=5.0 × 1.5 = 7.5"""
    layer = make_layer()
    event = make_news_event(tier="S", ai_score=5.0, source_count=1)
    score = layer._compute_news_score([event])
    assert abs(score - 7.5) < 0.01


@pytest.mark.unit
def test_compute_news_score_tier_a_weight() -> None:
    """Tier A × 1건: keyword_score=3.0 × 1.2 = 3.6"""
    layer = make_layer()
    event = make_news_event(tier="A", keyword_score=3.0, source_count=1)
    score = layer._compute_news_score([event])
    assert abs(score - 3.6) < 0.01


@pytest.mark.unit
def test_compute_news_score_multi_source_bonus() -> None:
    """source_count=2: 1.0 + (2-1)×0.15 = 1.15 보너스"""
    layer = make_layer()
    event = make_news_event(tier="A", keyword_score=3.0, source_count=2)
    score = layer._compute_news_score([event])
    expected = 3.0 * 1.2 * 1.15
    assert abs(score - expected) < 0.01


@pytest.mark.unit
def test_compute_news_score_source_bonus_cap() -> None:
    """source_count=10: 보너스 상한 1.5 적용"""
    layer = make_layer()
    event = make_news_event(tier="A", keyword_score=3.0, source_count=10)
    score = layer._compute_news_score([event])
    expected = 3.0 * 1.2 * 1.5
    assert abs(score - expected) < 0.01


@pytest.mark.unit
def test_compute_news_score_empty() -> None:
    """입력 없으면 0.0"""
    layer = make_layer()
    assert layer._compute_news_score([]) == 0.0


# ────────────────────────────────────────────────────────
# YouTube 보너스 산출
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_compute_youtube_bonus_topic_match() -> None:
    """뉴스-YouTube 키워드 2개 이상 교집합 → 보너스 적용"""
    layer = make_layer()
    news = [make_news_event(matched_keywords=["crisis", "plunge", "sell-off"])]
    yt = [make_yt_event(channel_weight=1.3, matched_keywords=["crisis", "plunge"])]
    bonus = layer._compute_youtube_bonus(news, yt)
    assert abs(bonus - 1.3) < 0.01  # 1.0 × 1.3


@pytest.mark.unit
def test_compute_youtube_bonus_no_match() -> None:
    """키워드 교집합 없으면 보너스 0.0"""
    layer = make_layer()
    news = [make_news_event(matched_keywords=["recession"])]
    yt = [make_yt_event(matched_keywords=["긴급", "위기"])]
    bonus = layer._compute_youtube_bonus(news, yt)
    assert bonus == 0.0


@pytest.mark.unit
def test_compute_youtube_bonus_no_news() -> None:
    """뉴스 없으면 보너스 0.0"""
    layer = make_layer()
    yt = [make_yt_event()]
    bonus = layer._compute_youtube_bonus([], yt)
    assert bonus == 0.0


# ────────────────────────────────────────────────────────
# 레벨 판정
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_judge_level_tier_s_auto_l1() -> None:
    """Tier S auto_l1 이벤트 → 무조건 L1"""
    layer = make_layer()
    event = make_news_event(tier="S", auto_l1=True, ai_score=5.0)
    level, reason = layer._judge_level(2.0, [event], health_score=0.5)
    assert level == "L1"
    assert "auto_l1" in reason


@pytest.mark.unit
def test_judge_level_l1_score_threshold() -> None:
    """score ≥ 7.0 + source_count ≥ 2 + health ≥ 0.90 → L1"""
    layer = make_layer()
    event = make_news_event(source_count=2)
    level, reason = layer._judge_level(7.5, [event], health_score=0.95)
    assert level == "L1"


@pytest.mark.unit
def test_judge_level_l1_blocked_by_health() -> None:
    """L1 점수 충족이나 health < 0.90 → L2 강등"""
    layer = make_layer()
    event = make_news_event(source_count=2)
    level, reason = layer._judge_level(7.5, [event], health_score=0.85)
    assert level == "L2"
    assert "강등" in reason


@pytest.mark.unit
def test_judge_level_l2_score_range() -> None:
    """5.0 ≤ score < 7.0 + health ≥ 0.80 → L2"""
    layer = make_layer()
    event = make_news_event()
    level, reason = layer._judge_level(5.5, [event], health_score=0.85)
    assert level == "L2"


@pytest.mark.unit
def test_judge_level_l2_youtube_solo() -> None:
    """뉴스 없음 + YouTube 점수 ≥ 6.0 → L2"""
    layer = make_layer()
    yt_event = make_yt_event(channel_weight=1.3, ai_score=YOUTUBE_SOLO_L2_MIN_SCORE)
    level, reason = layer._judge_level(
        score=YOUTUBE_SOLO_L2_MIN_SCORE * 1.3,
        news_events=[],
        health_score=0.85,
        youtube_events=[yt_event],
    )
    assert level == "L2"


@pytest.mark.unit
def test_judge_level_l3() -> None:
    """3.0 ≤ score < 5.0 → L3"""
    layer = make_layer()
    event = make_news_event()
    level, _ = layer._judge_level(4.0, [event], health_score=0.75)
    assert level == "L3"


@pytest.mark.unit
def test_judge_level_none() -> None:
    """score < 3.0 → NONE"""
    layer = make_layer()
    level, _ = layer._judge_level(2.0, [], health_score=1.0)
    assert level == "NONE"


# ────────────────────────────────────────────────────────
# 건강도
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_compute_health_score_all_success() -> None:
    """뉴스 + YouTube 모두 있으면 0.0 초과 (FR-03 다요인화)"""
    layer = make_layer()
    score = layer._compute_health_score([make_news_event()], [make_yt_event()])
    assert score > 0.0
    assert score <= 1.0


@pytest.mark.unit
def test_compute_health_score_no_youtube() -> None:
    """뉴스만 있으면 0.0 초과 (FR-03 다요인화 — YouTube 없으면 다양성 감소)"""
    layer = make_layer()
    score = layer._compute_health_score([make_news_event()], [])
    assert score > 0.0
    assert score <= 1.0


@pytest.mark.unit
def test_compute_health_score_no_news() -> None:
    """YouTube만 있으면 0.0 초과 (FR-03 다요인화 — 뉴스 없으면 diversity/cross_val 낮음)"""
    layer = make_layer()
    score = layer._compute_health_score([], [make_yt_event()])
    # dedup 요소(0.20)에 기본값 0.3 적용 → 최소 0.06 이상
    assert score >= 0.06
    assert score <= 1.0


@pytest.mark.unit
def test_compute_health_score_all_empty() -> None:
    """둘 다 없으면 0.0"""
    layer = make_layer()
    score = layer._compute_health_score([], [])
    assert score == 0.0


# ────────────────────────────────────────────────────────
# E2E: detect() 통합
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_detect_returns_macro_news_result() -> None:
    """detect() 반환 타입 = MacroNewsResult"""
    news = [make_news_event(tier="A", ai_score=3.5)]
    yt = [make_yt_event()]
    layer = make_layer(news_events=news, youtube_events=yt)
    result = layer.detect()
    assert isinstance(result, MacroNewsResult)
    assert 0.0 <= result.score <= 10.0
    assert result.level in ("L1", "L2", "L3", "NONE")
    assert isinstance(result.reasoning, str) and len(result.reasoning) > 0
    assert 0.0 <= result.health_score <= 1.0


@pytest.mark.unit
def test_detect_collector_failure_graceful() -> None:
    """Collector 실패 시에도 NONE 레벨로 정상 반환"""
    news_mock = MagicMock()
    news_mock.collect.side_effect = RuntimeError("RSS 연결 실패")
    yt_mock = MagicMock()
    yt_mock.collect.return_value = []
    layer = MacroNewsLayer(news_collector=news_mock, youtube_collector=yt_mock)
    result = layer.detect()
    assert result.level == "NONE"
    assert result.score == 0.0
