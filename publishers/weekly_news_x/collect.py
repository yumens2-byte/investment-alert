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
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic

from core.logger import get_logger

VERSION = "1.4.0"  # V4 freshness + V5 required keywords validation 추가 (2026-05-18)

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


# ────────────────────────────────────────────────────────────────────────
# v1.4.0 신규 — V4 freshness + V5 required keywords validation
# ────────────────────────────────────────────────────────────────────────
# 도입 배경: 2026-05-18 마스터 결정 — X 자동 발행 도입 시 검증 강화 필요.
#   V4: 본문이 stale 데이터를 다루지 않는지 (날짜 신선도)
#   V5: 미국 주식 뉴스 핵심 키워드 누락 여부 (지수/시장)
# 운영 정책: 한국 일요일 = 미국 토요일 휴장일이므로 fresh window = 3일이 안전
#   (예: 한국 5/18(일) → 본문이 5/15(금), 5/14(목) 데이터 사용 정상)
# 임계값은 ENV override 가능 (운영 보면서 조정).
# 롤백: 두 함수 통째로 주석 처리 + main()에서 호출부 제거 + VERSION 환원.
# ────────────────────────────────────────────────────────────────────────


def validate_freshness(
    thread_text: str,
    today_kst: datetime,
    fresh_window_days: int | None = None,
) -> tuple[bool, str]:
    """
    제목: 본문 날짜 신선도 검증 (V4)

    내용: 본문에서 "M/D" 또는 "MM/DD" 패턴을 추출하여 today_kst 기준
          fresh_window_days 이내의 날짜인지 검사한다.
          한 건이라도 fresh window 내 날짜가 있으면 통과.
          모든 추출 날짜가 stale이면 실패.

    Args:
        thread_text: 마크다운 본문 전체
        today_kst: KST 기준 today (datetime)
        fresh_window_days: 신선 임계값 (기본 3, ENV WEEKLY_NEWS_FRESH_WINDOW_DAYS override)

    Returns:
        (passed, message): 통과 여부와 사유 메시지

    Examples:
        >>> from datetime import datetime
        >>> from zoneinfo import ZoneInfo
        >>> today = datetime(2026, 5, 18, tzinfo=ZoneInfo("Asia/Seoul"))
        >>> validate_freshness("5/15 데이터 + 5/14 보조", today, 3)
        (True, '...')
        >>> validate_freshness("3/1 데이터만 있음", today, 3)
        (False, '...')
    """
    if fresh_window_days is None:
        fresh_window_days = int(os.environ.get("WEEKLY_NEWS_FRESH_WINDOW_DAYS", "3"))

    # M/D 또는 MM/DD 패턴 (예: 5/15, 05/15, 5/14)
    # 단, URL이나 다른 숫자 패턴(예: 3.5~3.75%) 제외 위해 단순 \b 경계 사용
    date_pattern = re.compile(r"\b(\d{1,2})/(\d{1,2})\b")
    matches = date_pattern.findall(thread_text)

    if not matches:
        # 본문에 날짜 언급이 전혀 없으면 검증 불가 — 보수적으로 통과 처리
        # (V5가 별도로 키워드 누락 검증 수행)
        return (True, "본문에 날짜 패턴 미발견 — V4 skip (V5 keyword 검증으로 보완)")

    today_date = today_kst.date()
    threshold = today_date - timedelta(days=fresh_window_days)

    fresh_dates = []
    stale_dates = []
    for month_str, day_str in matches:
        try:
            month, day = int(month_str), int(day_str)
            if not (1 <= month <= 12 and 1 <= day <= 31):
                continue
            # 연도는 today_kst.year로 가정 (작년-올해 경계 = 12월-1월은 운영상 드뭄)
            extracted = today_date.replace(month=month, day=day)
            if extracted > today_date:
                # 미래 날짜는 작년 같은 일자로 추정 (예: today 5/18, 매치 12/31 → 작년)
                extracted = extracted.replace(year=today_date.year - 1)

            if extracted >= threshold:
                fresh_dates.append(f"{month}/{day}")
            else:
                stale_dates.append(f"{month}/{day}")
        except ValueError:
            # 유효하지 않은 날짜 (예: 2/30) — skip
            continue

    if fresh_dates:
        return (
            True,
            f"V4 PASS — fresh window {fresh_window_days}일 내 날짜 {len(fresh_dates)}건: "
            f"{', '.join(fresh_dates[:5])}",
        )
    else:
        return (
            False,
            f"V4 FAIL — 모든 추출 날짜가 stale (>{fresh_window_days}일 전): "
            f"{', '.join(stale_dates[:5])}",
        )


def validate_required_keywords(thread_text: str) -> tuple[bool, str]:
    """
    제목: 미국 주식 뉴스 필수 키워드 검증 (V5)

    내용: 본문에 다음 조건 모두 만족해야 통과:
        - 지수 키워드: S&P, Nasdaq, Dow, SPX 중 최소 1개
        - 시장 키워드: 금리/Fed/원유/달러/VIX/연준/국채 중 최소 2개

    임계값은 ENV override 가능:
        - WEEKLY_NEWS_REQUIRED_INDEX_MIN (기본 1)
        - WEEKLY_NEWS_REQUIRED_MARKET_MIN (기본 2)

    Args:
        thread_text: 마크다운 본문 전체

    Returns:
        (passed, message): 통과 여부와 사유 메시지
    """
    index_min = int(os.environ.get("WEEKLY_NEWS_REQUIRED_INDEX_MIN", "1"))
    market_min = int(os.environ.get("WEEKLY_NEWS_REQUIRED_MARKET_MIN", "2"))

    index_keywords = ["S&P", "Nasdaq", "Dow", "SPX", "NASDAQ", "DOW"]
    market_keywords = [
        "금리",
        "Fed",
        "FED",
        "원유",
        "달러",
        "VIX",
        "연준",
        "국채",
        "Treasury",
        "FOMC",
    ]

    index_hits = [k for k in index_keywords if k in thread_text]
    market_hits = [k for k in market_keywords if k in thread_text]

    if len(index_hits) < index_min:
        return (
            False,
            f"V5 FAIL — 지수 키워드 부족: {len(index_hits)}개 발견 (최소 {index_min}개 필요). "
            f"검사 대상: {', '.join(index_keywords)}",
        )

    if len(market_hits) < market_min:
        return (
            False,
            f"V5 FAIL — 시장 키워드 부족: {len(market_hits)}개 발견 (최소 {market_min}개 필요). "
            f"검사 대상: {', '.join(market_keywords[:5])}...",
        )

    return (
        True,
        f"V5 PASS — 지수 {len(index_hits)}개 ({', '.join(index_hits[:3])}), "
        f"시장 {len(market_hits)}개 ({', '.join(market_hits[:3])})",
    )


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

    # ──────────────────────────────────────────────────────────
    # v1.4.0 Step 5.6 — V4 freshness + V5 required keywords 검증
    # 도입 배경: 2026-05-18 X 자동 발행 도입 시 검증 강화 (마스터 결정)
    #   V4 freshness: 본문 데이터가 stale 아닌지 (기본 fresh window 3일)
    #   V5 required keywords: 미국 주식 뉴스 필수 키워드 누락 여부
    # 실패 시: notify_draft_failure(stage="validation_v4_v5") → exit 1
    # 임계값 ENV override 가능 (운영 보면서 조정).
    # 롤백: 본 블록 통째로 주석 처리 + VERSION 1.3.1 환원.
    # ──────────────────────────────────────────────────────────
    v4_passed, v4_msg = validate_freshness(final_text, today)
    logger.info(f"[collect]   {v4_msg}")
    v5_passed, v5_msg = validate_required_keywords(final_text)
    logger.info(f"[collect]   {v5_msg}")

    if not (v4_passed and v5_passed):
        failed_validations = []
        if not v4_passed:
            failed_validations.append(v4_msg)
        if not v5_passed:
            failed_validations.append(v5_msg)
        detail_lines = "\n".join(f"  - {m}" for m in failed_validations)

        logger.error(
            f"[collect]   ❌ V4/V5 검증 실패 ({len(failed_validations)}건):\n{detail_lines}"
        )
        notify_draft_failure(
            stage="validation_v4_v5",
            error_msg=(
                f"V4/V5 validation 실패 {len(failed_validations)}건:\n{detail_lines}\n"
                "→ archive 저장 안 함 / PR 생성 차단 / X 자동 발행 차단."
            ),
            weekday=weekday,
        )
        _write_step_summary(
            f"### ❌ collect 실패: validation_v4_v5\n"
            f"- 실패 검증: {len(failed_validations)}건\n"
            f"- 상세:\n```\n{detail_lines}\n```\n"
            f"- archive 저장 안 함 (PR 생성 차단 / X 발행 차단)\n"
            f"- 재시도: Actions → Weekly News Draft → Re-run failed jobs"
        )
        return 1

    logger.info("[collect]   ✅ V4 freshness + V5 required keywords 검증 통과")

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
