"""
제목: 주말 미국 뉴스 수집·요약 모듈
내용: Claude API + web_search 도구로 미국 주요뉴스 6건을 수집하고,
      한국 투자자용 X 스레드 마크다운을 생성하여 logs/weekly_news/ 에 저장한다.

      DRY_RUN과 무관 (수집 단계는 항상 실행).
      ANTHROPIC_API_KEY 환경변수 필수.

주요 함수:
  - load_prompt(): 시스템 프롬프트 로드
  - get_today_kst(): KST 기준 today datetime 반환
  - build_user_message(today): user role 메시지 조립
  - extract_text_from_response(response): tool_use 블록 제외 텍스트 추출
  - save_archive(content, today): logs/weekly_news/YYYY/MM/...md 저장
  - main(): 위 단계 일괄 실행 + GITHUB_OUTPUT 기록
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic

from core.logger import get_logger

VERSION = "1.0.0"

logger = get_logger(__name__)

# ────────────────────────────────────────────────────────
# 모듈 상수
# ────────────────────────────────────────────────────────
MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 4096
WEB_SEARCH_MAX_USES = 8

_HERE = Path(__file__).resolve().parent
PROMPT_PATH = _HERE / "prompts" / "us_news_summary.md"
ARCHIVE_ROOT = _HERE.parent.parent / "logs" / "weekly_news"


def load_prompt() -> str:
    """
    제목: 시스템 프롬프트 파일 로드
    내용: prompts/us_news_summary.md 를 UTF-8로 읽어 반환.

    Returns:
        str: 시스템 프롬프트 본문
    """
    return PROMPT_PATH.read_text(encoding="utf-8")


def get_today_kst() -> datetime:
    """
    제목: 한국 시간 기준 today datetime 반환
    내용: GitHub Actions는 UTC로 실행되므로 KST를 명시 변환.

    Returns:
        datetime: timezone-aware (Asia/Seoul)
    """
    return datetime.now(ZoneInfo("Asia/Seoul"))


def build_user_message(today: datetime) -> str:
    """
    제목: user role 메시지 조립
    내용: 오늘 날짜를 명시한 한국어 user prompt를 반환.

    Args:
        today: KST 기준 today

    Returns:
        str: user role 메시지
    """
    return (
        f"오늘은 한국 시간 {today.strftime('%Y년 %m월 %d일 %A')}이다. "
        "미국 현지 시간 기준 최근 24시간 내 발생한 주요 뉴스 6건을 "
        "web_search 도구로 수집한 뒤, 시스템 프롬프트의 형식대로 정리하라."
    )


def extract_text_from_response(response: anthropic.types.Message) -> str:
    """
    제목: 응답에서 최종 텍스트 추출
    내용: response.content는 [TextBlock, ServerToolUseBlock, ToolResultBlock, ...] 혼합.
          block.type == "text" 인 것만 추려서 합친다.

    Args:
        response: Anthropic Messages API 응답

    Returns:
        str: 합쳐진 텍스트 (strip 적용)
    """
    texts = []
    for block in response.content:
        if block.type == "text":
            texts.append(block.text)
    return "\n".join(texts).strip()


def save_archive(content: str, today: datetime) -> Path:
    """
    제목: archive 마크다운 저장
    내용: logs/weekly_news/YYYY/MM/YYYY-MM-DD-{weekday}.md 형태로 저장.

    Args:
        content: 마크다운 본문
        today: KST today

    Returns:
        Path: 저장된 파일 경로
    """
    yyyy = today.strftime("%Y")
    mm = today.strftime("%m")
    dd = today.strftime("%d")
    weekday = today.strftime("%A").lower()  # saturday

    target_dir = ARCHIVE_ROOT / yyyy / mm
    target_dir.mkdir(parents=True, exist_ok=True)

    file_path = target_dir / f"{yyyy}-{mm}-{dd}-{weekday}.md"
    file_path.write_text(content, encoding="utf-8")
    return file_path


def main() -> int:
    """
    제목: 수집 파이프라인 엔트리포인트
    내용: Claude API 호출 → 마크다운 저장 → GITHUB_OUTPUT 기록.

    처리 플로우:
      1. ANTHROPIC_API_KEY 검증
      2. Anthropic Client 생성
      3. messages.create with web_search tool
      4. 텍스트 추출 → save_archive
      5. GITHUB_OUTPUT 에 archive_path, date 기록
      6. 실패 시 Telegram INTERNAL 알림

    Returns:
        int: 0 성공, 1 실패
    """
    # 지연 import (테스트 mock 용이)
    from publishers.weekly_news_x.notifier import notify_draft_failure

    today = get_today_kst()
    weekday = today.strftime("%A")  # 'Saturday' / 'Sunday' 등

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("[collect] ANTHROPIC_API_KEY 미설정")
        notify_draft_failure(
            stage="missing_api_key",
            error_msg="ANTHROPIC_API_KEY 환경변수 미설정",
            weekday=weekday,
        )
        return 1

    client = anthropic.Anthropic(api_key=api_key)

    logger.info(f"[collect] v{VERSION} 시작 — date={today.strftime('%Y-%m-%d %A')}")

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=load_prompt(),
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": WEB_SEARCH_MAX_USES,
            }],
            messages=[{
                "role": "user",
                "content": build_user_message(today),
            }],
        )
    except Exception as e:
        logger.error(f"[collect] Claude API 호출 실패: {type(e).__name__}: {e}")
        notify_draft_failure(
            stage="claude_api",
            error_msg=f"{type(e).__name__}: {e}",
            weekday=weekday,
        )
        return 1

    final_text = extract_text_from_response(response)
    if not final_text:
        logger.error("[collect] 응답 텍스트 비어있음")
        notify_draft_failure(
            stage="empty_response",
            error_msg="Claude API 응답 텍스트 비어있음 (max_tokens 부족 또는 tool_use만 반환)",
            weekday=weekday,
        )
        return 1

    saved = save_archive(final_text, today)
    logger.info(f"[collect] 저장 완료: {saved}")

    # GitHub Actions output 으로 후속 step에 경로 전달
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        repo_root = _HERE.parent.parent
        try:
            rel_path = saved.relative_to(repo_root)
        except ValueError:
            # archive 가 repo 외부일 때 (테스트 monkeypatch 등) 절대 경로 fallback
            rel_path = saved
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"archive_path={rel_path}\n")
            f.write(f"date={today.strftime('%Y-%m-%d')}\n")
            f.write(f"weekday={weekday}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
