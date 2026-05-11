"""
제목: weekly_news_x Telegram 알림 모듈
내용: 발행 성공/실패 시 운영자 채널(INTERNAL)에 Telegram 알림을 전송한다.
      기존 publishers/telegram_publisher.py 의 TelegramPublisher 클래스를 직접 재활용.

      알림은 INTERNAL 채널 전용 — 구독자(FREE/PAID)에게 노출되지 않음.
      Telegram 환경변수(TELEGRAM_BOT_TOKEN, TELEGRAM_INTERNAL_CHANNEL_ID) 미설정 시
      graceful skip — 본 알림 실패가 X 발행 자체를 막지 않는다.

주요 함수:
  - notify_success(archive_name, thread_url, tweet_count, sidecar_path,
                   force_republished): 발행 성공 알림
  - notify_failure(archive_name, stage, exit_code, error_msg): 발행 실패 알림
  - _send_internal(text): 내부 헬퍼 (TelegramPublisher.publish_internal 호출)
"""
from __future__ import annotations

import html
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from core.logger import get_logger
from publishers.telegram_publisher import TelegramPublisher

VERSION = "1.0.0"

logger = get_logger(__name__)


def _escape(text: str) -> str:
    """
    제목: Telegram HTML 이스케이프
    내용: < > & 문자를 HTML 엔터티로 치환. Telegram parse_mode=HTML 안전성.

    Args:
        text: 원본 텍스트

    Returns:
        str: 이스케이프된 텍스트
    """
    return html.escape(text, quote=False)


def _send_internal(text: str) -> bool:
    """
    제목: INTERNAL 채널 발행 공통 헬퍼
    내용: TelegramPublisher 인스턴스 생성 후 publish_internal 호출.
          환경변수 누락/네트워크 예외 모두 graceful skip (예외 전파 X).

    Args:
        text: HTML 포맷 메시지

    Returns:
        bool: 발행 성공 여부 (실패해도 예외 발생 안 함)
    """
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    internal_id = os.environ.get("TELEGRAM_INTERNAL_CHANNEL_ID", "")
    if not (bot_token and internal_id):
        logger.info(
            "[notifier] TELEGRAM_BOT_TOKEN / TELEGRAM_INTERNAL_CHANNEL_ID 미설정 — skip"
        )
        return False

    try:
        # DRY_RUN 환경 변수는 publish.py 단계에서 이미 분기되므로
        # 여기는 실발행 컨텍스트로 가정 (dry_run=False 강제 X — 클래스가 환경변수 참조)
        tg = TelegramPublisher()
        message_id = tg.publish_internal(text)
        logger.info(f"[notifier] TG INTERNAL 알림 발행 message_id={message_id}")
        return True
    except Exception as e:
        # 알림 실패가 X 발행 자체를 망가뜨리면 안 됨 → graceful skip
        logger.warning(f"[notifier] TG INTERNAL 알림 실패: {type(e).__name__}: {e}")
        return False


def notify_success(
    archive_name: str,
    thread_url: str,
    tweet_count: int,
    sidecar_path: str,
    force_republished: bool = False,
) -> bool:
    """
    제목: 발행 성공 알림
    내용: Thread URL, tweet 수, sidecar 경로를 INTERNAL 채널로 전송.

    Args:
        archive_name: archive .md 파일명 (디렉토리 제외)
        thread_url: X 첫 트윗 URL
        tweet_count: 발행된 트윗 수
        sidecar_path: sidecar JSON 경로 (저장소 상대)
        force_republished: 강제 재발행 여부

    Returns:
        bool: 알림 성공 여부
    """
    now_kst = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST")
    badge = "🔁 RE-PUBLISHED" if force_republished else "✅ PUBLISHED"

    text = (
        f"<b>{badge} · weekly_news_x</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🗂 archive: <code>{_escape(archive_name)}</code>\n"
        f"🧵 tweets: <b>{tweet_count}</b>\n"
        f"🔗 thread: {_escape(thread_url)}\n"
        f"📝 sidecar: <code>{_escape(sidecar_path)}</code>\n"
        f"🕒 {now_kst}"
    )
    return _send_internal(text)


def notify_failure(
    archive_name: str,
    stage: str,
    exit_code: int,
    error_msg: str = "",
) -> bool:
    """
    제목: 발행 실패 알림
    내용: 어느 단계에서 어떤 exit code로 실패했는지 INTERNAL 채널로 전송.

    Args:
        archive_name: archive .md 파일명 (없으면 'unknown')
        stage: 실패 단계 식별자
               (e.g. 'archive_not_found', 'validation', 'tweepy', 'sidecar_write')
        exit_code: publish.py exit code
        error_msg: 추가 오류 정보 (길이 제한 — 800자)

    Returns:
        bool: 알림 성공 여부
    """
    now_kst = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST")
    truncated_err = error_msg[:800] if error_msg else "(no error message)"

    text = (
        f"<b>❌ FAILED · weekly_news_x</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🗂 archive: <code>{_escape(archive_name)}</code>\n"
        f"⚙️ stage: <b>{_escape(stage)}</b>\n"
        f"🔢 exit code: <code>{exit_code}</code>\n"
        f"📋 error:\n<pre>{_escape(truncated_err)}</pre>\n"
        f"🕒 {now_kst}"
    )
    return _send_internal(text)
