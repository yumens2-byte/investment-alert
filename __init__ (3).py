"""
제목: AlertEngine 단위 테스트
내용: MacroNewsResult → AlertSignal 변환, 쿨다운 로직, 발행 정책 적용을
      Mock 기반으로 테스트합니다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from collectors.base import CollectorEvent
from detection.alert_engine import PUBLISH_POLICY, AlertEngine
from detection.macro_news_layer import MacroNewsResult


def make_result(
    level: str = "L2",
    score: float = 5.5,
    reasoning: str = "L2: score=5.5",
    health_score: float = 0.85,
    news_count: int = 1,
    yt_count: int = 0,
) -> MacroNewsResult:
    def make_event(title: str, src: str) -> CollectorEvent:
        return CollectorEvent(
            source_type="news",
            source_name=src,
            event_id="abc",
            title=title,
            summary="",
            url="https://x.com",
            published_at=datetime.now(UTC),
        )

    news = [make_event(f"News {i}", "reuters") for i in range(news_count)]
    yt = [make_event(f"YT {i}", "소수몽키") for i in range(yt_count)]

    return MacroNewsResult(
        score=score,
        level=level,  # type: ignore[arg-type]
        news_events=news,
        youtube_events=yt,
        news_score=score,
        youtube_bonus=0.0,
        top_news=news[:3],
        top_youtube=yt[:3],
        reasoning=reasoning,
        health_score=health_score,
    )


@pytest.fixture
def engine_no_store() -> AlertEngine:
    return AlertEngine(alert_store=None)


@pytest.fixture
def mock_store() -> MagicMock:
    store = MagicMock()
    store.is_cooldown_active.return_value = False
    store.save_alert.return_value = True
    store.set_cooldown.return_value = True
    return store


@pytest.fixture
def engine_with_store(mock_store: MagicMock) -> AlertEngine:
    return AlertEngine(alert_store=mock_store)


# ── alert_id 생성 ─────────────────────────────────────
@pytest.mark.unit
def test_process_generates_uuid_alert_id(engine_no_store: AlertEngine) -> None:
    """alert_id는 UUID4 형식 (36자, 하이픈 포함)"""
    signal = engine_no_store.process(make_result())
    assert len(signal.alert_id) == 36
    assert signal.alert_id.count("-") == 4


# ── 발행 정책 적용 ────────────────────────────────────
@pytest.mark.unit
def test_l1_all_channels_published(engine_no_store: AlertEngine) -> None:
    """L1: X + TG Free + TG Paid 모두 True"""
    signal = engine_no_store.process(make_result(level="L1", score=8.0))
    assert signal.publish_x is True
    assert signal.publish_tg_free is True
    assert signal.publish_tg_paid is True


@pytest.mark.unit
def test_l2_tg_only_published(engine_no_store: AlertEngine) -> None:
    """L2: X=False, TG Free=True, TG Paid=True"""
    signal = engine_no_store.process(make_result(level="L2", score=5.5))
    assert signal.publish_x is False
    assert signal.publish_tg_free is True
    assert signal.publish_tg_paid is True


@pytest.mark.unit
def test_l3_no_publish(engine_no_store: AlertEngine) -> None:
    """L3: 모든 채널 False (로그만)"""
    signal = engine_no_store.process(make_result(level="L3", score=3.5))
    assert signal.publish_x is False
    assert signal.publish_tg_free is False
    assert signal.publish_tg_paid is False


@pytest.mark.unit
def test_none_no_publish(engine_no_store: AlertEngine) -> None:
    """NONE: 모든 채널 False"""
    signal = engine_no_store.process(make_result(level="NONE", score=1.0))
    assert signal.should_publish is False


# ── 쿨다운 로직 ───────────────────────────────────────
@pytest.mark.unit
def test_cooldown_active_blocks_publish(mock_store: MagicMock, engine_with_store: AlertEngine) -> None:
    """쿨다운 활성이면 모든 채널 False"""
    mock_store.is_cooldown_active.return_value = True
    signal = engine_with_store.process(make_result(level="L2"))
    assert signal.is_cooldown_active is True
    assert signal.should_publish is False
    assert signal.publish_tg_free is False


@pytest.mark.unit
def test_cooldown_inactive_allows_publish(mock_store: MagicMock, engine_with_store: AlertEngine) -> None:
    """쿨다운 비활성이면 정책대로 발행"""
    mock_store.is_cooldown_active.return_value = False
    signal = engine_with_store.process(make_result(level="L2"))
    assert signal.is_cooldown_active is False
    assert signal.publish_tg_free is True


@pytest.mark.unit
def test_cooldown_check_skipped_for_none(mock_store: MagicMock, engine_with_store: AlertEngine) -> None:
    """NONE 레벨은 쿨다운 조회 안 함"""
    engine_with_store.process(make_result(level="NONE"))
    mock_store.is_cooldown_active.assert_not_called()


@pytest.mark.unit
def test_cooldown_failure_allows_publish(mock_store: MagicMock, engine_with_store: AlertEngine) -> None:
    """쿨다운 조회 실패 시 발행 허용 (보수적 처리)"""
    mock_store.is_cooldown_active.side_effect = RuntimeError("DB error")
    signal = engine_with_store.process(make_result(level="L2"))
    assert signal.publish_tg_free is True


# ── 감사로그 저장 ─────────────────────────────────────
@pytest.mark.unit
def test_save_alert_called_for_non_none(mock_store: MagicMock, engine_with_store: AlertEngine) -> None:
    """NONE 아닌 레벨에서 save_alert 호출"""
    engine_with_store.process(make_result(level="L2"))
    mock_store.save_alert.assert_called_once()


@pytest.mark.unit
def test_save_alert_not_called_for_none(mock_store: MagicMock, engine_with_store: AlertEngine) -> None:
    """NONE 레벨에서 save_alert 미호출"""
    engine_with_store.process(make_result(level="NONE"))
    mock_store.save_alert.assert_not_called()


# ── AlertSignal 속성 ──────────────────────────────────
@pytest.mark.unit
def test_signal_fields_populated(engine_no_store: AlertEngine) -> None:
    """AlertSignal 필드가 올바르게 채워짐"""
    result = make_result(level="L2", score=5.5, news_count=2)
    signal = engine_no_store.process(result)
    assert signal.level == "L2"
    assert abs(signal.score - 5.5) < 0.01
    assert len(signal.top_news_titles) == 2
    assert isinstance(signal.created_at, datetime)


@pytest.mark.unit
def test_signal_should_publish_l2(engine_no_store: AlertEngine) -> None:
    """L2 should_publish = True (쿨다운 없음)"""
    signal = engine_no_store.process(make_result(level="L2"))
    assert signal.should_publish is True


@pytest.mark.unit
def test_publish_policy_completeness() -> None:
    """PUBLISH_POLICY가 모든 레벨을 포함"""
    for level in ("L1", "L2", "L3", "NONE"):
        assert level in PUBLISH_POLICY
        for ch in ("x", "tg_free", "tg_paid"):
            assert ch in PUBLISH_POLICY[level]
