"""
제목: Q2 감지 정책 v2 + shadow 테스트
내용: q2_design.md §8 — v1 골든(Q2 변경 전 HEAD 516197e 로 산출, tests/fixtures/q2_v1_golden.json),
      v2 매처, YouTube 토큰 정규화, health v2/판정불가, shadow 기록, DETECTION_POLICY env.
      외부 네트워크 호출 없음.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import replace as dataclasses_replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import collectors.news_collector as nc_mod
from collectors.base import CollectorEvent
from collectors.news_collector import NewsCollector
from collectors.youtube_collector import YOUTUBE_URGENT_KEYWORDS_KR
from config import keyword_rules
from config.market_calendar import get_threshold_for_profile
from detection.dq_monitor import DataQualityState
from detection.macro_news_layer import MacroNewsLayer, MacroNewsResult
from detection.scoring_policy import (
    DetectionInput,
    KeywordMatcherV2,
    PolicyOutcome,
    ScoringPolicyV2,
    get_fresh_window_seconds,
    normalize_youtube_tokens,
    outcome_to_shadow,
    resolve_detection_policy,
)

GOLDEN = json.loads(
    (Path(__file__).parent / "fixtures" / "q2_v1_golden.json").read_text(encoding="utf-8")
)
NOW = datetime.now(UTC)
V1_DICT_NAMES = [
    "URGENT_KEYWORDS_L1", "URGENT_KEYWORDS_L2", "URGENT_KEYWORDS_TARIFF", "URGENT_KEYWORDS_GEO",
    "URGENT_KEYWORDS_FISCAL", "URGENT_KEYWORDS_TECH_REG", "URGENT_KEYWORDS_MACRO_DATA",
    "URGENT_KEYWORDS_CENTRAL_BANK", "URGENT_KEYWORDS_EARNINGS", "URGENT_KEYWORDS_POLICY",
]


# ────────────────────────────────────────────────────────
# helpers
# ────────────────────────────────────────────────────────
def ne(title, src="investing_com", tier="A", ks=0.0, sc=1, auto=False, kw=None, age_min=30):
    return CollectorEvent(
        source_type="news", source_name=src, event_id=src + title, title=title, summary="",
        url="https://u/" + title, published_at=NOW - timedelta(minutes=age_min), tier=tier,
        auto_l1=auto, keyword_score=ks, source_count=sc, matched_keywords=kw or ["x"],
    )


def ye(kws, ks=3.0, w=1.0, age_min=45, eid=None):
    return CollectorEvent(
        source_type="youtube", source_name="ch", event_id=eid or f"y{ks}{w}{kws}", title="t",
        summary="", url="https://y", published_at=NOW - timedelta(minutes=age_min),
        channel_weight=w, keyword_score=ks, matched_keywords=kws,
    )


def make_layer(news=None, yt=None, candidates=None, policy=None, raw_news=None):
    nm = MagicMock()
    nm.collect.return_value = news or []
    nm.last_raw_events = raw_news if raw_news is not None else (news or [])
    nm.last_candidates = candidates if candidates is not None else (news or [])
    nm.last_source_results = []
    ym = MagicMock()
    ym.collect.return_value = yt or []
    ym.last_raw_events = yt or []
    ym.last_failed_channels = []
    return MacroNewsLayer(news_collector=nm, youtube_collector=ym, dq_store=MagicMock(),
                          detection_policy=policy)


def golden_scenarios():
    return {
        "G-01": ([], []),
        "G-02": ([], [ye(["관세", "급락"], 5.0), ye(["FOMC"], 3.0), ye(["긴급"], 3.0)]),
        "G-03": ([ne("FOMC stmt", "fed_rss", "S", 5.0, auto=True, kw=["auto_l1"])], []),
        "G-04": ([ne("Fed signals rate cut", "investing_com", "A", 3.0, kw=["rate cut"])], []),
        "G-05": ([ne("Oil hormuz", "investing_com", "A", 4.5, sc=2, kw=["strait of hormuz"]),
                  ne("Oil hormuz", "marketwatch", "B", 4.5, sc=2, kw=["strait of hormuz"])],
                 [ye(["전쟁"], 1.0)]),
        "G-06": ([ne("Fed hawkish", "investing_com", "A", 5.0, kw=["fomc minutes", "hawkish"])],
                 [ye(["fomc minutes", "hawkish"], 4.0, 1.2)]),
    }


# ────────────────────────────────────────────────────────
# G: v1 골든 (결과 불변)
# ────────────────────────────────────────────────────────
@pytest.mark.unit
@pytest.mark.parametrize("profile", ["intraday", "extended", "holiday"])
@pytest.mark.parametrize("env", [None, ""])
@pytest.mark.parametrize("gid", ["G-01", "G-02", "G-03", "G-04", "G-05", "G-06"])
def test_g01_06_v1_golden(monkeypatch, gid, env, profile) -> None:
    if env is None:
        monkeypatch.delenv("DQ_FRESH_WINDOW_SECONDS", raising=False)
    else:
        monkeypatch.setenv("DQ_FRESH_WINDOW_SECONDS", env)
    monkeypatch.delenv("DETECTION_POLICY", raising=False)
    news, yt = golden_scenarios()[gid]
    with patch("detection.macro_news_layer.get_market_profile", return_value=profile):
        r = make_layer(news, yt).detect()
    expected = GOLDEN["layer"][f"{gid}|{profile}|{env!r}"]
    assert [r.level, r.score, r.news_score, r.youtube_bonus, r.health_score, r.reasoning] == expected
    assert r.active_policy == "v1"
    assert r.shadow is not None and r.shadow["policy"] == "v2"


@pytest.mark.unit
def test_g07_v1_dict_hash_and_count() -> None:
    payload = [[n, list(getattr(keyword_rules, n).items())] for n in V1_DICT_NAMES]
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()
    assert digest == GOLDEN["dict_sha256"]
    assert sum(len(p[1]) for p in payload) == GOLDEN["dict_count"] == 120
    # news_collector 재노출 동일 객체
    for n in V1_DICT_NAMES:
        assert getattr(nc_mod, n) is getattr(keyword_rules, n)
    assert nc_mod.KEYWORD_THRESHOLD == 2.0 and nc_mod.AI_SCORE_MIN_KEYWORD == 2.5


@pytest.mark.unit
def test_g07b_v1_filter_golden_titles() -> None:
    c = NewsCollector()
    for title, expected in GOLDEN["filter"].items():
        e = CollectorEvent(source_type="news", source_name="r", event_id="x", title=title,
                           summary="", url="u", published_at=NOW, tier="A")
        out = c._filter_by_keywords([e])
        got = [out[0].keyword_score, out[0].matched_keywords] if out else None
        assert got == expected, title


class _Entry:
    def __init__(self, title, src, age_h):
        self._d = {"title": title, "link": f"https://x/{src}/{title}", "summary": ""}
        self.published_parsed = (NOW - timedelta(hours=age_h)).utctimetuple()

    def get(self, k, d=""):
        return self._d.get(k, d)


class _Feed:
    def __init__(self, entries):
        self.status = 200
        self.entries = entries
        self.bozo = False


def _feeds():
    out = {}
    for src in ("fed_rss", "a1", "a2", "b1"):
        es = []
        for i in range(30):
            dup = i % 5 == 0
            old = i % 4 == 0
            title = f"{src} headline {i % 7 if dup else i} rate cut"
            es.append(_Entry(title, src, 30 if old else (i % 20) + 0.5))
        out["u-" + src] = _Feed(es)
    return out


_REG = {"S": {"fed_rss": {"url": "u-fed_rss", "auto_l1": True}},
        "A": {"a1": {"url": "u-a1"}, "a2": {"url": "u-a2"}}, "B": {"b1": {"url": "u-b1"}}}


@pytest.mark.unit
def test_g08_v1_subset_equals_first_10_entries() -> None:
    feeds = _feeds()
    with patch("collectors.news_collector.feedparser.parse", side_effect=lambda u: feeds[u]):
        nc = NewsCollector(source_registry=_REG)
        nc.collect()
    # 기대: 소스당 entries[:10] 만으로 처리한 결과 (현행 v1 로직 재현)
    seen: set[str] = set()
    expected: list[str] = []
    for tier in ("S", "A", "B"):
        for name, cfg in _REG[tier].items():
            for entry in feeds[cfg["url"]].entries[:10]:
                ev = nc._entry_to_event(entry, name, tier, cfg)
                if ev.event_id in seen:
                    continue
                seen.add(ev.event_id)
                if nc._is_within_window(ev.published_at):
                    expected.append(ev.event_id)
    got = [e.event_id for e in nc.last_raw_events]
    assert got == expected
    assert len(nc.last_candidates) > len(nc.last_raw_events)
    # 후보는 v1 raw 를 앞에 그대로 포함
    assert [e.event_id for e in nc.last_candidates[: len(got)]] == got


@pytest.mark.unit
def test_g08b_candidates_are_copies_before_keyword_filter() -> None:
    feeds = _feeds()
    with patch("collectors.news_collector.feedparser.parse", side_effect=lambda u: feeds[u]):
        nc = NewsCollector(source_registry=_REG)
        out = nc.collect()
    assert out, "v1 필터 통과 이벤트 존재"
    passed_ids = {e.event_id for e in out if not e.auto_l1}
    for c in nc.last_candidates:
        if c.event_id in passed_ids:
            assert c.keyword_score == 0.0 and c.matched_keywords == []
    assert all(c is not r for c in nc.last_candidates for r in nc.last_raw_events)


# ────────────────────────────────────────────────────────
# M: v2 매처 (설계 §3.3 재현표)
# ────────────────────────────────────────────────────────
V2_TABLE = [
    ("Delta delays debut of Saudi Arabia service until March after Riyadh airport attacks", 4.5),
    ("Starbucks says it is not buying Chipotle after takeover speculation", 0.0),
    ("FAA warns U.S. airlines of safety risks over Saudi Arabia after attacks", 4.5),
    ("Fed signals rate cut", 4.0),
    ("CPI rises 0.4%", 4.5),
    ("Treasury yields jump", 3.5),
    ("Oil surges after attacks on Saudi airport", 10.5),
    ("Fed signals rate cuts ahead", 4.0),
    ("Fed cuts rates by quarter point", 4.0),
    ("Powell says Fed will cut interest rates", 6.0),
    ("Stocks fall as inflation rises more than expected", 3.0),
    ("10-year Treasury yield hits highest since 2007", 3.5),
    ("Oil prices spike after drone strike on Saudi facility", 13.5),
    ("FOMC holds rates steady", 4.0),
    ("S&P 500 drops 3%", 4.0),
    ("Dow plunges 1,000 points", 6.0),
    ("Recession fears grow", 3.5),
    ("Emergency rate cut", 9.0),
    ("Emergency medical evacuation insurance: What it covers, what it costs, and how to compare "
     "plans", 0.9),
    ("Court blocks Alabama from erasing significantly Black US House district", 0.0),
    ("Twilio Stock Surges 31% On Earnings Beat As AI Tools Gain Traction", 2.6),
    ("Stock market today: Dow, S&P 500, Nasdaq slip as PCE inflation stays sticky, Nvidia "
     "earnings loom", 7.0),
    ("PCE Inflation Data Sets High Bar For Warsh's Jackson Hole Speech (Live Coverage)", 8.5),
    ("US rate futures cut chances of September rate hike after jobs data", 6.0),
    ("Alnylam Q2 2026 slides show strong growth amid guidance cut, stock plunge", 4.1),
    ("Ferrari CEO says he 'would not change anything' about polarizing Luce EV debut", 0.0),
    ("Audi continues U.S. product push with three-row Q9 SUV", 0.0),
    ("Oil prices fall 5% to 3-month low on hopes Strait of Hormuz will open", 5.5),
    ("Warsh promises sweeping Fed review after central bank hints at rate hike this year", 4.0),
    ("Federal Reserve issues FOMC statement", 3.0),
]


@pytest.mark.unit
@pytest.mark.parametrize("title,score", V2_TABLE)
def test_m01_30_v2_matcher_table(title, score) -> None:
    r = KeywordMatcherV2().match(title)
    assert r.score == pytest.approx(score)
    passes = r.score >= keyword_rules.KEYWORD_THRESHOLD_V2
    assert passes == (score >= 3.0)


@pytest.mark.unit
def test_m31_word_boundary_significantly() -> None:
    assert KeywordMatcherV2().match("erasing significantly Black district").rule_ids == []


@pytest.mark.unit
def test_m32_auto_l1_bypass() -> None:
    e = ne("Minutes of the Board's discount rate meetings", "fed_rss", "S", 5.0, auto=True,
           kw=["auto_l1"])
    [(out, topics)] = KeywordMatcherV2().filter_events([e])
    assert out.keyword_score == 5.0 and out.matched_keywords == ["auto_l1"] and topics == []


@pytest.mark.unit
def test_m33_filter_does_not_mutate_input() -> None:
    e = ne("Fed signals rate cut", ks=0.0, kw=["orig"])
    [(out, topics)] = KeywordMatcherV2().filter_events([e])
    assert e.keyword_score == 0.0 and e.matched_keywords == ["orig"]
    assert out is not e and out.keyword_score == 4.0 and topics == ["fed_policy"]


@pytest.mark.unit
def test_m34_generic_full_weight_with_anchor() -> None:
    r = KeywordMatcherV2().match("Oil surges")
    assert "g_move" in r.rule_ids and r.score == pytest.approx(1.0 + 3.0 + 2.0)


@pytest.mark.unit
def test_m35_v1_import_rules_track_v1_values() -> None:
    rules = {rid: w for rid, _, w, _, _ in keyword_rules.V2_RULES}
    for k, w in keyword_rules.URGENT_KEYWORDS_TARIFF.items():
        assert rules[f"v1:{k}"] == w


# ────────────────────────────────────────────────────────
# N: YouTube 토큰 정규화
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_n01_token_map_covers_all_youtube_keywords() -> None:
    assert set(keyword_rules.YT_TOKEN_MAP) == set(YOUTUBE_URGENT_KEYWORDS_KR)
    assert len(keyword_rules.YT_TOKEN_MAP) == 29


@pytest.mark.unit
def test_n02_bonus_on_topic_intersection() -> None:
    bonus = ScoringPolicyV2.compute_youtube_bonus([["fed_policy"]], [ye(["FOMC"], 3.0, 1.2)])
    assert bonus == pytest.approx(1.2)


@pytest.mark.unit
def test_n03_casefold() -> None:
    assert normalize_youtube_tokens(["fomc", "cpi"]) == {"fed_policy", "inflation"}


@pytest.mark.unit
def test_n04_none_tokens_no_bonus() -> None:
    assert normalize_youtube_tokens(["긴급", "속보"]) == set()
    assert ScoringPolicyV2.compute_youtube_bonus([["fed_policy"]], [ye(["긴급", "속보"])]) == 0.0


# ────────────────────────────────────────────────────────
# H: health v2 / 신뢰계수 / 판정
# ────────────────────────────────────────────────────────
def _inp(cands, yt=None, yt_raw=None, dq=None, profile="intraday"):
    return DetectionInput(
        news_v1=[], news_candidates=cands, youtube_events=yt or [], youtube_raw=yt_raw or [],
        market_profile=profile, thresholds=get_threshold_for_profile(profile), dq_state=dq,
    )


def _dq(degraded: bool) -> DataQualityState:
    return DataQualityState(
        fresh_event_ratio=0.0, source_success_rate=0.4 if degraded else 1.0, lag_seconds_p95=0.0,
        volume_zscore=None, degraded_flag=degraded,
        degraded_reasons=["source_success_rate<0.75"] if degraded else [],
        cycle_started_at=NOW, cycle_finished_at=NOW, source_results={},
    )


@pytest.mark.unit
def test_h01_no_candidates_undeterminable(monkeypatch) -> None:
    monkeypatch.delenv("DETECTION_POLICY", raising=False)
    yt = [ye(["관세", "급락"], 5.0, eid="a"), ye(["FOMC"], 3.0, eid="b")]
    r = make_layer([], yt, candidates=[], policy="v2").detect()
    assert r.level == "NONE" and r.health_score == 0.0 and r.active_policy == "v2"
    assert r.reasoning_json["contributing_factors"][0]["factor"] == "undeterminable_no_news"
    out = ScoringPolicyV2(make_layer()).evaluate(_inp([], yt, yt))
    assert out.health_score is None and out.level == "NONE" and "판정불가" in out.reason


@pytest.mark.unit
def test_h02_no_candidates_dq_degraded() -> None:
    out = ScoringPolicyV2(make_layer()).evaluate(_inp([], dq=_dq(True)))
    assert out.level == "SYSTEM_DEGRADED" and out.health_score is None


@pytest.mark.unit
@pytest.mark.parametrize("value", [None, "", "abc"])
def test_h03_window_default_86400(monkeypatch, value) -> None:
    if value is None:
        monkeypatch.delenv("DQ_FRESH_WINDOW_SECONDS", raising=False)
    else:
        monkeypatch.setenv("DQ_FRESH_WINDOW_SECONDS", value)
    assert get_fresh_window_seconds() == 86400


@pytest.mark.unit
def test_h04_health_a_only_with_yt(monkeypatch) -> None:
    monkeypatch.delenv("DQ_FRESH_WINDOW_SECONDS", raising=False)
    health, parts = ScoringPolicyV2.compute_health([ne("x")], [ye([], eid="r")])
    assert health == pytest.approx(0.7667, abs=1e-4)
    assert parts == {"diversity": pytest.approx(0.6), "recency": 1.0}


@pytest.mark.unit
def test_h05_trust_range() -> None:
    assert ScoringPolicyV2.compute_trust([ne("a", sc=1), ne("b", sc=1)])[0] == pytest.approx(0.6667,
                                                                                          abs=1e-4)
    assert ScoringPolicyV2.compute_trust([ne("a", sc=3)])[0] == pytest.approx(1.0)
    assert ScoringPolicyV2.compute_trust([])[0] == 1.0


@pytest.mark.unit
def test_h06_row67_hormuz_l1() -> None:
    title = "Oil prices fall 5% to 3-month low on hopes Strait of Hormuz will open"
    cands = [ne(title, "investing_com", "A", age_min=60), ne(title, "yahoo_finance", "B", age_min=60)]
    yt = [ye([], eid="r")]
    out = ScoringPolicyV2(make_layer()).evaluate(_inp(cands, yt, yt))
    assert out.level == "L1" and out.score == pytest.approx(10.0)
    assert out.health_score == pytest.approx(0.8833, abs=1e-4)


@pytest.mark.unit
def test_h07_today_log_scenario_l2() -> None:
    cands = [ne("Delta delays debut of Saudi Arabia service until March after Riyadh airport "
                "attacks", "reuters_markets"),
             ne("Starbucks says it is not buying Chipotle after takeover speculation",
                "reuters_markets"),
             ne("FAA warns U.S. airlines of safety risks over Saudi Arabia after attacks",
                "reuters_markets")]
    yt_raw = [ye([], eid=f"r{i}") for i in range(36)]
    out = ScoringPolicyV2(make_layer()).evaluate(_inp(cands, [], yt_raw))
    assert out.level == "L2"
    assert out.score == pytest.approx(7.2, abs=1e-3)
    assert out.health_score == pytest.approx(0.7667, abs=1e-4)


# ────────────────────────────────────────────────────────
# S: shadow 기록
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_s01_v1_active_shadow_v2(monkeypatch) -> None:
    monkeypatch.delenv("DETECTION_POLICY", raising=False)
    r = make_layer([ne("Fed signals rate cut", ks=3.0)]).detect()
    assert r.active_policy == "v1" and r.shadow["policy"] == "v2"
    assert set(r.shadow) == {"policy", "level", "score", "health", "news_score", "yt_bonus",
                             "trust", "top_news", "factors", "reason"}


@pytest.mark.unit
def test_s02_v2_active_shadow_v1_and_policy_version() -> None:
    cands = [ne("Fed signals rate cut")]
    r = make_layer([], [], candidates=cands, policy="v2").detect()
    assert r.active_policy == "v2" and r.shadow["policy"] == "v1"
    assert r.policy_version == "v2.0.0"
    assert r.reasoning_json["policy_version"] == "v2.0.0"
    assert r.news_events and r.news_events[0].matched_keywords == ["fed_actor", "rate_change"]


@pytest.mark.unit
def test_s03_shadow_error_recorded_v1_unchanged(monkeypatch) -> None:
    layer = make_layer([ne("Fed signals rate cut", ks=3.0)])

    def boom(_inp):
        raise RuntimeError("x")

    monkeypatch.setattr(layer._policies["v2"], "evaluate", boom)
    r = layer.detect()
    assert r.shadow == {"policy": "v2", "error": "RuntimeError: x"}
    assert r.level == "NONE" and r.score == 3.6


@pytest.mark.unit
def test_s03b_v2_active_failure_falls_back_to_v1(monkeypatch) -> None:
    layer = make_layer([ne("Fed signals rate cut", ks=3.0)], policy="v2")
    monkeypatch.setattr(layer._policies["v2"], "evaluate",
                        lambda _i: (_ for _ in ()).throw(ValueError("bad")))
    r = layer.detect()
    assert r.active_policy == "v1" and r.shadow["error"] == "ValueError: bad"
    assert any(w.startswith("detection_policy_fallback") for w in r.ops_warnings)


def _run_pipeline_with(result: MacroNewsResult, monkeypatch):
    import run_alert
    from core.run_recorder import RunRecorder

    monkeypatch.setattr(run_alert, "DRY_RUN", True)
    monkeypatch.setattr("detection.macro_news_layer.MacroNewsLayer.detect", lambda self: result)
    rec = RunRecorder(alert_store=None, dry_run=True)
    run_alert._run_pipeline(None, rec)
    return rec


def _result(level="NONE", shadow=None, active="v1") -> MacroNewsResult:
    return MacroNewsResult(score=0.0, level=level, news_events=[], youtube_events=[],
                           news_score=0.0, youtube_bonus=0.0, active_policy=active, shadow=shadow)


@pytest.mark.unit
def test_s04_level_none_path_records_shadow(monkeypatch) -> None:
    shadow = {"policy": "v2", "level": "L2", "score": 7.2, "health": None}
    rec = _run_pipeline_with(_result(shadow=shadow), monkeypatch)
    assert rec.row["outcome"] == "level_none"
    assert rec.row["detail"]["shadow"] == shadow
    assert rec.row["detail"]["detection_policy"] == "v1"


@pytest.mark.unit
def test_s05_shadow_json_serializable() -> None:
    out = PolicyOutcome(policy="v2", level="NONE", reason="r" * 300,
                        factors=[{"factor": "f", "weight": None, "obj": object()}] * 7,
                        score=1.23456, news_score=0.0, youtube_bonus=0.0, health_score=None,
                        news_events=[ne("t" * 200, kw=list("abcdefg"))] * 4, trust=None)
    s = outcome_to_shadow(out)
    text = json.dumps(s)
    assert '"health": null' in text
    assert len(s["top_news"]) == 3 and len(s["top_news"][0]["title"]) == 120
    assert len(s["top_news"][0]["kw"]) == 5 and len(s["factors"]) == 5
    assert len(s["reason"]) == 200 and s["score"] == 1.235


# ────────────────────────────────────────────────────────
# E: DETECTION_POLICY env
# ────────────────────────────────────────────────────────
@pytest.mark.unit
@pytest.mark.parametrize("value,expected", [(None, "v1"), ("", "v1"), (" V2 ", "v2"),
                                            ("v1", "v1"), ("v3", "v1")])
def test_e01_04_resolve(monkeypatch, caplog, value, expected) -> None:
    if value is None:
        monkeypatch.delenv("DETECTION_POLICY", raising=False)
    else:
        monkeypatch.setenv("DETECTION_POLICY", value)
    with caplog.at_level(logging.WARNING):
        assert resolve_detection_policy() == expected
    assert any("미지원" in m for m in caplog.messages) == (value == "v3")


@pytest.mark.unit
def test_e05_explicit_overrides_env(monkeypatch) -> None:
    monkeypatch.setenv("DETECTION_POLICY", "v1")
    layer = make_layer(policy="v2")
    assert layer.detection_policy == "v2"


@pytest.mark.unit
def test_e06_init_log_mentions_detection(monkeypatch, caplog) -> None:
    monkeypatch.setenv("DETECTION_POLICY", "v2")
    with caplog.at_level(logging.INFO):
        make_layer()
    assert any("detection=v2, shadow=v1" in m for m in caplog.messages)


# ────────────────────────────────────────────────────────
# R: 독립 리뷰 반영 회귀 (2026-10-13)
# ────────────────────────────────────────────────────────
# M5 오탐 8건 → v2 차단(< 3.0)
REVIEW_FALSE_POSITIVES = [
    "Airline stocks surge as airport traffic recovers",
    "Port workers strike, shipping crisis looms",
    "Pipeline company Kinder Morgan earnings beat",
    "Strikes hit UK rail network, travel chaos at airport",
    "Investors fed up as stocks plunge in crisis",
    "Netflix raises rates for subscribers",
    "Company keeps rates competitive",
    "Mortgage rates hold near 7%",
]


@pytest.mark.unit
@pytest.mark.parametrize("title", REVIEW_FALSE_POSITIVES)
def test_r01_review_false_positives_blocked(title) -> None:
    assert KeywordMatcherV2().match(title).score < keyword_rules.KEYWORD_THRESHOLD_V2


@pytest.mark.unit
def test_r02_credit_card_rates_weak_signal_non_anchor() -> None:
    """누락 1: 소비자 금리는 약신호(1.0, 비앵커)로 기록만 — 단독 통과 아님(설계 판단)."""
    r = KeywordMatcherV2().match("Credit card interest rates rise")
    assert r.rule_ids == ["consumer_rates"] and r.score == pytest.approx(1.0)
    assert r.topics == ["consumer_rates"]


@pytest.mark.unit
def test_r03_feds_raid_not_macro() -> None:
    """누락 2: 'Feds'(연방 수사기관) 은 매크로 아님 → 미매칭 유지(판단 기재)."""
    assert KeywordMatcherV2().match("Feds raid office").rule_ids == []


@pytest.mark.unit
@pytest.mark.parametrize("title,score,rules", [
    ("Fed raises rates by half point", 4.0, ["fed_actor", "rate_change_v"]),
    ("ECB holds rates steady", 4.0, ["fed_actor", "rate_change_v"]),
    ("BoE cuts rates", 4.0, ["fed_actor", "rate_change_v"]),
    ("Bank raises interest rates", 3.0, ["rate_change_vi"]),
    ("Fed cuts rates; mortgage rates fall", 5.0, ["fed_actor", "rate_change_v", "consumer_rates"]),
    ("Israel strikes Iranian refinery", 4.5, ["geo_attack_s", "geo_target"]),
    ("Apple strikes a deal with OpenAI", 0.0, []),
    ("Investors fed up with Fed delay", 1.0, ["fed_actor"]),
])
def test_r04_rule_dependencies(title, score, rules) -> None:
    r = KeywordMatcherV2().match(title)
    assert r.score == pytest.approx(score) and r.rule_ids == rules


@pytest.mark.unit
def test_r05_geo_target_alone_not_anchor() -> None:
    r = KeywordMatcherV2().match("Refinery outage lifts margins, shares surge")
    assert "geo_target" not in r.rule_ids
    assert r.score == pytest.approx(2.0 * keyword_rules.GENERIC_SOLO_FACTOR)


@pytest.mark.unit
def test_r06_registry_new_sources_v2_only() -> None:
    from config.settings import NEWS_SOURCE_REGISTRY as reg

    assert reg["S"]["fed_speeches"]["v2_only"] is True
    assert reg["B"]["marketwatch"]["v2_only"] is True
    assert not reg["S"]["fed_rss"].get("v2_only")
    assert not any(c.get("v2_only") for c in reg["A"].values())


@pytest.mark.unit
def test_r07_v2_only_source_excluded_from_v1_but_in_candidates() -> None:
    reg = {"A": {"a1": {"url": "u-a1"}}, "B": {"mw": {"url": "u-mw", "v2_only": True}}}
    title = "Stocks plunge as recession fears grow"
    feeds = {"u-a1": _Feed([_Entry(title, "a1", 1)]), "u-mw": _Feed([_Entry(title, "mw", 1)])}
    with patch("collectors.news_collector.feedparser.parse", side_effect=lambda u: feeds[u]):
        nc = NewsCollector(source_registry=reg)
        out = nc.collect()
    assert [e.source_name for e in out] == ["a1"] and out[0].source_count == 1
    assert [e.source_name for e in nc.last_raw_events] == ["a1"]
    assert {e.source_name for e in nc.last_candidates} == {"a1", "mw"}
    mw = [r for r in nc.last_source_results if r.name == "mw"][0]
    assert mw.ok is True and mw.in_window == 1


@pytest.mark.unit
def test_r08_v2_dq_source_level_v1_dq_legacy() -> None:
    """M3: v1 DQ=수집기 2키(정상), v2 shadow DQ=소스 단위(2/6 실패 → degraded)."""
    from collectors.news_collector import SourceResult

    names = ("fed_rss", "fed_speeches", "reuters_markets", "investing_com", "marketwatch")
    srs = [SourceResult(n, "A", 200 if n not in ("fed_speeches", "marketwatch") else 503,
                        1, 0, n not in ("fed_speeches", "marketwatch")) for n in names]
    fed = ne("Federal Reserve issues FOMC statement", "fed_rss", "S", 5.0, auto=True,
             kw=["auto_l1"])
    layer = make_layer([fed])
    layer.news_collector.last_source_results = srs
    r = layer.detect()
    assert r.dq_state.source_results == {"news_collector": True, "youtube_collector": True}
    assert r.dq_state.degraded_flag is False and r.level == "L1"
    assert r.shadow["level"] == "L2"  # v2: Tier S + 소스 단위 DQ degraded → L2 강등
    assert r.failed_sources == ["fed_speeches", "marketwatch"]


@pytest.mark.unit
def test_r09_snapshot_failure_isolated(monkeypatch, caplog) -> None:
    """m3: v2 후보 스냅샷 예외 → 빈 후보 + 경고, v1 결과 정상."""
    feeds = _feeds()
    with patch("collectors.news_collector.feedparser.parse", side_effect=lambda u: feeds[u]), \
            patch("collectors.news_collector.dataclasses.replace", side_effect=RuntimeError("boom")):
        nc = NewsCollector(source_registry=_REG)
        with caplog.at_level(logging.WARNING):
            out = nc.collect()
    assert nc.last_candidates == []
    assert out and nc.last_raw_events
    assert any("v2 후보 스냅샷 실패" in m for m in caplog.messages)


@pytest.mark.unit
def test_r10_layer_candidate_prep_failure_isolated(monkeypatch) -> None:
    layer = make_layer([ne("Fed signals rate cut", ks=3.0)])
    monkeypatch.setattr(layer, "_news_candidates", lambda _r: (_ for _ in ()).throw(ValueError("x")))
    r = layer.detect()
    assert r.level == "NONE" and r.score == 3.6
    assert r.shadow["policy"] == "v2" and r.shadow["health"] is None


@pytest.mark.unit
def test_r11_tg_failure_log_redacted(monkeypatch, caplog) -> None:
    """m1: 경고 발송 실패 로그에 봇 토큰 마스킹."""
    from types import SimpleNamespace

    import run_alert

    tg_instance = MagicMock()
    tg_instance.publish_internal.side_effect = RuntimeError(
        "https://api.telegram.org/bot123456:ABCdefSECRET/sendMessage"
    )
    monkeypatch.setattr("publishers.telegram_publisher.TelegramPublisher",
                        MagicMock(return_value=tg_instance))
    monkeypatch.setattr(run_alert, "DRY_RUN", False)
    store = MagicMock()
    store.is_cooldown_active.return_value = False
    result = SimpleNamespace(failed_sources=["marketwatch"], news_source_results=[],
                             dq_state=None, source_level_success_rate=0.833, level="NONE")
    with caplog.at_level(logging.WARNING):
        run_alert._notify_source_failures(store, result)
    text = " ".join(caplog.messages)
    assert "ABCdefSECRET" not in text and "발송 실패" in text


@pytest.mark.unit
def test_r12_warning_wording_and_news_collector_excluded(monkeypatch) -> None:
    """m2: 문구 '수집 소스 장애', news_collector 키는 경고 대상 제외."""
    from types import SimpleNamespace

    import run_alert

    tg_instance = MagicMock()
    monkeypatch.setattr("publishers.telegram_publisher.TelegramPublisher",
                        MagicMock(return_value=tg_instance))
    monkeypatch.setattr(run_alert, "DRY_RUN", False)
    store = MagicMock()
    store.is_cooldown_active.return_value = False
    base = {"news_source_results": [], "dq_state": None, "level": "NONE",
            "source_level_success_rate": 0.833}
    run_alert._notify_source_failures(store, SimpleNamespace(failed_sources=["news_collector"],
                                                             **base))
    tg_instance.publish_internal.assert_not_called()
    run_alert._notify_source_failures(
        store, SimpleNamespace(failed_sources=["youtube_collector"], **base)
    )
    text = tg_instance.publish_internal.call_args[0][0]
    assert "수집 소스 장애" in text and "success_rate=0.83" in text


@pytest.mark.unit
def test_r13_cross_validation_module_function_equivalent() -> None:
    """m4: 모듈 함수와 NewsCollector 메서드 결과 동일."""
    from collectors.news_collector import apply_cross_validation, compute_topic_hash

    a = [ne("Oil hormuz shock", "s1"), ne("Oil hormuz shock", "s2"), ne("Other", "s3")]
    b = [dataclasses_replace(e) for e in a]
    apply_cross_validation(a)
    NewsCollector()._apply_cross_validation(b)
    assert [(e.topic_hash, e.source_count) for e in a] == [(e.topic_hash, e.source_count) for e in b]
    assert compute_topic_hash("Fed announces emergency rate cut") == \
        NewsCollector()._compute_topic_hash("Fed announces emergency rate cut")
