"""
제목: weekly_news_x Telegram 알림 모듈
내용: 발행 성공/실패 및 draft 생성/실패 시 운영자 채널(INTERNAL)에
      Telegram 알림을 전송한다.
      기존 publishers/telegram_publisher.py 의 TelegramPublisher 클래스를 직접 재활용.

      알림은 INTERNAL 채널 전용 — 구독자(FREE/PAID)에게 노출되지 않음.
      Telegram 환경변수(TELEGRAM_BOT_TOKEN, TELEGRAM_INTERNAL_CHANNEL_ID) 미설정 시
      graceful skip — 본 알림 실패가 X 발행 자체를 막지 않는다.

주요 함수:
  - notify_success(archive_name, thread_url, tweet_count, sidecar_path,
                   force_republished): 발행 성공 알림
  - notify_failure(archive_name, stage, exit_code, error_msg): 발행 실패 알림
  - notify_draft_created(archive_path, pr_url, weekday, dry_run): draft 생성 알림
  - notify_draft_failure(stage, error_msg, weekday): draft 실패 알림
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

    # 텍스트 미리보기 (HTML 태그 제외한 plain text의 첫 줄)
    preview = text.replace("\n", " ").strip()[:60]
    logger.info(
        f"[notifier] INTERNAL 채널 발행 시도 "
        f"(메시지 {len(text)}자, 미리보기: {preview}...)"
    )

    if not (bot_token and internal_id):
        logger.info(
            "[notifier]   ⏭ TELEGRAM_BOT_TOKEN / TELEGRAM_INTERNAL_CHANNEL_ID 미설정 — skip"
        )
        return False
    logger.info(
        f"[notifier]   환경변수 확인 OK "
        f"(bot_token {len(bot_token)}자, channel_id={internal_id[:8]}...)"
    )

    try:
        tg = TelegramPublisher()
        message_id = tg.publish_internal(text)
        logger.info(f"[notifier]   ✅ Telegram INTERNAL 발행 성공 message_id={message_id}")
        return True
    except Exception as e:
        logger.warning(
            f"[notifier]   ⚠️ Telegram INTERNAL 발행 실패 (graceful skip): "
            f"{type(e).__name__}: {e}"
        )
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


def notify_draft_created(
    archive_path: str,
    pr_url: str = "",
    weekday: str = "Saturday",
    dry_run: bool = False,
) -> bool:
    """
    제목: draft 생성 완료 알림
    내용: collect.py 성공 + PR 생성 직후 INTERNAL 채널로 알림.

    Args:
        archive_path: 생성된 archive .md 경로 (저장소 상대)
        pr_url: 생성된 PR URL (선택)
        weekday: 'Saturday' | 'Sunday'
        dry_run: DRY_RUN 모드 여부 (true면 PR 미생성 안내)

    Returns:
        bool: 알림 성공 여부
    """
    now_kst = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST")
    badge = "🧪 DRAFT [DRY-RUN]" if dry_run else "📝 DRAFT CREATED"
    pr_line = (
        f"🔗 PR: {_escape(pr_url)}\n" if pr_url else
        "🔗 PR: (DRY_RUN — PR 미생성)\n" if dry_run else
        "🔗 PR: (생성 대기 중)\n"
    )

    text = (
        f"<b>{badge} · weekly_news_x ({weekday})</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🗂 archive: <code>{_escape(archive_path)}</code>\n"
        f"{pr_line}"
        f"🕒 {now_kst}\n"
        f"\n"
        f"→ 마스터 검수 후 Merge 시 X 발행 진행."
    )
    return _send_internal(text)


def notify_draft_failure(
    stage: str,
    error_msg: str = "",
    weekday: str = "Saturday",
) -> bool:
    """
    제목: draft 생성 실패 알림
    내용: collect 단계 실패 시 INTERNAL 채널로 알림. PR 생성 자체가 안 됨.

    Args:
        stage: 실패 단계 ('collect', 'comic_voice' 등)
        error_msg: 추가 오류 정보
        weekday: 'Saturday' | 'Sunday'

    Returns:
        bool: 알림 성공 여부
    """
    now_kst = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST")
    truncated_err = error_msg[:800] if error_msg else "(no error message)"

    text = (
        f"<b>❌ DRAFT FAILED · weekly_news_x ({weekday})</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"⚙️ stage: <b>{_escape(stage)}</b>\n"
        f"📋 error:\n<pre>{_escape(truncated_err)}</pre>\n"
        f"🕒 {now_kst}\n"
        f"\n"
        f"→ PR 생성 안 됨. Actions 로그 확인 필요."
    )
    return _send_internal(text)
