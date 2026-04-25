"""
제목: X(Twitter) 발행 모듈
내용: tweepy v4를 사용하여 X API에 트윗을 발행합니다.
      DRY_RUN=true 환경에서는 실제 발행 없이 로그만 출력합니다.

주요 클래스:
  - XPublisher: X API 발행 클라이언트

주요 함수:
  - XPublisher.publish(text): 트윗 발행, tweet_id 반환
"""

from __future__ import annotations

import os

from core.logger import get_logger

VERSION = "1.0.0"

logger = get_logger(__name__)


class XPublisher:
    """
    제목: X(Twitter) 발행 클라이언트
    내용: tweepy v4 Client를 사용한 X API v2 트윗 발행.
          DRY_RUN 모드에서는 실제 발행 없이 시뮬레이션.

    책임:
      - 트윗 발행 (publish)
      - DRY_RUN 모드 지원
      - API 오류 시 상세 로깅
    """

    def __init__(self, dry_run: bool | None = None) -> None:
        """
        제목: XPublisher 초기화

        Args:
            dry_run: True면 모의 실행. None이면 DRY_RUN 환경변수 참조.
        """
        from config.settings import get_env_bool
        self.dry_run = dry_run if dry_run is not None else get_env_bool("DRY_RUN", True)
        self._client: object | None = None

        if not self.dry_run:
            self._client = self._build_client()

        logger.info(f"[XPublisher] v{VERSION} 초기화 (dry_run={self.dry_run})")

    def _build_client(self) -> object:
        """
        제목: tweepy Client 생성
        내용: 환경변수에서 X API 키를 로드하여 tweepy.Client를 생성합니다.

        Returns:
            tweepy.Client: 인증된 클라이언트

        Raises:
            RuntimeError: 필수 환경변수 누락 시
        """
        try:
            import tweepy  # type: ignore[import]
        except ImportError as e:
            raise RuntimeError("tweepy 패키지 미설치 — pip install tweepy") from e

        api_key = os.getenv("X_API_KEY", "")
        api_secret = os.getenv("X_API_SECRET", "")
        access_token = os.getenv("X_ACCESS_TOKEN", "")
        access_secret = os.getenv("X_ACCESS_TOKEN_SECRET", "")

        if not all([api_key, api_secret, access_token, access_secret]):
            raise RuntimeError("X API 환경변수 누락. .env 파일 확인 필요.")

        return tweepy.Client(
            consumer_key=api_key,
            consumer_secret=api_secret,
            access_token=access_token,
            access_token_secret=access_secret,
        )

    def publish(self, text: str) -> str:
        """
        제목: 트윗 발행
        내용: DRY_RUN=false이면 실제 트윗을 발행하고 tweet_id를 반환합니다.
              DRY_RUN=true이면 "DRY_RUN" 문자열 반환.

        처리 플로우:
          1. DRY_RUN 체크 → 시뮬레이션 반환
          2. tweepy Client로 트윗 생성
          3. tweet_id 반환

        Args:
            text: 트윗 본문 (280자 이내)

        Returns:
            str: 발행 성공 시 tweet_id, DRY_RUN 시 "DRY_RUN"

        Raises:
            RuntimeError: 발행 실패 시
        """
        if self.dry_run:
            logger.info(f"[XPublisher] DRY_RUN — 트윗 시뮬레이션: {text[:60]}...")
            return "DRY_RUN"

        try:
            assert self._client is not None
            response = self._client.create_tweet(text=text)  # type: ignore[union-attr]
            tweet_id = str(response.data["id"])  # type: ignore[index]
            logger.info(f"[XPublisher] 트윗 발행 성공: tweet_id={tweet_id}")
            return tweet_id
        except Exception as e:
            logger.error(f"[XPublisher] 트윗 발행 실패: {type(e).__name__}: {e}")
            raise RuntimeError(f"X 발행 실패: {e}") from e
