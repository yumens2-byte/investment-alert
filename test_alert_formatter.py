"""
제목: Telegram Bot API 발행 모듈
내용: requests를 사용하여 Telegram Bot API로 메시지를 발행합니다.
      TG Free 채널과 TG Paid 채널을 분리하여 관리합니다.
      DRY_RUN=true 환경에서는 실제 발행 없이 로그만 출력합니다.

주요 클래스:
  - TelegramPublisher: Telegram Bot API 발행 클라이언트

주요 함수:
  - TelegramPublisher.publish_free(text): 무료 채널 발행
  - TelegramPublisher.publish_paid(text): 유료 채널 발행
"""

from __future__ import annotations

import os

import requests

from core.logger import get_logger

VERSION = "1.0.0"

logger = get_logger(__name__)

# 제목: Telegram API 기본 URL
TG_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"

# 제목: 요청 타임아웃
REQUEST_TIMEOUT_SEC = 15


class TelegramPublisher:
    """
    제목: Telegram Bot 발행 클라이언트
    내용: Bot API를 통해 무료/유료 채널에 메시지를 발행합니다.
          HTML parse_mode를 기본으로 사용합니다.

    책임:
      - TG Free 채널 발행
      - TG Paid 채널 발행
      - DRY_RUN 모드 지원
      - API 오류 상세 로깅
    """

    def __init__(self, dry_run: bool | None = None) -> None:
        """
        제목: TelegramPublisher 초기화

        Args:
            dry_run: True면 모의 실행. None이면 DRY_RUN 환경변수 참조.
        """
        from config.settings import get_env_bool
        self.dry_run = dry_run if dry_run is not None else get_env_bool("DRY_RUN", True)

        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.free_channel_id = os.getenv("TELEGRAM_FREE_CHANNEL_ID", "")
        self.paid_channel_id = os.getenv("TELEGRAM_PAID_CHANNEL_ID", "")

        logger.info(f"[TelegramPublisher] v{VERSION} 초기화 (dry_run={self.dry_run})")

    def publish_free(self, text: str) -> str:
        """
        제목: TG 무료 채널 발행

        Args:
            text: HTML 포맷 메시지

        Returns:
            str: message_id (DRY_RUN 시 "DRY_RUN")
        """
        return self._publish(text, channel_id=self.free_channel_id, channel_name="FREE")

    def publish_paid(self, text: str) -> str:
        """
        제목: TG 유료 채널 발행

        Args:
            text: HTML 포맷 메시지

        Returns:
            str: message_id (DRY_RUN 시 "DRY_RUN")
        """
        return self._publish(text, channel_id=self.paid_channel_id, channel_name="PAID")

    def _publish(self, text: str, channel_id: str, channel_name: str) -> str:
        """
        제목: Telegram 채널 발행 공통 로직
        내용: Bot API sendMessage 엔드포인트를 호출합니다.
              DRY_RUN이면 시뮬레이션 반환.

        처리 플로우:
          1. DRY_RUN 체크
          2. 환경변수 검증 (bot_token, channel_id)
          3. Bot API POST 요청
          4. message_id 반환

        Args:
            text: 발행할 메시지
            channel_id: 대상 채널 ID
            channel_name: 로그용 채널 이름 ('FREE'|'PAID')

        Returns:
            str: message_id

        Raises:
            RuntimeError: 발행 실패 시
        """
        if self.dry_run:
            logger.info(f"[TelegramPublisher] DRY_RUN — TG {channel_name} 시뮬레이션: {text[:40]}...")
            return "DRY_RUN"

        if not self.bot_token or not channel_id:
            raise RuntimeError(
                f"TG {channel_name} 환경변수 누락 "
                f"(bot_token={'설정됨' if self.bot_token else '없음'}, "
                f"channel_id={'설정됨' if channel_id else '없음'})"
            )

        url = TG_API_BASE.format(token=self.bot_token)
        payload = {
            "chat_id": channel_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        try:
            resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()

            data = resp.json()
            if not data.get("ok"):
                raise RuntimeError(f"TG API 응답 오류: {data}")

            message_id = str(data["result"]["message_id"])
            logger.info(f"[TelegramPublisher] TG {channel_name} 발행 성공: message_id={message_id}")
            return message_id

        except requests.RequestException as e:
            logger.error(f"[TelegramPublisher] TG {channel_name} 발행 실패: {e}")
            raise RuntimeError(f"TG {channel_name} 발행 실패: {e}") from e
