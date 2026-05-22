"""
제목: Sector Alert 친근 톤 헬퍼 모듈
내용: sector_formatter.py의 format_tg_free/paid를 보완하는 친근 톤 생성기.
      L1/L2 + rotation_type(4종) 모두 Gemini 2.5 Flash-Lite로 생성.

      외부 뉴스가 없는 sector 알림 특성상 alert_formatter_kind보다 간단:
      - 영문 뉴스 번역 불필요
      - X 발행 없음 → TG 전용
      - Internal 채널은 기존 톤 유지 (운영자 디버깅 친화)

      SECTOR_KIND_TONE_ENABLED=true 환경변수일 때만 동작.
      실패/검증 탈락 시 None 반환 → 호출자가 기존 sector_formatter로 fallback.

주요 함수:
  - format_tg_free_kind(signal) -> str | None
  - format_tg_paid_kind(signal) -> str | None
  - validate_sector_kind_output(text) -> tuple[bool, str]

검증 상수는 alert_formatter_kind에서 import (DRY).

연관 문서:
  - docs/alert_kind_tone_guidelines.md (sector도 동일 가이드라인 적용)
  - publishers/prompts/sector_kind_gemini.md
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from core.logger import configure_root_logger, get_logger

# alert_formatter_kind의 검증 상수 재사용 (DRY)
from publishers.alert_formatter_kind import (
    _DISCLAIMER_KEYWORDS,
    _FORBIDDEN_GENDER,
    _FORBIDDEN_POLITICS,
    _FORBIDDEN_PRIVATE,
    _FORBIDDEN_RECOMMEND,
    _FORBIDDEN_RELIGION,
    _FORBIDDEN_THREAT,
    _NON_KR_PATTERN,
    _THREAT_SAFE_CONTAINS,
)

VERSION = "1.0.0"

logger = get_logger(__name__)

# ────────────────────────────────────────────────────────
# 모듈 상수
# ────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
PROMPT_PATH = _HERE / "prompts" / "sector_kind_gemini.md"

# 길이 임계값 (TG 전용)
TG_MIN_LEN = 200
TG_MAX_LEN = 1200

# 모델 디폴트
DEFAULT_MODEL = "gemini-2.5-flash-lite"

# rotation_type별 친근 톤 헤더 + 무드 (가이드만 — LLM이 자연스럽게 생성)
ROTATION_HEADERS = {
    "DEFENSIVE_ROTATION": "🌧️ 시장이 조심스러운 분위기예요",
    "RISK_ON_ROTATION": "☀️ 시장이 좀 밝은 쪽으로 움직였어요",
    "ROTATION_WATCH_DEF": "👀 살짝 신호가 보여요",
    "ROTATION_WATCH_RISK": "👀 살짝 변화가 느껴져요",
}

# 친절한 섹터 해석 매핑 (LLM 프롬프트 참고용)
_SECTOR_FRIENDLY_NAMES = {
    "방어주": "건강/유틸리티/필수소비재 같은 안정 섹터",
    "경기민감": "산업/리츠/소재 같은 경기 민감 섹터",
}


def _ensure_log_file_for(name: str) -> Path:
    """제목: 표준 로그 파일 설정"""
    ts = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d_%H%M%S")
    log_dir = _HERE.parent / "logs"
    log_file = log_dir / f"{name}_{ts}.log"
    configure_root_logger(log_file=str(log_file))
    return log_file


# ────────────────────────────────────────────────────────
# 출력 검증
# ────────────────────────────────────────────────────────
def validate_sector_kind_output(text: str) -> tuple[bool, str]:
    """
    제목: sector 친근 톤 출력 후처리 검증
    내용: alert_formatter_kind의 검증 상수 재사용 + sector 길이 기준.
          위협 어휘는 _THREAT_SAFE_CONTAINS로 안전 단어(분위기 등) false positive 회피.

    Args:
        text: LLM이 반환한 메시지

    Returns:
        tuple[bool, str]: (통과 여부, 실패 사유)
    """
    if not text or not text.strip():
        return False, "empty_output"

    stripped = text.strip()

    # 1) 위협 어휘 (안전 단어 제외)
    for w in _FORBIDDEN_THREAT:
        safe_words = _THREAT_SAFE_CONTAINS.get(w, [])
        masked = stripped
        for sw in safe_words:
            masked = masked.replace(sw, "□" * len(sw))
        if w in masked:
            return False, f"forbidden_word({w!r})"

    # 2) 정치·젠더·종교·사적·권유 어휘
    other_forbidden = (
        _FORBIDDEN_POLITICS
        + _FORBIDDEN_GENDER
        + _FORBIDDEN_RELIGION
        + _FORBIDDEN_PRIVATE
        + _FORBIDDEN_RECOMMEND
    )
    for w in other_forbidden:
        if w in stripped:
            return False, f"forbidden_word({w!r})"

    # 3) 비한국어
    if _NON_KR_PATTERN.search(stripped):
        return False, "non_korean_char"

    # 4) 면책 필수
    if not any(k in stripped for k in _DISCLAIMER_KEYWORDS):
        return False, "missing_disclaimer"

    # 5) 길이 (TG only)
    n = len(stripped)
    if not (TG_MIN_LEN <= n <= TG_MAX_LEN):
        return False, f"tg_length_out_of_range({n}자)"

    return True, ""


# ────────────────────────────────────────────────────────
# Gemini 호출
# ────────────────────────────────────────────────────────
def _call_gemini_sector(prompt_user: str) -> str | None:
    """제목: Gemini Flash-Lite로 sector 친근 톤 메시지 생성"""
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        logger.warning("[sector_formatter_kind] GEMINI_API_KEY 미설정 → skip")
        return None

    model = os.environ.get("SECTOR_KIND_MODEL", DEFAULT_MODEL)

    try:
        system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.error(f"[sector_formatter_kind] 프롬프트 없음: {PROMPT_PATH}")
        return None

    try:
        from google import genai as _genai  # noqa: PLC0415

        client = _genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model,
            contents=system_prompt + "\n\n---\n\n" + prompt_user,
        )
        return (response.text or "").strip()
    except Exception as e:
        logger.warning(
            f"[sector_formatter_kind] Gemini 호출 실패: {type(e).__name__}: {e}"
        )
        return None


# ────────────────────────────────────────────────────────
# 사용자 프롬프트 빌더
# ────────────────────────────────────────────────────────
def _format_pct(v: float | None) -> str:
    """제목: 수치 표시 — None은 '—', 양수는 +기호"""
    if v is None:
        return "—"
    return f"{v:+.2f}%p"


def _build_user_prompt_sector(signal: object, audience: str) -> str:
    """
    제목: sector용 사용자 프롬프트 빌드
    내용: SectorSignal의 핵심 속성을 가독성 있는 한국어로 변환해 LLM에 전달.

    Args:
        signal: SectorSignal (rotation_type, level, spread_*, def_avg_*, cyc_avg_* 등)
        audience: 'free' | 'paid' (internal은 기존 톤 유지하므로 호출 안 됨)

    Returns:
        str: LLM용 사용자 프롬프트
    """
    rotation_type = getattr(signal, "rotation_type", "NONE")
    level = getattr(signal, "level", "L?")
    spread_5d = getattr(signal, "spread_5d", None)
    def_avg_5d = getattr(signal, "def_avg_5d", None)
    cyc_avg_5d = getattr(signal, "cyc_avg_5d", None)
    spread_1d = getattr(signal, "spread_1d", None)

    header_hint = ROTATION_HEADERS.get(rotation_type, "👀 시장 흐름 관찰")

    # rotation_type 친근 해석 힌트
    rotation_explain = {
        "DEFENSIVE_ROTATION": "방어주로 자금이 몰리고 있어요. 시장이 조금 보수적이에요.",
        "RISK_ON_ROTATION": "경기민감주로 자금이 움직였어요. 시장 분위기가 좀 좋아요.",
        "ROTATION_WATCH_DEF": "방어주 쪽이 살짝 강해진 초기 신호예요.",
        "ROTATION_WATCH_RISK": "경기민감주 쪽이 살짝 강해진 초기 신호예요.",
    }.get(rotation_type, "")

    return (
        f"[등급] {level}\n"
        f"[로테이션 타입] {rotation_type}\n"
        f"[권장 헤더] {header_hint}\n"
        f"[로테이션 해석] {rotation_explain}\n\n"
        f"[수치]\n"
        f"  - 5일 누적 spread: {_format_pct(spread_5d)} "
        f"(양수면 방어주 우세, 음수면 경기민감 우세)\n"
        f"  - 방어주(건강/유틸리티/필수소비재) 5일: {_format_pct(def_avg_5d)}\n"
        f"  - 경기민감(산업/리츠/소재) 5일: {_format_pct(cyc_avg_5d)}\n"
        f"  - 1일 spread: {_format_pct(spread_1d)}\n\n"
        f"[채널] tg ({audience})\n"
        f"위 정보를 활용해 친근한 TG HTML 메시지만 출력하세요. "
        f"길이는 한국어 200~1000자."
    )


# ────────────────────────────────────────────────────────
# Public: TG Free 메시지 생성
# ────────────────────────────────────────────────────────
def format_tg_free_kind(signal: object) -> str | None:
    """
    제목: sector TG Free 친근 톤 메시지 생성
    내용: Gemini Flash-Lite로 친근한 HTML 메시지 생성.
          실패/검증 탈락 시 None → 호출자가 기존 sector_formatter로 fallback.

    Args:
        signal: SectorSignal

    Returns:
        str | None: 친근 톤 HTML 메시지 또는 None
    """
    return _format_kind(signal, audience="free")


def format_tg_paid_kind(signal: object) -> str | None:
    """
    제목: sector TG Paid 친근 톤 메시지 생성
    내용: Free와 동일 톤. paid는 약간 더 상세한 해석 가능 (프롬프트 가이드).
    """
    return _format_kind(signal, audience="paid")


def _format_kind(signal: object, audience: str) -> str | None:
    """
    제목: 친근 톤 메시지 생성 공통 흐름
    """
    log_file = _ensure_log_file_for(f"sector_formatter_kind_{audience}")
    t0 = time.perf_counter()
    rotation_type = getattr(signal, "rotation_type", "NONE")
    level = getattr(signal, "level", "L?")

    logger.info("=" * 60)
    logger.info(
        f"[sector_formatter_kind] v{VERSION} format_{audience}_kind 시작 "
        f"level={level}, rotation={rotation_type}"
    )
    logger.info(f"[sector_formatter_kind] 로그 파일: {log_file}")

    # 1) Gemini 호출
    user_prompt = _build_user_prompt_sector(signal, audience)
    t1 = time.perf_counter()
    msg = _call_gemini_sector(user_prompt)
    logger.info(
        f"[sector_formatter_kind] [Step 1 Gemini 호출] "
        f"{time.perf_counter() - t1:.2f}s "
        f"→ {'성공 (' + str(len(msg)) + '자)' if msg else '실패/None'}"
    )

    if not msg:
        logger.info(
            f"[sector_formatter_kind] 종료 (총 {time.perf_counter() - t0:.2f}s, None)"
        )
        return None

    # 2) 검증
    ok, reason = validate_sector_kind_output(msg)
    if not ok:
        logger.warning(f"[sector_formatter_kind] 검증 실패: {reason}")
        logger.warning(f"[sector_formatter_kind] 생성된 텍스트(검증 실패):\n{msg}")
        logger.info(
            f"[sector_formatter_kind] 종료 (총 {time.perf_counter() - t0:.2f}s, None)"
        )
        return None

    logger.info(f"[sector_formatter_kind] {audience} 검증 통과 ({len(msg)}자)")
    logger.info(
        f"[sector_formatter_kind] 생성 메시지:\n----- 시작 -----\n{msg}\n----- 끝 -----"
    )
    logger.info(f"[sector_formatter_kind] 완료 (총 {time.perf_counter() - t0:.2f}s)")
    logger.info("=" * 60)
    return msg
