"""
제목: B-fix 검증 테스트 (이슈 B 수정)
내용: 운영 첫 가동에서 발견된 거짓 SYSTEM_DEGRADED 시나리오를 회귀 보호.

발견 시나리오 (2026-04-26 운영 로그):
  - RSS 정상 수집: news 10건 + youtube 2건
  - 키워드 필터 후 events=0건
  - 이전 동작: DQ가 fresh_ratio=0/0=0으로 계산 → 거짓 DEGRADED
  - B-fix 후: DQ가 raw_events 사용 → fresh_ratio=1.0 → degraded=False

이 테스트는 동일 시나리오에서 거짓 DEGRADED가 재발하지 않음을 검증.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from collectors.base import CollectorEvent
from detection.macro_news_layer import MacroNewsLayer


def _make_event(name: str, age_minutes: int = 15) -> CollectorEvent:
    """fresh window(1시간) 내 CollectorEvent 생성"""
    now = datetime.now(UTC)
    return CollectorEvent(
        source_type="news",
        source_name=name,
        event_id=hashlib.sha256(name.encode()).hexdigest(),
        title=f"{name} 일반",
        summary="요약",
        url=f"https://x.com/{name}",
        published_at=now - timedelta(minutes=age_minutes),
        tier="A",
        channel_weight=1.0,
        auto_l1=False,
        keyword_score=0.0,
        matched_keywords=[],
    )


def test_bfix_keyword_filter_zero_but_raw_present_is_not_degraded() -> None:
    """B-fix: 키워드 필터 후 0건이지만 raw_events가 있으면 degraded=False"""
    # 운영 시나리오와 동일: RSS 10건 정상, 키워드 매칭 0건
    raw_news = [_make_event(f"news_{i}", age_minutes=15) for i in range(10)]
    raw_yt = [_make_event(f"yt_{i}", age_minutes=10) for i in range(2)]

    news_mock = MagicMock()
    news_mock.collect.return_value = []  # 키워드 필터 후 빈 list
    news_mock.last_raw_events = raw_news  # raw는 보존됨

    yt_mock = MagicMock()
    yt_mock.collect.return_value = []
    yt_mock.last_raw_events = raw_yt

    layer = MacroNewsLayer(news_collector=news_mock, youtube_collector=yt_mock)
    # dq_store는 자동 생성된 인스턴스 — Supabase 미연결이라 save_dq_state는 실패하지만 영향 없음

    result = layer.detect()

    # 핵심 어설션: 거짓 DEGRADED 재발 방지
    assert result.dq_state is not None
    assert result.dq_state.degraded_flag is False, (
        "RSS 정상 수집 + 키워드 미매칭은 시스템 건강 정상이어야 함"
    )
    assert result.dq_state.fresh_event_ratio > 0.9, (
        "raw_events 12건 모두 fresh이므로 ratio는 1.0 근처"
    )
    assert result.level != "SYSTEM_DEGRADED", (
        "거짓 SYSTEM_DEGRADED가 발생해서는 안 됨"
    )


def test_bfix_collect_failure_still_triggers_degraded() -> None:
    """B-fix: collect() 자체 실패 시엔 여전히 DEGRADED (FR-03 정상 동작)"""
    news_mock = MagicMock()
    news_mock.collect.side_effect = RuntimeError("RSS 연결 실패")
    news_mock.last_raw_events = []  # 수집 실패 → raw도 빈 list

    yt_mock = MagicMock()
    yt_mock.collect.return_value = []
    yt_mock.last_raw_events = []

    layer = MacroNewsLayer(news_collector=news_mock, youtube_collector=yt_mock)
    result = layer.detect()

    # collect 실패는 source_results=False → 여전히 DEGRADED
    assert result.dq_state is not None
    assert result.dq_state.degraded_flag is True
    assert result.level == "SYSTEM_DEGRADED"


def test_bfix_news_collector_exposes_last_raw_events_attribute() -> None:
    """B-fix: NewsCollector 인스턴스가 last_raw_events 속성을 가진다"""
    from collectors.news_collector import NewsCollector

    nc = NewsCollector()
    assert hasattr(nc, "last_raw_events")
    assert isinstance(nc.last_raw_events, list)
    assert nc.last_raw_events == []  # 초기값


def test_bfix_youtube_collector_exposes_last_raw_events_attribute() -> None:
    """B-fix: YouTubeCollector 인스턴스가 last_raw_events 속성을 가진다"""
    from collectors.youtube_collector import YouTubeCollector

    yt = YouTubeCollector()
    assert hasattr(yt, "last_raw_events")
    assert isinstance(yt.last_raw_events, list)
    assert yt.last_raw_events == []


def test_bfix_dq_uses_raw_events_not_filtered_events() -> None:
    """B-fix: collect() 반환과 last_raw_events가 다를 때 DQ는 last_raw_events를 사용"""
    raw_news = [_make_event(f"news_{i}", age_minutes=15) for i in range(5)]

    news_mock = MagicMock()
    news_mock.collect.return_value = []  # 필터 후 0건
    news_mock.last_raw_events = raw_news  # raw 5건

    yt_mock = MagicMock()
    yt_mock.collect.return_value = []
    yt_mock.last_raw_events = []  # YT는 0건

    layer = MacroNewsLayer(news_collector=news_mock, youtube_collector=yt_mock)
    result = layer.detect()

    # DQ는 raw 5건을 사용 → fresh=1.0, degraded=False
    assert result.dq_state.fresh_event_ratio == 1.0
    assert result.dq_state.degraded_flag is False
    # 한편 result.news_events는 collect() 반환을 사용 → 0건
    assert len(result.news_events) == 0


# ── B-fix2 추가 테스트 (2026-04-26) ─────────────────────────────────


def test_bfix2_old_news_with_healthy_source_is_not_degraded() -> None:
    """B-fix2: validator 통과한 RSS의 발행 시각이 1시간 이상 과거여도
    source 정상이면 degraded=False. 운영 첫 가동에서 발견된 시나리오 회귀 보호.

    실제 운영 시나리오:
      - validator window=24h → 1.7~12.4시간 전 RSS 통과
      - 이전 동작 (B-fix2 전): fresh_window 1h → fresh=0 → 거짓 DEGRADED
      - B-fix2 후: fresh_window 24h → fresh=1.0 → 정상
    """
    # 1.7시간 전, 12.4시간 전 (운영 로그 실측 시각)
    raw_news = [
        _make_event("yahoo_old", age_minutes=int(1.7 * 60)),
        _make_event("reuters_old", age_minutes=int(12.4 * 60)),
    ]

    news_mock = MagicMock()
    news_mock.collect.return_value = []
    news_mock.last_raw_events = raw_news

    yt_mock = MagicMock()
    yt_mock.collect.return_value = []
    yt_mock.last_raw_events = []

    layer = MacroNewsLayer(news_collector=news_mock, youtube_collector=yt_mock)
    result = layer.detect()

    # 핵심: 24h fresh window 안이므로 fresh_ratio=1.0 → degraded=False
    assert result.dq_state is not None
    assert result.dq_state.degraded_flag is False
    assert result.dq_state.fresh_event_ratio == 1.0
    assert result.level != "SYSTEM_DEGRADED"


def test_bfix2_fresh_window_env_override(monkeypatch) -> None:
    """B-fix2: DQ_FRESH_WINDOW_SECONDS env로 윈도우 동적 조정 가능"""
    from detection.dq_monitor import DataQualityMonitor

    # env로 6시간으로 단축
    monkeypatch.setenv("DQ_FRESH_WINDOW_SECONDS", str(6 * 3600))
    monitor = DataQualityMonitor()
    assert monitor.fresh_window_seconds == 6 * 3600

    # env 미설정 시 기본 24h
    monkeypatch.delenv("DQ_FRESH_WINDOW_SECONDS", raising=False)
    monitor2 = DataQualityMonitor()
    assert monitor2.fresh_window_seconds == 24 * 3600


def test_bfix2_fresh_ratio_min_default_is_zero() -> None:
    """B-fix2: fresh_event_ratio_min 기본값은 0.0 (정보성 지표)"""
    from detection.dq_monitor import DataQualityMonitor

    monitor = DataQualityMonitor()
    assert monitor.thresholds["fresh_event_ratio_min"] == 0.0


def test_bfix2_source_success_threshold_default_is_strengthened() -> None:
    """B-fix2: source_success_rate_min 기본값 0.50 → 0.75로 강화"""
    from detection.dq_monitor import DataQualityMonitor

    monitor = DataQualityMonitor()
    assert monitor.thresholds["source_success_rate_min"] == 0.75
