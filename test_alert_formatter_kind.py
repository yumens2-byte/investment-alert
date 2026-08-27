"""
제목: alert_formatter_kind 모듈 단위 테스트
내용: 친근 톤 헬퍼 모듈의 핵심 로직을 mock 기반 검증.
      Claude/Gemini API 호출 없이 동작 검증.

테스트 카테고리:
  - 검증 로직 (길이/금기어/면책/비한국어/채널)
  - 영문→한국어 번역 (캐시 hit/miss, API 실패 fallback)
  - format_x_kind / format_tg_kind 흐름 (L1=Claude / L2/L3=Gemini)
  - 검증 실패 시 None 반환
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from publishers import alert_formatter_kind
from publishers.alert_formatter_kind import (
    LEVEL_HEADERS,
    format_tg_kind,
    format_x_kind,
    translate_news_to_kr,
    validate_kind_output,
)

# ────────────────────────────────────────────────────────
# 검증 로직 (10개)
# ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_validate_x_pass_normal() -> None:
    """정상 X 메시지 → 통과"""
    text = (
        "🔔 잠깐 살펴봐요\n\n"
        "오늘 미국 시장이 평소보다 좀 흔들렸어요.\n"
        "나스닥이 -1.5% 정도 빠졌네요.\n"
        "투자 권유는 아니에요. 차근차근 같이 봐요.\n"
        "#미국증시 #시장경보"
    )
    ok, reason = validate_kind_output(text, "x")
    assert ok is True
    assert reason == ""


@pytest.mark.unit
def test_validate_x_length_too_short() -> None:
    """X 80자 미만 → fail"""
    ok, reason = validate_kind_output("짧아요. 참고만 해주세요.", "x")
    assert ok is False
    assert "length" in reason


@pytest.mark.unit
def test_validate_x_length_too_long() -> None:
    """X 270자 초과 → fail"""
    text = ("오늘 시장 메모예요. " * 30) + " 참고만 해주세요."
    ok, reason = validate_kind_output(text, "x")
    assert ok is False
    assert "length" in reason


@pytest.mark.unit
def test_validate_forbidden_threat_word() -> None:
    """위협 어휘 '폭락' → fail"""
    text = (
        "🔔 잠깐 살펴봐요\n\n"
        "오늘 시장이 폭락했어요.\n"
        "나스닥이 많이 빠졌네요.\n"
        "투자 권유는 아니에요. 차근차근 봐요.\n"
        "#미국증시"
    )
    ok, reason = validate_kind_output(text, "x")
    assert ok is False
    assert "forbidden_word" in reason


@pytest.mark.unit
def test_validate_forbidden_politics() -> None:
    """정치 어휘 차단"""
    text = (
        "🔔 잠깐 살펴봐요\n\n"
        "대통령이 한마디 했고 시장이 흔들렸어요.\n"
        "나스닥이 빠졌네요.\n"
        "참고만 해주세요.\n"
        "#미국증시"
    )
    ok, reason = validate_kind_output(text, "x")
    assert ok is False
    assert "forbidden_word" in reason


@pytest.mark.unit
def test_validate_forbidden_recommend() -> None:
    """매수 권유 차단"""
    text = (
        "🔔 잠깐 살펴봐요\n\n"
        "오늘 시장이 흔들렸어요. 지금 매수하세요.\n"
        "참고만 해주세요.\n"
        "#미국증시 #시장경보"
    )
    ok, reason = validate_kind_output(text, "x")
    assert ok is False
    assert "forbidden_word" in reason


@pytest.mark.unit
def test_validate_missing_disclaimer() -> None:
    """면책 키워드 누락 → fail (v1.1.0 신규 키워드 모두 회피하여 진짜 면책 부재 검증)"""
    text = (
        "🔔 잠깐 살펴봐요\n\n"
        "오늘 미국 시장 흐름을 정리하자면,\n"
        "변동성이 평소보다 조금 컸어요.\n"
        "그리고 채권 금리도 좀 움직였습니다.\n"
        "#미국증시 #시장경보"
    )
    ok, reason = validate_kind_output(text, "x")
    assert ok is False
    assert reason == "missing_disclaimer", f"실제 reason: {reason}"


@pytest.mark.unit
def test_validate_non_korean_char() -> None:
    """비한국어 문자(일본 가나 등) → fail"""
    text = (
        "🔔 잠깐 살펴봐요\n\n"
        "오늘 시장이 흔들렸어요 です.\n"
        "나스닥이 빠졌네요.\n"
        "참고만 해주세요.\n"
        "#미국증시"
    )
    ok, reason = validate_kind_output(text, "x")
    assert ok is False
    assert reason == "non_korean_char"


@pytest.mark.unit
def test_validate_unknown_channel() -> None:
    """알 수 없는 channel → fail"""
    ok, reason = validate_kind_output("아무 텍스트", "unknown")
    assert ok is False
    assert "unknown_channel" in reason


@pytest.mark.unit
def test_validate_tg_pass_normal() -> None:
    """정상 TG 메시지 → 통과"""
    text = (
        "🔔 <b>잠깐 살펴봐요</b>\n"
        "<i>— 방금 감지</i>\n\n"
        "오늘 미국 시장이 평소보다 많이 흔들렸어요.\n"
        "변동성 지표가 좀 올라갔고요.\n"
        "S&P 500은 -2.5% 마감했네요.\n"
        "연준이 금리 정책에 단호한 분위기인 것 같아요.\n\n"
        "📰 <b>오늘의 뉴스</b>\n"
        "  • 연준이 금리에 좀 더 단호한 분위기예요\n"
        "  • 미국 채권 금리가 다시 올랐네요\n"
        "  • S&P 500이 약세로 마감했어요\n\n"
        "투자 권유는 아니에요. 차근차근 같이 봐요.\n"
        "<code>ID: ALERT-20</code>"
    )
    ok, reason = validate_kind_output(text, "tg")
    assert ok is True, f"검증 실패 사유: {reason}"
    assert reason == ""


# ────────────────────────────────────────────────────────
# 영문→한국어 번역 (4개)
# ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_translate_empty_list_returns_empty() -> None:
    """빈 리스트 → 빈 리스트"""
    assert translate_news_to_kr([]) == []


@pytest.mark.unit
def test_translate_korean_passes_through(tmp_path: Path) -> None:
    """한국어 50% 이상이면 번역 스킵, 원본 반환"""
    titles = ["코스피 -1.2% 마감했어요"]
    result = translate_news_to_kr(titles)
    assert result == titles


@pytest.mark.unit
def test_translate_no_api_key_returns_original() -> None:
    """API 키 없으면 원문 그대로 (캐시 hit도 없을 때)"""
    titles = ["Fed Hints at More Aggressive Tightening Path"]
    with patch.dict("os.environ", {}, clear=True):
        with patch.object(alert_formatter_kind, "TRANSLATE_CACHE_DIR", Path("/tmp/nocache_test")):
            # 캐시 비우기
            import shutil
            shutil.rmtree("/tmp/nocache_test", ignore_errors=True)
            result = translate_news_to_kr(titles)
    assert result == titles


@pytest.mark.unit
def test_translate_cache_hit(tmp_path: Path) -> None:
    """캐시 fresh → API 호출 없이 캐시 사용"""
    titles = ["Fed Hints Cache Test"]
    with patch.object(alert_formatter_kind, "TRANSLATE_CACHE_DIR", tmp_path):
        # 캐시 미리 작성
        cache_path = alert_formatter_kind._translate_cache_path(titles[0])
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            '{"src": "Fed Hints Cache Test", "kr": "연준이 캐시된 번역이에요", "model": "test"}',
            encoding="utf-8",
        )
        # API 호출이 일어나면 안 됨을 검증
        with patch.dict("os.environ", {"GEMINI_API_KEY": "fake"}, clear=False):
            result = translate_news_to_kr(titles)
    assert result == ["연준이 캐시된 번역이에요"]


# ────────────────────────────────────────────────────────
# format_x_kind 흐름 (5개)
# ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_format_x_kind_l1_uses_claude() -> None:
    """L1은 Claude 호출"""
    fake_anthropic_module = MagicMock()
    fake_client = MagicMock()
    fake_block = MagicMock()
    fake_block.type = "text"
    fake_block.text = (
        "🔔 잠깐 살펴봐요\n\n"
        "오늘 미국 시장이 평소보다 많이 흔들렸어요.\n"
        "나스닥이 -1.8% 정도 빠졌네요.\n"
        "참고만 해주세요. 차근차근 같이 봐요.\n"
        "#미국증시 #시장경보"
    )
    fake_response = MagicMock()
    fake_response.content = [fake_block]
    fake_client.messages.create.return_value = fake_response
    fake_anthropic_module.Anthropic.return_value = fake_client

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "fake"}, clear=False):
        with patch.dict(sys.modules, {"anthropic": fake_anthropic_module}):
            # 번역도 mock (캐시로 원문 반환 보장)
            with patch.object(alert_formatter_kind, "translate_news_to_kr", return_value=["뉴스 1"]):
                result = format_x_kind(
                    "L1", 8.2, "VIX 급등 + 채권 금리 상승",
                    ["Fed Hints"], "#미국증시 #시장경보",
                )

    assert result is not None
    assert "잠깐 살펴봐요" in result
    assert fake_client.messages.create.called


@pytest.mark.unit
def test_format_x_kind_l2_uses_gemini() -> None:
    """L2는 Gemini 호출"""
    fake_genai_module = MagicMock()
    fake_client = MagicMock()
    fake_response = MagicMock()
    fake_response.text = (
        "📬 오늘 시장 메모\n\n"
        "환율이 좀 움직였어요. 원/달러 1,420원이네요.\n"
        "외환 당국 발언도 같이 있었고요.\n"
        "참고만 해주세요. 잠깐 챙겨봐요.\n"
        "#환율 #시장경보"
    )
    fake_client.models.generate_content.return_value = fake_response
    fake_genai_module.Client.return_value = fake_client

    with patch.dict("os.environ", {"GEMINI_API_KEY": "fake"}, clear=False):
        with patch.dict(sys.modules, {
            "google": MagicMock(genai=fake_genai_module),
            "google.genai": fake_genai_module,
        }):
            with patch.object(alert_formatter_kind, "translate_news_to_kr", return_value=["뉴스 1"]):
                result = format_x_kind(
                    "L2", 5.5, "환율 변동성",
                    ["FX Volatility"], "#환율 #시장경보",
                )

    assert result is not None
    assert "오늘 시장 메모" in result


@pytest.mark.unit
def test_format_x_kind_invalid_level_returns_none() -> None:
    """잘못된 level → None"""
    result = format_x_kind("L99", 0, "", [], "#tag")
    assert result is None


@pytest.mark.unit
def test_format_x_kind_validation_fail_returns_none() -> None:
    """LLM이 위협 어휘 출력 → 검증 실패 → None"""
    fake_anthropic_module = MagicMock()
    fake_client = MagicMock()
    fake_block = MagicMock()
    fake_block.type = "text"
    # 위협 어휘 '폭락' 포함
    fake_block.text = (
        "🔔 잠깐 살펴봐요\n\n"
        "오늘 시장이 폭락했어요.\n"
        "참고만 해주세요. 차근차근 봐요.\n"
        "#미국증시"
    )
    fake_response = MagicMock()
    fake_response.content = [fake_block]
    fake_client.messages.create.return_value = fake_response
    fake_anthropic_module.Anthropic.return_value = fake_client

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "fake"}, clear=False):
        with patch.dict(sys.modules, {"anthropic": fake_anthropic_module}):
            with patch.object(alert_formatter_kind, "translate_news_to_kr", return_value=["뉴스"]):
                result = format_x_kind(
                    "L1", 8.0, "test", ["t"], "#tag",
                )
    assert result is None


@pytest.mark.unit
def test_format_x_kind_api_exception_returns_none() -> None:
    """Claude API 예외 → None"""
    fake_anthropic_module = MagicMock()
    fake_client = MagicMock()
    fake_client.messages.create.side_effect = RuntimeError("API down")
    fake_anthropic_module.Anthropic.return_value = fake_client

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "fake"}, clear=False):
        with patch.dict(sys.modules, {"anthropic": fake_anthropic_module}):
            with patch.object(alert_formatter_kind, "translate_news_to_kr", return_value=["뉴스"]):
                result = format_x_kind(
                    "L1", 8.0, "test", ["t"], "#tag",
                )
    assert result is None


# ────────────────────────────────────────────────────────
# format_tg_kind 흐름 (3개)
# ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_format_tg_kind_l3_uses_gemini() -> None:
    """L3 TG → Gemini 호출, HTML 메시지 반환"""
    fake_genai_module = MagicMock()
    fake_client = MagicMock()
    fake_response = MagicMock()
    # 정상 TG HTML (200자 이상 + 면책 포함)
    fake_response.text = (
        "🌿 <b>가볍게 한 번</b>\n"
        "<i>— 방금 감지</i>\n\n"
        "오늘 코스피 거래가 평소보다 좀 많았어요.\n"
        "외국인 순매도가 한 주 누적되고 있고요.\n"
        "한 번 짚고 가볼 만한 흐름이에요.\n\n"
        "📰 <b>오늘의 뉴스</b>\n"
        "  • S&P 500이 약세로 마감했어요\n"
        "  • 미국 채권 금리가 다시 올랐어요\n"
        "  • 환율이 좀 움직였네요\n\n"
        "투자 권유는 아니에요. 참고만 해주세요.\n"
        "<code>ID: ALERT-20</code>"
    )
    fake_client.models.generate_content.return_value = fake_response
    fake_genai_module.Client.return_value = fake_client

    with patch.dict("os.environ", {"GEMINI_API_KEY": "fake"}, clear=False):
        with patch.dict(sys.modules, {
            "google": MagicMock(genai=fake_genai_module),
            "google.genai": fake_genai_module,
        }):
            with patch.object(alert_formatter_kind, "translate_news_to_kr", return_value=["뉴스1", "뉴스2", "뉴스3"]):
                result = format_tg_kind(
                    "L3", 3.5, "코스피 거래량 증가",
                    ["S&P 500 Down"], ["미주은 영상"],
                    0.92, "ALERT-20260522-1015-7f8a", "방금 감지",
                )

    assert result is not None
    assert "가볍게 한 번" in result
    assert "ALERT-20" in result


@pytest.mark.unit
def test_format_tg_kind_invalid_level_returns_none() -> None:
    """잘못된 level → None"""
    result = format_tg_kind("LX", 0, "", [], [], 0.9, "id", "t")
    assert result is None


@pytest.mark.unit
def test_format_tg_kind_validation_fail_returns_none() -> None:
    """TG가 너무 짧으면 검증 실패 → None"""
    fake_genai_module = MagicMock()
    fake_client = MagicMock()
    fake_response = MagicMock()
    fake_response.text = "📬 짧음. 참고만 해주세요."  # 200자 미만
    fake_client.models.generate_content.return_value = fake_response
    fake_genai_module.Client.return_value = fake_client

    with patch.dict("os.environ", {"GEMINI_API_KEY": "fake"}, clear=False):
        with patch.dict(sys.modules, {
            "google": MagicMock(genai=fake_genai_module),
            "google.genai": fake_genai_module,
        }):
            with patch.object(alert_formatter_kind, "translate_news_to_kr", return_value=[]):
                result = format_tg_kind(
                    "L2", 5.5, "test", [], [], 0.9, "id", "t",
                )
    assert result is None


# ────────────────────────────────────────────────────────
# 상수 안정성 (2개)
# ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_level_headers_defined() -> None:
    """LEVEL_HEADERS 3개 등급 모두 정의 (v1.1.1: L2는 빈 헤더)"""
    assert "L1" in LEVEL_HEADERS
    assert "L2" in LEVEL_HEADERS
    assert "L3" in LEVEL_HEADERS
    assert "잠깐" in LEVEL_HEADERS["L1"]
    # v1.1.1: L2는 카드 알림 같은 헤더 박스가 어색해서 본문부터 시작
    assert LEVEL_HEADERS["L2"] == ""
    assert "가볍게" in LEVEL_HEADERS["L3"]


@pytest.mark.unit
def test_version_string_is_set() -> None:
    """VERSION 상수 명시"""
    assert isinstance(alert_formatter_kind.VERSION, str)
    assert alert_formatter_kind.VERSION.startswith("1.")


# ────────────────────────────────────────────────────────
# v1.0.2 신규: 자기 행위 마무리 패턴 (4개)
# v1.0.1의 "사라마라/메모예요/적어둬요"는 여전히 메타 발화로 어색.
# 자기 행위·일상 종결을 진짜 일상 면책으로 인정.
# ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_validate_disclaimer_jeonin_grayang() -> None:
    """일상 면책 '저는 그냥 …' 통과"""
    text = (
        "🔔 잠깐 살펴봐요\n\n"
        "오늘 미국 시장이 평소보다 많이 흔들렸어요.\n"
        "변동성 지표가 2배 가까이 올랐어요.\n\n"
        "저는 그냥 적금 통장이나 한 번 보고 자려고요.\n"
        "#미국증시 #시장경보"
    )
    ok, reason = validate_kind_output(text, "x")
    assert ok is True, f"실패 사유: {reason}"


@pytest.mark.unit
def test_validate_disclaimer_yogi_kkaji() -> None:
    """자연 종결 '여기까지' 통과"""
    text = (
        "🌿 가볍게 한 번\n\n"
        "오늘 코스피 거래가 평소보다 좀 많았어요.\n"
        "외국인 순매도가 한 주 누적 1.2조네요.\n\n"
        "오늘은 여기까지 봤어요.\n"
        "#미국시장 #InvestmentAlert"
    )
    ok, reason = validate_kind_output(text, "x")
    assert ok is True, f"실패 사유: {reason}"


@pytest.mark.unit
def test_validate_disclaimer_iman_juryeoyo() -> None:
    """자연 종결 '저는 이만' 통과"""
    text = (
        "📬 오늘 시장 메모\n\n"
        "환율이 좀 움직였어요. 원/달러 1,420원을 넘어섰네요.\n"
        "외환 당국 한마디도 같이 나왔고요.\n\n"
        "저는 이만 줄여요.\n"
        "#환율 #시장경보"
    )
    ok, reason = validate_kind_output(text, "x")
    assert ok is True, f"실패 사유: {reason}"


@pytest.mark.unit
def test_validate_disclaimer_still_required_when_absent() -> None:
    """면책 표현이 전혀 없으면 여전히 fail (회귀 방지)"""
    text = (
        "🔔 잠깐 살펴봐요\n\n"
        "오늘 미국 시장이 평소보다 많이 흔들렸어요.\n"
        "변동성 지표가 2배 가까이 올랐어요.\n\n"
        "차근차근 봐요.\n"
        "#미국증시 #시장경보"
    )
    ok, reason = validate_kind_output(text, "x")
    assert ok is False
    assert reason == "missing_disclaimer"


# ────────────────────────────────────────────────────────
# v1.1.0 신규: 대화 유도형 + 센치 종결 (4개)
# ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_validate_disclaimer_question_eotteoseyo() -> None:
    """대화 유도형 '어떠세요?' 통과"""
    text = (
        "🔔 잠깐 살펴봐요\n\n"
        "오늘 미국 시장이 평소보다 많이 흔들렸어요.\n"
        "변동성 지표가 2배 가까이 올랐어요.\n\n"
        "괜히 마음이 조금 가라앉네요.\n"
        "다들 어떻게 보내고 계세요?\n"
        "#미국증시 #시장경보"
    )
    ok, reason = validate_kind_output(text, "x")
    assert ok is True, f"실패 사유: {reason}"


@pytest.mark.unit
def test_validate_disclaimer_question_yeoreobun() -> None:
    """대화 유도형 '여러분의 …' 통과"""
    text = (
        "📬 오늘 시장 메모\n\n"
        "환율이 좀 움직였어요. 원/달러 1,420원을 넘어섰네요.\n"
        "외환 당국 한마디도 같이 나왔고요.\n\n"
        "여러분의 오늘 하루는 어떠셨어요?\n"
        "#환율 #시장경보"
    )
    ok, reason = validate_kind_output(text, "x")
    assert ok is True, f"실패 사유: {reason}"


@pytest.mark.unit
def test_validate_disclaimer_greeting_jaljayo() -> None:
    """인사 종결 '다들 잘 자요' 통과"""
    text = (
        "🌿 가볍게 한 번\n\n"
        "오늘 코스피 거래가 평소보다 좀 많았어요.\n"
        "외국인 순매도가 한 주 누적 1.2조네요.\n\n"
        "저는 일찍 자려고요.\n"
        "다들 잘 자요. 내일 또 봐요.\n"
        "#미국시장 #InvestmentAlert"
    )
    ok, reason = validate_kind_output(text, "x")
    assert ok is True, f"실패 사유: {reason}"


@pytest.mark.unit
def test_validate_disclaimer_question_with_sentimental() -> None:
    """센치 + 대화 유도 결합 통과"""
    text = (
        "📬 오늘 시장 메모\n\n"
        "미국 10년물 금리가 4.85%까지 올라갔어요.\n"
        "인플레이션 기대감이 다시 올라온 모습이에요.\n\n"
        "이런 날엔 따뜻한 차 한 잔이 생각나요.\n"
        "여러분은 오늘 어떤 하루였어요?\n"
        "#미국채권 #시장경보"
    )
    ok, reason = validate_kind_output(text, "x")
    assert ok is True, f"실패 사유: {reason}"


# ────────────────────────────────────────────────────────
# v1.1.0 신규: 이미지 생성 함수 (3개)
# ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_generate_alert_image_no_api_key_returns_none() -> None:
    """API 키 없으면 None"""
    with patch.dict("os.environ", {}, clear=True):
        result = alert_formatter_kind.generate_alert_image_kind("L1", "테스트")
    assert result is None


@pytest.mark.unit
def test_generate_alert_image_invalid_level_returns_none() -> None:
    """잘못된 level → None"""
    with patch.dict("os.environ", {"GEMINI_API_KEY": "fake"}, clear=False):
        result = alert_formatter_kind.generate_alert_image_kind("L99", "테스트")
    assert result is None


@pytest.mark.unit
def test_generate_alert_image_success(tmp_path: Path) -> None:
    """정상 Gemini 응답 → PNG 저장 성공"""
    import struct
    import zlib

    def _make_png(width: int, height: int, payload_size: int = 80 * 1024) -> bytes:
        sig = b"\x89PNG\r\n\x1a\n"
        ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
        ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data)
        ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + struct.pack(">I", ihdr_crc)
        dummy = b"\x00" * payload_size
        idat = struct.pack(">I", len(dummy)) + b"IDAT" + dummy + struct.pack(">I", 0)
        iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", 0)
        return sig + ihdr + idat + iend

    fake_png = _make_png(1024, 576)
    fake_part = MagicMock()
    fake_part.inline_data.data = fake_png
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

    with patch.object(alert_formatter_kind, "IMAGE_OUTPUT_DIR", tmp_path):
        with patch.dict("os.environ", {"GEMINI_API_KEY": "fake"}, clear=False):
            with patch.dict(sys.modules, {
                "google": fake_module,
                "google.genai": fake_genai,
                "google.genai.types": fake_types,
            }):
                result = alert_formatter_kind.generate_alert_image_kind(
                    "L2", "환율 변동성"
                )

    assert result is not None
    assert result.exists()
    assert result.stat().st_size > 30 * 1024
