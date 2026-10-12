"""
제목: Q1 — 뉴스 소스별 수집 결과 / DQ 소스 단위 성공률 / 소스 장애 운영 경고 테스트
내용: 설계 q1_design.md §8 T01~T22. 외부 네트워크 호출 없음 (feedparser.parse patch).
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import run_alert
from collectors.base import CollectorEvent
from collectors.news_collector import NewsCollector, SourceResult, build_source_result
from detection.dq_monitor import DataQualityMonitor, DataQualityState
from detection.macro_news_layer import MacroNewsLayer, MacroNewsResult

NEWS_SOURCES = ("fed_rss", "fed_speeches", "reuters_markets", "investing_com", "marketwatch")


# ────────────────────────────────────────────────────────
# 헬퍼
# ────────────────────────────────────────────────────────
class _Feed(SimpleNamespace):
    """feedparser 결과 대용 (status/bozo 속성 유무 제어용)."""


def _entry(title: str, hours_ago: float) -> MagicMock:
    entry = MagicMock()
    data = {"title": title, "link": f"https://e.com/{title}", "summary": "s"}
    entry.get = lambda key, default="": data.get(key, default)
    entry.published_parsed = time.gmtime(time.time() - hours_ago * 3600)
    return entry


def _feed(status=200, entries=None, bozo=False, with_status=True, bozo_exception=None) -> _Feed:
    f = _Feed(entries=entries if entries is not None else [], bozo=bozo)
    if with_status:
        f.status = status
    if bozo_exception is not None:
        f.bozo_exception = bozo_exception
    return f


def _single_registry(name: str = "src", tier: str = "A") -> dict:
    return {tier: {name: {"url": f"https://{name}.example/rss", "auto_l1": False}}}


def _sr(name: str, ok: bool, tier: str = "A", error: str | None = None) -> SourceResult:
    return SourceResult(name, tier, 200 if ok else 404, 5 if ok else 0, 1 if ok else 0, ok,
                        None if ok else (error or "http_404"))


def _layer(source_results: list[SourceResult] | None, yt_fail: bool = False,
           news_fail: bool = False) -> MacroNewsLayer:
    news = MagicMock()
    if news_fail:
        news.collect.side_effect = RuntimeError("rss down")
    else:
        news.collect.return_value = []
    news.last_raw_events = []
    if source_results is not None:
        news.last_source_results = source_results
    yt = MagicMock()
    if yt_fail:
        yt.collect.side_effect = RuntimeError("yt down")
    else:
        yt.collect.return_value = []
    yt.last_raw_events = []
    yt.last_failed_channels = []
    return MacroNewsLayer(news_collector=news, youtube_collector=yt, dq_store=MagicMock())


# ────────────────────────────────────────────────────────
# T01~T08 NewsCollector 소스별 결과
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_t01_in_window_zero_is_success() -> None:
    feed = _feed(200, [_entry(f"old{i}", 72) for i in range(15)])
    nc = NewsCollector(source_registry=_single_registry("fed_rss", "S"))
    with patch("collectors.news_collector.feedparser.parse", return_value=feed):
        events = nc._collect_tier("S")
    assert events == []
    (r,) = nc.last_source_results
    assert (r.ok, r.in_window, r.entries_total, r.error, r.http_status) == (True, 0, 15, None, 200)


@pytest.mark.unit
def test_t02_http_404_fails_with_warning(caplog) -> None:
    nc = NewsCollector(source_registry=_single_registry("yahoo", "B"))
    with patch("collectors.news_collector.feedparser.parse",
               return_value=_feed(404, [], bozo=True)), caplog.at_level(logging.WARNING):
        nc._collect_tier("B")
    (r,) = nc.last_source_results
    assert (r.ok, r.http_status, r.error) == (False, 404, "http_404")
    assert any("소스 실패" in m and "http_404" in m for m in caplog.messages)


@pytest.mark.unit
def test_t03_exception_marks_failure_and_continues() -> None:
    registry = {"A": {"bad": {"url": "https://bad/rss"}, "good": {"url": "https://good/rss"}}}
    nc = NewsCollector(source_registry=registry)
    nc.retry_delay = 0
    good = _feed(200, [_entry("fresh", 1)])

    def fake_parse(url):
        if "bad" in url:
            raise RuntimeError("net err")
        return good

    with patch("collectors.news_collector.feedparser.parse", side_effect=fake_parse), \
            patch("collectors.base.time.sleep"):
        events = nc._collect_tier("A")
    bad, ok = nc.last_source_results
    assert (bad.name, bad.ok, bad.http_status) == ("bad", False, None)
    assert bad.error == "exception:CollectorException"
    assert ok.ok is True and len(events) == 1


@pytest.mark.unit
def test_t04_no_status_bozo_urlerror_fails() -> None:
    import urllib.error

    feed = _feed(with_status=False, bozo=True, bozo_exception=urllib.error.URLError("x"))
    r = build_source_result("fed_speeches", "S", feed, 0)
    assert (r.ok, r.http_status, r.error) == (False, None, "no_status:URLError")


@pytest.mark.unit
def test_t05_bozo_with_200_and_entries_is_success() -> None:
    feed = _feed(200, [_entry("a", 1), _entry("b", 1), _entry("c", 1)], bozo=True)
    r = build_source_result("investing_com", "A", feed, 3)
    assert (r.ok, r.bozo, r.error) == (True, True, None)


@pytest.mark.unit
def test_t06_empty_feed_fails() -> None:
    r = build_source_result("marketwatch", "B", _feed(200, []), 0)
    assert (r.ok, r.error) == (False, "empty_feed")


@pytest.mark.unit
@pytest.mark.parametrize("status,ok", [(301, True), (302, True), (304, True), (199, False),
                                       (400, False), (500, False)])
def test_t06b_2xx_3xx_success_rule(status, ok) -> None:
    """Q1-1 확정: 2xx/3xx + entries≥1 성공."""
    r = build_source_result("s", "A", _feed(status, [_entry("a", 1)]), 1)
    assert r.ok is ok


@pytest.mark.unit
def test_t06c_no_status_without_bozo_and_entries_is_success() -> None:
    """Q1-1 확정: status 속성 없음(로컬 파일 등) + bozo 없음 + entries≥1 → 성공."""
    r = build_source_result("s", "A", _feed(with_status=False, entries=[_entry("a", 1)]), 1)
    assert (r.ok, r.http_status) == (True, None)


@pytest.mark.unit
def test_t07_magicmock_feed_keeps_events_and_status_none() -> None:
    feed = MagicMock()
    feed.entries = [_entry("fresh", 1)]
    nc = NewsCollector(source_registry=_single_registry("s", "A"))
    with patch("collectors.news_collector.feedparser.parse", return_value=feed):
        events = nc._collect_tier("A")
    (r,) = nc.last_source_results
    assert len(events) == 1
    assert r.http_status is None and r.bozo is False


@pytest.mark.unit
def test_t08_collect_resets_source_results_each_call() -> None:
    nc = NewsCollector()
    feed = _feed(200, [_entry("fresh", 1)])
    with patch("collectors.news_collector.feedparser.parse", return_value=feed):
        nc.collect()
        nc.collect()
    assert [r.name for r in nc.last_source_results] == list(NEWS_SOURCES)


@pytest.mark.unit
def test_t09_registry_keys() -> None:
    from config.settings import NEWS_SOURCE_REGISTRY

    keys = [k for tier in ("S", "A", "B") for k in NEWS_SOURCE_REGISTRY[tier]]
    assert keys == list(NEWS_SOURCES)
    assert "yahoo_finance" not in keys
    assert NEWS_SOURCE_REGISTRY["S"]["fed_speeches"]["auto_l1"] is False
    assert NEWS_SOURCE_REGISTRY["S"]["fed_rss"]["auto_l1"] is True
    assert NEWS_SOURCE_REGISTRY["B"]["marketwatch"]["url"] == (
        "https://feeds.content.dowjones.io/public/rss/mw_topstories"
    )


# ────────────────────────────────────────────────────────
# T10~T14 MacroNewsLayer / DQ
#   리뷰 M3 (2026-10-13): v1 DQ 입력 = 수집기 단위 2키(origin/main 동일).
#   소스 단위(뉴스 5 + youtube 1)는 failed_sources/운영 경고/ v2 DQ(shadow) 에만 사용.
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_t10_one_failure_is_healthy_with_warning() -> None:
    srs = [_sr(n, n != "marketwatch") for n in NEWS_SOURCES]
    result = _layer(srs).detect()
    assert result.dq_state.source_results == {"news_collector": True, "youtube_collector": True}
    assert result.dq_state.source_success_rate == pytest.approx(1.0)
    assert result.dq_state.degraded_flag is False
    assert result.failed_sources == ["marketwatch"]
    assert result.source_level_success_rate == pytest.approx(round(5 / 6, 3))
    assert any(w.startswith("news_source_failures:") for w in result.ops_warnings)
    assert len(result.news_source_results) == 5


@pytest.mark.unit
def test_t11_two_failures_v1_unchanged_v2_degraded() -> None:
    """v1 은 수집기 단위라 소스 2개 실패로 강등되지 않음. v2(shadow)는 소스 단위 DQ 로 degraded."""
    srs = [_sr(n, n not in ("fed_speeches", "investing_com")) for n in NEWS_SOURCES]
    result = _layer(srs).detect()
    assert result.dq_state.degraded_flag is False
    assert result.level == "NONE"
    assert result.source_level_success_rate == pytest.approx(round(4 / 6, 3))
    assert result.shadow["policy"] == "v2" and result.shadow["level"] == "SYSTEM_DEGRADED"


@pytest.mark.unit
def test_t12_news_collect_exception_keeps_legacy_semantics() -> None:
    result = _layer(None, news_fail=True).detect()
    assert result.dq_state.source_results == {"news_collector": False, "youtube_collector": True}
    assert result.level == "SYSTEM_DEGRADED"
    # 리뷰 m2: collect 전체 예외는 SYSTEM_DEGRADED 경보와 중복 → 운영 경고 대상 제외
    assert result.failed_sources == []


@pytest.mark.unit
def test_t13_mock_collector_without_source_results_falls_back() -> None:
    result = _layer(None).detect()
    assert result.dq_state.source_results == {"news_collector": True, "youtube_collector": True}
    assert result.failed_sources == []
    assert result.news_source_results == []


@pytest.mark.unit
def test_t14_youtube_exception_legacy_dq_and_source_warning() -> None:
    """v1 DQ: YouTube 예외 → 1/2=0.5 → SYSTEM_DEGRADED (origin/main 동일). 운영 경고에도 포함."""
    srs = [_sr(n, True) for n in NEWS_SOURCES]
    result = _layer(srs, yt_fail=True).detect()
    assert result.dq_state.source_results == {"news_collector": True, "youtube_collector": False}
    assert result.dq_state.source_success_rate == pytest.approx(0.5)
    assert result.dq_state.degraded_flag is True
    assert result.level == "SYSTEM_DEGRADED"
    assert result.failed_sources == ["youtube_collector"]
    assert result.source_level_success_rate == pytest.approx(round(5 / 6, 3))
    assert result.shadow["level"] != "SYSTEM_DEGRADED"


# ────────────────────────────────────────────────────────
# T15~T18 run_alert 운영 경고
# ────────────────────────────────────────────────────────
def _result_with_failures(failed: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        failed_sources=failed,
        news_source_results=[_sr(n, False, tier="B").to_dict() for n in failed],
        dq_state=SimpleNamespace(source_success_rate=0.83),
        level="NONE",
    )


@pytest.fixture
def tg(monkeypatch):
    tg_instance = MagicMock()
    tg_cls = MagicMock(return_value=tg_instance)
    monkeypatch.setattr("publishers.telegram_publisher.TelegramPublisher", tg_cls)
    monkeypatch.setattr(run_alert, "DRY_RUN", False)
    return tg_instance


@pytest.mark.unit
def test_t15_failure_sends_internal_once_and_sets_cooldown(tg) -> None:
    store = MagicMock()
    store.is_cooldown_active.return_value = False
    run_alert._notify_source_failures(store, _result_with_failures(["marketwatch"]))
    assert tg.publish_internal.call_count == 1
    assert "marketwatch" in tg.publish_internal.call_args[0][0]
    store.is_cooldown_active.assert_called_once_with("ops:source_fail:marketwatch")
    store.set_cooldown.assert_called_once_with(
        "ops:source_fail:marketwatch", "source_fail", minutes=1440
    )


@pytest.mark.unit
def test_t16_cooldown_active_suppresses(tg) -> None:
    store = MagicMock()
    store.is_cooldown_active.return_value = True
    run_alert._notify_source_failures(store, _result_with_failures(["marketwatch"]))
    tg.publish_internal.assert_not_called()
    store.set_cooldown.assert_not_called()


@pytest.mark.unit
def test_t16b_partial_cooldown_only_fresh_sources(tg) -> None:
    store = MagicMock()
    store.is_cooldown_active.side_effect = lambda key: key.endswith("fed_speeches")
    run_alert._notify_source_failures(store, _result_with_failures(["fed_speeches", "marketwatch"]))
    text = tg.publish_internal.call_args[0][0]
    assert "marketwatch" in text and "fed_speeches" not in text
    store.set_cooldown.assert_called_once()


@pytest.mark.unit
def test_t17_dry_run_skips_tg_but_records_detail(monkeypatch) -> None:
    monkeypatch.setattr(run_alert, "DRY_RUN", True)
    tg_cls = MagicMock()
    monkeypatch.setattr("publishers.telegram_publisher.TelegramPublisher", tg_cls)
    srs = [_sr("marketwatch", False, "B")]
    result = MacroNewsResult(
        score=0.0, level="NONE", news_events=[], youtube_events=[], news_score=0.0,
        youtube_bonus=0.0, failed_sources=["marketwatch"],
        news_source_results=[r.to_dict() for r in srs],
    )
    monkeypatch.setattr("detection.macro_news_layer.MacroNewsLayer.detect", lambda self: result)
    rec_holder = {}
    real_recorder = run_alert.RunRecorder

    def _recorder(*a, **kw):
        rec_holder["rec"] = real_recorder(*a, **kw)
        return rec_holder["rec"]

    monkeypatch.setattr(run_alert, "RunRecorder", _recorder)
    run_alert.run()
    detail = rec_holder["rec"].row["detail"]
    assert detail["failed_sources"] == ["marketwatch"]
    assert detail["source_results"][0]["name"] == "marketwatch"
    tg_cls.return_value.publish_internal.assert_not_called()


@pytest.mark.unit
def test_t17b_no_store_skips(tg) -> None:
    run_alert._notify_source_failures(None, _result_with_failures(["marketwatch"]))
    tg.publish_internal.assert_not_called()


@pytest.mark.unit
def test_t18_publish_exception_is_swallowed(tg) -> None:
    store = MagicMock()
    store.is_cooldown_active.return_value = False
    tg.publish_internal.side_effect = RuntimeError("tg down")
    run_alert._notify_source_failures(store, _result_with_failures(["marketwatch"]))  # no raise
    store.set_cooldown.assert_not_called()


@pytest.mark.unit
def test_t18b_no_failures_noop(tg) -> None:
    store = MagicMock()
    run_alert._notify_source_failures(store, _result_with_failures([]))
    store.is_cooldown_active.assert_not_called()
    tg.publish_internal.assert_not_called()


# ────────────────────────────────────────────────────────
# T19~T22 공통 파서 / v1 불변 / 직렬화 / 쿨다운 회귀
# ────────────────────────────────────────────────────────
@pytest.mark.unit
@pytest.mark.parametrize("raw,expected", [(None, 77), ("", 77), ("   ", 77), ("abc", 77),
                                          ("3600", 3600), (" 120 ", 120)])
def test_t19_get_env_int(monkeypatch, raw, expected) -> None:
    from config.settings import get_env_int

    if raw is None:
        monkeypatch.delenv("Q1_TEST_INT", raising=False)
    else:
        monkeypatch.setenv("Q1_TEST_INT", raw)
    assert get_env_int("Q1_TEST_INT", 77) == expected


@pytest.mark.unit
def test_t20_empty_window_env_v1_health_5400_dq_86400(monkeypatch) -> None:
    from detection.macro_news_layer import _get_recency_window_seconds

    monkeypatch.setenv("DQ_FRESH_WINDOW_SECONDS", "")
    assert _get_recency_window_seconds() == 5400  # v1 불변
    assert DataQualityMonitor().fresh_window_seconds == 86400


@pytest.mark.unit
def test_t21_dq_payload_source_results_json_serializable() -> None:
    now = datetime.now(UTC)
    state = DataQualityState(
        fresh_event_ratio=1.0, source_success_rate=5 / 6, lag_seconds_p95=1.0,
        volume_zscore=None, degraded_flag=False, degraded_reasons=[],
        cycle_started_at=now - timedelta(seconds=3), cycle_finished_at=now,
        source_results={**{n: True for n in NEWS_SOURCES}, "youtube_collector": False},
    )
    payload = state.to_dict()
    assert all(isinstance(k, str) and isinstance(v, bool)
               for k, v in payload["source_results"].items())
    json.dumps(payload)
    assert payload["source_success_rate"] == 0.833


@pytest.mark.unit
def test_t22_set_cooldown_default_minutes_regression() -> None:
    from db.alert_store import AlertStore

    store = AlertStore.__new__(AlertStore)
    client = MagicMock()
    store._get_client = lambda: client  # type: ignore[method-assign]
    upsert = client.table.return_value.upsert

    before = datetime.now(UTC)
    assert store.set_cooldown("L1", "a1") is True
    until = datetime.fromisoformat(upsert.call_args[0][0]["cooldown_until"])
    assert timedelta(minutes=59) < until - before < timedelta(minutes=61)

    assert store.set_cooldown("ops:source_fail:x", "source_fail", minutes=1440) is True
    until = datetime.fromisoformat(upsert.call_args[0][0]["cooldown_until"])
    assert timedelta(minutes=1439) < until - before < timedelta(minutes=1441)


@pytest.mark.unit
def test_source_result_is_collector_event_agnostic() -> None:
    """SourceResult.to_dict 키 계약 (ia_alert_run.detail.source_results)."""
    assert set(_sr("a", True).to_dict()) == {
        "name", "tier", "http_status", "entries_total", "in_window", "ok", "error", "bozo",
    }
    assert CollectorEvent  # import 유지 (타입 참조)
