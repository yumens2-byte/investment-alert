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
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic

from core.logger import get_logger

VERSION = "1.3.1"  # X Premium 380자 정책 반영 (publish.py TWEET_LIMIT 추종, 2026-05-17)

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


def _log_banner(title: str) -> None:
    """제목: 로그 구획 배너 출력"""
    logger.info("=" * 60)
    logger.info(f"  {title}")
    logger.info("=" * 60)


def _log_env_diagnostics() -> None:
    """제목: 환경 진단 정보 출력 (시크릿 유무, 모델, 한도 등)"""
    logger.info("[collect] 환경 진단:")
    logger.info(f"  - MODEL = {MODEL}")
    logger.info(f"  - MAX_TOKENS = {MAX_TOKENS}")
    logger.info(f"  - WEB_SEARCH_MAX_USES = {WEB_SEARCH_MAX_USES}")
    logger.info(f"  - ARCHIVE_ROOT = {ARCHIVE_ROOT}")
    logger.info(f"  - ANTHROPIC_API_KEY: {'설정됨' if os.environ.get('ANTHROPIC_API_KEY') else '없음'}")
    logger.info(f"  - TELEGRAM_BOT_TOKEN: {'설정됨' if os.environ.get('TELEGRAM_BOT_TOKEN') else '없음'}")
    logger.info(
        f"  - TELEGRAM_INTERNAL_CHANNEL_ID: "
        f"{'설정됨' if os.environ.get('TELEGRAM_INTERNAL_CHANNEL_ID') else '없음'}"
    )
    logger.info(f"  - GITHUB_OUTPUT: {'있음' if os.environ.get('GITHUB_OUTPUT') else '없음'}")
    logger.info(f"  - GITHUB_STEP_SUMMARY: {'있음' if os.environ.get('GITHUB_STEP_SUMMARY') else '없음'}")


def _write_step_summary(content: str) -> None:
    """
    제목: GitHub Actions Step Summary에 마크다운 추가
    내용: GITHUB_STEP_SUMMARY 파일이 있으면 append. 로컬에서는 no-op.
    """
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    try:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(content + "\n")
    except OSError as e:
        logger.warning(f"[collect] STEP_SUMMARY 쓰기 실패: {e}")



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
      7. STEP_SUMMARY 마크다운 작성 (GitHub Actions UI 표시용)

    Returns:
        int: 0 성공, 1 실패
    """
    # 지연 import (테스트 mock 용이)
    # v1.3.0: publish 모듈에서 parse_thread/count_x_chars/TWEET_LIMIT 재사용 (단일 진실 소스)
    from publishers.weekly_news_x.notifier import notify_draft_failure
    from publishers.weekly_news_x.publish import (
        TWEET_LIMIT,
        count_x_chars,
        parse_thread,
    )

    started = time.monotonic()
    today = get_today_kst()
    weekday = today.strftime("%A")

    _log_banner(f"weekly_news collect v{VERSION} 시작")
    logger.info(f"[collect] KST 기준 today = {today.strftime('%Y-%m-%d %A %H:%M:%S %Z')}")
    _log_env_diagnostics()

    # ── Step 1: API 키 검증 ──
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("[collect] ❌ ANTHROPIC_API_KEY 미설정 → 종료 (exit 1)")
        notify_draft_failure(
            stage="missing_api_key",
            error_msg="ANTHROPIC_API_KEY 환경변수 미설정",
            weekday=weekday,
        )
        _write_step_summary("### ❌ collect 실패: ANTHROPIC_API_KEY 미설정")
        return 1
    logger.info(f"[collect] ✅ API 키 확인 (길이 {len(api_key)}자, prefix {api_key[:8]}...)")

    # ── Step 2: Anthropic Client 생성 ──
    logger.info("[collect] [1/4] Anthropic Client 생성 중...")
    client = anthropic.Anthropic(api_key=api_key)
    logger.info(f"[collect]   ✅ Client 준비 완료 (model={MODEL})")

    # ── Step 3: 프롬프트 로드 ──
    logger.info("[collect] [2/4] 시스템 프롬프트 로드 중...")
    system_prompt = load_prompt()
    user_msg = build_user_message(today)
    logger.info(f"[collect]   ✅ system 프롬프트 {len(system_prompt)}자, user 메시지 {len(user_msg)}자")

    # ── Step 4: Claude API 호출 (web_search 포함) ──
    logger.info(f"[collect] [3/4] Claude API 호출 중 (web_search max_uses={WEB_SEARCH_MAX_USES})...")
    api_start = time.monotonic()
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system_prompt,
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": WEB_SEARCH_MAX_USES,
            }],
            messages=[{"role": "user", "content": user_msg}],
        )
    except Exception as e:
        api_elapsed = time.monotonic() - api_start
        logger.error(
            f"[collect]   ❌ Claude API 호출 실패 (소요 {api_elapsed:.1f}초): "
            f"{type(e).__name__}: {e}"
        )
        notify_draft_failure(
            stage="claude_api",
            error_msg=f"{type(e).__name__}: {e}",
            weekday=weekday,
        )
        _write_step_summary(
            f"### ❌ collect 실패: Claude API 오류\n"
            f"- 소요시간: {api_elapsed:.1f}초\n"
            f"- 에러: `{type(e).__name__}: {e}`"
        )
        return 1

    api_elapsed = time.monotonic() - api_start
    logger.info(f"[collect]   ✅ Claude API 응답 수신 (소요 {api_elapsed:.1f}초)")

    # 응답 블록 분석
    block_types: dict[str, int] = {}
    for block in response.content:
        block_types[block.type] = block_types.get(block.type, 0) + 1
    logger.info(f"[collect]   📦 응답 블록 분포: {block_types}")

    # 토큰 사용량 (response.usage 필드)
    try:
        usage = response.usage
        logger.info(
            f"[collect]   🔢 토큰: input={usage.input_tokens} / "
            f"output={usage.output_tokens}"
        )
        if hasattr(usage, "server_tool_use") and usage.server_tool_use:
            sw = getattr(usage.server_tool_use, "web_search_requests", 0)
            logger.info(f"[collect]   🔍 web_search 실제 사용: {sw}회")
    except AttributeError:
        logger.warning("[collect]   ⚠️ 토큰 사용량 정보 없음 (response.usage 미지원)")
        usage = None

    # ── Step 5: 텍스트 추출 ──
    final_text = extract_text_from_response(response)
    if not final_text:
        logger.error("[collect]   ❌ 응답 텍스트 비어있음 → 종료 (exit 1)")
        notify_draft_failure(
            stage="empty_response",
            error_msg="Claude API 응답 텍스트 비어있음 (max_tokens 부족 또는 tool_use만 반환)",
            weekday=weekday,
        )
        _write_step_summary(
            "### ❌ collect 실패: 응답 텍스트 비어있음\n"
            f"- 블록 분포: `{block_types}`\n"
            "- max_tokens 한도 또는 tool_use만 반환된 경우"
        )
        return 1

    logger.info(f"[collect]   ✅ 텍스트 추출 완료 ({len(final_text)}자)")

    # 마크다운 청크 사전 분석 (publish 단계에서 어떻게 split 될지 미리 표시)
    # v1.3.0: publish 모듈의 parse_thread()를 그대로 사용 — 단일 진실 소스
    chunks_pre = parse_thread(final_text)
    chunk_count = len(chunks_pre)
    logger.info(f"[collect]   🧵 예상 스레드 청크 수: {chunk_count}개")

    # ── 응답 형식 사후 검증 (v1.1.0 신규) ──
    # 기대: 헤더 + 6 뉴스 + 시사점 = 8 청크, '---' 구분자 7개
    # 너무 적으면 마크다운 형식이 깨졌거나 메타-질문 응답일 가능성
    MIN_CHUNK_COUNT = 5
    META_PATTERNS = [
        "사용자님", "마스터님", "어떻게 진행", "어떤 방식",
        "옵션 1", "옵션 2", "옵션1", "옵션2",
        "찾기 어려운", "확보되지 않았", "충족하기 어려운",
        "두 가지 옵션", "제안드립니다", "진행할까요",
    ]
    detected_meta = [p for p in META_PATTERNS if p in final_text]

    if chunk_count < MIN_CHUNK_COUNT or detected_meta:
        reason_parts = []
        if chunk_count < MIN_CHUNK_COUNT:
            reason_parts.append(f"청크 부족 ({chunk_count} < {MIN_CHUNK_COUNT})")
        if detected_meta:
            reason_parts.append(f"메타-질문 패턴 감지: {detected_meta[:3]}")
        reason = " / ".join(reason_parts)

        logger.error(f"[collect]   ❌ 응답 형식 검증 실패 — {reason}")
        logger.error(f"[collect]      응답 미리보기 (앞 300자): {final_text[:300]!r}")
        notify_draft_failure(
            stage="invalid_format",
            error_msg=(
                f"응답이 X 스레드 마크다운 형식 위반.\n"
                f"검증 실패 사유: {reason}\n"
                f"응답 미리보기: {final_text[:200]}..."
            ),
            weekday=weekday,
        )
        _write_step_summary(
            f"### ❌ collect 실패: invalid_format\n"
            f"- 사유: {reason}\n"
            f"- 청크 수: {chunk_count}개 (기대 ≥ {MIN_CHUNK_COUNT})\n"
            f"- 메타 패턴: {detected_meta if detected_meta else '없음'}\n"
            f"- archive 저장 안 함 (PR 생성 차단)\n"
            f"- 응답 미리보기:\n```\n{final_text[:400]}\n```"
        )
        return 1

    logger.info(f"[collect]   ✅ 형식 검증 통과 (청크 {chunk_count}개, 메타 패턴 0건)")

    # ──────────────────────────────────────────────────────────
    # Step 5.5: X 글자수 사전 검증 (v1.3.0 신규, 2026-05-17)
    # ──────────────────────────────────────────────────────────
    # 도입 배경: 2026-05-16 사고 — Claude 응답 청크 #2=340자, #4=311자 로 publish
    #            단계 validate_tweets() ValueError → exit 2 미발행.
    #            본 검증으로 PR 생성 전에 차단하여 마스터 검수 부담 제거.
    # 동일 함수(parse_thread/count_x_chars/TWEET_LIMIT)를 publish 모듈에서 import하여
    # publish 단계와 동일 정책 보장.
    # 롤백: 본 블록 통째로 주석 처리 + VERSION 1.2.0 환원.
    # ──────────────────────────────────────────────────────────
    overflow_list: list[tuple[int, int, str]] = []
    for idx, chunk in enumerate(chunks_pre, start=1):
        n_chars = count_x_chars(chunk)
        if n_chars > TWEET_LIMIT:
            preview = chunk[:80].replace("\n", " ")
            overflow_list.append((idx, n_chars, preview))

    if overflow_list:
        detail_lines = [
            f"  - tweet #{i}: {n}/{TWEET_LIMIT}자 (초과 {n - TWEET_LIMIT}자) — {preview}..."
            for i, n, preview in overflow_list
        ]
        detail = "\n".join(detail_lines)
        logger.error(
            f"[collect]   ❌ X 글자수 검증 실패 — {len(overflow_list)}개 청크 초과:\n{detail}"
        )
        notify_draft_failure(
            stage="length_exceeded",
            error_msg=(
                f"X {TWEET_LIMIT}자 정책 초과 청크 {len(overflow_list)}개:\n{detail}\n"
                "→ archive 저장 안 함 / PR 생성 차단."
            ),
            weekday=weekday,
        )
        _write_step_summary(
            f"### ❌ collect 실패: length_exceeded\n"
            f"- 초과 청크: {len(overflow_list)}개\n"
            f"- 상세:\n```\n{detail}\n```\n"
            f"- archive 저장 안 함 (PR 생성 차단)\n"
            f"- 재시도: Actions → Weekly News Draft → Re-run failed jobs"
        )
        return 1

    logger.info(f"[collect]   ✅ X 글자수 검증 통과 (모든 {chunk_count}개 청크 ≤ {TWEET_LIMIT}자)")

    # ── Step 6: archive 저장 ──
    logger.info("[collect] [4/4] archive 저장 중...")
    saved = save_archive(final_text, today)
    logger.info(f"[collect]   ✅ 저장 완료: {saved}")

    # ── GitHub Actions output 기록 ──
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        repo_root = _HERE.parent.parent
        try:
            rel_path = saved.relative_to(repo_root)
        except ValueError:
            rel_path = saved
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"archive_path={rel_path}\n")
            f.write(f"date={today.strftime('%Y-%m-%d')}\n")
            f.write(f"weekday={weekday}\n")
        logger.info(f"[collect]   📝 GITHUB_OUTPUT 기록: archive_path={rel_path}")

    total_elapsed = time.monotonic() - started

    # ── STEP_SUMMARY 마크다운 작성 ──
    in_tok = usage.input_tokens if usage else "?"
    out_tok = usage.output_tokens if usage else "?"
    summary_md = (
        f"### ✅ Weekly News Collect 성공\n"
        f"| 항목 | 값 |\n"
        f"|---|---|\n"
        f"| 일자 | `{today.strftime('%Y-%m-%d %A')}` |\n"
        f"| Archive | `{saved.name}` |\n"
        f"| 텍스트 길이 | {len(final_text):,}자 |\n"
        f"| 예상 트윗 청크 | {chunk_count}개 |\n"
        f"| 입력 토큰 | {in_tok} |\n"
        f"| 출력 토큰 | {out_tok} |\n"
        f"| API 응답 시간 | {api_elapsed:.1f}초 |\n"
        f"| 총 소요 | {total_elapsed:.1f}초 |\n"
        f"| 블록 분포 | `{block_types}` |\n"
    )
    _write_step_summary(summary_md)

    _log_banner(f"weekly_news collect 완료 (총 {total_elapsed:.1f}초)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
