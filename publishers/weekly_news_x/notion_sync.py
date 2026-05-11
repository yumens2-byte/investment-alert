"""
제목: Notion DB 적재 모듈 (옵션)
내용: 발행된 X 스레드 마크다운을 Notion 워크스페이스 DB에 적재.
      NOTION_TOKEN + NOTION_DB_ID 둘 다 설정 시에만 동작.

      Notion DB 필요 속성:
        - Title(title), Date(date), Tweet URL(url),
          Status(select: Draft/Published/Failed),
          Source File(rich_text), Content(rich_text)

주요 함수:
  - sync_to_notion(...): Notion API 호출
  - main(): 최신 archive .md 자동 적재
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from core.logger import get_logger

VERSION = "1.0.0"

logger = get_logger(__name__)

NOTION_API = "https://api.notion.com/v1/pages"
NOTION_VERSION = "2022-06-28"

ARCHIVE_ROOT = Path(__file__).resolve().parent.parent.parent / "logs" / "weekly_news"


def sync_to_notion(
    title: str,
    date_iso: str,
    tweet_url: str | None,
    content_md: str,
    source_path: str,
    status: str = "Published",
) -> bool:
    """
    제목: Notion DB 페이지 생성
    내용: 시크릿 설정되어 있을 때만 호출. requests로 직접 호출 (의존성 최소화).
          rich_text는 2000자 청크로 분할.

    Args:
        title: 페이지 제목
        date_iso: 'YYYY-MM-DD'
        tweet_url: X 스레드 첫 트윗 URL
        content_md: 마크다운 본문
        source_path: archive 상대 경로
        status: 'Draft' | 'Published' | 'Failed'

    Returns:
        bool: 성공 여부
    """
    token = os.environ.get("NOTION_TOKEN")
    db_id = os.environ.get("NOTION_DB_ID")
    if not (token and db_id):
        logger.info("[notion_sync] NOTION_TOKEN/DB_ID 미설정 — skip")
        return False

    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }

    content_chunks = [content_md[i:i + 1900] for i in range(0, len(content_md), 1900)]
    rich_text_blocks = [{"type": "text", "text": {"content": c}} for c in content_chunks]

    payload: dict = {
        "parent": {"database_id": db_id},
        "properties": {
            "Title": {"title": [{"type": "text", "text": {"content": title}}]},
            "Date": {"date": {"start": date_iso}},
            "Status": {"select": {"name": status}},
            "Source File": {"rich_text": [{"type": "text", "text": {"content": source_path}}]},
            "Content": {"rich_text": rich_text_blocks[:1]},
        },
        "children": [{
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": rich_text_blocks},
        }],
    }
    if tweet_url:
        payload["properties"]["Tweet URL"] = {"url": tweet_url}

    try:
        resp = requests.post(NOTION_API, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        page_id = resp.json().get("id", "")
        logger.info(f"[notion_sync] 페이지 생성 완료 page_id={page_id}")
        return True
    except Exception as e:
        logger.error(f"[notion_sync] 실패: {type(e).__name__}: {e}")
        return False


def main() -> int:
    """
    제목: 최신 archive 자동 적재 엔트리포인트
    내용: logs/weekly_news/ 최신 .md를 Notion에 적재.
          THREAD_URL/STATUS 환경변수가 있으면 그 값을 사용.

    Returns:
        int: 0 성공/skip(시크릿 미설정 = 의도된 옵션 비활성), 1 실제 실패
    """
    # 시크릿 미설정은 "옵션 모듈 비활성" — 정상 skip (exit 0)
    if not (os.environ.get("NOTION_TOKEN") and os.environ.get("NOTION_DB_ID")):
        logger.info(
            "[notion_sync] NOTION_TOKEN/NOTION_DB_ID 미설정 — skip (옵션 모듈 비활성, exit 0)"
        )
        return 0

    candidates = sorted(ARCHIVE_ROOT.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        logger.error("[notion_sync] archive .md 없음")
        return 1

    md_path = candidates[0]
    today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
    title = f"[{today}] US News Brief"
    repo_root = ARCHIVE_ROOT.parent.parent

    ok = sync_to_notion(
        title=title,
        date_iso=today,
        tweet_url=os.environ.get("THREAD_URL"),
        content_md=md_path.read_text(encoding="utf-8"),
        source_path=str(md_path.relative_to(repo_root)),
        status=os.environ.get("STATUS", "Published"),
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
