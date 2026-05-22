"""
제목: Telegram Bot API 발행 모듈
내용: requests를 사용하여 Telegram Bot API로 메시지를 발행합니다.
      TG Free 채널과 TG Paid 채널을 분리하여 관리합니다.
      DRY_RUN=true 환경에서는 실제 발행 없이 로그만 출력합니다.

      v1.1.0: 이미지 첨부 메서드 추가 (sendPhoto)
      - publish_with_photo(text, photo_path, channel) 신규

주요 클래스:
  - TelegramPublisher: Telegram Bot API 발행 클라이언트

주요 함수:
  - TelegramPublisher.publish_free(text): 무료 채널 발행 (텍스트)
  - TelegramPublisher.publish_paid(text): 유료 채널 발행 (텍스트)
  - TelegramPublisher.publish_internal(text): 내부 운영 채널 발행 (텍스트)
  - TelegramPublisher.publish_with_photo(text, photo_path, target): 사진 + 캡션 발행
"""

from __future__ import annotations

import os
from pathlib import Path

import requests

from core.logger import get_logger

VERSION = "1.1.0"

logger = get_logger(__name__)

# 제목: Telegram API 기본 URL
TG_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"
TG_API_PHOTO = "https://api.telegram.org/bot{token}/sendPhoto"

# 제목: 요청 타임아웃
REQUEST_TIMEOUT_SEC = 15
PHOTO_UPLOAD_TIMEOUT_SEC = 30

# 제목: TG sendPhoto의 caption 최대 길이 (1024자)
TG_PHOTO_CAPTION_MAX = 1024


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
        self.internal_channel_id = os.getenv("TELEGRAM_INTERNAL_CHANNEL_ID", "")

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

    def publish_internal(self, text: str) -> str:
        """
        제목: TG 내부 운영 채널 발행 (FR-04 / B5 패치)
        내용: L3 조기 전조, SYSTEM_DEGRADED 경보, save_alert 실패 등
              운영자 전용 메시지를 발행한다. 일반 구독자에게는 노출되지 않음.

        Args:
            text: HTML 포맷 메시지

        Returns:
            str: message_id (DRY_RUN 시 "DRY_RUN")

        Raises:
            RuntimeError: TELEGRAM_INTERNAL_CHANNEL_ID 미설정 또는 발행 실패 시
        """
        return self._publish(
            text,
            channel_id=self.internal_channel_id,
            channel_name="INTERNAL",
        )

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

    def publish_with_photo(
        self,
        text: str,
        photo_path: Path | str,
        target: str = "free",
    ) -> str:
        """
        제목: TG 채널에 사진 + 캡션 발행 (v1.1.0)
        내용: sendPhoto 엔드포인트로 이미지와 함께 메시지를 발행한다.
              caption 최대 1024자 — 초과 시 텍스트는 절단 (이미지 발행 우선).

              graceful: 실패 시 RuntimeError 발생 → 호출자가 텍스트만 fallback.
              파일 부재/접근 실패 시 사진 없이 텍스트만 발행.

        Args:
            text: HTML 포맷 메시지 (caption, 1024자 이내 권장)
            photo_path: 첨부할 PNG 파일 경로
            target: 'free' | 'paid' | 'internal' (디폴트 free)

        Returns:
            str: message_id (DRY_RUN 시 "DRY_RUN")

        Raises:
            RuntimeError: API 호출 실패 시
        """
        channel_map = {
            "free": (self.free_channel_id, "FREE"),
            "paid": (self.paid_channel_id, "PAID"),
            "internal": (self.internal_channel_id, "INTERNAL"),
        }
        if target not in channel_map:
            raise RuntimeError(f"알 수 없는 target: {target!r}")

        channel_id, channel_name = channel_map[target]
        photo_path = Path(photo_path)

        if self.dry_run:
            logger.info(
                f"[TelegramPublisher] DRY_RUN — TG {channel_name} 사진+캡션 시뮬레이션: "
                f"photo={photo_path.name}, caption={text[:40]}..."
            )
            return "DRY_RUN"

        # 사진 파일 부재 시 텍스트만 발행 (graceful)
        if not photo_path.exists():
            logger.warning(
                f"[TelegramPublisher] 사진 파일 부재 ({photo_path}) "
                f"→ 텍스트만 fallback"
            )
            return self._publish(text, channel_id=channel_id, channel_name=channel_name)

        if not self.bot_token or not channel_id:
            raise RuntimeError(
                f"TG {channel_name} 환경변수 누락 "
                f"(bot_token={'설정됨' if self.bot_token else '없음'}, "
                f"channel_id={'설정됨' if channel_id else '없음'})"
            )

        # caption 길이 제한 (1024자)
        caption = text
        if len(caption) > TG_PHOTO_CAPTION_MAX:
            caption = caption[:TG_PHOTO_CAPTION_MAX - 1] + "…"
            logger.warning(
                f"[TelegramPublisher] caption {len(text)}자 → "
                f"{TG_PHOTO_CAPTION_MAX}자로 절단"
            )

        url = TG_API_PHOTO.format(token=self.bot_token)
        try:
            with open(photo_path, "rb") as f:
                files = {"photo": (photo_path.name, f, "image/png")}
                data = {
                    "chat_id": channel_id,
                    "caption": caption,
                    "parse_mode": "HTML",
                }
                resp = requests.post(
                    url, data=data, files=files,
                    timeout=PHOTO_UPLOAD_TIMEOUT_SEC,
                )
                resp.raise_for_status()

            payload = resp.json()
            if not payload.get("ok"):
                raise RuntimeError(f"TG sendPhoto API 응답 오류: {payload}")

            message_id = str(payload["result"]["message_id"])
            logger.info(
                f"[TelegramPublisher] TG {channel_name} 사진+캡션 발행 성공: "
                f"message_id={message_id}, photo={photo_path.name}"
            )
            return message_id

        except requests.RequestException as e:
            logger.error(
                f"[TelegramPublisher] TG {channel_name} 사진 발행 실패: {e}"
            )
            raise RuntimeError(f"TG {channel_name} 사진 발행 실패: {e}") from e
