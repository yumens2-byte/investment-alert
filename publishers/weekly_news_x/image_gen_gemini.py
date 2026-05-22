"""
제목: Gemini 이미지 생성 모듈 (옵션, 여성 친화 비주얼 고도화)
내용: Google Gemini Nano Banana 2 (gemini-2.5-flash-image / gemini-3.1-flash-image-preview)를
      사용하여 X 헤더 첨부용 에디토리얼 미니멀리즘 이미지를 생성한다.

      기존 DALL-E 3 기반 image_gen.py와 완전히 격리된 신규 모듈이며,
      publish.py의 IMAGE_PROVIDER 환경변수로 분기 선택된다.

      google-genai 패키지 미설치 또는 GEMINI_API_KEY 미설정 시 graceful skip.

주요 함수:
  - load_prompt_template(): 시스템 프롬프트 텍스트 로드
  - extract_topic_hint(archive_text): archive 마크다운에서 주제 키워드 추출
  - build_english_headline(archive_text): 영문 짧은 헤드라인 생성 (규칙 기반)
  - validate_image_file(path): 생성된 이미지의 크기·비율 후처리 검증
  - generate_header_image_gemini(brief_summary, out_path): 메인 함수

연관 문서:
  - docs/visual_guidelines_gemini.md (비주얼 가이드라인)
  - publishers/weekly_news_x/prompts/image_gen_gemini.md (LLM 프롬프트)
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from core.logger import configure_root_logger, get_logger

VERSION = "1.2.0"

logger = get_logger(__name__)

# ────────────────────────────────────────────────────────
# 모듈 상수
# ────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
PROMPT_PATH = _HERE / "prompts" / "image_gen_gemini.md"

# 모델 선택 (환경변수로 토글 가능)
DEFAULT_MODEL = "gemini-2.5-flash-image"  # 안정판 (Nano Banana 1)
PREVIEW_MODEL = "gemini-3.1-flash-image-preview"  # 최신 (Nano Banana 2)

# 이미지 검증 임계값
MIN_FILE_SIZE = 50 * 1024  # 50KB
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
TARGET_ASPECT_RATIO = 16 / 9  # 16:9 (와이드)
ASPECT_RATIO_TOLERANCE = 0.10  # ±10% 허용

# 안전 키워드 (헤드라인 추출 시 우선순위)
RALLY_KEYWORDS = ["상승", "rally", "+", "호조", "어닝 서프라이즈", "강세"]
DECLINE_KEYWORDS = ["하락", "decline", "-", "약세", "fall"]
MACRO_KEYWORDS = ["금리", "FOMC", "환율", "FX", "rate"]


def load_prompt_template() -> str | None:
    """
    제목: 시스템 프롬프트 텍스트 로드
    내용: prompts/image_gen_gemini.md 파일에서 첫 번째 코드 블록(```...```) 내부만 추출.

          v1.1.0 변경:
          - 기존: 파일 전체를 그대로 반환 → 변수 정의 표·안전검증·변경이력 등
            마크다운 메타 정보가 그대로 LLM에 누출되는 버그 발생
          - 수정: 정규식으로 첫 번째 fenced code block 내부만 추출
          - 효과: 토큰 약 30% 절감 + LLM 혼란 방지

    Returns:
        str | None: 코드 블록 내부 텍스트 또는 파일/블록 부재 시 None
    """
    try:
        raw = PROMPT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.error(f"[image_gen_gemini] 프롬프트 파일 없음: {PROMPT_PATH}")
        return None

    # 첫 번째 ```...``` 코드 블록 내부만 추출 (메타 정보 누출 방지)
    match = re.search(r"```\n(.+?)\n```", raw, re.DOTALL)
    if not match:
        logger.error(
            "[image_gen_gemini] 프롬프트 파일에 코드 블록 없음 — '```'로 감싸야 함"
        )
        return None

    # ── dead code preservation (v1.0.0 로직 보존) ──
    # return raw  # v1.0.0: 파일 전체 그대로 반환 (메타 정보 누출 버그)

    return match.group(1).strip()


def extract_topic_hint(archive_text: str, max_len: int = 300) -> str:
    """
    제목: archive에서 주제 키워드 추출
    내용: 마크다운 첫 '---' 이전의 시장 요약을 max_len 자로 잘라 반환.
          기존 publish.upload_header_image()의 summary_ctx 패턴과 동일.

    Args:
        archive_text: archive .md 전체 텍스트
        max_len: 최대 길이

    Returns:
        str: 주제 키워드 텍스트
    """
    head = archive_text.split("---")[0]
    return head.strip()[:max_len]


def build_english_headline(archive_text: str) -> str:
    """
    제목: 영문 짧은 헤드라인 생성 (규칙 기반)
    내용: 시장 영향 크기 순으로 매칭. 시장 전체 영향(매크로) > 환율 >
          섹터 단위(빅테크) > 일반 동향.

          v1.1.0 변경:
          - 매크로(FOMC/금리/환율) 우선 매칭으로 재배치

          v1.1.1 변경:
          - 매칭 범위를 archive 첫 1000자 → '시장 요약' 첫 섹션('---' 이전)으로 한정
          - v1.1.0 베타테스트에서 발견: 빅테크 archive의 본문 끝에 부수적으로 등장한
            '미국 10년물 금리' 매크로 데이터가 1순위 매칭에 잡혀 메인 토픽 오판하는 버그
          - 메인 토픽은 항상 시장 요약 첫 섹션에 명시되므로 그 부분만 매칭

    Args:
        archive_text: archive .md 전체 텍스트

    Returns:
        str: 영문 짧은 헤드라인 (10단어 이내)
    """
    # v1.1.1: 매칭 범위를 시장 요약 첫 섹션으로 한정 (메인 토픽만)
    head = extract_topic_hint(archive_text, max_len=1000).lower()

    # ── dead code preservation (v1.0.0 로직 보존) ──
    # 1) "엔비디아"/"nvidia"/"빅테크" → "Tech Earnings Lift Markets"
    # 2) "fomc"/"파월"/"금리" → "Fed Holds Rates Steady"
    # ... 빅테크 우선 매칭으로 FOMC archive 오분류 (Stage 2-C 파일럿에서 발견)

    # ── dead code preservation (v1.1.0 로직 보존) ──
    # head = archive_text[:1000].lower()
    # ... archive 본문 끝의 부수적 매크로 데이터가 잘못 매칭됨 (v1.1.0 베타테스트에서 발견)

    # ── v1.1.0 신규 우선순위 (유지) ──
    # 1순위: 매크로 시그널 (시장 전체 영향)
    if "fomc" in head or "파월" in head or "금리" in head:
        return "Fed Holds Rates Steady"
    if "환율" in head or "달러" in head:
        return "Currency Markets in Focus"

    # 2순위: 섹터 단위 시그널
    if "엔비디아" in head or "nvidia" in head or "빅테크" in head:
        return "Tech Earnings Lift Markets"

    # 3순위: 일반 동향 (마지막 fallback)
    has_rally = any(kw.lower() in head for kw in RALLY_KEYWORDS)
    has_decline = any(kw.lower() in head for kw in DECLINE_KEYWORDS)
    has_macro = any(kw.lower() in head for kw in MACRO_KEYWORDS)

    if has_rally:
        return "Markets Edge Higher"
    if has_decline:
        return "Calm Pullback This Week"
    if has_macro:
        return "Macro Signals Mixed"
    return "Weekly Market Brief"


def validate_image_file(path: Path) -> tuple[bool, str]:
    """
    제목: 생성된 이미지 후처리 검증
    내용: 파일 크기 및 가로:세로 비율을 검증. PIL 없이 PNG 헤더 직접 파싱.

    Args:
        path: 이미지 파일 경로

    Returns:
        tuple[bool, str]: (통과 여부, 실패 사유)
    """
    if not path.exists():
        return False, "file_not_found"

    size = path.stat().st_size
    if size < MIN_FILE_SIZE:
        return False, f"file_too_small({size}B)"
    if size > MAX_FILE_SIZE:
        return False, f"file_too_large({size}B)"

    # PNG 헤더에서 가로·세로 추출 (PIL 없이)
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
        diff = abs(ratio - TARGET_ASPECT_RATIO) / TARGET_ASPECT_RATIO
        if diff > ASPECT_RATIO_TOLERANCE:
            return False, f"aspect_ratio_off({width}x{height})"
    except Exception as e:
        return False, f"header_parse_error({type(e).__name__})"

    return True, ""


def generate_header_image_gemini(brief_summary: str, out_path: Path) -> Path | None:
    """
    제목: Gemini Nano Banana 헤더 이미지 생성
    내용: Google Gemini 이미지 모델로 에디토리얼 미니멀리즘 이미지를 생성하여
          out_path에 PNG로 저장. google-genai 패키지/키 미설정 시 graceful skip.

          v1.2.0 강화:
          - 로그 파일 저장 (logs/image_gen_gemini_{ts}.log)
          - 단계별 elapsed time 측정
          - 환경 진단 로그
          - 이미지 PNG 메타 (해상도/크기) 로그

    Args:
        brief_summary: archive 첫 부분 텍스트 (앞 500자 권장)
        out_path: 저장 경로

    Returns:
        Path | None: 저장 경로 또는 실패 시 None
    """
    # ── 0. 로그 파일 설정 ──
    _log_ts = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d_%H%M%S")
    _log_dir = Path(__file__).resolve().parent.parent.parent / "logs"
    _log_file = _log_dir / f"image_gen_gemini_{_log_ts}.log"
    configure_root_logger(log_file=str(_log_file))

    _t_start = time.perf_counter()
    logger.info("=" * 60)
    logger.info(f"[image_gen_gemini] v{VERSION} 시작")
    logger.info(f"[image_gen_gemini] 로그 파일: {_log_file}")
    logger.info("=" * 60)

    # ── 0-1. 환경 진단 로그 ──
    logger.info(f"[image_gen_gemini] GEMINI_API_KEY = {'설정됨 (' + str(len(os.environ.get('GEMINI_API_KEY') or os.environ.get('GOOGLE_API_KEY') or '')) + '자)' if (os.environ.get('GEMINI_API_KEY') or os.environ.get('GOOGLE_API_KEY')) else '없음'}")
    logger.info(f"[image_gen_gemini] GEMINI_IMAGE_MODEL = {os.environ.get('GEMINI_IMAGE_MODEL', '(미설정, default=' + DEFAULT_MODEL + ')')}")
    logger.info(f"[image_gen_gemini] PROMPT_PATH = {PROMPT_PATH}")
    logger.info(f"[image_gen_gemini] 출력 경로 = {out_path}")
    logger.info(f"[image_gen_gemini] 입력 텍스트 길이 = {len(brief_summary):,}자")

    # ── 1. 패키지 체크 ──
    _t_step = time.perf_counter()
    try:
        from google import genai  # type: ignore[import]
        from google.genai import types  # type: ignore[import]
    except ImportError:
        logger.warning("[image_gen_gemini] google-genai 패키지 미설치 — skip")
        logger.info(f"[image_gen_gemini] 종료 (elapsed: {time.perf_counter() - _t_start:.2f}s)")
        return None
    logger.info(f"[image_gen_gemini] [Step 1 패키지 체크] {time.perf_counter() - _t_step:.2f}s")

    # ── 2. API 키 체크 ──
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        logger.warning("[image_gen_gemini] GEMINI_API_KEY/GOOGLE_API_KEY 미설정 — skip")
        logger.info(f"[image_gen_gemini] 종료 (elapsed: {time.perf_counter() - _t_start:.2f}s)")
        return None

    # ── 3. 프롬프트 로드 ──
    _t_step = time.perf_counter()
    template = load_prompt_template()
    if template is None:
        logger.info(f"[image_gen_gemini] 종료 (elapsed: {time.perf_counter() - _t_start:.2f}s)")
        return None
    logger.info(f"[image_gen_gemini] [Step 3 프롬프트 로드] {time.perf_counter() - _t_step:.2f}s ({len(template):,}자)")

    # ── 4. 모델 선택 ──
    model_name = os.environ.get("GEMINI_IMAGE_MODEL", DEFAULT_MODEL)

    # ── 5. 프롬프트 변수 주입 ──
    topic_hint = extract_topic_hint(brief_summary)
    headline = build_english_headline(brief_summary)
    final_prompt = (
        template
        .replace("{TOPIC_HINT}", topic_hint)
        .replace("{ENGLISH_HEADLINE}", headline)
    )

    logger.info(
        f"[image_gen_gemini] model={model_name}, headline={headline!r}, "
        f"topic_hint_len={len(topic_hint)}, final_prompt_len={len(final_prompt):,}"
    )

    # ── 6. API 호출 ──
    _t_llm = time.perf_counter()
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model_name,
            contents=final_prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
            ),
        )
    except Exception as e:
        logger.error(f"[image_gen_gemini] Gemini API 실패: {type(e).__name__}: {e}")
        logger.info(f"[image_gen_gemini] 종료 (elapsed: {time.perf_counter() - _t_start:.2f}s)")
        return None
    logger.info(f"[image_gen_gemini] [Step 6 Gemini API 호출] {time.perf_counter() - _t_llm:.2f}s")

    # ── 7. 이미지 데이터 추출 ──
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
        logger.error(f"[image_gen_gemini] 응답 파싱 실패: {type(e).__name__}: {e}")
        logger.info(f"[image_gen_gemini] 종료 (elapsed: {time.perf_counter() - _t_start:.2f}s)")
        return None

    if not image_bytes:
        logger.warning("[image_gen_gemini] 응답에 이미지 데이터 없음")
        logger.info(f"[image_gen_gemini] 종료 (elapsed: {time.perf_counter() - _t_start:.2f}s)")
        return None

    logger.info(f"[image_gen_gemini] 이미지 바이트 수신: {len(image_bytes):,} bytes")

    # ── 8. 파일 저장 ──
    _t_step = time.perf_counter()
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(image_bytes)
        logger.info(f"[image_gen_gemini] 저장 완료 → {out_path} ({out_path.stat().st_size:,} bytes)")
    except Exception as e:
        logger.error(f"[image_gen_gemini] 파일 저장 실패: {type(e).__name__}: {e}")
        logger.info(f"[image_gen_gemini] 종료 (elapsed: {time.perf_counter() - _t_start:.2f}s)")
        return None
    logger.info(f"[image_gen_gemini] [Step 8 파일 저장] {time.perf_counter() - _t_step:.2f}s")

    # ── 9. 후처리 검증 (해상도·크기) ──
    ok, reason = validate_image_file(out_path)
    if not ok:
        logger.warning(f"[image_gen_gemini] 이미지 검증 실패: {reason}")
        # 손상된 파일 제거 (graceful)
        try:
            out_path.unlink(missing_ok=True)
            logger.info("[image_gen_gemini] 손상된 파일 제거 완료")
        except Exception:
            pass
        logger.info(f"[image_gen_gemini] 종료 (elapsed: {time.perf_counter() - _t_start:.2f}s)")
        return None

    # PNG 해상도 정확히 추출 (이미 validate에서 검증했지만 로그용)
    try:
        with open(out_path, "rb") as f:
            header = f.read(24)
        width = int.from_bytes(header[16:20], "big")
        height = int.from_bytes(header[20:24], "big")
        logger.info(f"[image_gen_gemini] 이미지 해상도: {width}x{height} (비율 {width/height:.3f})")
    except Exception:
        pass

    logger.info("=" * 60)
    logger.info(f"[image_gen_gemini] 완료 (총 elapsed: {time.perf_counter() - _t_start:.2f}s)")
    logger.info("=" * 60)
    return out_path
