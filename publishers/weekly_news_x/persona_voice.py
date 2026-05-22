"""
제목: 일상 텍스트 페르소나 한줄평 추가 모듈 (옵션)
내용: 한국 직장인 페르소나(내부 식별자 "도윤")의 일상 톤으로
      archive 마크다운 끝에 짧은 일기 한 청크를 추가한다.

      마커·헤더·이모지 없이 순수 일상 텍스트만 출력하며,
      X 스레드 발행 시 별도 청크로 자연스럽게 섞인다.

      APPEND_PERSONA_VOICE=true 환경변수일 때만 동작.
      `{archive_name}.persona.json` sidecar 파일로 중복 추가 방지.

      comic_voice.py와 격리: 마커가 다르며(없음), sidecar 경로도 다름.

주요 함수:
  - select_variation_seed(): 시작·종결 패턴 무작위 선택
  - generate_persona_voice(news_summary, seed): Claude API 호출로 한줄평 생성
  - validate_output(text): 길이·금기 어휘·마커 후처리 검증
  - is_already_added(archive_path): sidecar 존재 여부로 중복 체크
  - write_persona_sidecar(archive_path, voice_block, seed): sidecar JSON 작성
  - append_to_archive(md_path, voice_block): 마크다운 끝에 '---' + 텍스트 추가
  - main(): 위 단계 일괄 실행

연관 문서:
  - docs/persona_doyun_character_sheet.md (작가용 내부 시트)
  - publishers/weekly_news_x/prompts/persona_voice.md (LLM 프롬프트)
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic

from config.settings import get_env_bool
from core.logger import configure_root_logger, get_logger

VERSION = "1.1.0"

logger = get_logger(__name__)

# ────────────────────────────────────────────────────────
# 모듈 상수
# ────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
PROMPT_PATH = _HERE / "prompts" / "persona_voice.md"
ARCHIVE_ROOT = _HERE.parent.parent / "logs" / "weekly_news"
MODEL = "claude-sonnet-4-5"
SIDECAR_SUFFIX = ".persona.json"

# 출력 검증 임계값
MIN_LENGTH = 60
MAX_LENGTH = 180

# 안티봇 변동 패턴
OPENING_PATTERNS = [
    "data_first",
    "daily_first",
    "time_first",
    "news_first",
    "self_first",
]
CLOSING_PATTERNS = [
    "self_humor",
    "next_week",
    "one_line_conclusion",
    "silence",
    "price_compare",
]

# LLM이 잘못 출력했을 때 차단할 어휘
FORBIDDEN_CHARS = ["🧑\u200d💻", "🎭", "**", "##", '"', "#", "[", "]"]

# 어휘 차원 금기 키워드
FORBIDDEN_WORDS = [
    # 정치
    "대통령", "국회", "여당", "야당", "정당", "선거", "의원", "정치",
    # 젠더
    "페미", "젠더",
    # 종교
    "예배", "교회", "종교", "기도",
    # 사적
    "결혼", "출산", "미혼",
    # 권유 어휘
    "사세요", "매수하세요", "추천합니다",
    # 단정 어휘 일부
    "반드시 오른다", "확실히 폭락",
]


def select_variation_seed() -> dict:
    """
    제목: 안티봇 변동 시드 선택
    내용: 시작·종결 패턴을 무작위로 선택하고 자조성 유머 포함 여부를 결정.

    Returns:
        dict: {opening_pattern, closing_pattern, humor_flag, temperature}
    """
    opening = random.choice(OPENING_PATTERNS)
    closing = random.choice(CLOSING_PATTERNS)
    # 자조성 유머는 25~35% 빈도
    humor_flag = random.random() < 0.30
    # Claude API 온도는 0.7 ± 0.15
    temperature = round(0.7 + (random.random() - 0.5) * 0.30, 2)

    return {
        "opening_pattern": opening,
        "closing_pattern": closing,
        "humor_flag": humor_flag,
        "temperature": temperature,
    }


def validate_output(text: str) -> tuple[bool, str]:
    """
    제목: LLM 출력 후처리 검증
    내용: 길이·금기 어휘·마커 차단. 검증 실패 시 archive 무변경 처리에 사용.

    Args:
        text: LLM이 반환한 텍스트

    Returns:
        tuple[bool, str]: (통과 여부, 실패 사유)
    """
    if not text or not text.strip():
        return False, "empty_output"

    stripped = text.strip()

    # 길이 체크
    length = len(stripped)
    if length < MIN_LENGTH or length > MAX_LENGTH:
        return False, f"length_out_of_range({length}자)"

    # 마커·이모지·마크다운 차단
    for c in FORBIDDEN_CHARS:
        if c in stripped:
            return False, f"forbidden_char({c!r})"

    # 금기 어휘 차단
    for w in FORBIDDEN_WORDS:
        if w in stripped:
            return False, f"forbidden_word({w!r})"

    return True, ""


def generate_persona_voice(news_summary: str, seed: dict) -> str | None:
    """
    제목: 페르소나 일상 텍스트 한줄평 생성
    내용: 시스템 프롬프트(persona_voice.md)에 따라 일상 한줄평을 생성하여
          순수 텍스트로 반환. 마커·헤더·따옴표 없음.

    Args:
        news_summary: 그날 뉴스 요약 (앞 2000자만 사용)
        seed: select_variation_seed() 결과

    Returns:
        str | None: 일상 텍스트 또는 실패 시 None
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("[persona_voice] ANTHROPIC_API_KEY 미설정")
        return None

    try:
        system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.error(f"[persona_voice] 프롬프트 파일 없음: {PROMPT_PATH}")
        return None

    # 시드를 프롬프트에 주입
    system_prompt = (
        system_prompt
        .replace("{OPENING_PATTERN}", seed["opening_pattern"])
        .replace("{CLOSING_PATTERN}", seed["closing_pattern"])
        .replace("{HUMOR_FLAG}", "true" if seed["humor_flag"] else "false")
    )

    client = anthropic.Anthropic(api_key=api_key)
    user_msg = (
        "다음은 오늘의 미국 주요뉴스 요약이다. "
        "이를 참고하여 일상 텍스트 한줄평을 작성하라.\n\n"
        f"{news_summary[:2000]}"
    )

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=512,
            temperature=seed["temperature"],
            system=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )
    except Exception as e:
        logger.error(f"[persona_voice] Claude API 실패: {type(e).__name__}: {e}")
        return None

    for block in response.content:
        if block.type == "text":
            return block.text.strip()
    return None


def _sidecar_path(archive_path: Path) -> Path:
    """제목: archive 옆 sidecar JSON 경로 계산"""
    return archive_path.with_suffix(archive_path.suffix + SIDECAR_SUFFIX)


def is_already_added(archive_path: Path) -> bool:
    """
    제목: 중복 추가 여부 체크
    내용: `{archive_name}.persona.json` sidecar 파일 존재 여부로 판단.

    Args:
        archive_path: archive .md 파일 경로

    Returns:
        bool: 이미 추가됨 → True
    """
    return _sidecar_path(archive_path).exists()


def write_persona_sidecar(archive_path: Path, voice_block: str, seed: dict) -> Path:
    """
    제목: persona sidecar JSON 작성
    내용: 어떤 패턴으로 어떤 길이로 추가되었는지 기록.
          향후 분석 데이터로 활용 가능.

    Args:
        archive_path: archive .md 파일 경로
        voice_block: 추가된 텍스트
        seed: 사용된 시드

    Returns:
        Path: 작성된 sidecar 경로
    """
    sc_path = _sidecar_path(archive_path)
    payload = {
        "version": VERSION,
        "added_at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
        "model": MODEL,
        "opening_pattern": seed["opening_pattern"],
        "closing_pattern": seed["closing_pattern"],
        "humor_flag": seed["humor_flag"],
        "temperature": seed["temperature"],
        "char_count": len(voice_block.strip()),
    }
    sc_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return sc_path


def append_to_archive(md_path: Path, voice_block: str) -> bool:
    """
    제목: archive 마크다운 끝에 일상 텍스트 청크 추가
    내용: '---' 구분자 + 빈 줄 + 텍스트 + 빈 줄 형태로 추가.
          publish.py의 '---' 분할 로직이 별도 청크로 인식한다.

    Args:
        md_path: archive .md 경로
        voice_block: 추가할 일상 텍스트

    Returns:
        bool: 성공 여부
    """
    try:
        original = md_path.read_text(encoding="utf-8")
        merged = original.rstrip() + "\n\n---\n\n" + voice_block.strip() + "\n"
        md_path.write_text(merged, encoding="utf-8")
        logger.info(f"[persona_voice] 추가 완료 → {md_path}")
        return True
    except Exception as e:
        logger.error(f"[persona_voice] append 실패: {type(e).__name__}: {e}")
        return False


def main() -> int:
    """
    제목: 페르소나 한줄평 추가 엔트리포인트
    내용: APPEND_PERSONA_VOICE=true 시에만 동작. 미설정 시 0 반환(skip).

          PERSONA_DRY_RUN=true 시: 생성만 하고 archive 무변경(테스트용).

          v1.1.0: 로그 파일 저장 추가
          - logs/persona_voice_{YYYYMMDD_HHMMSS}.log
          - 처리 시간 측정, 모든 단계별 elapsed time 기록
          - archive 변경 전후 비교 로그

    Returns:
        int: 0 성공/skip, 1 실패
    """
    # ── 0. 로그 파일 설정 (콘솔 + 파일 동시 출력) ──
    _log_ts = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d_%H%M%S")
    _log_dir = Path(__file__).resolve().parent.parent.parent / "logs"
    _log_file = _log_dir / f"persona_voice_{_log_ts}.log"
    configure_root_logger(log_file=str(_log_file))

    _t_start = time.perf_counter()
    logger.info("=" * 60)
    logger.info(f"[persona_voice] v{VERSION} 시작")
    logger.info(f"[persona_voice] 로그 파일: {_log_file}")
    logger.info("=" * 60)

    # ── 0-1. 환경 진단 로그 ──
    logger.info(f"[persona_voice] PYTHON: {sys.version.split()[0]}")
    logger.info(f"[persona_voice] APPEND_PERSONA_VOICE = {os.environ.get('APPEND_PERSONA_VOICE', '(미설정)')}")
    logger.info(f"[persona_voice] PERSONA_DRY_RUN = {os.environ.get('PERSONA_DRY_RUN', '(미설정)')}")
    logger.info(f"[persona_voice] ANTHROPIC_API_KEY = {'설정됨 (' + str(len(os.environ.get('ANTHROPIC_API_KEY', ''))) + '자)' if os.environ.get('ANTHROPIC_API_KEY') else '없음'}")
    logger.info(f"[persona_voice] PROMPT_PATH = {PROMPT_PATH}")
    logger.info(f"[persona_voice] ARCHIVE_ROOT = {ARCHIVE_ROOT}")
    logger.info(f"[persona_voice] MODEL = {MODEL}")
    logger.info(f"[persona_voice] SIDECAR_SUFFIX = {SIDECAR_SUFFIX}")

    # ── 1. 활성화 체크 ──
    if not get_env_bool("APPEND_PERSONA_VOICE", default=False):
        logger.info("[persona_voice] APPEND_PERSONA_VOICE 미설정 또는 false — skip")
        logger.info(f"[persona_voice] 종료 (elapsed: {time.perf_counter() - _t_start:.2f}s)")
        return 0

    # ── 2. archive 탐색 ──
    _t_step = time.perf_counter()
    if not ARCHIVE_ROOT.exists():
        logger.error(f"[persona_voice] archive 디렉토리 없음: {ARCHIVE_ROOT}")
        logger.info(f"[persona_voice] 종료 (elapsed: {time.perf_counter() - _t_start:.2f}s)")
        return 1

    candidates = sorted(
        ARCHIVE_ROOT.rglob("*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    logger.info(f"[persona_voice] archive 후보 수: {len(candidates)}")
    if not candidates:
        logger.error("[persona_voice] archive .md 없음")
        logger.info(f"[persona_voice] 종료 (elapsed: {time.perf_counter() - _t_start:.2f}s)")
        return 1

    md_path = candidates[0]
    file_mtime = datetime.fromtimestamp(md_path.stat().st_mtime, ZoneInfo("Asia/Seoul"))
    file_size = md_path.stat().st_size
    logger.info(f"[persona_voice] 대상 archive: {md_path}")
    logger.info(f"[persona_voice]   - mtime: {file_mtime.isoformat()}")
    logger.info(f"[persona_voice]   - size: {file_size:,} bytes")
    logger.info(f"[persona_voice] [Step 2 archive 탐색] {time.perf_counter() - _t_step:.2f}s")

    # ── 3. 중복 체크 ──
    if is_already_added(md_path):
        logger.info(f"[persona_voice] sidecar 이미 존재 ({_sidecar_path(md_path).name}) — skip")
        logger.info(f"[persona_voice] 종료 (elapsed: {time.perf_counter() - _t_start:.2f}s)")
        return 0

    # ── 4. 시드 선택 + LLM 호출 ──
    _t_step = time.perf_counter()
    seed = select_variation_seed()
    logger.info(
        f"[persona_voice] 시드: opening={seed['opening_pattern']}, "
        f"closing={seed['closing_pattern']}, "
        f"humor={seed['humor_flag']}, temp={seed['temperature']}"
    )

    summary = md_path.read_text(encoding="utf-8")
    logger.info(f"[persona_voice] archive 본문 읽기 완료 ({len(summary):,}자)")

    _t_llm = time.perf_counter()
    voice = generate_persona_voice(summary, seed)
    logger.info(f"[persona_voice] [Step 4 Claude API 호출] {time.perf_counter() - _t_llm:.2f}s")
    if not voice:
        logger.warning("[persona_voice] 생성 실패 — archive 무변경")
        logger.info(f"[persona_voice] 종료 (elapsed: {time.perf_counter() - _t_start:.2f}s)")
        return 0

    # ── 5. 후처리 검증 ──
    ok, reason = validate_output(voice)
    if not ok:
        logger.warning(f"[persona_voice] 검증 실패: {reason} — archive 무변경")
        logger.warning(f"[persona_voice]   생성된 텍스트 (검증 실패):\n{voice}")
        logger.info(f"[persona_voice] 종료 (elapsed: {time.perf_counter() - _t_start:.2f}s)")
        return 0

    logger.info(f"[persona_voice] 생성·검증 통과 ({len(voice.strip())}자)")
    logger.info(f"[persona_voice] 생성된 텍스트:\n----- 시작 -----\n{voice}\n----- 끝 -----")

    # ── 6. DRY_RUN 분기 ──
    if get_env_bool("PERSONA_DRY_RUN", default=False):
        logger.info("[persona_voice] PERSONA_DRY_RUN=true — archive 무변경, 결과 로그만")
        logger.info(f"[persona_voice] 종료 (DRY_RUN, elapsed: {time.perf_counter() - _t_start:.2f}s)")
        return 0

    # ── 7. archive 추가 + sidecar 작성 ──
    _t_step = time.perf_counter()
    size_before = md_path.stat().st_size
    if not append_to_archive(md_path, voice):
        logger.error("[persona_voice] archive 추가 실패")
        logger.info(f"[persona_voice] 종료 (elapsed: {time.perf_counter() - _t_start:.2f}s)")
        return 1
    size_after = md_path.stat().st_size
    logger.info(f"[persona_voice] archive 크기 변화: {size_before:,} → {size_after:,} bytes (+{size_after - size_before:,})")
    logger.info(f"[persona_voice] [Step 7 archive 추가] {time.perf_counter() - _t_step:.2f}s")

    try:
        sc_path = write_persona_sidecar(md_path, voice, seed)
        logger.info(f"[persona_voice] sidecar 작성: {sc_path.name} ({sc_path.stat().st_size} bytes)")
    except Exception as e:
        logger.error(f"[persona_voice] sidecar 작성 실패: {type(e).__name__}: {e}")
        # archive는 이미 변경됨. 다음 실행 시 중복 추가 가능성 있으므로 1 반환.
        logger.info(f"[persona_voice] 종료 (elapsed: {time.perf_counter() - _t_start:.2f}s)")
        return 1

    logger.info("=" * 60)
    logger.info(f"[persona_voice] 완료 (총 elapsed: {time.perf_counter() - _t_start:.2f}s)")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
