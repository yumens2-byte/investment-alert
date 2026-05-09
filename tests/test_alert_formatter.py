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


# ────────────────────────────────────────────────────────
# v1.2.0 패치 회귀 테스트 (B3 / B4 — 안티봇 셔플)
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_format_x_template_uses_random_hashtag_pool(
    formatter: AlertFormatter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B3 신규 (v1.2.0): _format_x_template은 _X_HASHTAG_POOL에서 랜덤 선택"""
    from publishers.alert_formatter import _X_HASHTAG_POOL

    # DRY_RUN=true 강제 → 템플릿 경로 사용 (AI 트윗 회피)
    monkeypatch.setenv("DRY_RUN", "true")
    msg = formatter.format_x("L2", 5.5, "test", [])
    # POOL 중 하나의 첫 해시태그가 본문에 포함되어야 함
    assert any(tag.split()[0] in msg for tag in _X_HASHTAG_POOL)


@pytest.mark.unit
def test_format_tg_includes_random_time_phrase(formatter: AlertFormatter) -> None:
    """B3 신규 (v1.2.0): format_tg는 _TG_HEADER_TIME_PHRASES에서 시간문구 추가"""
    from publishers.alert_formatter import _TG_HEADER_TIME_PHRASES

    msg = formatter.format_tg("L2", 5.5, "test", [], [], 0.85, "uuid-1")
    assert any(phrase in msg for phrase in _TG_HEADER_TIME_PHRASES)


@pytest.mark.unit
def test_format_tg_time_phrase_varies_across_calls(formatter: AlertFormatter) -> None:
    """B3 신규 (v1.2.0): 동일 입력 다회 호출 시 시간문구 변화 (안티봇 검증)"""
    from publishers.alert_formatter import _TG_HEADER_TIME_PHRASES

    msgs = [
        formatter.format_tg("L2", 5.5, "test", [], [], 0.85, f"uuid-{i}")
        for i in range(20)
    ]
    # 20회 중 최소 2종 이상 시간문구 등장 (확률적 검증)
    found = {p for p in _TG_HEADER_TIME_PHRASES if any(p in m for m in msgs)}
    assert len(found) >= 2


# ────────────────────────────────────────────────────────
# v1.3.0 패치 회귀 테스트 (B6 / B7 / B8)
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_format_x_template_picks_random_top_news_from_top3(
    formatter: AlertFormatter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B6 신규 (v1.3.0): _format_x_template은 상위 3개 중 랜덤 선택"""
    monkeypatch.setenv("DRY_RUN", "true")
    titles = ["AAA breaking news", "BBB second item", "CCC third item", "DDD fourth"]
    msgs = [formatter.format_x("L2", 5.5, "test", titles) for _ in range(20)]
    found = {p for p in ("AAA", "BBB", "CCC") if any(p in m for m in msgs)}
    assert len(found) >= 2  # 20회 중 최소 2개 등장 (확률적)
    assert not any("DDD" in m for m in msgs)  # 4번째는 절대 등장 안 함


@pytest.mark.unit
def test_format_x_force_template_skips_ai(
    formatter: AlertFormatter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B7 신규 (v1.3.0): force_template=True면 DRY_RUN=false에서도 AI 호출 안 함"""
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("GEMINI_API_KEY", "fake_key")  # AI 시도 환경 강제
    # force_template=True → AI 우회 → 템플릿 결정론적 호출
    msg = formatter.format_x("L2", 5.5, "test", ["news A"], force_template=True)
    # 템플릿 prefix 'L2' 메타 헤더 포함
    assert "Score 5.5" in msg


@pytest.mark.unit
def test_format_x_force_template_default_false(
    formatter: AlertFormatter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B7 신규 (v1.3.0): force_template default False → 기존 동작 유지"""
    monkeypatch.setenv("DRY_RUN", "true")  # 어차피 템플릿 경로
    msg1 = formatter.format_x("L2", 5.5, "test", ["news A"])
    msg2 = formatter.format_x("L2", 5.5, "test", ["news A"], force_template=False)
    # 두 호출 모두 템플릿 → 길이 비슷 (해시태그 셔플로 완전 동일은 아님)
    assert "Score 5.5" in msg1
    assert "Score 5.5" in msg2


# ────────────────────────────────────────────────────────
# v1.3.1 패치 회귀 테스트 (B9 — 사이클 내 채널 간 중복 회피)
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_format_tg_no_duplicate_phrase_within_cycle(formatter: AlertFormatter) -> None:
    """B9 신규 (v1.3.1): 같은 인스턴스가 7회 이내 연속 호출 시 phrase 중복 없음.
    Free → Paid → Internal 호출 시 동일 phrase 14% 충돌 위험 차단."""
    from publishers.alert_formatter import _TG_HEADER_TIME_PHRASES

    used_phrases = []
    # 풀 크기(7개)까지 호출 — 모두 다른 phrase가 나와야 함
    for _i in range(len(_TG_HEADER_TIME_PHRASES)):
        msg = formatter.format_tg("L2", 5.5, "test", [], [], 0.85, "uuid-test")
        # 메시지에서 어떤 phrase가 사용됐는지 추출
        for phrase in _TG_HEADER_TIME_PHRASES:
            if phrase in msg:
                used_phrases.append(phrase)
                break

    # 풀 크기만큼 호출했으므로 모두 다른 phrase여야 함
    assert len(used_phrases) == len(_TG_HEADER_TIME_PHRASES)
    assert len(set(used_phrases)) == len(_TG_HEADER_TIME_PHRASES), (
        f"중복 발생: {used_phrases}"
    )


@pytest.mark.unit
def test_format_tg_phrase_pool_resets_after_exhaustion(formatter: AlertFormatter) -> None:
    """B9 신규 (v1.3.1): 풀 소진 후 자동 리셋되어 정상 동작 지속."""
    from publishers.alert_formatter import _TG_HEADER_TIME_PHRASES

    pool_size = len(_TG_HEADER_TIME_PHRASES)
    # 풀 크기 +5회 호출 — 리셋 후에도 정상 동작
    for _i in range(pool_size + 5):
        msg = formatter.format_tg("L2", 5.5, "test", [], [], 0.85, "uuid-test")
        # 항상 7개 풀 중 하나가 들어있어야 함
        assert any(p in msg for p in _TG_HEADER_TIME_PHRASES)


@pytest.mark.unit
def test_format_tg_separate_instances_independent_state() -> None:
    """B9 신규 (v1.3.1): 인스턴스 별로 독립 상태 — 다른 사이클은 영향 없음."""
    fmt1 = AlertFormatter()
    fmt2 = AlertFormatter()
    # 두 인스턴스 모두 빈 상태로 시작
    assert fmt1._tg_phrases_used == set()
    assert fmt2._tg_phrases_used == set()
    # fmt1만 호출
    fmt1.format_tg("L2", 5.5, "test", [], [], 0.85, "uuid-1")
    # fmt2 상태는 그대로
    assert len(fmt1._tg_phrases_used) == 1
    assert fmt2._tg_phrases_used == set()
