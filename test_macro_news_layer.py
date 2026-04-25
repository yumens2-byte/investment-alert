"""
제목: XPublisher / TelegramPublisher 단위 테스트
내용: DRY_RUN 모드 동작, API 호출 모의, 오류 처리를 테스트합니다.
      실제 API 호출 없이 mock으로만 검증합니다.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from publishers.telegram_publisher import TelegramPublisher
from publishers.x_publisher import XPublisher


# ────────────────────────────────────────────────────────
# XPublisher
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_x_publisher_dry_run_returns_dry_run() -> None:
    """DRY_RUN=True이면 'DRY_RUN' 문자열 반환"""
    pub = XPublisher(dry_run=True)
    result = pub.publish("테스트 트윗")
    assert result == "DRY_RUN"


@pytest.mark.unit
def test_x_publisher_dry_run_no_api_call() -> None:
    """DRY_RUN=True이면 tweepy Client 미생성"""
    pub = XPublisher(dry_run=True)
    assert pub._client is None


@pytest.mark.unit
def test_x_publisher_real_publish_success() -> None:
    """실발행 성공 시 tweet_id 반환"""
    env = {
        "X_API_KEY": "key", "X_API_SECRET": "sec",
        "X_ACCESS_TOKEN": "tok", "X_ACCESS_TOKEN_SECRET": "toksec",
    }
    with patch.dict("os.environ", env):
        with patch("publishers.x_publisher.XPublisher._build_client") as mock_build:
            mock_client = MagicMock()
            mock_client.create_tweet.return_value = MagicMock(data={"id": "123456"})
            mock_build.return_value = mock_client
            pub = XPublisher(dry_run=False)
            result = pub.publish("Emergency rate cut")
    assert result == "123456"


@pytest.mark.unit
def test_x_publisher_real_publish_failure_raises() -> None:
    """tweepy 예외 시 RuntimeError 발생"""
    with patch("publishers.x_publisher.XPublisher._build_client") as mock_build:
        mock_client = MagicMock()
        mock_client.create_tweet.side_effect = RuntimeError("API error")
        mock_build.return_value = mock_client
        pub = XPublisher(dry_run=False)
        with pytest.raises(RuntimeError, match="X 발행 실패"):
            pub.publish("테스트")


# ────────────────────────────────────────────────────────
# TelegramPublisher
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_tg_publisher_dry_run_free() -> None:
    """DRY_RUN=True이면 TG Free도 'DRY_RUN' 반환"""
    pub = TelegramPublisher(dry_run=True)
    assert pub.publish_free("테스트") == "DRY_RUN"


@pytest.mark.unit
def test_tg_publisher_dry_run_paid() -> None:
    """DRY_RUN=True이면 TG Paid도 'DRY_RUN' 반환"""
    pub = TelegramPublisher(dry_run=True)
    assert pub.publish_paid("테스트") == "DRY_RUN"


@pytest.mark.unit
def test_tg_publisher_real_free_success() -> None:
    """실발행 성공 시 message_id 반환"""
    pub = TelegramPublisher(dry_run=False)
    pub.bot_token = "testtoken"
    pub.free_channel_id = "@test_free"

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"ok": True, "result": {"message_id": 42}}
    mock_resp.raise_for_status.return_value = None

    with patch("publishers.telegram_publisher.requests.post", return_value=mock_resp):
        result = pub.publish_free("테스트 메시지")

    assert result == "42"


@pytest.mark.unit
def test_tg_publisher_missing_token_raises() -> None:
    """bot_token 없으면 RuntimeError"""
    pub = TelegramPublisher(dry_run=False)
    pub.bot_token = ""
    pub.free_channel_id = "@channel"

    with pytest.raises(RuntimeError, match="환경변수 누락"):
        pub.publish_free("테스트")


@pytest.mark.unit
def test_tg_publisher_api_error_raises() -> None:
    """TG API ok=False이면 RuntimeError"""
    pub = TelegramPublisher(dry_run=False)
    pub.bot_token = "tok"
    pub.free_channel_id = "@ch"

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"ok": False, "description": "Bad Request"}
    mock_resp.raise_for_status.return_value = None

    with patch("publishers.telegram_publisher.requests.post", return_value=mock_resp):
        with pytest.raises(RuntimeError):
            pub.publish_free("테스트")
