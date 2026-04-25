"""
제목: 프로젝트 공통 예외 클래스
내용: investment-alert 전역에서 사용하는 커스텀 예외를 정의합니다.
      각 예외는 구체적 오류 원인을 분류하여 상위 핸들러가 적절히 대응할 수 있게 합니다.

주요 클래스:
  - InvestmentAlertError: 프로젝트 최상위 예외
  - CollectorException: 데이터 수집 실패 시
  - ValidationException: 이벤트 유효성 검증 실패 시
  - ConfigurationException: 환경변수/설정 누락 시
  - DetectionException: 감지 레이어(MacroNewsLayer) 실패 시
"""

from __future__ import annotations

VERSION = "1.0.0"


class InvestmentAlertError(Exception):
    """
    제목: 프로젝트 최상위 예외
    내용: investment-alert의 모든 커스텀 예외가 상속하는 베이스 클래스.
          except InvestmentAlertError 한 줄로 전체 커스텀 예외를 포괄할 수 있음.

    책임:
      - 프로젝트 예외 체계의 루트
      - 메시지 + 선택적 원인(cause) 보관
    """

    def __init__(self, message: str, cause: Exception | None = None) -> None:
        """
        제목: 예외 초기화
        내용: 메시지와 원인 예외를 저장

        Args:
            message: 사람이 읽을 수 있는 오류 설명
            cause: 이 예외를 유발한 하위 예외 (선택)
        """
        super().__init__(message)
        self.message = message
        self.cause = cause

    def __str__(self) -> str:
        if self.cause:
            return f"{self.message} (원인: {type(self.cause).__name__}: {self.cause})"
        return self.message


class CollectorException(InvestmentAlertError):
    """
    제목: 데이터 수집 실패 예외
    내용: RSS 파싱 실패, HTTP 오류, 타임아웃 등 Collector 레벨 오류 시 발생

    책임:
      - 어떤 소스에서 실패했는지 source_name 보관
      - 재시도 가능 여부(retryable) 플래그 보관
    """

    def __init__(
        self,
        message: str,
        source_name: str | None = None,
        retryable: bool = True,
        cause: Exception | None = None,
    ) -> None:
        """
        제목: Collector 예외 초기화

        Args:
            message: 오류 메시지
            source_name: 실패한 소스 이름 (예: 'fed_rss', 'reuters_markets')
            retryable: 재시도 가능 여부 (네트워크 오류는 True, 파싱 오류는 False)
            cause: 하위 예외
        """
        super().__init__(message, cause)
        self.source_name = source_name
        self.retryable = retryable


class ValidationException(InvestmentAlertError):
    """
    제목: 이벤트 유효성 검증 실패 예외
    내용: NewsValidator 등에서 이벤트가 필수 조건을 만족하지 못할 때 발생

    책임:
      - 실패한 검증 규칙명(rule) 보관
    """

    def __init__(
        self,
        message: str,
        rule: str | None = None,
        cause: Exception | None = None,
    ) -> None:
        """
        제목: Validation 예외 초기화

        Args:
            message: 오류 메시지
            rule: 실패한 검증 규칙 식별자 (예: 'speculative_content', 'stale_article')
            cause: 하위 예외
        """
        super().__init__(message, cause)
        self.rule = rule


class ConfigurationException(InvestmentAlertError):
    """
    제목: 설정 오류 예외
    내용: 필수 환경변수 누락, 잘못된 형식의 설정값 등에서 발생

    책임:
      - 누락/오류 설정 키 보관
    """

    def __init__(
        self,
        message: str,
        config_key: str | None = None,
        cause: Exception | None = None,
    ) -> None:
        """
        제목: Configuration 예외 초기화

        Args:
            message: 오류 메시지
            config_key: 문제가 된 설정 키 (예: 'YOUTUBE_CHANNELS')
            cause: 하위 예외
        """
        super().__init__(message, cause)
        self.config_key = config_key


class DetectionException(InvestmentAlertError):
    """
    제목: 감지 레이어 실패 예외
    내용: MacroNewsLayer 점수 계산/레벨 판정 중 발생하는 오류

    책임:
      - 실패한 단계(stage) 보관
    """

    def __init__(
        self,
        message: str,
        stage: str | None = None,
        cause: Exception | None = None,
    ) -> None:
        """
        제목: Detection 예외 초기화

        Args:
            message: 오류 메시지
            stage: 실패 단계 (예: 'score_computation', 'level_judgment')
            cause: 하위 예외
        """
        super().__init__(message, cause)
        self.stage = stage
