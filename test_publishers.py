"""
제목: AlertFormatter 단위 테스트
내용: X/TG 메시지 포맷, 280자 제한, 레벨별 이모지/헤더를 테스트합니다.
"""

from __future__ import annotations

import pytest

from publishers.alert_formatter import LEVEL_META, X_MAX_LENGTH, AlertFormatter


@pytest.fixture
def formatter() -> AlertFormatter:
    return AlertFormatter()


# ── format_x ─────────────────────────────────────────
@pytest.mark.unit
def test_format_x_within_length(formatter: AlertFormatter) -> None:
    """X 메시지는 280자 이내"""
    msg = formatter.format_x(
        level="L1",
        score=8.5,
        reasoning="L1: Tier S auto_l1 이벤트 감지 (source=fed_rss)",
        top_news_titles=["Fed announces emergency rate cut"],
    )
    assert len(msg) <= X_MAX_LENGTH


@pytest.mark.unit
def test_format_x_l1_prefix(formatter: AlertFormatter) -> None:
    """L1 메시지에 CRITICAL 이모지 포함"""
    msg = formatter.format_x("L1", 8.0, "L1 판정", [])
    assert "🚨" in msg


@pytest.mark.unit
def test_format_x_l2_prefix(formatter: AlertFormatter) -> None:
    """L2 메시지에 WARNING 이모지 포함"""
    msg = formatter.format_x("L2", 5.5, "L2 판정", [])
    assert "⚠️" in msg


@pytest.mark.unit
def test_format_x_score_included(formatter: AlertFormatter) -> None:
    """X 메시지에 점수 포함"""
    msg = formatter.format_x("L2", 5.5, "판정근거", [])
    assert "5.5" in msg


@pytest.mark.unit
def test_format_x_news_truncated(formatter: AlertFormatter) -> None:
    """뉴스 제목 60자 이후 말줄임"""
    long_title = "A" * 100
    msg = formatter.format_x("L2", 5.0, "근거", [long_title])
    assert "..." in msg


@pytest.mark.unit
def test_format_x_no_news(formatter: AlertFormatter) -> None:
    """뉴스 없어도 메시지 생성 가능"""
    msg = formatter.format_x("L2", 5.0, "근거", [])
    assert len(msg) > 0


# ── format_tg ─────────────────────────────────────────
@pytest.mark.unit
def test_format_tg_l1_header(formatter: AlertFormatter) -> None:
    """L1 TG 메시지에 CRITICAL 헤더 포함"""
    msg = formatter.format_tg("L1", 8.0, "L1 판정", [], [], 1.0, "uuid-1234")
    assert "L1 CRITICAL" in msg


@pytest.mark.unit
def test_format_tg_score_health(formatter: AlertFormatter) -> None:
    """TG 메시지에 Score, Health 포함"""
    msg = formatter.format_tg("L2", 5.5, "근거", [], [], 0.85, "uuid-1234")
    assert "5.50" in msg
    assert "85%" in msg


@pytest.mark.unit
def test_format_tg_top_news_listed(formatter: AlertFormatter) -> None:
    """TG 메시지에 상위 뉴스 3건 포함"""
    news = ["News 1", "News 2", "News 3", "News 4"]
    msg = formatter.format_tg("L2", 5.5, "근거", news, [], 0.85, "uuid-1234")
    assert "News 1" in msg
    assert "News 3" in msg
    assert "News 4" not in msg  # 4번째는 제외 (상위 3건만)


@pytest.mark.unit
def test_format_tg_youtube_listed(formatter: AlertFormatter) -> None:
    """TG 메시지에 YouTube 제목 포함"""
    msg = formatter.format_tg("L1", 8.0, "근거", [], ["YT 긴급속보"], 0.9, "uuid-1234")
    assert "YT 긴급속보" in msg


@pytest.mark.unit
def test_format_tg_alert_id_shortened(formatter: AlertFormatter) -> None:
    """TG 메시지에 alert_id 앞 8자리 포함"""
    alert_id = "abcdef12-1234-5678-abcd-ef1234567890"
    msg = formatter.format_tg("L2", 5.0, "근거", [], [], 0.8, alert_id)
    assert alert_id[:8] in msg


@pytest.mark.unit
def test_format_tg_disclaimer(formatter: AlertFormatter) -> None:
    """TG 메시지에 투자 참고 정보 면책 문구 포함"""
    msg = formatter.format_tg("L2", 5.0, "근거", [], [], 0.8, "uuid")
    assert "투자 참고 정보" in msg


# ── LEVEL_META 완전성 ─────────────────────────────────
@pytest.mark.unit
def test_level_meta_completeness() -> None:
    """LEVEL_META가 L1/L2/L3 모두 정의"""
    for level in ("L1", "L2", "L3"):
        assert level in LEVEL_META
        assert "emoji" in LEVEL_META[level]
        assert "tg_header" in LEVEL_META[level]
