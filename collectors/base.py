"""
제목: 모든 Collector의 추상 기반 클래스 및 공통 이벤트 모델
내용: RSS/API로부터 데이터를 수집하는 모든 Collector의 공통 인터페이스를 정의하고,
      재시도 로직, 이벤트 유효성 검증, 표준 데이터 구조(CollectorEvent)를 제공합니다.

주요 클래스:
  - CollectorEvent: 모든 Collector가 반환하는 공통 이벤트 데이터 클래스
  - BaseCollector: 구체 Collector(NewsCollector, YouTubeCollector)가 상속하는 추상 클래스

주요 함수:
  - BaseCollector.collect(): 하위 클래스에서 구현할 추상 메서드
  - BaseCollector._retry_request(): 지수 백오프 재시도 로직
  - BaseCollector._validate_event(): 이벤트 필수 필드 검증
  - CollectorEvent.compute_event_id(): 해시 기반 중복 제거 ID 생성
"""

from __future__ import annotations

import hashlib
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from core.exceptions import CollectorException, ValidationException
from core.logger import get_logger

VERSION = "1.0.0"

logger = get_logger(__name__)


# ────────────────────────────────────────────────────────
# 공통 이벤트 데이터 클래스
# ────────────────────────────────────────────────────────
@dataclass
class CollectorEvent:
    """
    제목: 모든 Collector가 반환하는 공통 이벤트 구조
    내용: 뉴스(News), 유튜브(YouTube) 등 서로 다른 소스에서 수집한 이벤트를
          동일한 데이터 구조로 표현하여 상위 레이어(MacroNewsLayer)에서
          일관되게 처리할 수 있도록 합니다.

    책임:
      - 공통 식별 필드 제공(source_type, source_name, event_id)
      - 콘텐츠 필드 제공(title, summary, url, published_at)
      - 필터링 결과 보관(keyword_score, matched_keywords)
      - AI 분석 결과 보관(ai_score, ai_reasoning)
      - 교차검증 메타 보관(source_count, topic_hash, channel_weight, tier)
    """

    # ── 공통 식별 ─────────────────────────────────────
    source_type: str  # 'news' | 'youtube'
    source_name: str  # 'fed_rss', '소수몽키' 등
    event_id: str  # 중복 제거용 고유 ID (hash 또는 video_id)

    # ── 콘텐츠 ────────────────────────────────────────
    title: str
    summary: str
    url: str
    published_at: datetime  # UTC timezone-aware

    # ── 수집원 메타 ───────────────────────────────────
    tier: str | None = None  # 'S'|'A'|'B' (news only), youtube는 None
    channel_weight: float = 1.0  # youtube 채널 가중치 (news는 1.0)
    auto_l1: bool = False  # Tier S auto_l1 소스 여부

    # ── 1차 필터링 결과 ───────────────────────────────
    keyword_score: float = 0.0
    matched_keywords: list[str] = field(default_factory=list)

    # ── AI 분석 결과 ──────────────────────────────────
    ai_score: float | None = None
    ai_reasoning: str | None = None

    # ── 교차검증 ──────────────────────────────────────
    source_count: int = 1  # 동일 주제 다른 소스 수
    topic_hash: str | None = None

    @staticmethod
    def compute_event_id(source_name: str, url: str, title: str) -> str:
        """
        제목: 이벤트 고유 ID 생성
        내용: 소스명 + URL + 제목을 결합한 SHA256 해시의 앞 16자리 반환.
              URL이 비어있거나 변경될 가능성을 고려해 제목도 포함.

        Args:
            source_name: 소스 식별자
            url: 이벤트 URL
            title: 이벤트 제목

        Returns:
            str: 16자리 해시 ID
        """
        raw = f"{source_name}|{url}|{title}".encode()
        return hashlib.sha256(raw).hexdigest()[:16]

    @property
    def effective_score(self) -> float:
        """
        제목: 실효 점수 반환
        내용: AI 점수가 있으면 AI 점수를, 없으면 키워드 점수를 반환.
              상위 레이어(MacroNewsLayer)의 점수 계산에서 공통 진입점.

        Returns:
            float: AI 점수 우선, fallback으로 키워드 점수
        """
        return self.ai_score if self.ai_score is not None else self.keyword_score


# ────────────────────────────────────────────────────────
# 추상 Collector 베이스
# ────────────────────────────────────────────────────────
class BaseCollector(ABC):
    """
    제목: 모든 Collector의 추상 기반 클래스
    내용: 데이터 수집의 공통 인터페이스를 정의하고,
          재시도 로직 및 에러 처리를 제공합니다.

    책임:
      - collect() 메서드 추상 메서드 정의
      - _retry_request() 재시도 로직(지수 백오프)
      - _validate_event() 이벤트 유효성 검증
      - _now_utc() 테스트 가능한 현재 시각 공급자
    """

    def __init__(
        self,
        source_name: str,
        timeout: int = 15,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> None:
        """
        제목: Collector 초기화

        Args:
            source_name: Collector 식별자 (로그용)
            timeout: HTTP 요청 타임아웃 초
            max_retries: 최대 재시도 횟수
            retry_delay: 첫 재시도 지연 시간 (초). 이후 지수 백오프
        """
        self.source_name = source_name
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    @abstractmethod
    def collect(self) -> list[CollectorEvent]:
        """
        제목: 수집 실행 (하위 클래스 구현 필수)
        내용: 각 Collector 고유 로직으로 이벤트를 수집하여
              CollectorEvent 리스트를 반환합니다.

        Returns:
            list[CollectorEvent]: 수집된 이벤트 리스트

        Raises:
            CollectorException: 수집 실패 시
        """
        raise NotImplementedError

    def _retry_request(self, func: Callable, *args: Any, **kwargs: Any) -> Any:
        """
        제목: HTTP 요청 재시도 로직
        내용: 지수 백오프로 max_retries번 재시도합니다.
              ValidationException은 재시도하지 않고 즉시 전파(비재시도 오류).

        처리 플로우:
          1. 함수 실행 시도
          2. ValidationException → 즉시 전파 (재시도 무의미)
          3. 기타 예외 → retry_delay × (2^attempt) 대기 후 재시도
          4. max_retries 초과 시 CollectorException 발생

        Args:
            func: 실행할 함수
            *args, **kwargs: func에 전달할 인자

        Returns:
            Any: func 실행 결과

        Raises:
            CollectorException: 모든 재시도 실패 시
            ValidationException: 검증 오류 (재시도 안 함)
        """
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                return func(*args, **kwargs)
            except ValidationException:
                # 제목: 검증 오류는 재시도 안 함
                # 내용: 데이터 문제이므로 같은 요청 재시도해도 동일 결과
                raise
            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    # 제목: 지수 백오프 대기
                    # 내용: 1초 → 2초 → 4초 ...
                    wait = self.retry_delay * (2**attempt)
                    logger.warning(
                        f"[{self.source_name}] 시도 {attempt + 1}/{self.max_retries + 1} 실패: "
                        f"{type(e).__name__}: {e} (재시도 대기 {wait:.1f}초)"
                    )
                    time.sleep(wait)
                else:
                    logger.error(
                        f"[{self.source_name}] 최종 실패 (시도 {attempt + 1}회): "
                        f"{type(e).__name__}: {e}"
                    )

        raise CollectorException(
            f"{self.source_name}: {self.max_retries + 1}회 재시도 후 최종 실패",
            source_name=self.source_name,
            retryable=False,
            cause=last_error,
        )

    def _validate_event(self, event: CollectorEvent) -> None:
        """
        제목: 이벤트 필수 필드 검증
        내용: CollectorEvent의 필수 필드가 비어있지 않은지 확인.

        처리 플로우:
          1. title 비어있음 → ValidationException
          2. url 비어있음 → ValidationException
          3. published_at가 datetime 아님 → ValidationException
          4. source_name 비어있음 → ValidationException

        Args:
            event: 검증할 이벤트

        Raises:
            ValidationException: 필수 필드 누락 시
        """
        if not event.title or not event.title.strip():
            raise ValidationException(
                f"이벤트 title 누락 (source={event.source_name})",
                rule="required_title",
            )
        if not event.url or not event.url.strip():
            raise ValidationException(
                f"이벤트 url 누락 (source={event.source_name}, title={event.title[:30]})",
                rule="required_url",
            )
        if not isinstance(event.published_at, datetime):
            raise ValidationException(
                f"published_at 타입 오류 (source={event.source_name})",
                rule="datetime_type",
            )
        if not event.source_name or not event.source_name.strip():
            raise ValidationException(
                "source_name 누락",
                rule="required_source_name",
            )

    @staticmethod
    def _now_utc() -> datetime:
        """
        제목: 현재 UTC 시각 반환
        내용: datetime.now(timezone.utc)의 얇은 래퍼.
              테스트에서 mock하기 쉽도록 메서드로 분리.

        Returns:
            datetime: timezone-aware UTC 현재 시각
        """
        return datetime.now(UTC)
