"""
제목: 코믹 캐릭터 한줄평 추가 모듈 (옵션)
내용: 마스터 부캐 코믹스의 캐릭터(Max Bullhorn / Baron Bearsworth / The Volatician)
      중 1명의 한줄평을 생성하여 archive 마크다운 끝에 추가한다.

      APPEND_COMIC_VOICE=true 환경변수일 때만 동작.

주요 함수:
  - generate_comic_voice(news_summary): Claude API 호출로 한줄평 생성
  - append_to_archive(md_path, voice_block): 마크다운 끝에 '---' + 블록 추가
  - main(): 위 두 단계 실행
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import anthropic

from config.settings import get_env_bool
from core.logger import get_logger

VERSION = "1.0.0"

logger = get_logger(__name__)

_HERE = Path(__file__).resolve().parent
PROMPT_PATH = _HERE / "prompts" / "comic_voice.md"
ARCHIVE_ROOT = _HERE.parent.parent / "logs" / "weekly_news"
MODEL = "claude-sonnet-4-5"


def generate_comic_voice(news_summary: str) -> str | None:
    """
    제목: 코믹 캐릭터 한줄평 생성
    내용: 시스템 프롬프트(comic_voice.md)에 따라 캐릭터 1명을 선정하고
          한줄평을 마크다운 블록으로 반환.

    Args:
        news_summary: 그날의 뉴스 요약 (앞 2000자만 사용)

    Returns:
        str | None: 마크다운 블록 또는 실패 시 None
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("[comic_voice] ANTHROPIC_API_KEY 미설정")
        return None

    client = anthropic.Anthropic(api_key=api_key)
    system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
    user_msg = (
        "다음은 오늘의 미국 주요뉴스 요약이다. "
        "이 중 가장 적합한 캐릭터 1명을 선택하여 한줄평을 작성하라.\n\n"
        f"{news_summary[:2000]}"
    )

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=512,
            system=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )
    except Exception as e:
        logger.error(f"[comic_voice] Claude API 실패: {type(e).__name__}: {e}")
        return None

    for block in response.content:
        if block.type == "text":
            return block.text.strip()
    return None


def append_to_archive(md_path: Path, voice_block: str) -> bool:
    """
    제목: archive 마크다운 끝에 한줄평 추가
    내용: 이미 '🎭'가 포함되어 있으면 idempotent 처리(skip).

    Args:
        md_path: archive .md 경로
        voice_block: 마크다운 블록 (시작 부분에 '**🎭 ...')

    Returns:
        bool: 성공 여부
    """
    try:
        original = md_path.read_text(encoding="utf-8")
        if "🎭" in original:
            logger.info("[comic_voice] 이미 추가됨 — skip")
            return True
        merged = original.rstrip() + "\n\n---\n\n" + voice_block + "\n"
        md_path.write_text(merged, encoding="utf-8")
        logger.info(f"[comic_voice] 추가 완료 → {md_path}")
        return True
    except Exception as e:
        logger.error(f"[comic_voice] append 실패: {type(e).__name__}: {e}")
        return False


def main() -> int:
    """
    제목: 한줄평 추가 엔트리포인트
    내용: APPEND_COMIC_VOICE=true 시에만 동작. 미설정 시 0 반환(skip).

    Returns:
        int: 0 성공/skip, 1 실패
    """
    if not get_env_bool("APPEND_COMIC_VOICE", default=False):
        logger.info("[comic_voice] APPEND_COMIC_VOICE 미설정 — skip")
        return 0

    candidates = sorted(ARCHIVE_ROOT.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        logger.error("[comic_voice] archive .md 없음")
        return 1

    md_path = candidates[0]
    summary = md_path.read_text(encoding="utf-8")

    voice = generate_comic_voice(summary)
    if not voice:
        logger.warning("[comic_voice] 생성 실패 — archive 무변경")
        return 0

    return 0 if append_to_archive(md_path, voice) else 1


if __name__ == "__main__":
    sys.exit(main())
