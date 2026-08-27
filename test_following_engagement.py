from __future__ import annotations

from dataclasses import replace

from requests_oauthlib import OAuth1

from following_engagement.clients import OpenAiAnalyzer, XTimelineClient
from following_engagement.config import FollowingConfig
from following_engagement.execution import (
    DryRunActionExecutor,
    ExecutionModeRouter,
    LiveActionExecutor,
    LiveSafetyGuard,
    ShadowActionExecutor,
)
from following_engagement.models import (
    ActionCandidate,
    ActionStatus,
    ActionType,
    Analysis,
    ExecutionMode,
    TimelinePost,
)
from following_engagement.pipeline import DecisionEngine, PostPreFilter
from following_engagement.service import FollowingService
from following_engagement.state import StateRepository


class SpyWriter:
    def __init__(self) -> None:
        self.calls = 0

    def write(self, action: object, text: str, post_id: str) -> str:
        self.calls += 1
        return "written-id"


def candidate() -> ActionCandidate:
    return ActionCandidate(
        "123",
        "author-1",
        "analyst",
        "A useful market analysis " * 2,
        ActionType.QUOTE,
        95,
        90,
        88,
        "A distinct generated insight",
    )


def test_execution_mode_is_fail_safe(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "unexpected")
    assert FollowingConfig.from_env().execution_mode == ExecutionMode.DRY_RUN


def test_config_reuses_existing_x_credential_key_names(monkeypatch):
    expected = {
        "X_API_KEY": "api-key",
        "X_API_SECRET": "api-secret",
        "X_ACCESS_TOKEN": "access-token",
        "X_ACCESS_TOKEN_SECRET": "access-secret",
    }
    for name, value in expected.items():
        monkeypatch.setenv(name, value)
    config = FollowingConfig.from_env()
    assert config.x_api_key == expected["X_API_KEY"]
    assert config.x_api_secret == expected["X_API_SECRET"]
    assert config.access_token == expected["X_ACCESS_TOKEN"]
    assert config.access_token_secret == expected["X_ACCESS_TOKEN_SECRET"]


def test_dry_run_and_shadow_never_call_writer(tmp_path):
    state = StateRepository(tmp_path / "state.db")
    writer = SpyWriter()
    live_config = replace(FollowingConfig(), execution_mode=ExecutionMode.LIVE)
    live = LiveActionExecutor(state, LiveSafetyGuard(live_config, state), writer)

    dry_router = ExecutionModeRouter(
        ExecutionMode.DRY_RUN, DryRunActionExecutor(), ShadowActionExecutor(state), live
    )
    assert dry_router.execute(candidate()).status == ActionStatus.DRY_RUN_COMPLETED
    assert writer.calls == 0

    shadow_router = ExecutionModeRouter(
        ExecutionMode.SHADOW, DryRunActionExecutor(), ShadowActionExecutor(state), live
    )
    assert shadow_router.execute(candidate()).status == ActionStatus.SHADOW_COMPLETED
    assert writer.calls == 0
    assert state.has_post("123")


def test_live_guard_and_success(tmp_path):
    state = StateRepository(tmp_path / "state.db")
    writer = SpyWriter()
    not_live = LiveActionExecutor(state, LiveSafetyGuard(FollowingConfig(), state), writer)
    assert not_live.execute(candidate()).status == ActionStatus.SKIPPED_POLICY
    assert writer.calls == 0

    state = StateRepository(tmp_path / "live-state.db")
    live_config = replace(FollowingConfig(), execution_mode=ExecutionMode.LIVE)
    result = LiveActionExecutor(state, LiveSafetyGuard(live_config, state), writer).execute(
        candidate()
    )
    assert result.status == ActionStatus.EXECUTED
    assert result.actual_x_post_id == "written-id"
    assert writer.calls == 1


def test_pre_filter_rules(tmp_path):
    state = StateRepository(tmp_path / "state.db")
    config = replace(FollowingConfig(), user_id="me", min_text_length=10)
    prefilter = PostPreFilter(config, state)
    assert prefilter.reason(TimelinePost("1", "me", "self", "AI market analysis")) == "OWN_POST"
    assert prefilter.reason(TimelinePost("2", "a", "blocked", "tiny")) == "TEXT_TOO_SHORT"
    assert (
        prefilter.reason(TimelinePost("3", "a", "shop", "AI market giveaway now"))
        == "EXCLUDED_OR_PROMOTIONAL"
    )
    assert prefilter.reason(TimelinePost("4", "a", "user", "AI market analysis")) is None


def test_decision_threshold_limit_and_similarity(tmp_path):
    state = StateRepository(tmp_path / "state.db")
    config = replace(FollowingConfig(), max_actions_per_run=1)
    engine = DecisionEngine(config, state)
    post = TimelinePost("1", "a", "analyst", "AI market analysis " * 3)
    low = Analysis(True, "AI", 20, 50, 90, 90, "", ActionType.QUOTE, "", "text")
    assert engine.decide(post, low)[1] == "LOW_RELEVANCE"
    high = replace(low, relevance_score=95, generated_text="valuable generated analysis")
    assert engine.decide(post, high)[0] is not None
    assert engine.decide(replace(post, post_id="2"), high)[1] == "PER_RUN_LIMIT"


def test_decision_rejects_ai_not_relevant(tmp_path):
    state = StateRepository(tmp_path / "state.db")
    engine = DecisionEngine(FollowingConfig(), state)
    post = TimelinePost("1", "a", "analyst", "AI market analysis " * 3)
    analysis = Analysis(False, "OTHER", 99, 99, 99, 99, "", ActionType.QUOTE, "", "text")
    assert engine.decide(post, analysis) == (None, "NOT_RELEVANT")


def test_openai_structured_json_parser_clamps_scores():
    result = OpenAiAnalyzer.parse(
        '{"relevant":true,"category":"AI","relevanceScore":120,'
        '"importanceScore":80,"engagementValue":75,"contentValue":-2,"summary":"s",'
        '"recommendedAction":"QUOTE","reason":"r","generatedText":"g"}'
    )
    assert result.relevance_score == 100
    assert result.content_value == 0


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self):
        self.headers = {}
        self.calls = []

    def get(self, url, params, timeout):
        self.calls.append(params.copy())
        number = len(self.calls)
        return FakeResponse(
            {
                "data": [{"id": str(number), "author_id": "a", "text": "AI market " * 5}],
                "includes": {"users": [{"id": "a", "username": "u"}]},
                "meta": {"next_token": "next"} if number == 1 else {},
            }
        )


def test_timeline_since_id_and_pagination():
    session = FakeSession()
    posts = XTimelineClient("key", "secret", "token", "token-secret", session).fetch(
        "me", "99", 300
    )
    assert [post.post_id for post in posts] == ["1", "2"]
    assert session.calls[0]["since_id"] == "99"
    assert session.calls[1]["pagination_token"] == "next"
    assert isinstance(session.auth, OAuth1)


class StubTimeline:
    def __init__(self, posts):
        self.posts = posts

    def fetch(self, user_id, since_id, maximum):
        return self.posts


class StubAnalyzer:
    def __init__(self, fail=False):
        self.fail = fail

    def analyze(self, post):
        if self.fail:
            raise ValueError("invalid structured response")
        return Analysis(
            True,
            "AI",
            95,
            90,
            88,
            90,
            "summary",
            ActionType.QUOTE,
            "high-value analysis",
            "A generated market insight",
        )


class LowScoreAnalyzer:
    def analyze(self, post):
        return Analysis(
            True, "AI", 10, 10, 10, 10, "summary", ActionType.QUOTE, "low score", "draft"
        )


def _service(tmp_path, mode, analyzer=None):
    state = StateRepository(tmp_path / f"{mode.value}.db")
    config = replace(
        FollowingConfig(),
        execution_mode=mode,
        user_id="me",
        min_text_length=10,
        include_topics=("market",),
    )
    writer = SpyWriter()
    live_config = replace(config, execution_mode=ExecutionMode.LIVE)
    router = ExecutionModeRouter(
        mode,
        DryRunActionExecutor(),
        ShadowActionExecutor(state),
        LiveActionExecutor(state, LiveSafetyGuard(live_config, state), writer),
    )
    post = TimelinePost("100", "author", "analyst", "A detailed market infrastructure view")
    service = FollowingService(
        config,
        StubTimeline([post]),
        analyzer or StubAnalyzer(),
        state,
        router,
    )
    return service, state, writer


def test_dry_run_pipeline_writes_zero_and_updates_checkpoint(tmp_path):
    service, state, writer = _service(tmp_path, ExecutionMode.DRY_RUN)
    summary = service.run()
    assert summary.candidates == 1
    assert summary.would_execute == 1
    assert summary.actual_writes == 0
    assert writer.calls == 0
    assert state.checkpoint() == "100"
    assert not state.has_post("100")


def test_shadow_pipeline_writes_zero_and_persists_audit(tmp_path):
    service, state, writer = _service(tmp_path, ExecutionMode.SHADOW)
    summary = service.run()
    assert summary.would_execute == 1
    assert summary.actual_writes == 0
    assert writer.calls == 0
    assert state.has_post("100")
    assert state.audit_count(would_execute=True) == 1


def test_shadow_pipeline_persists_skipped_decision(tmp_path):
    service, state, writer = _service(tmp_path, ExecutionMode.SHADOW, LowScoreAnalyzer())
    summary = service.run()
    assert summary.candidates == 0
    assert writer.calls == 0
    assert state.audit_count(would_execute=False) == 1


def test_analysis_failure_does_not_advance_checkpoint(tmp_path):
    service, state, writer = _service(tmp_path, ExecutionMode.DRY_RUN, StubAnalyzer(fail=True))
    summary = service.run()
    assert summary.errors == 1
    assert summary.actual_writes == 0
    assert writer.calls == 0
    assert state.checkpoint() is None
