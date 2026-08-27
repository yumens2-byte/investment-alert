"""
제목: BaseCollector 및 커스텀 예외 단위 테스트
내용: _retry_request, _validate_event, _now_utc, 예외 클래스 __init__/__str__을
      외부 의존성 없이 테스트합니다. Coverage 보완 목적.
"""

from __future__ import annotations

from datetime import UTC
from typing import Any
from unittest.mock import MagicMock

import pytest

from collectors.base import BaseCollector, CollectorEvent
from core.exceptions import (
    CollectorException,
    ConfigurationException,
    DetectionException,
    InvestmentAlertError,
    ValidationException,
)


# ────────────────────────────────────────────────────────
# 구체 Collector stub (추상 클래스 테스트 용)
# ────────────────────────────────────────────────────────
class _StubCollector(BaseCollector):
    """테스트용 최소 구현 Collector"""

    def collect(self) -> list[CollectorEvent]:
        return []


@pytest.fixture
def stub() -> _StubCollector:
    return _StubCollector(source_name="stub", timeout=5, max_retries=2, retry_delay=0.0)


def _make_valid_event() -> CollectorEvent:
    """검증을 통과하는 CollectorEvent"""
    from datetime import datetime
    return CollectorEvent(
        source_type="news",
        source_name="test",
        event_id="abc123",
        title="Valid title",
        summary="",
        url="https://example.com/article",
        published_at=datetime.now(UTC),
    )


# ────────────────────────────────────────────────────────
# 예외 클래스
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_investment_alert_error_message() -> None:
    """InvestmentAlertError 기본 메시지"""
    e = InvestmentAlertError("test error")
    assert str(e) == "test error"
    assert e.message == "test error"
    assert e.cause is None


@pytest.mark.unit
def test_investment_alert_error_with_cause() -> None:
    """InvestmentAlertError with cause → str에 원인 포함"""
    cause = ValueError("original")
    e = InvestmentAlertError("wrapper", cause=cause)
    assert "original" in str(e)


@pytest.mark.unit
def test_collector_exception_fields() -> None:
    """CollectorException 필드 초기화"""
    e = CollectorException("collect failed", source_name="fed_rss", retryable=False)
    assert e.source_name == "fed_rss"
    assert e.retryable is False


@pytest.mark.unit
def test_validation_exception_rule_field() -> None:
    """ValidationException rule 필드"""
    e = ValidationException("bad url", rule="required_url")
    assert e.rule == "required_url"


@pytest.mark.unit
def test_configuration_exception_config_key() -> None:
    """ConfigurationException config_key 필드"""
    e = ConfigurationException("missing env", config_key="YOUTUBE_CHANNELS")
    assert e.config_key == "YOUTUBE_CHANNELS"


@pytest.mark.unit
def test_detection_exception_stage_field() -> None:
    """DetectionException stage 필드"""
    e = DetectionException("score failed", stage="score_computation")
    assert e.stage == "score_computation"


# ────────────────────────────────────────────────────────
# _retry_request
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_retry_request_success_first_try(stub: _StubCollector) -> None:
    """첫 시도 성공 시 즉시 반환"""
    func = MagicMock(return_value="ok")
    result = stub._retry_request(func, "arg1")
    assert result == "ok"
    func.assert_called_once_with("arg1")


@pytest.mark.unit
def test_retry_request_succeeds_on_second_try(stub: _StubCollector) -> None:
    """첫 시도 실패, 두 번째 성공"""
    call_count = {"n": 0}

    def flaky(*args: Any) -> str:
        call_count["n"] += 1
        if call_count["n"] < 2:
            raise RuntimeError("temporary failure")
        return "ok"

    result = stub._retry_request(flaky)
    assert result == "ok"
    assert call_count["n"] == 2


@pytest.mark.unit
def test_retry_request_raises_after_max_retries(stub: _StubCollector) -> None:
    """max_retries 초과 시 CollectorException 발생"""
    always_fail = MagicMock(side_effect=RuntimeError("always fails"))
    with pytest.raises(CollectorException):
        stub._retry_request(always_fail)
    # max_retries=2 이므로 총 3회 호출
    assert always_fail.call_count == 3


@pytest.mark.unit
def test_retry_request_does_not_retry_validation_exception(stub: _StubCollector) -> None:
    """ValidationException은 즉시 재전파 (재시도 안 함)"""
    func = MagicMock(side_effect=ValidationException("bad data"))
    with pytest.raises(ValidationException):
        stub._retry_request(func)
    func.assert_called_once()


# ────────────────────────────────────────────────────────
# _validate_event
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_validate_event_valid_passes(stub: _StubCollector) -> None:
    """유효 이벤트는 예외 없음"""
    event = _make_valid_event()
    stub._validate_event(event)  # 예외 발생 없으면 OK


@pytest.mark.unit
def test_validate_event_empty_title_raises(stub: _StubCollector) -> None:
    """title 비어있으면 ValidationException"""
    event = _make_valid_event()
    event.title = ""
    with pytest.raises(ValidationException) as exc_info:
        stub._validate_event(event)
    assert exc_info.value.rule == "required_title"


@pytest.mark.unit
def test_validate_event_empty_url_raises(stub: _StubCollector) -> None:
    """url 비어있으면 ValidationException"""
    event = _make_valid_event()
    event.url = ""
    with pytest.raises(ValidationException) as exc_info:
        stub._validate_event(event)
    assert exc_info.value.rule == "required_url"


@pytest.mark.unit
def test_validate_event_invalid_datetime_raises(stub: _StubCollector) -> None:
    """published_at이 datetime이 아니면 ValidationException"""
    event = _make_valid_event()
    event.published_at = "2025-01-01"  # type: ignore[assignment]
    with pytest.raises(ValidationException) as exc_info:
        stub._validate_event(event)
    assert exc_info.value.rule == "datetime_type"


# ────────────────────────────────────────────────────────
# _now_utc
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_now_utc_is_timezone_aware(stub: _StubCollector) -> None:
    """_now_utc는 UTC timezone-aware datetime 반환"""
    from datetime import datetime
    dt = stub._now_utc()
    assert isinstance(dt, datetime)
    assert dt.tzinfo is not None


# ────────────────────────────────────────────────────────
# CollectorEvent
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_collector_event_effective_score_ai_priority() -> None:
    """ai_score가 있으면 effective_score = ai_score"""
    from datetime import datetime
    event = CollectorEvent(
        source_type="news", source_name="x", event_id="y",
        title="t", summary="", url="https://x.com",
        published_at=datetime.now(UTC),
        keyword_score=2.0, ai_score=7.5,
    )
    assert event.effective_score == 7.5


@pytest.mark.unit
def test_collector_event_effective_score_fallback() -> None:
    """ai_score 없으면 effective_score = keyword_score"""
    from datetime import datetime
    event = CollectorEvent(
        source_type="news", source_name="x", event_id="y",
        title="t", summary="", url="https://x.com",
        published_at=datetime.now(UTC),
        keyword_score=3.5, ai_score=None,
    )
    assert event.effective_score == 3.5
