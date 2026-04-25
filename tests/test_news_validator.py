"""
제목: NewsValidator 단위 테스트
내용: False Positive 제거 로직의 각 검증 규칙을 독립적으로 테스트합니다.
      외부 API 호출 없는 순수 단위 테스트입니다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from collectors.base import CollectorEvent
from validators.news_validator import NewsValidator


# ────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────
@pytest.fixture
def validator() -> NewsValidator:
    """기본 NewsValidator 인스턴스 (24시간 윈도우)"""
    return NewsValidator(window_hours=24)


def make_event(
    title: str = "Market plunge on crisis",
    summary: str = "",
    url: str = "https://reuters.com/article/1",
    source_name: str = "reuters_markets",
    tier: str = "A",
    auto_l1: bool = False,
    published_at: datetime | None = None,
) -> CollectorEvent:
    """
    테스트용 CollectorEvent 생성 헬퍼.
    published_at 기본값: 1시간 전 (24h 범위 내)
    """
    if published_at is None:
        published_at = datetime.now(UTC) - timedelta(hours=1)

    return CollectorEvent(
        source_type="news",
        source_name=source_name,
        event_id=CollectorEvent.compute_event_id(source_name, url, title),
        title=title,
        summary=summary,
        url=url,
        published_at=published_at,
        tier=tier,
        auto_l1=auto_l1,
    )


# ────────────────────────────────────────────────────────
# 테스트: auto_l1 우회
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_auto_l1_bypasses_all_validation(validator: NewsValidator) -> None:
    """
    제목: Tier S auto_l1은 모든 검증 우회
    내용: 추측성 표현이 있어도 auto_l1이면 통과해야 한다
    """
    event = make_event(
        title="Market could crash according to analyst says",
        auto_l1=True,
        url="",  # URL이 비어도
        published_at=datetime.now(UTC) - timedelta(hours=30),  # 24h 초과도
    )
    assert validator.validate(event) is True


# ────────────────────────────────────────────────────────
# 테스트: URL 유효성
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_invalid_url_empty_excluded(validator: NewsValidator) -> None:
    """URL이 빈 문자열이면 제외"""
    event = make_event(url="")
    assert validator.validate(event) is False


@pytest.mark.unit
def test_invalid_url_no_scheme_excluded(validator: NewsValidator) -> None:
    """http/https가 없는 URL은 제외"""
    event = make_event(url="reuters.com/article/1")
    assert validator.validate(event) is False


@pytest.mark.unit
def test_valid_url_https_passed(validator: NewsValidator) -> None:
    """https URL은 통과"""
    event = make_event(url="https://reuters.com/article/1")
    assert validator.validate(event) is True


# ────────────────────────────────────────────────────────
# 테스트: 시간 범위
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_within_24h_passed(validator: NewsValidator) -> None:
    """1시간 전 발행 기사는 통과"""
    event = make_event(published_at=datetime.now(UTC) - timedelta(hours=1))
    assert validator.validate(event) is True


@pytest.mark.unit
def test_over_24h_excluded(validator: NewsValidator) -> None:
    """25시간 전 기사는 제외"""
    event = make_event(published_at=datetime.now(UTC) - timedelta(hours=25))
    assert validator.validate(event) is False


@pytest.mark.unit
def test_boundary_exactly_24h_excluded(validator: NewsValidator) -> None:
    """정확히 24시간 전은 제외 경계"""
    event = make_event(published_at=datetime.now(UTC) - timedelta(hours=24, seconds=1))
    assert validator.validate(event) is False


# ────────────────────────────────────────────────────────
# 테스트: 추측성 표현
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_speculative_could_excluded(validator: NewsValidator) -> None:
    """'could' 포함 제목은 제외"""
    event = make_event(title="Market could crash next week")
    assert validator.validate(event) is False


@pytest.mark.unit
def test_speculative_analyst_says_excluded(validator: NewsValidator) -> None:
    """'analyst says' 포함 제목은 제외"""
    event = make_event(title="Analyst says market is overvalued")
    assert validator.validate(event) is False


@pytest.mark.unit
def test_speculative_pattern_in_summary_excluded(validator: NewsValidator) -> None:
    """요약에 'might' 포함 시 제외"""
    event = make_event(
        title="Market update",
        summary="The market might face headwinds according to reports",
    )
    assert validator.validate(event) is False


# ────────────────────────────────────────────────────────
# 테스트: validate_all 배치 처리
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_validate_all_filters_invalid(validator: NewsValidator) -> None:
    """배치 검증: 유효 1건 + 무효 2건 → 유효 1건만 반환"""
    valid_event = make_event(title="Market plunge on crisis")
    stale_event = make_event(published_at=datetime.now(UTC) - timedelta(hours=30))
    speculative_event = make_event(title="Analyst says stocks could rise")

    result = validator.validate_all([valid_event, stale_event, speculative_event])

    assert len(result) == 1
    assert result[0].title == "Market plunge on crisis"


@pytest.mark.unit
def test_validate_all_empty_input(validator: NewsValidator) -> None:
    """빈 입력 → 빈 리스트 반환"""
    result = validator.validate_all([])
    assert result == []
