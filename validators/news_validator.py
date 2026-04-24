"""
제목: 뉴스 이벤트 유효성 검증 모듈
내용: 수집된 뉴스 이벤트에서 False Positive를 제거합니다.
      추측성 기사, 과거 재탕 기사, URL 누락 이벤트를 필터링하여
      MacroNewsLayer의 점수 오염을 방지합니다.

주요 클래스:
  - NewsValidator: 단일 이벤트 또는 배치 검증 수행

주요 함수:
  - NewsValidator.validate(event): 단일 이벤트 검증, True=통과 False=제외
  - NewsValidator.validate_all(events): 배치 검증, 통과 이벤트만 반환
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from collectors.base import CollectorEvent
from core.logger import get_logger

VERSION = "1.0.0"

logger = get_logger(__name__)


class NewsValidator:
    """
    제목: 뉴스 이벤트 유효성 검증기
    내용: 3인 전문가 협의 설계서의 False Positive 최소화 전략을 구현합니다.
          추측성·재탕 기사를 제외하여 Alert 정확도를 높입니다.

    책임:
      - 추측성 표현 감지 (could/may/analyst says 등)
      - 24시간 초과 기사 제거 (과거 재탕 방지)
      - URL 유효성 검증
      - Tier S auto_l1 소스는 모든 검증 우회 (무조건 통과)
    """

    # ────────────────────────────────────────────────────
    # 추측성 표현 패턴 (소문자 포함 검사)
    # ────────────────────────────────────────────────────
    SPECULATIVE_PATTERNS: list[str] = [
        "analyst says",
        "could",
        "may signal",
        "might",
        "opinion:",
        "commentary:",
        "according to analysts",
        "sources say",
        "reportedly",
    ]

    # ────────────────────────────────────────────────────
    # 재탕 기사 시간 패턴 (과거형 날짜 언급)
    # ────────────────────────────────────────────────────
    STALE_TITLE_PATTERNS: list[str] = [
        "last week",
        "last month",
        "earlier this year",
        "recap:",
        "in review:",
    ]

    def __init__(self, window_hours: int = 24) -> None:
        """
        제목: NewsValidator 초기화

        Args:
            window_hours: 허용할 기사 발행 시간 범위 (기본: 24시간)
        """
        self.window_hours = window_hours
        logger.info(f"[NewsValidator] v{VERSION} 초기화 (window={window_hours}h)")

    def validate(self, event: CollectorEvent) -> bool:
        """
        제목: 단일 이벤트 검증
        내용: 아래 순서로 검증하며 하나라도 실패하면 False 반환.
              Tier S auto_l1 소스는 전체 검증 우회.

        처리 플로우:
          1. auto_l1 플래그 확인 → True이면 즉시 통과
          2. URL 비어있음 여부 검사
          3. 발행 시각이 window_hours 이내인지 검사
          4. 추측성 표현 포함 여부 검사
          5. 재탕 패턴 포함 여부 검사

        Args:
            event: 검증할 CollectorEvent

        Returns:
            bool: True=통과, False=제외
        """
        # 제목: Tier S auto_l1 우회
        # 내용: 연준/백악관 공식 채널은 무조건 신뢰
        if event.auto_l1:
            return True

        # 제목: URL 유효성 검증
        # 내용: URL이 비어있거나 http로 시작하지 않으면 제외
        if not self._is_valid_url(event.url):
            logger.debug(
                f"[NewsValidator] URL 불량 제외: source={event.source_name}, "
                f"url='{event.url[:40]}'"
            )
            return False

        # 제목: 발행 시각 범위 검증
        # 내용: 24시간 초과 기사는 재탕 위험
        if not self._is_within_window(event.published_at):
            logger.debug(
                f"[NewsValidator] 기간 초과 제외: source={event.source_name}, "
                f"published={event.published_at}"
            )
            return False

        # 제목: 추측성 표현 검사
        # 내용: 제목 + 요약의 소문자 텍스트에서 패턴 탐색
        if self._has_speculative_content(event.title, event.summary):
            logger.debug(
                f"[NewsValidator] 추측성 제외: source={event.source_name}, "
                f"title='{event.title[:40]}'"
            )
            return False

        # 제목: 재탕 패턴 검사
        if self._has_stale_pattern(event.title):
            logger.debug(
                f"[NewsValidator] 재탕 제외: source={event.source_name}, "
                f"title='{event.title[:40]}'"
            )
            return False

        return True

    def validate_all(self, events: list[CollectorEvent]) -> list[CollectorEvent]:
        """
        제목: 배치 이벤트 검증
        내용: 입력 이벤트 리스트에서 validate를 통과한 것만 반환합니다.
              제외된 건수를 INFO 레벨로 로깅합니다.

        Args:
            events: 검증할 CollectorEvent 리스트

        Returns:
            list[CollectorEvent]: 유효한 이벤트만 포함한 리스트
        """
        if not events:
            return []

        passed = [e for e in events if self.validate(e)]
        excluded = len(events) - len(passed)

        logger.info(
            f"[NewsValidator] 검증 완료: "
            f"입력={len(events)}, 통과={len(passed)}, 제외={excluded}"
        )
        return passed

    # ────────────────────────────────────────────────────
    # 내부 검증 헬퍼
    # ────────────────────────────────────────────────────
    def _is_valid_url(self, url: str) -> bool:
        """
        제목: URL 형식 검증
        내용: http 또는 https로 시작하고 비어있지 않아야 함.

        Args:
            url: 검증할 URL 문자열

        Returns:
            bool: 유효하면 True
        """
        if not url or not url.strip():
            return False
        stripped = url.strip().lower()
        return stripped.startswith("http://") or stripped.startswith("https://")

    def _is_within_window(self, published_at: datetime) -> bool:
        """
        제목: 발행 시각 범위 검증
        내용: published_at이 window_hours 이내인지 확인합니다.
              timezone-aware/naive 모두 처리합니다.

        Args:
            published_at: 이벤트 발행 시각

        Returns:
            bool: window_hours 이내이면 True
        """
        now = datetime.now(UTC)

        # 제목: timezone-naive 처리
        # 내용: naive datetime은 UTC로 간주하여 비교
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=UTC)

        cutoff = now - timedelta(hours=self.window_hours)
        return published_at >= cutoff

    def _has_speculative_content(self, title: str, summary: str) -> bool:
        """
        제목: 추측성 표현 탐지
        내용: 제목 + 요약의 소문자 텍스트에서 SPECULATIVE_PATTERNS를 탐색합니다.

        Args:
            title: 기사 제목
            summary: 기사 요약

        Returns:
            bool: 추측성 표현이 있으면 True (→ 제외 대상)
        """
        combined = f"{title} {summary}".lower()
        return any(pattern in combined for pattern in self.SPECULATIVE_PATTERNS)

    def _has_stale_pattern(self, title: str) -> bool:
        """
        제목: 재탕 기사 패턴 탐지
        내용: 제목에서 과거 시점을 언급하는 패턴을 탐색합니다.

        Args:
            title: 기사 제목

        Returns:
            bool: 재탕 패턴이 있으면 True (→ 제외 대상)
        """
        title_lower = title.lower()
        return any(pattern in title_lower for pattern in self.STALE_TITLE_PATTERNS)
