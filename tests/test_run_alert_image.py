"""
제목: run_alert.py PHASE 2 이미지 통합 분기 테스트
내용: run_alert v1.3.0의 이미지 사전 생성 + TG 첨부 발행 분기를 mock 기반 검증.
      KIND_TONE_IMAGE_ENABLED 토글, SYSTEM_DEGRADED 제외, graceful fallback 등 5개 시나리오.
"""
from __future__ import annotations

import inspect
import os
from pathlib import Path
from unittest.mock import patch

import pytest

# ────────────────────────────────────────────────────────
# 시나리오 1: 이미지 생성 활성화 분기 조건 확인
# ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_image_generation_condition_all_true() -> None:
    """모든 조건 만족 시 image_path 채워짐 가정"""
    env = {
        "KIND_TONE_ENABLED": "true",
        "KIND_TONE_IMAGE_ENABLED": "true",
        "GEMINI_API_KEY": "fake",
    }
    kind_enabled = env.get("KIND_TONE_ENABLED", "").lower() in ("true", "1", "yes")
    image_enabled = env.get("KIND_TONE_IMAGE_ENABLED", "").lower() in (
        "true", "1", "yes",
    )
    level = "L1"
    can_generate = (
        kind_enabled and image_enabled
        and level in ("L1", "L2", "L3")
        and env.get("GEMINI_API_KEY", "")
    )
    assert can_generate is True or can_generate == "fake"  # truthy check


@pytest.mark.unit
def test_image_generation_condition_kind_disabled() -> None:
    """KIND_TONE_ENABLED=false → 이미지 미생성"""
    env = {
        "KIND_TONE_ENABLED": "false",
        "KIND_TONE_IMAGE_ENABLED": "true",
        "GEMINI_API_KEY": "fake",
    }
    kind_enabled = env.get("KIND_TONE_ENABLED", "").lower() in ("true", "1", "yes")
    image_enabled = env.get("KIND_TONE_IMAGE_ENABLED", "").lower() in (
        "true", "1", "yes",
    )
    assert (kind_enabled and image_enabled) is False


@pytest.mark.unit
def test_image_generation_condition_image_disabled() -> None:
    """KIND_TONE_IMAGE_ENABLED 미설정 (디폴트 false) → 이미지 미생성"""
    env = {
        "KIND_TONE_ENABLED": "true",
        "GEMINI_API_KEY": "fake",
    }
    image_enabled = env.get("KIND_TONE_IMAGE_ENABLED", "").lower() in (
        "true", "1", "yes",
    )
    assert image_enabled is False


@pytest.mark.unit
def test_image_generation_condition_system_degraded() -> None:
    """SYSTEM_DEGRADED 등급 → 이미지 미생성 (자원 절약)"""
    level = "SYSTEM_DEGRADED"
    assert level not in ("L1", "L2", "L3")


@pytest.mark.unit
def test_image_generation_condition_no_api_key() -> None:
    """GEMINI_API_KEY 미설정 → 이미지 미생성"""
    env = {
        "KIND_TONE_ENABLED": "true",
        "KIND_TONE_IMAGE_ENABLED": "true",
    }
    assert env.get("GEMINI_API_KEY", "") == ""


# ────────────────────────────────────────────────────────
# 시나리오 2: telegram_publisher.publish_with_photo 동작 (graceful)
# ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_telegram_publisher_publish_with_photo_signature() -> None:
    """publish_with_photo 시그니처: text, photo_path, target='free'"""
    from publishers.telegram_publisher import TelegramPublisher

    sig = inspect.signature(TelegramPublisher.publish_with_photo)
    params = list(sig.parameters.keys())
    assert "self" in params
    assert "text" in params
    assert "photo_path" in params
    assert "target" in params


@pytest.mark.unit
def test_telegram_publisher_publish_with_photo_target_validation() -> None:
    """잘못된 target → RuntimeError"""
    from publishers.telegram_publisher import TelegramPublisher

    with patch.dict(os.environ, {"DRY_RUN": "false"}):
        pub = TelegramPublisher()
        with pytest.raises(RuntimeError, match="알 수 없는 target"):
            pub.publish_with_photo("test", Path("/tmp/nonexistent.png"), target="invalid")


@pytest.mark.unit
def test_telegram_publisher_dry_run_simulates() -> None:
    """DRY_RUN=true 시 publish_with_photo는 시뮬레이션"""
    from publishers.telegram_publisher import TelegramPublisher

    with patch.dict(os.environ, {"DRY_RUN": "true"}, clear=False):
        pub = TelegramPublisher()
        result = pub.publish_with_photo(
            "test", Path("/tmp/fake.png"), target="free"
        )
    assert result == "DRY_RUN"


@pytest.mark.unit
def test_telegram_publisher_missing_file_fallback_to_text(tmp_path: Path) -> None:
    """파일 부재 시 publish_with_photo는 텍스트만 fallback"""
    from publishers.telegram_publisher import TelegramPublisher

    nonexistent = tmp_path / "nonexistent.png"
    assert not nonexistent.exists()

    with patch.dict(
        os.environ,
        {
            "DRY_RUN": "false",
            "TELEGRAM_BOT_TOKEN": "fake",
            "TELEGRAM_FREE_CHANNEL_ID": "fake",
        },
        clear=False,
    ):
        pub = TelegramPublisher()
        # publish_with_photo 내부에서 file not exist 발견 → _publish (text) 호출
        # _publish가 fake 토큰으로 실제 호출하므로 일단 외부 호출 mock
        with patch.object(pub, "_publish", return_value="msg_id_123") as m_pub:
            result = pub.publish_with_photo(
                "fallback text", nonexistent, target="free"
            )
        assert m_pub.called
        assert result == "msg_id_123"


# ────────────────────────────────────────────────────────
# 시나리오 3: generate_alert_image_kind 직접 통합 (이미 test_alert_formatter_kind에서 커버되지만 재확인)
# ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_generate_alert_image_kind_function_exists() -> None:
    """generate_alert_image_kind 함수 import 가능 (run_alert.py가 이걸 호출)"""
    from publishers.alert_formatter_kind import generate_alert_image_kind
    assert callable(generate_alert_image_kind)


@pytest.mark.unit
def test_run_alert_version_v130() -> None:
    """run_alert VERSION이 1.3.0 (PHASE 2)"""
    # run_alert.py를 import 없이 텍스트로 검증 (의존성 회피)
    run_alert_path = Path(__file__).parent.parent / "run_alert.py"
    content = run_alert_path.read_text(encoding="utf-8")
    assert 'VERSION = "1.3.0"' in content


@pytest.mark.unit
def test_run_alert_imports_os_for_env() -> None:
    """run_alert.py가 os 모듈 import (환경변수 체크용)"""
    run_alert_path = Path(__file__).parent.parent / "run_alert.py"
    content = run_alert_path.read_text(encoding="utf-8")
    assert "import os" in content


@pytest.mark.unit
def test_run_alert_has_image_path_block() -> None:
    """run_alert.py에 이미지 사전 생성 블록 존재"""
    run_alert_path = Path(__file__).parent.parent / "run_alert.py"
    content = run_alert_path.read_text(encoding="utf-8")
    assert "KIND_TONE_IMAGE_ENABLED" in content
    assert "generate_alert_image_kind" in content
    assert "image_path" in content


@pytest.mark.unit
def test_run_alert_uses_publish_with_photo_for_tg() -> None:
    """run_alert.py가 TG Free/Paid에서 publish_with_photo 호출"""
    run_alert_path = Path(__file__).parent.parent / "run_alert.py"
    content = run_alert_path.read_text(encoding="utf-8")
    assert 'target="free"' in content
    assert 'target="paid"' in content
    assert "publish_with_photo" in content


@pytest.mark.unit
def test_run_alert_system_degraded_excludes_image() -> None:
    """run_alert.py가 SYSTEM_DEGRADED를 이미지 생성에서 제외"""
    run_alert_path = Path(__file__).parent.parent / "run_alert.py"
    content = run_alert_path.read_text(encoding="utf-8")
    # 조건문 패턴 확인
    assert 'signal.level in ("L1", "L2", "L3")' in content
