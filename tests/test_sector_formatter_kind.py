"""
제목: sector_formatter_kind 모듈 단위 테스트
내용: sector 친근 톤 헬퍼 모듈의 핵심 로직을 mock 기반 검증.
      Gemini API 호출 없이 동작 검증.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from publishers import sector_formatter_kind
from publishers.sector_formatter_kind import (
    ROTATION_HEADERS,
    format_tg_free_kind,
    format_tg_paid_kind,
    validate_sector_kind_output,
)


def _signal(rotation_type="DEFENSIVE_ROTATION", level="L2",
            spread_5d=2.1, def_avg_5d=1.5, cyc_avg_5d=-0.6, spread_1d=0.3):
    """제목: 테스트용 SectorSignal stub"""
    return SimpleNamespace(
        rotation_type=rotation_type,
        level=level,
        spread_5d=spread_5d,
        def_avg_5d=def_avg_5d,
        cyc_avg_5d=cyc_avg_5d,
        spread_1d=spread_1d,
        health_score=0.92,
        rows_used=5,
        shadow_mode=False,
        policy_version="1.0.0",
        alert_id="SECTOR-20260523-1115-abc123",
    )


def _mock_gemini(text: str):
    fake_response = MagicMock()
    fake_response.text = text
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = fake_response
    fake_genai = MagicMock()
    fake_genai.Client.return_value = fake_client
    return fake_genai


# ────────────────────────────────────────────────────────
# 검증 로직 (6개)
# ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_validate_pass_normal() -> None:
    """정상 sector 메시지 → 통과 (200~1200자, 자기 행위 + 대화 유도)"""
    text = (
        "🌧️ <b>시장이 조심스러운 분위기예요</b>\n"
        "<i>— 최근 감지</i>\n\n"
        "지난 5일 동안 방어주 쪽으로 자금이 좀 옮겨갔어요.\n"
        "건강/유틸리티/필수소비재 같은 안정 섹터가 +1.5%p 오르는 동안,\n"
        "산업/리츠/소재 같은 경기 민감 섹터는 -0.6%p로 살짝 빠졌네요.\n\n"
        "📊 <b>지난 5일 흐름</b>\n"
        "  • 방어주: +1.5%p\n"
        "  • 경기민감: -0.6%p\n\n"
        "📅 <b>오늘 하루</b>: +0.3%p\n\n"
        "이런 분위기엔 마음이 좀 차분해지네요.\n"
        "여러분의 오늘 시장은 어떠셨어요?"
    )
    ok, reason = validate_sector_kind_output(text)
    assert ok is True, f"실패 사유: {reason}"


@pytest.mark.unit
def test_validate_length_too_short() -> None:
    """200자 미만 → fail"""
    text = "🌧️ 짧음. 저는 그냥 봐요."
    ok, reason = validate_sector_kind_output(text)
    assert ok is False
    assert "tg_length_out_of_range" in reason


@pytest.mark.unit
def test_validate_forbidden_threat() -> None:
    """위협 어휘 '폭락' → fail"""
    text = (
        "🌧️ 시장이 폭락했어요\n\n"
        + ("방어주가 좀 올랐어요. " * 20)
        + "\n여러분은 어떠세요?"
    )
    ok, reason = validate_sector_kind_output(text)
    assert ok is False
    assert "forbidden_word" in reason


@pytest.mark.unit
def test_validate_threat_safe_word() -> None:
    """'분위기'에서 '위기' false positive 회피 확인"""
    text = (
        "🌧️ 시장이 조심스러운 분위기예요\n\n"
        "오늘은 방어주 쪽으로 자금이 좀 움직였어요.\n"
        "지난 5일 흐름은 방어주가 +1.5%p, 경기민감이 -0.6%p 정도네요.\n"
        "이런 흐름이 한 번 더 이어질지는 좀 더 봐야 할 것 같아요.\n"
        "오늘 하루만 보면 +0.3%p로 잠깐 약간만 변동이 있었어요.\n"
        "조심스러운 분위기 속에서도 흐름은 차분한 편이었네요.\n\n"
        "저는 그냥 흐름만 한 번 짚고 넘기려고요.\n"
        "여러분은 오늘 어떠셨어요?"
    )
    ok, reason = validate_sector_kind_output(text)
    assert ok is True, f"실패 사유: {reason}"


@pytest.mark.unit
def test_validate_missing_disclaimer() -> None:
    """면책 키워드 누락 → fail"""
    text = (
        "🌧️ 시장이 조심스러운 분위기예요\n\n"
        + ("방어주가 좀 올랐어요. 경기민감주는 살짝 빠졌네요. " * 5)
        + "\n흐름이 조금 바뀐 것 같아요."
    )
    ok, reason = validate_sector_kind_output(text)
    assert ok is False
    assert reason == "missing_disclaimer"


@pytest.mark.unit
def test_validate_non_korean() -> None:
    """비한국어 → fail"""
    text = (
        "🌧️ 시장이 조심스러운 분위기예요\n\n"
        + ("방어주가 좀 올랐어요. " * 20)
        + "です. 다들 어떠세요?"
    )
    ok, reason = validate_sector_kind_output(text)
    assert ok is False
    assert reason == "non_korean_char"


# ────────────────────────────────────────────────────────
# format_tg_free_kind / format_tg_paid_kind 흐름 (5개)
# ────────────────────────────────────────────────────────

SIM_GOOD = (
    "🌧️ <b>시장이 조심스러운 분위기예요</b>\n"
    "<i>— 최근 감지</i>\n\n"
    "지난 5일 동안 방어주 쪽으로 자금이 좀 옮겨갔어요.\n"
    "건강/유틸리티/필수소비재 같은 안정 섹터가 +1.5%p 오르는 동안,\n"
    "산업/리츠/소재 같은 경기 민감 섹터는 -0.6%p로 살짝 빠졌네요.\n\n"
    "📊 <b>지난 5일 흐름</b>\n"
    "  • 방어주: +1.5%p\n"
    "  • 경기민감: -0.6%p\n\n"
    "📅 <b>오늘 하루</b>: +0.3%p\n\n"
    "이런 분위기엔 마음이 좀 차분해지네요.\n"
    "여러분의 오늘 시장은 어떠셨어요?"
)


@pytest.mark.unit
def test_format_tg_free_success() -> None:
    """정상 Gemini 응답 → free 메시지 반환"""
    fake_genai = _mock_gemini(SIM_GOOD)
    with patch.dict("os.environ", {"GEMINI_API_KEY": "fake"}, clear=False):
        with patch.dict(sys.modules, {
            "google": MagicMock(genai=fake_genai),
            "google.genai": fake_genai,
        }):
            result = format_tg_free_kind(_signal())
    assert result is not None
    assert "시장이 조심스러운 분위기예요" in result


@pytest.mark.unit
def test_format_tg_paid_success() -> None:
    """정상 Gemini 응답 → paid 메시지 반환"""
    fake_genai = _mock_gemini(SIM_GOOD)
    with patch.dict("os.environ", {"GEMINI_API_KEY": "fake"}, clear=False):
        with patch.dict(sys.modules, {
            "google": MagicMock(genai=fake_genai),
            "google.genai": fake_genai,
        }):
            result = format_tg_paid_kind(_signal(rotation_type="RISK_ON_ROTATION"))
    assert result is not None
    assert "분위기예요" in result or "어떠셨어요" in result


@pytest.mark.unit
def test_format_tg_free_no_api_key_returns_none() -> None:
    """API 키 없으면 None"""
    with patch.dict("os.environ", {}, clear=True):
        result = format_tg_free_kind(_signal())
    assert result is None


@pytest.mark.unit
def test_format_tg_free_validation_fail_returns_none() -> None:
    """LLM이 위협 어휘 출력 → 검증 실패 → None"""
    bad_text = (
        "🌧️ 시장이 폭락했어요\n\n"
        + ("방어주가 좀 올랐어요. " * 20)
        + "\n저는 그냥 봐요."
    )
    fake_genai = _mock_gemini(bad_text)
    with patch.dict("os.environ", {"GEMINI_API_KEY": "fake"}, clear=False):
        with patch.dict(sys.modules, {
            "google": MagicMock(genai=fake_genai),
            "google.genai": fake_genai,
        }):
            result = format_tg_free_kind(_signal())
    assert result is None


@pytest.mark.unit
def test_format_tg_free_api_exception_returns_none() -> None:
    """Gemini API 예외 → None"""
    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = RuntimeError("API down")
    fake_genai = MagicMock()
    fake_genai.Client.return_value = fake_client

    with patch.dict("os.environ", {"GEMINI_API_KEY": "fake"}, clear=False):
        with patch.dict(sys.modules, {
            "google": MagicMock(genai=fake_genai),
            "google.genai": fake_genai,
        }):
            result = format_tg_free_kind(_signal())
    assert result is None


# ────────────────────────────────────────────────────────
# 상수 안정성 (3개)
# ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_rotation_headers_defined() -> None:
    """ROTATION_HEADERS 4종 모두 정의"""
    assert "DEFENSIVE_ROTATION" in ROTATION_HEADERS
    assert "RISK_ON_ROTATION" in ROTATION_HEADERS
    assert "ROTATION_WATCH_DEF" in ROTATION_HEADERS
    assert "ROTATION_WATCH_RISK" in ROTATION_HEADERS


@pytest.mark.unit
def test_version_string_is_set() -> None:
    """VERSION 상수 명시"""
    assert isinstance(sector_formatter_kind.VERSION, str)
    assert sector_formatter_kind.VERSION.startswith("1.")


@pytest.mark.unit
def test_disclaimer_keywords_imported_from_alert_formatter_kind() -> None:
    """alert_formatter_kind의 면책 상수 재사용 확인 (DRY)"""
    from publishers.alert_formatter_kind import _DISCLAIMER_KEYWORDS as src
    assert sector_formatter_kind._DISCLAIMER_KEYWORDS is src
