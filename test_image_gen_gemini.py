"""
제목: image_gen_gemini 모듈 단위 테스트
내용: Gemini Nano Banana 이미지 생성 모듈의 핵심 로직을 mock 기반 검증.
      실제 Gemini API 호출 없이 모든 분기 검증.

테스트 카테고리:
  - 환경/패키지 체크 분기
  - 프롬프트 로드
  - 영문 헤드라인 규칙
  - 주제 키워드 추출
  - PNG 헤더 파싱 + 비율 검증
  - 메인 함수 통합 흐름
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from publishers.weekly_news_x import image_gen_gemini
from publishers.weekly_news_x.image_gen_gemini import (
    build_english_headline,
    extract_topic_hint,
    generate_header_image_gemini,
    load_prompt_template,
    validate_image_file,
)


# ────────────────────────────────────────────────────────
# Helper: 유효한 PNG 헤더 만들기 (PIL 없이)
# ────────────────────────────────────────────────────────
def _make_png(width: int, height: int, payload_size: int = 60 * 1024) -> bytes:
    """유효한 PNG 시그니처 + IHDR + 더미 IDAT + IEND"""
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data)
    ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + struct.pack(">I", ihdr_crc)
    # 더미 IDAT (실제 압축은 아니지만 파일 크기 채우기용)
    dummy = b"\x00" * payload_size
    idat = struct.pack(">I", len(dummy)) + b"IDAT" + dummy + struct.pack(">I", 0)
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", 0)
    return sig + ihdr + idat + iend


# ────────────────────────────────────────────────────────
# 헤드라인 규칙 (5개)
# ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_headline_bigtech() -> None:
    """빅테크/엔비디아 키워드 → Tech Earnings"""
    text = "엔비디아 어닝 서프라이즈로 빅테크가 동반 상승했다."
    assert build_english_headline(text) == "Tech Earnings Lift Markets"


@pytest.mark.unit
def test_headline_fomc() -> None:
    """FOMC/파월 키워드 → Fed Holds"""
    text = "FOMC 금리 동결. 파월 의장 매파적 발언으로 시장 약세."
    assert build_english_headline(text) == "Fed Holds Rates Steady"


@pytest.mark.unit
def test_headline_currency() -> None:
    """환율 키워드 → Currency Markets"""
    text = "원/달러 1420원 돌파. 외환 당국 구두 개입."
    assert build_english_headline(text) == "Currency Markets in Focus"


@pytest.mark.unit
def test_headline_generic_rally() -> None:
    """상승 키워드만 → Markets Edge Higher"""
    text = "이번 주 시장은 +1.5% 상승 마감."
    assert build_english_headline(text) == "Markets Edge Higher"


@pytest.mark.unit
def test_headline_default_fallback() -> None:
    """매칭 없음 → Weekly Market Brief"""
    text = "특이 사항 없는 평이한 한 주."
    assert build_english_headline(text) == "Weekly Market Brief"


# ────────────────────────────────────────────────────────
# 주제 키워드 추출 (1개)
# ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_extract_topic_hint_splits_on_separator() -> None:
    """'---' 이전 텍스트를 max_len으로 자름"""
    text = "이번 주 시장 요약\n주요 데이터 포함.\n---\n뉴스 1\n---\n뉴스 2"
    result = extract_topic_hint(text, max_len=100)
    assert "뉴스 1" not in result
    assert "이번 주 시장 요약" in result


# ────────────────────────────────────────────────────────
# PNG 검증 (3개)
# ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_validate_image_pass_valid_16_9(tmp_path: Path) -> None:
    """16:9 (1024×576) → 통과"""
    img = tmp_path / "ok.png"
    img.write_bytes(_make_png(1024, 576))
    ok, reason = validate_image_file(img)
    assert ok is True
    assert reason == ""


@pytest.mark.unit
def test_validate_image_fail_wrong_ratio(tmp_path: Path) -> None:
    """1:1 (1024×1024) → 비율 실패"""
    img = tmp_path / "square.png"
    img.write_bytes(_make_png(1024, 1024))
    ok, reason = validate_image_file(img)
    assert ok is False
    assert "aspect_ratio" in reason


@pytest.mark.unit
def test_validate_image_fail_too_small(tmp_path: Path) -> None:
    """50KB 미만 → 실패"""
    img = tmp_path / "tiny.png"
    img.write_bytes(_make_png(1024, 576, payload_size=1024))  # 약 1KB
    ok, reason = validate_image_file(img)
    assert ok is False
    assert "too_small" in reason


# ────────────────────────────────────────────────────────
# 프롬프트 로드 (2개)
# ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_load_prompt_template_returns_text() -> None:
    """실제 prompt 파일이 존재하면 텍스트 반환"""
    result = load_prompt_template()
    assert result is not None
    assert len(result) > 100


@pytest.mark.unit
def test_load_prompt_template_missing(tmp_path: Path) -> None:
    """프롬프트 파일 부재 시 None"""
    missing = tmp_path / "missing.md"
    with patch.object(image_gen_gemini, "PROMPT_PATH", missing):
        result = load_prompt_template()
    assert result is None


# ────────────────────────────────────────────────────────
# 메인 함수 분기 (3개)
# ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_generate_skip_when_no_api_key(tmp_path: Path) -> None:
    """API 키 없으면 None"""
    out = tmp_path / "x.png"
    with patch.dict("os.environ", {}, clear=True):
        result = generate_header_image_gemini("뉴스 요약", out)
    assert result is None


@pytest.mark.unit
def test_generate_skip_when_package_missing(tmp_path: Path) -> None:
    """google-genai 패키지 부재 시 None"""
    out = tmp_path / "x.png"
    env = {"GEMINI_API_KEY": "fake_key"}
    # google.genai import를 ImportError로 강제
    with patch.dict("os.environ", env, clear=False):
        with patch.dict(sys.modules, {"google.genai": None}):
            result = generate_header_image_gemini("뉴스 요약", out)
    assert result is None


@pytest.mark.unit
def test_generate_full_flow_with_mock(tmp_path: Path) -> None:
    """API 호출 mock + 정상 PNG 응답 → 저장 성공"""
    out = tmp_path / "out.png"
    fake_png = _make_png(1024, 576)

    # google.genai 모듈 자체를 mock으로 주입
    fake_genai = MagicMock()
    fake_types = MagicMock()
    fake_client = MagicMock()
    fake_part = MagicMock()
    fake_part.inline_data.data = fake_png
    fake_candidate = MagicMock()
    fake_candidate.content.parts = [fake_part]
    fake_response = MagicMock()
    fake_response.candidates = [fake_candidate]
    fake_client.models.generate_content.return_value = fake_response
    fake_genai.Client.return_value = fake_client

    fake_module = MagicMock()
    fake_module.genai = fake_genai
    fake_module.genai.types = fake_types

    env = {"GEMINI_API_KEY": "fake_key"}
    with patch.dict("os.environ", env, clear=False):
        with patch.dict(sys.modules, {
            "google": fake_module,
            "google.genai": fake_genai,
            "google.genai.types": fake_types,
        }):
            result = generate_header_image_gemini(
                "엔비디아 어닝 서프라이즈로 빅테크 상승.",
                out,
            )

    assert result == out
    assert out.exists()
    assert out.stat().st_size > 50 * 1024


# ────────────────────────────────────────────────────────
# 추가: 미커버 분기 (4개)
# ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_headline_decline_keyword() -> None:
    """하락 키워드 매칭 → Calm Pullback"""
    text = "이번 주 미국 증시는 약세 흐름이었다. fall 마감."
    # decline 키워드 매칭 → "Calm Pullback This Week" 반환
    result = build_english_headline(text)
    assert result == "Calm Pullback This Week"


@pytest.mark.unit
def test_headline_macro_only() -> None:
    """매크로 키워드 단독 (rate만) → Macro Signals Mixed"""
    text = "Treasury rate moves were modest this period."
    result = build_english_headline(text)
    assert result == "Macro Signals Mixed"


@pytest.mark.unit
def test_generate_api_error_returns_none(tmp_path: Path) -> None:
    """Gemini API 호출 자체가 예외 → None 반환"""
    out = tmp_path / "x.png"

    fake_genai = MagicMock()
    fake_types = MagicMock()
    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = RuntimeError("API down")
    fake_genai.Client.return_value = fake_client
    fake_module = MagicMock()
    fake_module.genai = fake_genai

    env = {"GEMINI_API_KEY": "fake_key"}
    with patch.dict("os.environ", env, clear=False):
        with patch.dict(sys.modules, {
            "google": fake_module,
            "google.genai": fake_genai,
            "google.genai.types": fake_types,
        }):
            result = generate_header_image_gemini("뉴스", out)
    assert result is None
    assert not out.exists()


@pytest.mark.unit
def test_generate_no_image_data_returns_none(tmp_path: Path) -> None:
    """응답에 inline_data가 없으면 None"""
    out = tmp_path / "x.png"

    fake_part = MagicMock()
    fake_part.inline_data = None  # 이미지 데이터 없음
    fake_candidate = MagicMock()
    fake_candidate.content.parts = [fake_part]
    fake_response = MagicMock()
    fake_response.candidates = [fake_candidate]

    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = fake_response
    fake_genai = MagicMock()
    fake_genai.Client.return_value = fake_client
    fake_types = MagicMock()
    fake_module = MagicMock()
    fake_module.genai = fake_genai

    env = {"GEMINI_API_KEY": "fake_key"}
    with patch.dict("os.environ", env, clear=False):
        with patch.dict(sys.modules, {
            "google": fake_module,
            "google.genai": fake_genai,
            "google.genai.types": fake_types,
        }):
            result = generate_header_image_gemini("뉴스", out)
    assert result is None
    assert not out.exists()


# ────────────────────────────────────────────────────────
# v1.1.0 신규: 우선순위 + 코드 블록 추출 (3개)
# ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_headline_fomc_priority_over_bigtech() -> None:
    """v1.1.0 핵심 회귀 방지:
    FOMC archive에 '빅테크' 부수 키워드가 있어도 'Fed Holds' 우선 매칭."""
    text = (
        "지난주 FOMC가 매파적 메시지를 전했다. "
        "파월 의장의 발언으로 시장은 약세였다. "
        "다음 주 관전 포인트: 빅테크 가이던스 후속 발표."
    )
    result = build_english_headline(text)
    assert result == "Fed Holds Rates Steady", (
        f"빅테크 키워드가 부수적으로 있어도 FOMC 우선이어야 함. 실제: {result}"
    )


@pytest.mark.unit
def test_load_prompt_extracts_code_block_only(tmp_path: Path) -> None:
    """v1.1.0: 마크다운 메타 정보를 코드 블록만 추출하여 차단"""
    fake_prompt = tmp_path / "fake_prompt.md"
    fake_prompt.write_text(
        "# 메타 헤더\n\n"
        "> 이건 LLM에 전송하면 안 되는 메타 정보\n\n"
        "## 변수 정의 표\n\n"
        "| 변수 | 입력 |\n"
        "|------|------|\n"
        "| `{TOPIC_HINT}` | archive 키워드 |\n\n"
        "## 시스템 프롬프트\n\n"
        "```\n"
        "PROMPT_CONTENT_TO_LLM\n"
        "TOPIC: {TOPIC_HINT}\n"
        "HEADLINE: {ENGLISH_HEADLINE}\n"
        "```\n\n"
        "## 변경 이력\n\n"
        "이 부분도 누출되면 안 됨.\n",
        encoding="utf-8",
    )
    with patch.object(image_gen_gemini, "PROMPT_PATH", fake_prompt):
        result = load_prompt_template()

    assert result is not None
    # 코드 블록 안의 내용만 포함
    assert "PROMPT_CONTENT_TO_LLM" in result
    assert "{TOPIC_HINT}" in result
    assert "{ENGLISH_HEADLINE}" in result
    # 메타 정보는 제외
    assert "메타 헤더" not in result
    assert "변수 정의 표" not in result
    assert "변경 이력" not in result
    assert "이 부분도 누출되면" not in result


@pytest.mark.unit
def test_load_prompt_no_code_block_returns_none(tmp_path: Path) -> None:
    """v1.1.0: 코드 블록이 없는 프롬프트 파일은 None 반환"""
    fake_prompt = tmp_path / "no_code_block.md"
    fake_prompt.write_text(
        "# 헤더만 있고\n\n코드 블록이 없는 문서.\n",
        encoding="utf-8",
    )
    with patch.object(image_gen_gemini, "PROMPT_PATH", fake_prompt):
        result = load_prompt_template()
    assert result is None


# ────────────────────────────────────────────────────────
# v1.1.1 신규: 매칭 범위 한정 회귀 방지 (1개)
# ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_headline_main_topic_priority_over_body_macro() -> None:
    """v1.1.1 핵심 회귀 방지:
    시장 요약(메인 토픽)은 빅테크이고, 본문에 부수적으로 '금리'·'환율'이 등장해도
    메인 토픽(빅테크) 우선이어야 함."""
    text = """# 2026-05-23 미국 증시 주간 브리핑

## 시장 요약

이번 주 미국 증시는 빅테크 실적 호조에 힘입어 강세를 보였다.
나스닥은 +1.8%로 마감했으며, S&P 500도 +1.1% 상승했다.

---

### 3) 매크로 환경

미국 10년물 금리는 4.42%로 소폭 하락했고, 달러 인덱스는 안정세를 유지했다.
원/달러는 1,395원 수준에서 거래되었다.
"""
    result = build_english_headline(text)
    assert result == "Tech Earnings Lift Markets", (
        f"메인 토픽(빅테크)이 본문의 부수적 매크로 데이터에 의해 덮이면 안 됨. 실제: {result}"
    )
