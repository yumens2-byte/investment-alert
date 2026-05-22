"""
제목: Alert 친근 톤 헬퍼 모듈 (옵션)
내용: alert_formatter.py의 format_x/format_tg를 보완하는 친근 톤 생성기.
      L1은 Claude Sonnet 4.5, L2/L3는 Gemini 2.5 Flash-Lite로 분담.
      영문 뉴스 제목은 Gemini Flash-Lite로 한국어 의역.

      KIND_TONE_ENABLED=true 환경변수일 때만 동작.
      실패/검증 탈락 시 None 반환 → 호출자가 기존 톤(_generate_ai_tweet → 템플릿)으로 fallback.

      마스터 명령 패턴 준수:
      - 기존 alert_formatter.py의 _NON_KR_PATTERN, _X_HASHTAG_POOL, X_MAX_LENGTH 재사용
      - 25 안티봇 변동(시간문구·해시태그) 그대로 활용
      - graceful degradation: 본 발행 영향 0건

주요 함수:
  - format_x_kind(level, score, reasoning, top_news_titles, hashtags) -> str | None
  - format_tg_kind(...) -> str | None
  - translate_news_to_kr(titles) -> list[str]
  - validate_kind_output(text, channel) -> tuple[bool, str]

연관 문서:
  - docs/alert_kind_tone_guidelines.md
  - publishers/prompts/alert_kind_l1_claude.md
  - publishers/prompts/alert_kind_l2_l3_gemini.md
  - publishers/prompts/alert_kind_news_translate.md
"""
from __future__ import annotations

import hashlib
import json
import os
import re as _re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from core.logger import configure_root_logger, get_logger

VERSION = "1.1.1"

logger = get_logger(__name__)

# ────────────────────────────────────────────────────────
# 모듈 상수
# ────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
PROMPT_L1_PATH = _HERE / "prompts" / "alert_kind_l1_claude.md"
PROMPT_L2_L3_PATH = _HERE / "prompts" / "alert_kind_l2_l3_gemini.md"
PROMPT_TRANSLATE_PATH = _HERE / "prompts" / "alert_kind_news_translate.md"
PROMPT_IMAGE_PATH = _HERE / "prompts" / "alert_kind_image_gemini.md"

# 영문→한국어 번역 캐시 디렉토리 (1주 보관)
TRANSLATE_CACHE_DIR = _HERE.parent / "logs" / "translate_cache"
TRANSLATE_CACHE_TTL_SECONDS = 7 * 24 * 3600

# 이미지 출력 디렉토리
IMAGE_OUTPUT_DIR = _HERE.parent / "logs" / "alert_images"

# 길이 임계값
X_MIN_LEN = 80
X_MAX_LEN = 270  # alert_formatter.X_MAX_LENGTH(275)와 호환 안전 마진
TG_MIN_LEN = 200
TG_MAX_LEN = 1500

# 모델 디폴트
DEFAULT_L1_MODEL = "claude-sonnet-4-5"
DEFAULT_L2_L3_MODEL = "gemini-2.5-flash-lite"
DEFAULT_TRANSLATE_MODEL = "gemini-2.5-flash-lite"

# 등급별 헤더 (시스템이 보장, LLM에는 가이드만)
# v1.1.1: L2는 헤더 박스가 카드 알림처럼 어색해서 제거.
#         L2는 본문 첫 문장이 도입부 역할.
LEVEL_HEADERS = {
    "L1": "🔔 잠깐 살펴봐요",
    "L2": "",  # 헤더 없음 — 본문 첫 문장으로 자연스럽게 시작
    "L3": "🌿 가볍게 한 번",
}

# 위협 어휘 차단 (가이드라인 + 도윤 페르소나 금기 어휘 통합)
# 주의: 단어 경계 매칭 필요 — "분위기"에서 "위기" false positive 회피
_FORBIDDEN_THREAT = [
    "위기", "폭락", "공포", "긴급", "심각", "위험", "충격",
]
# 위협 어휘 매칭 시 제외할 안전 단어 (false positive 회피)
# 예: "분위기"는 "위기"로 잘못 매칭되면 안 됨
_THREAT_SAFE_CONTAINS = {
    "위기": ["분위기"],
    "위험": [],
    "공포": [],
    "심각": [],
}
_FORBIDDEN_POLITICS = [
    "대통령", "국회", "여당", "야당", "정당", "선거", "의원", "정치",
]
_FORBIDDEN_GENDER = ["페미", "젠더"]
_FORBIDDEN_RELIGION = ["예배", "교회", "종교", "기도"]
_FORBIDDEN_PRIVATE = ["결혼", "출산", "미혼"]
_FORBIDDEN_RECOMMEND = [
    "사세요", "매수하세요", "추천합니다",
    "반드시 오른다", "확실히 폭락",
]
_FORBIDDEN_WORDS_ALL = (
    _FORBIDDEN_THREAT
    + _FORBIDDEN_POLITICS
    + _FORBIDDEN_GENDER
    + _FORBIDDEN_RELIGION
    + _FORBIDDEN_PRIVATE
    + _FORBIDDEN_RECOMMEND
)

# 면책 표현 — 일상 대화에 녹는 자연 패턴
# v1.0.1: 메모예요/적어둬요/사라마라 등 (여전히 면책 티 남는 문제)
# v1.0.2: 자기 행위·자기 일상으로 마무리하는 패턴 위주
# v1.1.0: 대화 유도형 + 센치한 종결 추가 (F성향 강화)
#         "여러분은 어떠세요?" / "다들 어떻게 보내세요?" 같은 질문형
#         "잘 자요" / "또 봐요" 같은 인사 종결
_DISCLAIMER_KEYWORDS = [
    # 자기 행위 마무리 (혼잣말, v1.0.2)
    "저는 그냥", "저는 이런", "저는 오늘", "저는 이만", "저는 여기",
    "저도 그냥", "저도 이런",
    # 짧은 종결 (v1.0.2)
    "여기까지", "이만 줄", "오늘은 여기", "이 정도면",
    # 부드러운 관찰 동사 (자기 행동, v1.0.2)
    "보고 자", "보고 넘기", "보고 마", "보고 끝",
    "들여다봐", "들여다보",
    # 일상 메모 (자연스러운 경우만, v1.0.2)
    "오늘 흐름", "오늘의 메모", "오늘은 이 정도",
    # v1.1.0 신규: 대화 유도형 (F성향, 마스터 지시)
    "어떠세요", "어떠셨어요", "어떠신가요",
    "어떻게 보내", "어떻게 지내", "어떻게 마무리",
    "들려주세요", "알려주세요",
    "함께", "같이 봐요", "같이 짚어",
    # v1.1.0 신규: 인사 종결 (대화 유도 + 따뜻함)
    "잘 자요", "잘 자", "또 봐요", "내일 또",
    "다들", "여러분",
    # 기존 명시적 면책 (유지 — fallback)
    "참고", "권유는 아니", "권유가 아니",
]

# 비한국어 가드 (alert_formatter.py와 동일 패턴)
_NON_KR_PATTERN = _re.compile(
    r"[\u0900-\u097F"
    r"\u0600-\u06FF"
    r"\u0400-\u04FF"
    r"\u3040-\u30FF"
    r"\u0E00-\u0E7F"
    r"\u0590-\u05FF"
    r"\u0370-\u03FF]"
)


def _ensure_log_file_for(name: str) -> Path:
    """제목: 신규 모듈 표준 로그 파일 설정"""
    ts = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d_%H%M%S")
    log_dir = _HERE.parent / "logs"
    log_file = log_dir / f"{name}_{ts}.log"
    configure_root_logger(log_file=str(log_file))
    return log_file


# ────────────────────────────────────────────────────────
# 출력 검증
# ────────────────────────────────────────────────────────
def validate_kind_output(text: str, channel: str) -> tuple[bool, str]:
    """
    제목: 친근 톤 출력 후처리 검증
    내용: 채널·금기 어휘·면책·비한국어·길이 순.
          길이는 가장 마지막에 체크 (어휘/면책 위반이 더 중요한 진단).

    Args:
        text: LLM이 반환한 메시지
        channel: 'x' 또는 'tg'

    Returns:
        tuple[bool, str]: (통과 여부, 실패 사유)
    """
    if not text or not text.strip():
        return False, "empty_output"

    if channel not in ("x", "tg"):
        return False, f"unknown_channel({channel!r})"

    stripped = text.strip()

    # 1) 위협 어휘 (안전 단어 제외 후 매칭)
    for w in _FORBIDDEN_THREAT:
        safe_words = _THREAT_SAFE_CONTAINS.get(w, [])
        # 안전 단어들을 임시로 마스킹한 텍스트에서 검사
        masked = stripped
        for sw in safe_words:
            masked = masked.replace(sw, "□" * len(sw))
        if w in masked:
            return False, f"forbidden_word({w!r})"

    # 2) 나머지 금기 어휘 (안전 단어 회피 불필요)
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

    # 5) 길이 (가장 마지막)
    n = len(stripped)
    if channel == "x":
        if not (X_MIN_LEN <= n <= X_MAX_LEN):
            return False, f"x_length_out_of_range({n}자)"
    else:  # tg
        if not (TG_MIN_LEN <= n <= TG_MAX_LEN):
            return False, f"tg_length_out_of_range({n}자)"

    return True, ""


# ────────────────────────────────────────────────────────
# 영문 뉴스 → 한국어 의역 (캐시)
# ────────────────────────────────────────────────────────
def _translate_cache_path(title: str) -> Path:
    """제목 해시 기반 캐시 경로"""
    h = hashlib.sha256(title.encode("utf-8")).hexdigest()[:16]
    TRANSLATE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return TRANSLATE_CACHE_DIR / f"{h}.json"


def _is_cache_fresh(path: Path) -> bool:
    """1주 이내 캐시면 fresh"""
    if not path.exists():
        return False
    age = time.time() - path.stat().st_mtime
    return age < TRANSLATE_CACHE_TTL_SECONDS


def translate_news_to_kr(titles: list[str]) -> list[str]:
    """
    제목: 영문 뉴스 제목을 친근한 한국어 1줄로 의역
    내용: Gemini 2.5 Flash-Lite + 1주 캐시. 캐시 hit 시 API 호출 없음.
          캐시 디렉토리: logs/translate_cache/{sha256_16}.json
          영문이 아니거나 한국어 비율이 높으면 원문 그대로 반환.

    Args:
        titles: 원본 뉴스 제목 리스트 (영문 또는 한국어 혼재)

    Returns:
        list[str]: 한국어 의역된 제목 리스트 (원본과 동일 순서)
    """
    if not titles:
        return []

    api_key = os.environ.get("GEMINI_API_KEY", "")
    model = os.environ.get("KIND_TONE_TRANSLATE_MODEL", DEFAULT_TRANSLATE_MODEL)

    # 프롬프트 로드 (모든 호출 공유)
    try:
        translate_prompt = PROMPT_TRANSLATE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning(
            f"[alert_formatter_kind] 번역 프롬프트 파일 없음: {PROMPT_TRANSLATE_PATH}"
            f" → 원본 그대로 반환"
        )
        return list(titles)

    result: list[str] = []
    for title in titles:
        if not title or not title.strip():
            result.append(title)
            continue

        # 한국어 비율이 50% 이상이면 번역 스킵
        kr_chars = sum(1 for c in title if "\uac00" <= c <= "\ud7a3")
        if kr_chars * 2 >= len(title):
            result.append(title)
            continue

        # 캐시 확인
        cache_path = _translate_cache_path(title)
        if _is_cache_fresh(cache_path):
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                result.append(cached.get("kr", title))
                continue
            except Exception:
                pass

        # API 키 없으면 원문 그대로
        if not api_key:
            result.append(title)
            continue

        # Gemini 호출
        try:
            from google import genai as _genai  # noqa: PLC0415

            client = _genai.Client(api_key=api_key)
            prompt = (
                translate_prompt
                + "\n\n## 입력\n"
                + title.strip()
                + "\n\n## 출력\n"
            )
            response = client.models.generate_content(
                model=model,
                contents=prompt,
            )
            kr = (response.text or "").strip()
            # 첫 줄만 사용
            kr = kr.split("\n")[0].strip().strip('"').strip("'")[:80]
            if not kr or _NON_KR_PATTERN.search(kr):
                kr = title
            result.append(kr)
            # 캐시 저장
            try:
                cache_path.write_text(
                    json.dumps({"src": title, "kr": kr, "model": model}, ensure_ascii=False),
                    encoding="utf-8",
                )
            except Exception:
                pass
        except Exception as e:
            logger.warning(
                f"[alert_formatter_kind] 뉴스 번역 실패: {type(e).__name__}: {e}"
                f" → 원본 그대로"
            )
            result.append(title)

    return result


# ────────────────────────────────────────────────────────
# Claude L1 호출
# ────────────────────────────────────────────────────────
def _call_claude_l1(prompt_user: str) -> str | None:
    """제목: Claude Sonnet 4.5로 L1 메시지 생성"""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("[alert_formatter_kind] ANTHROPIC_API_KEY 미설정 → L1 skip")
        return None

    model = os.environ.get("KIND_TONE_L1_MODEL", DEFAULT_L1_MODEL)

    try:
        system_prompt = PROMPT_L1_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.error(f"[alert_formatter_kind] L1 프롬프트 없음: {PROMPT_L1_PATH}")
        return None

    try:
        import anthropic  # noqa: PLC0415

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=1500,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt_user}],
        )
        for block in response.content:
            if getattr(block, "type", "") == "text":
                return block.text.strip()
        return None
    except Exception as e:
        logger.warning(
            f"[alert_formatter_kind] Claude L1 호출 실패: {type(e).__name__}: {e}"
        )
        return None


# ────────────────────────────────────────────────────────
# Gemini L2/L3 호출
# ────────────────────────────────────────────────────────
def _call_gemini_l2_l3(prompt_user: str) -> str | None:
    """제목: Gemini Flash-Lite로 L2/L3 메시지 생성"""
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        logger.warning("[alert_formatter_kind] GEMINI_API_KEY 미설정 → L2/L3 skip")
        return None

    model = os.environ.get("KIND_TONE_L2_MODEL", DEFAULT_L2_L3_MODEL)

    try:
        system_prompt = PROMPT_L2_L3_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.error(f"[alert_formatter_kind] L2/L3 프롬프트 없음: {PROMPT_L2_L3_PATH}")
        return None

    try:
        from google import genai as _genai  # noqa: PLC0415

        client = _genai.Client(api_key=api_key)
        # Gemini는 system role이 없으므로 합쳐서 보냄
        response = client.models.generate_content(
            model=model,
            contents=system_prompt + "\n\n---\n\n" + prompt_user,
        )
        return (response.text or "").strip()
    except Exception as e:
        logger.warning(
            f"[alert_formatter_kind] Gemini L2/L3 호출 실패: {type(e).__name__}: {e}"
        )
        return None


# ────────────────────────────────────────────────────────
# 사용자 프롬프트 빌드
# ────────────────────────────────────────────────────────
def _build_user_prompt_x(
    level: str,
    score: float,
    reasoning: str,
    top_news_kr: list[str],
    hashtags: str,
) -> str:
    """제목: X 채널용 사용자 프롬프트 빌드"""
    news_block = "\n".join(f"- {t}" for t in top_news_kr) if top_news_kr else "(없음)"
    return (
        f"[등급] {level}\n"
        f"[Score] {score:.1f}/10 (값은 표시하지 말고 톤만 반영)\n"
        f"[판정 근거]\n{reasoning[:300]}\n\n"
        f"[한국어 뉴스]\n{news_block}\n\n"
        f"[해시태그] {hashtags}\n\n"
        f"[채널] x\n"
        f"위 형식과 톤대로 X용 메시지만 출력하세요. 길이는 한국어 80~250자."
    )


def _build_user_prompt_tg(
    level: str,
    score: float,
    reasoning: str,
    top_news_kr: list[str],
    top_youtube: list[str],
    health_score: float,
    alert_id_short: str,
    time_phrase: str,
) -> str:
    """제목: TG 채널용 사용자 프롬프트 빌드"""
    news_block = "\n".join(f"- {t}" for t in top_news_kr) if top_news_kr else "(없음)"
    yt_block = "\n".join(f"- {t}" for t in top_youtube) if top_youtube else "(없음)"
    return (
        f"[등급] {level}\n"
        f"[Score] {score:.1f}/10 (값은 표시하지 말고 톤만 반영)\n"
        f"[Health] {health_score:.0%}\n"
        f"[판정 근거]\n{reasoning[:500]}\n\n"
        f"[한국어 뉴스]\n{news_block}\n\n"
        f"[YouTube]\n{yt_block}\n\n"
        f"[ALERT_ID_SHORT] {alert_id_short}\n"
        f"[TIME_PHRASE] {time_phrase}\n\n"
        f"[채널] tg\n"
        f"위 형식과 톤대로 TG용 HTML 메시지만 출력하세요. 길이는 한국어 200~1200자."
    )


# ────────────────────────────────────────────────────────
# Public: X 메시지 생성
# ────────────────────────────────────────────────────────
def format_x_kind(
    level: str,
    score: float,
    reasoning: str,
    top_news_titles: list[str],
    hashtags: str,
) -> str | None:
    """
    제목: X 친근 톤 메시지 생성
    내용: L1=Claude, L2/L3=Gemini 분담. 영문 뉴스는 한국어로 의역.
          검증 실패/API 실패 시 None 반환 → 호출자가 fallback.

    Args:
        level: 'L1' | 'L2' | 'L3'
        score: Macro-News Score
        reasoning: 시스템 판정 근거
        top_news_titles: 원본 뉴스 제목 리스트
        hashtags: 해시태그 1줄

    Returns:
        str | None: 친근 톤 메시지 또는 None
    """
    log_file = _ensure_log_file_for("alert_formatter_kind_x")
    t0 = time.perf_counter()
    logger.info("=" * 60)
    logger.info(f"[alert_formatter_kind] v{VERSION} format_x_kind 시작 level={level}")
    logger.info(f"[alert_formatter_kind] 로그 파일: {log_file}")

    if level not in LEVEL_HEADERS:
        logger.warning(f"[alert_formatter_kind] 알 수 없는 level: {level}")
        return None

    # 1. 영문 뉴스 한국어 의역
    t1 = time.perf_counter()
    top_news_kr = translate_news_to_kr(top_news_titles[:3])
    logger.info(
        f"[alert_formatter_kind] [Step 1 뉴스 번역] {time.perf_counter() - t1:.2f}s "
        f"({len(top_news_kr)}건)"
    )

    # 2. LLM 호출 (등급별 분담)
    user_prompt = _build_user_prompt_x(level, score, reasoning, top_news_kr, hashtags)
    t1 = time.perf_counter()
    if level == "L1":
        msg = _call_claude_l1(user_prompt)
    else:
        msg = _call_gemini_l2_l3(user_prompt)
    logger.info(
        f"[alert_formatter_kind] [Step 2 LLM 호출 {level}] "
        f"{time.perf_counter() - t1:.2f}s "
        f"→ {'성공 (' + str(len(msg)) + '자)' if msg else '실패/None'}"
    )

    if not msg:
        logger.info(f"[alert_formatter_kind] 종료 (총 {time.perf_counter() - t0:.2f}s, None)")
        return None

    # 3. 검증
    ok, reason = validate_kind_output(msg, "x")
    if not ok:
        logger.warning(f"[alert_formatter_kind] X 검증 실패: {reason}")
        logger.warning(f"[alert_formatter_kind] 생성된 텍스트(검증 실패):\n{msg}")
        logger.info(f"[alert_formatter_kind] 종료 (총 {time.perf_counter() - t0:.2f}s, None)")
        return None

    logger.info(f"[alert_formatter_kind] X 검증 통과 ({len(msg)}자)")
    logger.info(f"[alert_formatter_kind] 생성 메시지:\n----- 시작 -----\n{msg}\n----- 끝 -----")
    logger.info(f"[alert_formatter_kind] 완료 (총 {time.perf_counter() - t0:.2f}s)")
    logger.info("=" * 60)
    return msg


# ────────────────────────────────────────────────────────
# Public: TG 메시지 생성
# ────────────────────────────────────────────────────────
def format_tg_kind(
    level: str,
    score: float,
    reasoning: str,
    top_news_titles: list[str],
    top_youtube_titles: list[str],
    health_score: float,
    alert_id: str,
    time_phrase: str,
) -> str | None:
    """
    제목: TG 친근 톤 HTML 메시지 생성
    내용: L1=Claude, L2/L3=Gemini 분담. 영문 뉴스 한국어 의역.
          검증 실패/API 실패 시 None 반환 → 호출자가 fallback.

    Args:
        level: 'L1' | 'L2' | 'L3'
        score: Macro-News Score
        reasoning: 시스템 판정 근거
        top_news_titles: 원본 뉴스 제목 리스트
        top_youtube_titles: YouTube 제목 리스트
        health_score: 데이터 건강도 (0.0~1.0)
        alert_id: 감사 추적 ID
        time_phrase: 시간 문구

    Returns:
        str | None: 친근 톤 HTML 메시지 또는 None
    """
    log_file = _ensure_log_file_for("alert_formatter_kind_tg")
    t0 = time.perf_counter()
    logger.info("=" * 60)
    logger.info(f"[alert_formatter_kind] v{VERSION} format_tg_kind 시작 level={level}")
    logger.info(f"[alert_formatter_kind] 로그 파일: {log_file}")

    if level not in LEVEL_HEADERS:
        logger.warning(f"[alert_formatter_kind] 알 수 없는 level: {level}")
        return None

    # 1. 뉴스 번역
    t1 = time.perf_counter()
    top_news_kr = translate_news_to_kr(top_news_titles[:3])
    logger.info(
        f"[alert_formatter_kind] [Step 1 뉴스 번역] {time.perf_counter() - t1:.2f}s "
        f"({len(top_news_kr)}건)"
    )

    # 2. LLM 호출
    alert_id_short = (alert_id[:8] if alert_id else "")
    user_prompt = _build_user_prompt_tg(
        level, score, reasoning, top_news_kr,
        top_youtube_titles[:2], health_score, alert_id_short, time_phrase,
    )
    t1 = time.perf_counter()
    if level == "L1":
        msg = _call_claude_l1(user_prompt)
    else:
        msg = _call_gemini_l2_l3(user_prompt)
    logger.info(
        f"[alert_formatter_kind] [Step 2 LLM 호출 {level}] "
        f"{time.perf_counter() - t1:.2f}s "
        f"→ {'성공 (' + str(len(msg)) + '자)' if msg else '실패/None'}"
    )

    if not msg:
        logger.info(f"[alert_formatter_kind] 종료 (총 {time.perf_counter() - t0:.2f}s, None)")
        return None

    # 3. 검증
    ok, reason = validate_kind_output(msg, "tg")
    if not ok:
        logger.warning(f"[alert_formatter_kind] TG 검증 실패: {reason}")
        logger.warning(f"[alert_formatter_kind] 생성된 텍스트(검증 실패):\n{msg}")
        logger.info(f"[alert_formatter_kind] 종료 (총 {time.perf_counter() - t0:.2f}s, None)")
        return None

    logger.info(f"[alert_formatter_kind] TG 검증 통과 ({len(msg)}자)")
    logger.info(f"[alert_formatter_kind] 생성 메시지:\n----- 시작 -----\n{msg}\n----- 끝 -----")
    logger.info(f"[alert_formatter_kind] 완료 (총 {time.perf_counter() - t0:.2f}s)")
    logger.info("=" * 60)
    return msg


# ════════════════════════════════════════════════════════
# v1.1.0 신규: Alert 이미지 생성 (Gemini)
# ════════════════════════════════════════════════════════
# 등급별 영문 헤드라인 + 시각 무드
_LEVEL_IMAGE_META = {
    "L1": {
        "headline_pool": [
            "A Pause to Look",
            "Markets Stir Today",
            "A Closer Look Today",
        ],
        "mood": "calm but slightly somber, quiet reflection, soft twilight tones",
    },
    "L2": {
        "headline_pool": [
            "A Note for Today",
            "Today's Market Memo",
            "Quiet Notes Today",
        ],
        "mood": "warm, thoughtful, like a cozy cafe afternoon",
    },
    "L3": {
        "headline_pool": [
            "Just a Light Glance",
            "A Light Check Today",
            "Softly Looking",
        ],
        "mood": "gentle, easy, like reading by a window",
    },
}


def _build_image_prompt(level: str, reasoning: str) -> str | None:
    """
    제목: Alert 이미지 생성 프롬프트 구축
    내용: 등급별 무드 + 가이드라인 + 영문 헤드라인 결합.

    Args:
        level: L1/L2/L3
        reasoning: 시스템 판정 근거 (시각화 힌트로 사용)

    Returns:
        str | None: Gemini용 최종 프롬프트
    """
    meta = _LEVEL_IMAGE_META.get(level)
    if not meta:
        return None

    import random as _random_local
    headline = _random_local.choice(meta["headline_pool"])
    mood = meta["mood"]

    # 프롬프트 파일 로드 (있으면)
    template = None
    if PROMPT_IMAGE_PATH.exists():
        try:
            raw = PROMPT_IMAGE_PATH.read_text(encoding="utf-8")
            m = _re.search(r"```\n(.+?)\n```", raw, _re.DOTALL)
            template = m.group(1).strip() if m else raw
        except Exception:
            template = None

    if template:
        return (
            template
            .replace("{LEVEL_MOOD}", mood)
            .replace("{ENGLISH_HEADLINE}", headline)
            .replace("{REASONING_HINT}", reasoning[:200])
        )

    # 프롬프트 파일 없으면 인라인 기본 프롬프트
    return (
        f"Generate a single editorial illustration image. No multiple images.\n"
        f"Style: Editorial minimalist illustration, The Economist + Korean "
        f"lifestyle magazine fusion.\n"
        f"Mood: {mood}\n"
        f"Color: soft off-white beige background (#F5F1EA), pale blue tint "
        f"(#E5EBF2), single muted lilac accent (#C9B8D9).\n"
        f"Composition: 70% negative space, single focal element, "
        f"aspect ratio 16:9.\n"
        f"Background MUST be a single flat color, NOT a pattern.\n"
        f"No clutter, no busy details.\n"
        f"NO humans (or one back-view silhouette only). "
        f"NO US flag, NO bull/bear mascots, NO dark backgrounds, NO red crashing arrows.\n"
        f"Add one short English headline at top: \"{headline}\".\n"
        f"Sans-serif, clean. No Korean text in image.\n"
        f"Context (for visual feel only): {reasoning[:200]}"
    )


def _validate_image_file(path: Path) -> tuple[bool, str]:
    """제목: 생성된 이미지 PNG 검증 (image_gen_gemini와 동일 로직)"""
    if not path.exists():
        return False, "file_not_found"
    size = path.stat().st_size
    if size < 30 * 1024:
        return False, f"file_too_small({size}B)"
    if size > 5 * 1024 * 1024:
        return False, f"file_too_large({size}B)"
    try:
        with open(path, "rb") as f:
            header = f.read(24)
        if header[:8] != b"\x89PNG\r\n\x1a\n":
            return False, "not_png_format"
        width = int.from_bytes(header[16:20], "big")
        height = int.from_bytes(header[20:24], "big")
        if height == 0:
            return False, "invalid_height"
        ratio = width / height
        if abs(ratio - 16 / 9) / (16 / 9) > 0.10:
            return False, f"aspect_ratio_off({width}x{height})"
    except Exception as e:
        return False, f"header_parse_error({type(e).__name__})"
    return True, ""


def generate_alert_image_kind(level: str, reasoning: str) -> Path | None:
    """
    제목: Alert 친근 톤 이미지 생성
    내용: Gemini 이미지로 등급별 무드 일러스트 1장 생성.
          실패 시 None 반환 → 호출자가 텍스트만 발행 fallback.

    Args:
        level: L1/L2/L3
        reasoning: 시스템 판정 근거

    Returns:
        Path | None: 저장된 PNG 경로 또는 None
    """
    log_file = _ensure_log_file_for("alert_formatter_kind_image")
    t0 = time.perf_counter()
    logger.info(f"[alert_formatter_kind] v{VERSION} 이미지 생성 시작 level={level}")
    logger.info(f"[alert_formatter_kind] 이미지 로그: {log_file}")

    if level not in _LEVEL_IMAGE_META:
        logger.warning(f"[alert_formatter_kind] 알 수 없는 level: {level} → 이미지 skip")
        return None

    # 1) 패키지·API 키 체크
    try:
        from google import genai as _genai_local  # noqa: PLC0415
        from google.genai import types as _types_local  # noqa: PLC0415, F401
    except ImportError:
        logger.warning("[alert_formatter_kind] google-genai 미설치 → 이미지 skip")
        return None
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        logger.warning("[alert_formatter_kind] GEMINI_API_KEY 미설정 → 이미지 skip")
        return None

    # 2) 프롬프트 구축
    prompt = _build_image_prompt(level, reasoning)
    if not prompt:
        return None
    logger.info(f"[alert_formatter_kind] 이미지 프롬프트 길이={len(prompt):,}자")

    # 3) 모델 선택
    model_name = os.environ.get(
        "KIND_IMAGE_MODEL", "gemini-2.5-flash-image"
    )

    # 4) API 호출
    t1 = time.perf_counter()
    try:
        client = _genai_local.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=_types_local.GenerateContentConfig(
                response_modalities=["IMAGE"],
            ),
        )
    except Exception as e:
        logger.warning(
            f"[alert_formatter_kind] 이미지 API 실패: {type(e).__name__}: {e}"
        )
        return None
    logger.info(
        f"[alert_formatter_kind] [Step Gemini Image API] "
        f"{time.perf_counter() - t1:.2f}s"
    )

    # 5) 이미지 추출
    image_bytes: bytes | None = None
    try:
        for candidate in response.candidates or []:
            for part in candidate.content.parts:
                if hasattr(part, "inline_data") and part.inline_data:
                    image_bytes = part.inline_data.data
                    break
            if image_bytes:
                break
    except Exception as e:
        logger.warning(f"[alert_formatter_kind] 이미지 응답 파싱 실패: {e}")
        return None

    if not image_bytes:
        logger.warning("[alert_formatter_kind] 이미지 응답에 inline_data 없음")
        return None

    # 6) 저장
    ts = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d_%H%M%S")
    IMAGE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = IMAGE_OUTPUT_DIR / f"alert_{level}_{ts}.png"
    try:
        out_path.write_bytes(image_bytes)
    except Exception as e:
        logger.warning(f"[alert_formatter_kind] 이미지 저장 실패: {e}")
        return None

    # 7) 검증
    ok, reason = _validate_image_file(out_path)
    if not ok:
        logger.warning(f"[alert_formatter_kind] 이미지 검증 실패: {reason}")
        try:
            out_path.unlink(missing_ok=True)
        except Exception:
            pass
        return None

    logger.info(
        f"[alert_formatter_kind] 이미지 완료 → {out_path} "
        f"({out_path.stat().st_size:,}B, "
        f"총 {time.perf_counter() - t0:.2f}s)"
    )
    return out_path
