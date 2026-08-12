"""
제목: persona_voice 모듈 단위 테스트
내용: 일상 텍스트 페르소나 한줄평 모듈의 핵심 로직을 mock 기반으로 검증.
      실제 Claude API 호출 없이 동작 검증.

테스트 카테고리:
  - main() 분기 동작 (env, archive, sidecar)
  - LLM 출력 검증 (길이/금기어휘/마커)
  - 안티봇 변동 시드 선택
  - archive append + sidecar 작성
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from publishers.weekly_news_x import persona_voice
from publishers.weekly_news_x.persona_voice import (
    CLOSING_PATTERNS,
    OPENING_PATTERNS,
    append_to_archive,
    is_already_added,
    main,
    select_variation_seed,
    validate_output,
    write_persona_sidecar,
)

# ────────────────────────────────────────────────────────
# main() 분기 동작 (3개)
# ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_main_skip_when_env_false() -> None:
    """APPEND_PERSONA_VOICE 미설정/false면 즉시 skip"""
    with patch.dict("os.environ", {"APPEND_PERSONA_VOICE": "false"}, clear=False):
        result = main()
    assert result == 0


@pytest.mark.unit
def test_main_skip_when_no_archive(tmp_path: Path) -> None:
    """archive .md 없으면 error + 1"""
    empty_dir = tmp_path / "empty_archive"
    empty_dir.mkdir()
    env = {"APPEND_PERSONA_VOICE": "true"}
    with patch.dict("os.environ", env, clear=False):
        with patch.object(persona_voice, "ARCHIVE_ROOT", empty_dir):
            result = main()
    assert result == 1


@pytest.mark.unit
def test_main_idempotent_skip(tmp_path: Path) -> None:
    """sidecar 이미 존재 시 skip"""
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    md_path = archive_dir / "2026-05-23.md"
    md_path.write_text("# 본문\n", encoding="utf-8")
    # sidecar 미리 작성
    sc_path = md_path.with_suffix(md_path.suffix + persona_voice.SIDECAR_SUFFIX)
    sc_path.write_text("{}", encoding="utf-8")

    env = {"APPEND_PERSONA_VOICE": "true"}
    with patch.dict("os.environ", env, clear=False):
        with patch.object(persona_voice, "ARCHIVE_ROOT", archive_dir):
            result = main()
    assert result == 0
    # archive 내용 변경 안 됨
    assert md_path.read_text(encoding="utf-8") == "# 본문\n"


# ────────────────────────────────────────────────────────
# 출력 검증 (7개)
# ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_validate_pass_normal_output() -> None:
    """정상 길이·금기어 없음 → 통과"""
    text = (
        "코스피 -1.2%.\n"
        "출근길에 김밥을 샀다.\n"
        "한 줄에 4500원.\n"
        "3년 전 3500원이었다.\n"
        "지수는 흔들려도 점심값은 그렇지 않다."
    )
    ok, reason = validate_output(text)
    assert ok is True
    assert reason == ""


@pytest.mark.unit
def test_validate_length_too_short() -> None:
    """30자 미만 → False"""
    ok, reason = validate_output("짧다.")
    assert ok is False
    assert "length" in reason


@pytest.mark.unit
def test_validate_length_too_long() -> None:
    """200자 초과 → False"""
    text = "가" * 200
    ok, reason = validate_output(text)
    assert ok is False
    assert "length" in reason


@pytest.mark.unit
def test_validate_forbidden_marker() -> None:
    """🎭 마커 포함 → False (comic_voice와 충돌 방지)"""
    text = (
        "🎭 캐릭터 한 마디입니다.\n"
        "오늘 시장은 매우 흔들렸다.\n"
        "나스닥은 결국 하락 마감했다.\n"
        "출근길에 따뜻한 커피를 샀다.\n"
        "별일 없는 하루였다.\n"
        "그렇게 퇴근했다."
    )
    ok, reason = validate_output(text)
    assert ok is False
    assert "forbidden_char" in reason


@pytest.mark.unit
def test_validate_forbidden_word_politics() -> None:
    """정치 어휘 포함 → False"""
    text = (
        "오늘 시장은 흔들렸다.\n"
        "대통령이 한마디 했다.\n"
        "거래는 평소대로 끝났다.\n"
        "퇴근길에 커피를 샀다.\n"
        "별일 없는 하루였다."
    )
    ok, reason = validate_output(text)
    assert ok is False
    assert "forbidden_word" in reason


@pytest.mark.unit
def test_validate_forbidden_word_gender() -> None:
    """젠더 어휘 포함 → False"""
    text = (
        "오늘 시장은 많이 흔들렸다.\n"
        "페미 관련 기사가 같이 떴다.\n"
        "나는 그냥 차트만 한참 봤다.\n"
        "별일 없는 하루였다 결국.\n"
        "그렇게 퇴근했다."
    )
    ok, reason = validate_output(text)
    assert ok is False
    assert "forbidden_word" in reason


@pytest.mark.unit
def test_validate_forbidden_word_recommend() -> None:
    """매수 권유 어휘 포함 → False"""
    text = (
        "코스피가 많이 떨어졌다 오늘.\n"
        "지금 사세요 진짜로요.\n"
        "기회는 지금이라고 한다.\n"
        "나는 그냥 차트를 한참 봤다.\n"
        "별일 없는 하루였다."
    )
    ok, reason = validate_output(text)
    assert ok is False
    assert "forbidden_word" in reason


# ────────────────────────────────────────────────────────
# 시드 선택 (2개)
# ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_select_variation_seed_returns_valid_keys() -> None:
    """select_variation_seed가 필수 키를 모두 반환"""
    seed = select_variation_seed()
    assert seed["opening_pattern"] in OPENING_PATTERNS
    assert seed["closing_pattern"] in CLOSING_PATTERNS
    assert isinstance(seed["humor_flag"], bool)
    assert 0.50 <= seed["temperature"] <= 0.90


@pytest.mark.unit
def test_select_variation_seed_randomness() -> None:
    """30회 호출 시 최소 2종 이상의 opening/closing 조합 출현"""
    openings = set()
    closings = set()
    for _ in range(30):
        seed = select_variation_seed()
        openings.add(seed["opening_pattern"])
        closings.add(seed["closing_pattern"])
    # 30회 중 1종만 나올 확률은 거의 0
    assert len(openings) >= 2
    assert len(closings) >= 2


# ────────────────────────────────────────────────────────
# archive 추가 + sidecar (3개)
# ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_append_to_archive_adds_separator(tmp_path: Path) -> None:
    """archive 끝에 '---' + 빈줄 + 텍스트 추가"""
    md = tmp_path / "x.md"
    md.write_text("# 본문\n청크1\n", encoding="utf-8")
    voice = "코스피 -1.2%.\n오늘은 별일 없다."

    ok = append_to_archive(md, voice)
    assert ok is True

    content = md.read_text(encoding="utf-8")
    assert "\n\n---\n\n" in content
    assert "코스피 -1.2%." in content
    assert "오늘은 별일 없다." in content


@pytest.mark.unit
def test_sidecar_json_structure(tmp_path: Path) -> None:
    """sidecar JSON에 필수 키 모두 존재"""
    md = tmp_path / "y.md"
    md.write_text("# 본문\n", encoding="utf-8")
    voice = "테스트 텍스트."
    seed = {
        "opening_pattern": "data_first",
        "closing_pattern": "self_humor",
        "humor_flag": True,
        "temperature": 0.75,
    }

    sc_path = write_persona_sidecar(md, voice, seed)
    assert sc_path.exists()

    data = json.loads(sc_path.read_text(encoding="utf-8"))
    required = {
        "version",
        "added_at",
        "model",
        "opening_pattern",
        "closing_pattern",
        "humor_flag",
        "temperature",
        "char_count",
    }
    assert required.issubset(data.keys())
    assert data["opening_pattern"] == "data_first"
    assert data["closing_pattern"] == "self_humor"
    assert data["humor_flag"] is True


@pytest.mark.unit
def test_is_already_added_false_when_no_sidecar(tmp_path: Path) -> None:
    """sidecar 없으면 False, 있으면 True"""
    md = tmp_path / "z.md"
    md.write_text("# 본문\n", encoding="utf-8")
    assert is_already_added(md) is False

    sc = md.with_suffix(md.suffix + persona_voice.SIDECAR_SUFFIX)
    sc.write_text("{}", encoding="utf-8")
    assert is_already_added(md) is True


# ────────────────────────────────────────────────────────
# Claude API 실패 시 처리 (1개)
# ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_generate_returns_none_on_api_error() -> None:
    """API 실패 시 None 반환 (예외 누출 없음)"""
    seed = {
        "opening_pattern": "data_first",
        "closing_pattern": "silence",
        "humor_flag": False,
        "temperature": 0.7,
    }
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "fake_key"}, clear=False):
        with patch("publishers.weekly_news_x.persona_voice.anthropic.Anthropic") as mock_cls:
            mock_client = MagicMock()
            mock_client.messages.create.side_effect = RuntimeError("API timeout")
            mock_cls.return_value = mock_client
            result = persona_voice.generate_persona_voice("뉴스 요약", seed)
    assert result is None


# ────────────────────────────────────────────────────────
# 추가: API 분기 + main 성공 흐름 (5개)
# ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_generate_returns_none_when_no_api_key() -> None:
    """ANTHROPIC_API_KEY 미설정 시 None 반환"""
    seed = {
        "opening_pattern": "data_first",
        "closing_pattern": "silence",
        "humor_flag": False,
        "temperature": 0.7,
    }
    with patch.dict("os.environ", {}, clear=True):
        result = persona_voice.generate_persona_voice("뉴스 요약", seed)
    assert result is None


@pytest.mark.unit
def test_generate_returns_none_when_prompt_missing(tmp_path: Path) -> None:
    """프롬프트 파일 부재 시 None"""
    seed = {
        "opening_pattern": "data_first",
        "closing_pattern": "silence",
        "humor_flag": False,
        "temperature": 0.7,
    }
    missing = tmp_path / "missing.md"
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "fake"}, clear=False):
        with patch.object(persona_voice, "PROMPT_PATH", missing):
            result = persona_voice.generate_persona_voice("뉴스", seed)
    assert result is None


@pytest.mark.unit
def test_generate_returns_text_on_success() -> None:
    """LLM 호출 성공 → 텍스트 반환"""
    seed = {
        "opening_pattern": "data_first",
        "closing_pattern": "silence",
        "humor_flag": False,
        "temperature": 0.7,
    }
    fake_text = "코스피 -1.2%로 시작했다.\n출근길에 김밥을 먹었다.\n별일 없는 하루였다.\n그렇게 끝났다."
    mock_block = MagicMock()
    mock_block.type = "text"
    mock_block.text = fake_text
    mock_response = MagicMock()
    mock_response.content = [mock_block]

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "fake"}, clear=False):
        with patch("publishers.weekly_news_x.persona_voice.anthropic.Anthropic") as mock_cls:
            mock_client = MagicMock()
            mock_client.messages.create.return_value = mock_response
            mock_cls.return_value = mock_client
            result = persona_voice.generate_persona_voice("뉴스 요약", seed)
    assert result == fake_text


@pytest.mark.unit
def test_append_to_archive_exception_returns_false(tmp_path: Path) -> None:
    """파일 쓰기 실패 시 False"""
    md = tmp_path / "ro.md"
    md.write_text("# 본문\n", encoding="utf-8")
    # read_text가 예외를 던지는 상황 모의
    with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
        ok = append_to_archive(md, "텍스트")
    assert ok is False


@pytest.mark.unit
def test_main_full_success_flow(tmp_path: Path) -> None:
    """main 전체 성공 흐름: env true → archive 발견 → 생성 → 검증 통과 → append + sidecar"""
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    md_path = archive_dir / "2026-05-23.md"
    md_path.write_text("# 본문\n[뉴스 요약]\n", encoding="utf-8")

    fake_text = (
        "코스피 -1.2%로 시작했다.\n"
        "출근길에 김밥을 사 먹었다.\n"
        "한 줄에 4500원이었다.\n"
        "3년 전 3500원이었다고 기억한다.\n"
        "지수는 흔들려도 점심값은 그렇지 않다."
    )
    mock_block = MagicMock()
    mock_block.type = "text"
    mock_block.text = fake_text
    mock_response = MagicMock()
    mock_response.content = [mock_block]

    env = {
        "APPEND_PERSONA_VOICE": "true",
        "ANTHROPIC_API_KEY": "fake",
    }
    with patch.dict("os.environ", env, clear=False):
        with patch.object(persona_voice, "ARCHIVE_ROOT", archive_dir):
            with patch("publishers.weekly_news_x.persona_voice.anthropic.Anthropic") as mock_cls:
                mock_client = MagicMock()
                mock_client.messages.create.return_value = mock_response
                mock_cls.return_value = mock_client
                result = main()

    assert result == 0
    # archive에 추가되었는지
    content = md_path.read_text(encoding="utf-8")
    assert "코스피 -1.2%로 시작했다." in content
    assert "\n\n---\n\n" in content
    # sidecar 생성되었는지
    sc = md_path.with_suffix(md_path.suffix + persona_voice.SIDECAR_SUFFIX)
    assert sc.exists()
    data = json.loads(sc.read_text(encoding="utf-8"))
    assert data["char_count"] > 0
    assert data["model"] == persona_voice.MODEL
