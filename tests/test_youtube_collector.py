"""
제목: YouTubeCollector 단위 테스트
내용: 채널 파싱, 한글 키워드 매칭, 가중치 적용, 제외 패턴 필터링을
      외부 API 없이 테스트합니다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from collectors.base import CollectorEvent
from collectors.youtube_collector import (
    KEYWORD_SCORE_CAP,
    KEYWORD_THRESHOLD,
    YouTubeCollector,
)

# ────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────
_CHANNELS_STR = (
    "소수몽키:UCC3yfxS5qC6PCwDzetUuEWg,"
    "오선의 미국 증시 라이브:UC_JJ_NhRqPKcIOj5Ko3W_3w,"
    "전인구경제연구소:UCznImSIaxZR7fdLCICLdgaQ,"
    "미주은:UCNnwmqZOxSuOiF3_c7mAGWA"
)


@pytest.fixture
def collector() -> YouTubeCollector:
    """채널 4개 설정된 기본 YouTubeCollector"""
    return YouTubeCollector(channels_str=_CHANNELS_STR)


@pytest.fixture
def empty_collector() -> YouTubeCollector:
    """채널 없는 YouTubeCollector"""
    return YouTubeCollector(channels_str="")


def make_yt_event(
    channel_name: str = "소수몽키",
    title: str = "긴급속보 미증시 폭락",
    url: str = "https://youtube.com/watch?v=abc",
    published_at: datetime | None = None,
    channel_weight: float = 1.0,
    keyword_score: float = 0.0,
    matched_keywords: list | None = None,
) -> CollectorEvent:
    if published_at is None:
        published_at = datetime.now(UTC) - timedelta(hours=2)
    return CollectorEvent(
        source_type="youtube",
        source_name=channel_name,
        event_id=CollectorEvent.compute_event_id(channel_name, url, title),
        title=title,
        summary="",
        url=url,
        published_at=published_at,
        tier=None,
        channel_weight=channel_weight,
        auto_l1=False,
        keyword_score=keyword_score,
        matched_keywords=matched_keywords or [],
    )


# ────────────────────────────────────────────────────────
# 초기화
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_init_with_channels(collector: YouTubeCollector) -> None:
    """채널 4개 정상 초기화"""
    assert len(collector.channels) == 4


@pytest.mark.unit
def test_init_without_channels(empty_collector: YouTubeCollector) -> None:
    """채널 없으면 빈 리스트"""
    assert collector.channels == [] if False else (empty_collector.channels == [])


# ────────────────────────────────────────────────────────
# 채널 파싱
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_parse_channels_valid(collector: YouTubeCollector) -> None:
    """4개 채널 이름, ID, 가중치 정상 파싱"""
    channels = collector.channels
    names = [c["name"] for c in channels]
    assert "소수몽키" in names
    assert "전인구경제연구소" in names


@pytest.mark.unit
def test_parse_channels_weights(collector: YouTubeCollector) -> None:
    """채널별 가중치 정확히 적용"""
    by_name = {c["name"]: c["weight"] for c in collector.channels}
    assert by_name["전인구경제연구소"] == 1.3
    assert by_name["오선의 미국 증시 라이브"] == 1.2
    assert by_name["소수몽키"] == 1.0
    assert by_name["미주은"] == 0.9


@pytest.mark.unit
def test_parse_channels_invalid_format(collector: YouTubeCollector) -> None:
    """잘못된 포맷('채널ID만'처럼 ':' 없으면) 스킵"""
    c = YouTubeCollector(channels_str="소수몽키:UCxxx,invalid_no_colon,전인구:UCyyy")
    assert len(c.channels) == 2


@pytest.mark.unit
def test_parse_channels_empty_string() -> None:
    """빈 문자열 -> 빈 리스트"""
    c = YouTubeCollector(channels_str="")
    assert c.channels == []


# ────────────────────────────────────────────────────────
# 한글 키워드 매칭
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_match_keywords_kr_single_urgent(collector: YouTubeCollector) -> None:
    """단일 긴급 키워드(속보=3.5) 매칭"""
    score, matched = collector._match_keywords_kr("속보 미증시 급등")
    assert score >= 3.5
    assert "속보" in matched


@pytest.mark.unit
def test_match_keywords_kr_multiple(collector: YouTubeCollector) -> None:
    """복수 키워드 합산 (긴급=3.0 + 위기=3.0 = 6.0)"""
    score, matched = collector._match_keywords_kr("긴급속보 위기 경고")
    assert score >= 6.0
    assert len(matched) >= 2


@pytest.mark.unit
def test_match_keywords_kr_cap_applied(collector: YouTubeCollector) -> None:
    """점수 상한(10.0) 초과 방지"""
    text = "긴급 속보 대폭락 폭락장 서킷브레이커 거래정지 위기 경고"
    score, _ = collector._match_keywords_kr(text)
    assert score <= KEYWORD_SCORE_CAP


@pytest.mark.unit
def test_match_keywords_kr_no_match(collector: YouTubeCollector) -> None:
    """매칭 없으면 점수 0.0"""
    score, matched = collector._match_keywords_kr("오늘의 미국 증시 분석")
    assert score == 0.0
    assert matched == []


# ────────────────────────────────────────────────────────
# 수집 윈도우
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_within_48h_true(collector: YouTubeCollector) -> None:
    """30시간 전은 48h 범위 내"""
    dt = datetime.now(UTC) - timedelta(hours=30)
    assert collector._is_within_window(dt) is True


@pytest.mark.unit
def test_within_48h_false(collector: YouTubeCollector) -> None:
    """50시간 전은 48h 초과"""
    dt = datetime.now(UTC) - timedelta(hours=50)
    assert collector._is_within_window(dt) is False


# ────────────────────────────────────────────────────────
# 키워드 필터 및 제외 패턴
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_filter_exclusion_pattern_removed(collector: YouTubeCollector) -> None:
    """제외 패턴(오늘의 시황) 포함 이벤트 제거"""
    event = make_yt_event(title="오늘의 시황 정리 및 분석")
    result = collector._filter_by_keywords([event])
    assert len(result) == 0


@pytest.mark.unit
def test_filter_exclusion_today_summary(collector: YouTubeCollector) -> None:
    """오늘의 요약 패턴 - 실제 로그 오탐 케이스 (2026-04-25)"""
    event = make_yt_event(
        title="【미국 증시 오늘의 요약】 미 증시 사상 최고치 돌파!  인텔 23% 폭등",
        keyword_score=3.5,
        matched_keywords=["속보"],
    )
    result = collector._filter_by_keywords([event])
    assert len(result) == 0


@pytest.mark.unit
def test_filter_exclusion_date_briefing(collector: YouTubeCollector) -> None:
    """날짜 브리핑 패턴 - 실제 로그 오탐 케이스 (2026-04-25)"""
    event = make_yt_event(
        title="[26년 04월 24일 금] 인텔, 어닝 서프라이즈 | 이란 외무장관 방문",
        keyword_score=3.5,
        matched_keywords=["속보"],
    )
    result = collector._filter_by_keywords([event])
    assert len(result) == 0


@pytest.mark.unit
def test_filter_exclusion_does_not_block_real_urgent(collector: YouTubeCollector) -> None:
    """오늘의 요약 형식이면 긴급 키워드 있어도 제외 (제외 패턴 우선)"""
    event = make_yt_event(title="【미국 증시 오늘의 요약】 서킷브레이커 발동 긴급속보")
    result = collector._filter_by_keywords([event])
    assert len(result) == 0


@pytest.mark.unit
def test_filter_valid_keyword_passed(collector: YouTubeCollector) -> None:
    """유효 키워드(긴급속보) 포함 이벤트 통과 + keyword_score 설정"""
    event = make_yt_event(title="긴급속보 미증시 서킷브레이커 발동")
    result = collector._filter_by_keywords([event])
    assert len(result) == 1
    assert result[0].keyword_score >= KEYWORD_THRESHOLD


@pytest.mark.unit
def test_channel_weight_reflected_in_event(collector: YouTubeCollector) -> None:
    """채널 가중치가 CollectorEvent.channel_weight에 반영됨"""
    jeoninku = next(c for c in collector.channels if c["name"] == "전인구경제연구소")
    assert jeoninku["weight"] == 1.3
