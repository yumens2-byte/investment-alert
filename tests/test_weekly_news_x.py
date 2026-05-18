"""
제목: weekly_news_x 패키지 단위 테스트
내용: collect / publish / comic_voice / notion_sync / image_gen 모듈의 핵심 함수를 검증.
      실제 API 호출 없이 mock으로만 검증.

      커버리지 영역:
      - count_x_chars: ASCII/한글/이모지/국가깃발/ZWJ 시퀀스
      - parse_thread: '---' 구분 분할
      - validate_tweets: 정상/초과 분기
      - extract_text_from_response: tool_use 블록 제외
      - save_archive: 디렉토리 생성 및 파일 저장
      - sync_to_notion: 시크릿 미설정/설정 분기
      - generate_comic_voice: 시크릿 미설정 시 None
      - append_to_archive: idempotent 동작
      - main 함수들의 모든 분기 (성공/실패/skip)
      - upload_header_image: ATTACH_IMAGE 분기
      - generate_header_image: openai 의존성/키/예외 분기
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ────────────────────────────────────────────────────────
# publish.count_x_chars
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_count_x_chars_ascii() -> None:
    """ASCII는 1자씩"""
    from publishers.weekly_news_x.publish import count_x_chars
    assert count_x_chars("Hello") == 5


@pytest.mark.unit
def test_count_x_chars_korean() -> None:
    """한글은 2자씩 (X 정책)"""
    from publishers.weekly_news_x.publish import count_x_chars
    assert count_x_chars("안녕") == 4
    assert count_x_chars("안녕하세요") == 10


@pytest.mark.unit
def test_count_x_chars_emoji() -> None:
    """single emoji = 2자"""
    from publishers.weekly_news_x.publish import count_x_chars
    assert count_x_chars("🔥") == 2
    assert count_x_chars("⚖️") >= 2  # VS16 흡수 후 2 이상


@pytest.mark.unit
def test_count_x_chars_regional_indicator() -> None:
    """국가깃발(regional indicator)은 보수적으로 2~4 카운트"""
    from publishers.weekly_news_x.publish import count_x_chars
    n = count_x_chars("🇺🇸")
    assert n >= 2  # X 실제 2자, 본 함수는 over-count 허용 (안전 방향)


@pytest.mark.unit
def test_count_x_chars_mixed() -> None:
    """영문+한글 혼합"""
    from publishers.weekly_news_x.publish import count_x_chars
    # 'Fed' 3 + ' ' 1 + '결정' 4 = 8
    assert count_x_chars("Fed 결정") == 8


# ────────────────────────────────────────────────────────
# publish.parse_thread / validate_tweets
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_parse_thread_basic() -> None:
    """'---'로 분리되는지"""
    from publishers.weekly_news_x.publish import parse_thread
    md = "A\n\n---\n\nB\n\n---\n\nC"
    chunks = parse_thread(md)
    assert chunks == ["A", "B", "C"]


@pytest.mark.unit
def test_parse_thread_strips_empty() -> None:
    """빈 청크 제거"""
    from publishers.weekly_news_x.publish import parse_thread
    md = "A\n\n---\n\n   \n\n---\n\nB"
    chunks = parse_thread(md)
    assert chunks == ["A", "B"]


@pytest.mark.unit
def test_validate_tweets_pass() -> None:
    """짧은 트윗은 통과"""
    from publishers.weekly_news_x.publish import validate_tweets
    result = validate_tweets(["짧은 트윗", "Another"])
    assert len(result) == 2


@pytest.mark.unit
def test_validate_tweets_fail_on_overflow() -> None:
    """TWEET_LIMIT 초과 시 ValueError"""
    from publishers.weekly_news_x.publish import validate_tweets
    over = "가" * 200  # 한글 200자 = X 카운트 400 → TWEET_LIMIT 초과 보장
    with pytest.raises(ValueError, match="Tweet length exceeded"):
        validate_tweets([over])


# ────────────────────────────────────────────────────────
# publish.find_latest_archive
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_find_latest_archive_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """archive 비어있으면 FileNotFoundError"""
    from publishers.weekly_news_x import publish as pub_mod
    monkeypatch.setattr(pub_mod, "ARCHIVE_ROOT", tmp_path)
    with pytest.raises(FileNotFoundError):
        pub_mod.find_latest_archive()


@pytest.mark.unit
def test_find_latest_archive_returns_newest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mtime 최신 파일 반환"""
    import os
    import time

    from publishers.weekly_news_x import publish as pub_mod
    monkeypatch.setattr(pub_mod, "ARCHIVE_ROOT", tmp_path)

    older = tmp_path / "old.md"
    older.write_text("old", encoding="utf-8")
    newer = tmp_path / "new.md"
    newer.write_text("new", encoding="utf-8")
    # mtime 차이 확보
    os.utime(older, (time.time() - 100, time.time() - 100))

    found = pub_mod.find_latest_archive()
    assert found == newer


# ────────────────────────────────────────────────────────
# publish.post_thread (mock)
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_post_thread_chains_replies() -> None:
    """두 번째 트윗부터 in_reply_to_tweet_id 전달"""
    from publishers.weekly_news_x.publish import post_thread

    mock_client = MagicMock()
    mock_client.create_tweet.side_effect = [
        MagicMock(data={"id": "100"}),
        MagicMock(data={"id": "101"}),
        MagicMock(data={"id": "102"}),
    ]

    ids = post_thread(mock_client, ["A", "B", "C"])

    assert ids == ["100", "101", "102"]
    calls = mock_client.create_tweet.call_args_list
    assert "in_reply_to_tweet_id" not in calls[0].kwargs
    assert calls[1].kwargs.get("in_reply_to_tweet_id") == "100"
    assert calls[2].kwargs.get("in_reply_to_tweet_id") == "101"


@pytest.mark.unit
def test_post_thread_attaches_image_only_on_first() -> None:
    """media_ids는 첫 트윗에만"""
    from publishers.weekly_news_x.publish import post_thread

    mock_client = MagicMock()
    mock_client.create_tweet.side_effect = [
        MagicMock(data={"id": "100"}),
        MagicMock(data={"id": "101"}),
    ]
    post_thread(mock_client, ["A", "B"], header_media_id="media_xyz")
    calls = mock_client.create_tweet.call_args_list
    assert calls[0].kwargs.get("media_ids") == ["media_xyz"]
    assert "media_ids" not in calls[1].kwargs


# ────────────────────────────────────────────────────────
# publish.upload_header_image (옵션 분기)
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_upload_header_image_skips_when_attach_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ATTACH_IMAGE 미설정/false 시 None"""
    from publishers.weekly_news_x.publish import upload_header_image
    monkeypatch.delenv("ATTACH_IMAGE", raising=False)
    assert upload_header_image() is None


@pytest.mark.unit
def test_upload_header_image_skips_when_archive_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ATTACH_IMAGE=true이지만 archive 없을 때 None"""
    from publishers.weekly_news_x import publish as pub_mod
    monkeypatch.setenv("ATTACH_IMAGE", "true")
    monkeypatch.setattr(pub_mod, "ARCHIVE_ROOT", tmp_path)
    # image_gen 임포트는 성공하되 archive가 비어있어 None
    with patch(
        "publishers.weekly_news_x.image_gen.generate_header_image",
        return_value=None,
    ):
        assert pub_mod.upload_header_image() is None


@pytest.mark.unit
def test_upload_header_image_returns_media_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """정상 플로우 — image_gen 성공 + media_upload 성공 시 media_id 반환"""
    from publishers.weekly_news_x import publish as pub_mod

    monkeypatch.setenv("ATTACH_IMAGE", "true")
    monkeypatch.setenv("X_API_KEY", "k")
    monkeypatch.setenv("X_API_SECRET", "s")
    monkeypatch.setenv("X_ACCESS_TOKEN", "t")
    monkeypatch.setenv("X_ACCESS_TOKEN_SECRET", "ts")

    monkeypatch.setattr(pub_mod, "ARCHIVE_ROOT", tmp_path)
    md_file = tmp_path / "today.md"
    md_file.write_text("# brief\n", encoding="utf-8")

    fake_img_path = tmp_path / "x.png"
    fake_img_path.write_bytes(b"fake")

    fake_media = MagicMock()
    fake_media.media_id = 999
    fake_media.media_id_string = "media_999"
    fake_api = MagicMock()
    fake_api.media_upload.return_value = fake_media

    with patch(
        "publishers.weekly_news_x.image_gen.generate_header_image",
        return_value=fake_img_path,
    ):
        with patch.object(pub_mod, "get_x_api_v1", return_value=fake_api):
            result = pub_mod.upload_header_image()

    assert result == "media_999"


# ────────────────────────────────────────────────────────
# publish.main — 모든 분기
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_publish_main_archive_path_not_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ARCHIVE_PATH 설정됐는데 파일 없으면 1 반환"""
    from publishers.weekly_news_x import publish as pub_mod
    monkeypatch.setenv("ARCHIVE_PATH", "no/such/file.md")
    monkeypatch.setattr(pub_mod, "REPO_ROOT", tmp_path)
    assert pub_mod.main() == 1


@pytest.mark.unit
def test_publish_main_no_archive_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ARCHIVE_PATH 없고 archive 비어있으면 1 반환"""
    from publishers.weekly_news_x import publish as pub_mod
    monkeypatch.delenv("ARCHIVE_PATH", raising=False)
    monkeypatch.setattr(pub_mod, "ARCHIVE_ROOT", tmp_path)
    assert pub_mod.main() == 1


@pytest.mark.unit
def test_publish_main_validation_failure_returns_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """TWEET_LIMIT 초과 시 2 반환"""
    from publishers.weekly_news_x import publish as pub_mod
    monkeypatch.setattr(pub_mod, "ARCHIVE_ROOT", tmp_path)
    md = tmp_path / "over.md"
    md.write_text("가" * 200, encoding="utf-8")
    assert pub_mod.main() == 2


@pytest.mark.unit
def test_publish_main_dry_run_returns_0(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """DRY_RUN=true이면 발행 없이 0 반환"""
    from publishers.weekly_news_x import publish as pub_mod
    monkeypatch.setattr(pub_mod, "ARCHIVE_ROOT", tmp_path)
    monkeypatch.setenv("DRY_RUN", "true")
    md = tmp_path / "ok.md"
    md.write_text("짧은 트윗\n\n---\n\n또 다른 트윗", encoding="utf-8")
    assert pub_mod.main() == 0


@pytest.mark.unit
def test_publish_main_real_publish_writes_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """DRY_RUN=false 정상 발행 시 GITHUB_OUTPUT에 thread_url 기록"""
    from publishers.weekly_news_x import publish as pub_mod

    monkeypatch.setattr(pub_mod, "ARCHIVE_ROOT", tmp_path)
    md = tmp_path / "ok.md"
    md.write_text("A\n\n---\n\nB", encoding="utf-8")

    output_file = tmp_path / "gh_output"
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    monkeypatch.setenv("X_SCREEN_NAME", "testhandle")
    monkeypatch.setenv("X_API_KEY", "k")
    monkeypatch.setenv("X_API_SECRET", "s")
    monkeypatch.setenv("X_ACCESS_TOKEN", "t")
    monkeypatch.setenv("X_ACCESS_TOKEN_SECRET", "ts")

    fake_client = MagicMock()
    fake_client.create_tweet.side_effect = [
        MagicMock(data={"id": "111"}),
        MagicMock(data={"id": "222"}),
    ]
    with patch.object(pub_mod, "get_x_client", return_value=fake_client):
        with patch.object(pub_mod, "upload_header_image", return_value=None):
            result = pub_mod.main()

    assert result == 0
    output = output_file.read_text(encoding="utf-8")
    assert "thread_url=https://x.com/testhandle/status/111" in output
    assert "tweet_count=2" in output
    # sidecar_path도 기록되었는지
    assert "sidecar_path=" in output


# ────────────────────────────────────────────────────────
# publish — Telegram notifier 통합
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_publish_main_failure_notifies_on_archive_not_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ARCHIVE_PATH 미발견 시 notify_failure(stage='archive_not_found') 호출"""
    from publishers.weekly_news_x import publish as pub_mod
    monkeypatch.setenv("ARCHIVE_PATH", "no/file.md")
    monkeypatch.setattr(pub_mod, "REPO_ROOT", tmp_path)

    with patch(
        "publishers.weekly_news_x.notifier.notify_failure",
        return_value=True,
    ) as mock_fail:
        result = pub_mod.main()

    assert result == 1
    assert mock_fail.called
    kwargs = mock_fail.call_args.kwargs
    assert kwargs["stage"] == "archive_not_found"
    assert kwargs["exit_code"] == 1


@pytest.mark.unit
def test_publish_main_failure_notifies_on_validation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """글자수 초과 시 notify_failure(stage='validation', exit_code=2) 호출"""
    from publishers.weekly_news_x import publish as pub_mod
    monkeypatch.setattr(pub_mod, "ARCHIVE_ROOT", tmp_path)
    md = tmp_path / "over.md"
    md.write_text("가" * 200, encoding="utf-8")

    with patch(
        "publishers.weekly_news_x.notifier.notify_failure",
        return_value=True,
    ) as mock_fail:
        result = pub_mod.main()

    assert result == 2
    assert mock_fail.called
    kwargs = mock_fail.call_args.kwargs
    assert kwargs["stage"] == "validation"
    assert kwargs["exit_code"] == 2


@pytest.mark.unit
def test_publish_main_failure_notifies_on_tweepy_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """tweepy.create_tweet 예외 시 notify_failure(stage='tweepy_publish') 호출"""
    from publishers.weekly_news_x import publish as pub_mod
    monkeypatch.setattr(pub_mod, "ARCHIVE_ROOT", tmp_path)
    md = tmp_path / "ok.md"
    md.write_text("A\n\n---\n\nB", encoding="utf-8")

    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("X_API_KEY", "k")
    monkeypatch.setenv("X_API_SECRET", "s")
    monkeypatch.setenv("X_ACCESS_TOKEN", "t")
    monkeypatch.setenv("X_ACCESS_TOKEN_SECRET", "ts")

    fake_client = MagicMock()
    fake_client.create_tweet.side_effect = RuntimeError("403 Forbidden")
    with patch.object(pub_mod, "get_x_client", return_value=fake_client):
        with patch.object(pub_mod, "upload_header_image", return_value=None):
            with patch(
                "publishers.weekly_news_x.notifier.notify_failure",
                return_value=True,
            ) as mock_fail:
                result = pub_mod.main()

    assert result == 1
    assert mock_fail.called
    kwargs = mock_fail.call_args.kwargs
    assert kwargs["stage"] == "tweepy_publish"
    assert "403 Forbidden" in kwargs["error_msg"]


@pytest.mark.unit
def test_publish_main_skip_no_notification(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """이미 발행된 archive(skip)는 알림 없음 (소음 방지)"""
    from publishers.weekly_news_x import publish as pub_mod
    monkeypatch.setattr(pub_mod, "ARCHIVE_ROOT", tmp_path)
    md = tmp_path / "x.md"
    md.write_text("A\n\n---\n\nB", encoding="utf-8")
    pub_mod.sidecar_path(md).write_text('{"version":"1.0.0"}', encoding="utf-8")
    monkeypatch.delenv("FORCE_REPUBLISH", raising=False)

    with patch(
        "publishers.weekly_news_x.notifier.notify_success",
        return_value=True,
    ) as mock_success:
        with patch(
            "publishers.weekly_news_x.notifier.notify_failure",
            return_value=True,
        ) as mock_fail:
            result = pub_mod.main()

    assert result == 0
    mock_success.assert_not_called()
    mock_fail.assert_not_called()


# ────────────────────────────────────────────────────────
# notifier 모듈 자체
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_notifier_send_skip_when_secrets_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TELEGRAM_BOT_TOKEN/INTERNAL_CHANNEL_ID 미설정 시 False (graceful skip)"""
    from publishers.weekly_news_x.notifier import _send_internal
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_INTERNAL_CHANNEL_ID", raising=False)
    assert _send_internal("test") is False


@pytest.mark.unit
def test_notifier_send_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """시크릿 설정 시 TelegramPublisher.publish_internal 호출"""
    from publishers.weekly_news_x import notifier

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_INTERNAL_CHANNEL_ID", "ch_id")

    fake_pub = MagicMock()
    fake_pub.publish_internal.return_value = "msg_123"
    with patch.object(notifier, "TelegramPublisher", return_value=fake_pub):
        result = notifier._send_internal("hello")

    assert result is True
    fake_pub.publish_internal.assert_called_once_with("hello")


@pytest.mark.unit
def test_notifier_send_handles_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TelegramPublisher 예외 시 False (X 발행 차단 X)"""
    from publishers.weekly_news_x import notifier

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_INTERNAL_CHANNEL_ID", "ch_id")

    fake_pub = MagicMock()
    fake_pub.publish_internal.side_effect = RuntimeError("network err")
    with patch.object(notifier, "TelegramPublisher", return_value=fake_pub):
        assert notifier._send_internal("hello") is False


@pytest.mark.unit
def test_notifier_notify_success_message_contents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """notify_success 메시지에 핵심 정보 모두 포함"""
    from publishers.weekly_news_x import notifier

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_INTERNAL_CHANNEL_ID", "ch_id")

    captured: dict = {}
    fake_pub = MagicMock()
    fake_pub.publish_internal.side_effect = lambda t: captured.setdefault("t", t) or "id"
    with patch.object(notifier, "TelegramPublisher", return_value=fake_pub):
        notifier.notify_success(
            archive_name="2026-05-09-saturday.md",
            thread_url="https://x.com/h/status/123",
            tweet_count=8,
            sidecar_path="logs/weekly_news/2026/05/...meta.json",
        )

    text = captured["t"]
    assert "PUBLISHED" in text
    assert "2026-05-09-saturday.md" in text
    assert "https://x.com/h/status/123" in text
    assert "8" in text


@pytest.mark.unit
def test_notifier_notify_success_force_republished_badge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """force_republished=True이면 'RE-PUBLISHED' 배지"""
    from publishers.weekly_news_x import notifier

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_INTERNAL_CHANNEL_ID", "ch_id")

    captured: dict = {}
    fake_pub = MagicMock()
    fake_pub.publish_internal.side_effect = lambda t: captured.setdefault("t", t) or "id"
    with patch.object(notifier, "TelegramPublisher", return_value=fake_pub):
        notifier.notify_success(
            archive_name="x.md", thread_url="u", tweet_count=1,
            sidecar_path="s", force_republished=True,
        )
    assert "RE-PUBLISHED" in captured["t"]


@pytest.mark.unit
def test_notifier_notify_failure_includes_stage_and_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """notify_failure 메시지에 stage/exit_code/error 포함"""
    from publishers.weekly_news_x import notifier

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_INTERNAL_CHANNEL_ID", "ch_id")

    captured: dict = {}
    fake_pub = MagicMock()
    fake_pub.publish_internal.side_effect = lambda t: captured.setdefault("t", t) or "id"
    with patch.object(notifier, "TelegramPublisher", return_value=fake_pub):
        notifier.notify_failure(
            archive_name="x.md",
            stage="tweepy_publish",
            exit_code=1,
            error_msg="RuntimeError: 403 Forbidden",
        )
    text = captured["t"]
    assert "FAILED" in text
    assert "tweepy_publish" in text
    assert "403 Forbidden" in text


@pytest.mark.unit
def test_notifier_escapes_html_in_archive_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """archive_name에 HTML 특수문자가 있어도 안전하게 이스케이프됨"""
    from publishers.weekly_news_x import notifier

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_INTERNAL_CHANNEL_ID", "ch_id")

    captured: dict = {}
    fake_pub = MagicMock()
    fake_pub.publish_internal.side_effect = lambda t: captured.setdefault("t", t) or "id"
    with patch.object(notifier, "TelegramPublisher", return_value=fake_pub):
        notifier.notify_failure(
            archive_name="<script>alert(1)</script>.md",
            stage="validation",
            exit_code=2,
            error_msg="error <b>bold</b>",
        )
    text = captured["t"]
    assert "&lt;script&gt;" in text
    assert "<script>" not in text


# ────────────────────────────────────────────────────────
# notifier — draft 알림 (notify_draft_created / notify_draft_failure)
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_notifier_draft_created_normal_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """일반 모드(dry_run=False) — 'DRAFT CREATED' 배지 + PR URL 포함"""
    from publishers.weekly_news_x import notifier

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_INTERNAL_CHANNEL_ID", "ch_id")

    captured: dict = {}
    fake_pub = MagicMock()
    fake_pub.publish_internal.side_effect = lambda t: captured.setdefault("t", t) or "id"
    with patch.object(notifier, "TelegramPublisher", return_value=fake_pub):
        notifier.notify_draft_created(
            archive_path="logs/weekly_news/2026/05/2026-05-09-saturday.md",
            pr_url="https://github.com/user/repo/pull/123",
            weekday="Saturday",
            dry_run=False,
        )
    text = captured["t"]
    assert "DRAFT CREATED" in text
    assert "Saturday" in text
    assert "logs/weekly_news/" in text
    assert "https://github.com/user/repo/pull/123" in text


@pytest.mark.unit
def test_notifier_draft_created_dry_run_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dry_run=True — '[DRY-RUN]' 배지 + PR 미생성 안내"""
    from publishers.weekly_news_x import notifier

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_INTERNAL_CHANNEL_ID", "ch_id")

    captured: dict = {}
    fake_pub = MagicMock()
    fake_pub.publish_internal.side_effect = lambda t: captured.setdefault("t", t) or "id"
    with patch.object(notifier, "TelegramPublisher", return_value=fake_pub):
        notifier.notify_draft_created(
            archive_path="logs/weekly_news/2026/05/x.md",
            pr_url="",
            weekday="Sunday",
            dry_run=True,
        )
    text = captured["t"]
    assert "DRY-RUN" in text
    assert "Sunday" in text
    assert "PR 미생성" in text


@pytest.mark.unit
def test_notifier_draft_failure_includes_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """notify_draft_failure 메시지에 stage/error/weekday 포함"""
    from publishers.weekly_news_x import notifier

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_INTERNAL_CHANNEL_ID", "ch_id")

    captured: dict = {}
    fake_pub = MagicMock()
    fake_pub.publish_internal.side_effect = lambda t: captured.setdefault("t", t) or "id"
    with patch.object(notifier, "TelegramPublisher", return_value=fake_pub):
        notifier.notify_draft_failure(
            stage="claude_api",
            error_msg="RateLimitError: rate limit exceeded",
            weekday="Saturday",
        )
    text = captured["t"]
    assert "DRAFT FAILED" in text
    assert "Saturday" in text
    assert "claude_api" in text
    assert "rate limit" in text


@pytest.mark.unit
def test_notifier_draft_skips_when_secrets_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """시크릿 미설정 시 draft 알림도 graceful skip"""
    from publishers.weekly_news_x.notifier import notify_draft_created, notify_draft_failure
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_INTERNAL_CHANNEL_ID", raising=False)

    assert notify_draft_created("x.md", "", "Saturday", False) is False
    assert notify_draft_failure("collect", "err", "Saturday") is False


# ────────────────────────────────────────────────────────
# collect.main — draft 알림 호출
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_collect_main_notifies_on_missing_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """API 키 미설정 시 notify_draft_failure(stage='missing_api_key') 호출"""
    from publishers.weekly_news_x import collect as col_mod
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with patch(
        "publishers.weekly_news_x.notifier.notify_draft_failure",
        return_value=True,
    ) as mock_fail:
        result = col_mod.main()

    assert result == 1
    assert mock_fail.called
    kwargs = mock_fail.call_args.kwargs
    assert kwargs["stage"] == "missing_api_key"


@pytest.mark.unit
def test_collect_main_notifies_on_api_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claude API 예외 시 notify_draft_failure(stage='claude_api') 호출"""
    from publishers.weekly_news_x import collect as col_mod
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    fake_client = MagicMock()
    fake_client.messages.create.side_effect = RuntimeError("network err")

    with patch.object(col_mod.anthropic, "Anthropic", return_value=fake_client):
        with patch(
            "publishers.weekly_news_x.notifier.notify_draft_failure",
            return_value=True,
        ) as mock_fail:
            result = col_mod.main()

    assert result == 1
    assert mock_fail.called
    kwargs = mock_fail.call_args.kwargs
    assert kwargs["stage"] == "claude_api"
    assert "network err" in kwargs["error_msg"]


@pytest.mark.unit
def test_collect_main_notifies_on_empty_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """응답 텍스트 빈 경우 notify_draft_failure(stage='empty_response') 호출"""
    from publishers.weekly_news_x import collect as col_mod
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    fake_response = MagicMock()
    fake_response.content = []
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response

    with patch.object(col_mod.anthropic, "Anthropic", return_value=fake_client):
        with patch(
            "publishers.weekly_news_x.notifier.notify_draft_failure",
            return_value=True,
        ) as mock_fail:
            result = col_mod.main()

    assert result == 1
    assert mock_fail.called
    kwargs = mock_fail.call_args.kwargs
    assert kwargs["stage"] == "empty_response"


# ────────────────────────────────────────────────────────
# collect.main — 응답 형식 사후 검증 (v1.2.0 신규)
# ────────────────────────────────────────────────────────
def _make_valid_thread_markdown() -> str:
    """제목: 정상 X 스레드 마크다운 8청크 생성 (테스트 fixture)"""
    return (
        "**🧵 [5/11 미국 주요뉴스 브리핑]**\n\n"
        "오늘의 핵심: A · B · C · D · E\n\n"
        "---\n\n"
        "**1/ 📊 첫 번째 뉴스**\n본문 1\n#tag1\n\n"
        "---\n\n"
        "**2/ 🔥 두 번째 뉴스**\n본문 2\n#tag2\n\n"
        "---\n\n"
        "**3/ 💾 세 번째 뉴스**\n본문 3\n#tag3\n\n"
        "---\n\n"
        "**4/ 🛢️ 네 번째 뉴스**\n본문 4\n#tag4\n\n"
        "---\n\n"
        "**5/ 🐉 다섯 번째 뉴스**\n본문 5\n#tag5\n\n"
        "---\n\n"
        "**6/ 🦅 여섯 번째 뉴스**\n본문 6\n#tag6\n\n"
        "---\n\n"
        "**📌 투자자 주목 포인트**\n주목 포인트\n#NVDA\n"
    )


@pytest.mark.unit
def test_collect_main_rejects_meta_question_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """메타-질문 응답 감지 시 stage='invalid_format'로 실패 (archive 저장 안 함)"""
    from publishers.weekly_news_x import collect as col_mod

    monkeypatch.setattr(col_mod, "ARCHIVE_ROOT", tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = (
        "사용자님, 검색한 결과를 확인한 결과 부족합니다.\n"
        "옵션 1: 24시간 확대\n"
        "옵션 2: 3건만 정리\n"
        "어떤 방식으로 진행할까요?"
    )
    fake_response = MagicMock()
    fake_response.content = [text_block]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response

    with patch.object(col_mod.anthropic, "Anthropic", return_value=fake_client):
        with patch(
            "publishers.weekly_news_x.notifier.notify_draft_failure",
            return_value=True,
        ) as mock_fail:
            result = col_mod.main()

    assert result == 1
    assert mock_fail.called
    kwargs = mock_fail.call_args.kwargs
    assert kwargs["stage"] == "invalid_format"
    # archive 저장 안 됨
    assert list(tmp_path.rglob("*.md")) == []


@pytest.mark.unit
def test_collect_main_rejects_too_few_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """청크 수 부족 시 invalid_format 실패"""
    from publishers.weekly_news_x import collect as col_mod

    monkeypatch.setattr(col_mod, "ARCHIVE_ROOT", tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "단 1개 청크만 있음 — 8청크 마크다운 아님"  # '---' 없음
    fake_response = MagicMock()
    fake_response.content = [text_block]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response

    with patch.object(col_mod.anthropic, "Anthropic", return_value=fake_client):
        with patch(
            "publishers.weekly_news_x.notifier.notify_draft_failure",
            return_value=True,
        ) as mock_fail:
            result = col_mod.main()

    assert result == 1
    kwargs = mock_fail.call_args.kwargs
    assert kwargs["stage"] == "invalid_format"


@pytest.mark.unit
def test_collect_main_accepts_valid_8chunk_markdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """정상 8청크 마크다운은 검증 통과 + archive 저장"""
    from publishers.weekly_news_x import collect as col_mod

    monkeypatch.setattr(col_mod, "ARCHIVE_ROOT", tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = _make_valid_thread_markdown()
    fake_response = MagicMock()
    fake_response.content = [text_block]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response

    with patch.object(col_mod.anthropic, "Anthropic", return_value=fake_client):
        result = col_mod.main()

    assert result == 0
    saved_files = list(tmp_path.rglob("*.md"))
    assert len(saved_files) == 1


@pytest.mark.unit
def test_collect_main_rejects_partial_meta_pattern(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """청크 수는 충족하지만 메타 패턴 1개라도 포함 시 실패"""
    from publishers.weekly_news_x import collect as col_mod

    monkeypatch.setattr(col_mod, "ARCHIVE_ROOT", tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    # 8청크 정상 마크다운이지만 끝에 메타-질문 추가됨
    contaminated = _make_valid_thread_markdown() + "\n\n어떤 방식으로 진행할까요?"
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = contaminated
    fake_response = MagicMock()
    fake_response.content = [text_block]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response

    with patch.object(col_mod.anthropic, "Anthropic", return_value=fake_client):
        with patch(
            "publishers.weekly_news_x.notifier.notify_draft_failure",
            return_value=True,
        ) as mock_fail:
            result = col_mod.main()

    assert result == 1
    kwargs = mock_fail.call_args.kwargs
    assert kwargs["stage"] == "invalid_format"


# ────────────────────────────────────────────────────────
# collect.main — v1.3.0 X 글자수 사전 검증 (length_exceeded stage)
# 도입 배경: 2026-05-16 사고 회귀 방지
# v1.3.1: X Premium 정책 반영 — TWEET_LIMIT을 publish 모듈에서 동적 참조하여
#          하드코딩 280/380 제거 (정책 변경 시 테스트 자동 추종)
# ────────────────────────────────────────────────────────
def _make_overflow_thread_markdown(overflow_indices: list[int]) -> str:
    """제목: 특정 인덱스의 청크를 TWEET_LIMIT 초과로 만든 8청크 마크다운 생성

    TWEET_LIMIT은 publish 모듈에서 동적 import — 정책 변경 시 자동 추종.

    Args:
        overflow_indices: 1~6 중 초과시킬 뉴스 청크 번호 (헤더/시사점은 제외)

    Returns:
        str: count_x_chars()로 > TWEET_LIMIT 가 되는 청크를 포함한 마크다운
    """
    from publishers.weekly_news_x.publish import TWEET_LIMIT

    # 한글 1자 = X 카운트 2자. TWEET_LIMIT를 50자 초과하도록 한글 수 동적 계산.
    # 예: TWEET_LIMIT=280 → 한글 165자 = X 카운트 330자 (초과 50)
    # 예: TWEET_LIMIT=380 → 한글 215자 = X 카운트 430자 (초과 50)
    overflow_korean_count = (TWEET_LIMIT + 50) // 2 + 1
    overflow_body = "가" * overflow_korean_count
    normal_body = "본문 정상"

    blocks = [
        "**🧵 [5/16 미국 주요뉴스 브리핑]**\n\n오늘의 핵심: A · B · C · D · E"
    ]
    for i in range(1, 7):
        body = overflow_body if i in overflow_indices else normal_body
        blocks.append(f"**{i}/ 📊 뉴스 {i}**\n{body}\n#tag{i}")
    blocks.append("**📌 투자자 주목 포인트**\n주목 포인트\n#NVDA")
    return "\n\n---\n\n".join(blocks)


@pytest.mark.unit
def test_collect_main_rejects_length_overflow_single(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """청크 1개가 TWEET_LIMIT 초과 시 length_exceeded stage로 차단 + archive 미생성"""
    from publishers.weekly_news_x import collect as col_mod

    monkeypatch.setattr(col_mod, "ARCHIVE_ROOT", tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = _make_overflow_thread_markdown(overflow_indices=[2])
    fake_response = MagicMock()
    fake_response.content = [text_block]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response

    with patch.object(col_mod.anthropic, "Anthropic", return_value=fake_client):
        with patch(
            "publishers.weekly_news_x.notifier.notify_draft_failure",
            return_value=True,
        ) as mock_fail:
            result = col_mod.main()

    assert result == 1
    assert mock_fail.called
    kwargs = mock_fail.call_args.kwargs
    assert kwargs["stage"] == "length_exceeded"
    # archive 미저장 확인 (마스터 메모리: "검증 실패 시 archive 저장 안 함")
    assert list(tmp_path.rglob("*.md")) == []


@pytest.mark.unit
def test_collect_main_rejects_length_overflow_multiple(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """청크 여러개가 TWEET_LIMIT 초과 시 모두 보고 + length_exceeded stage"""
    from publishers.weekly_news_x import collect as col_mod

    monkeypatch.setattr(col_mod, "ARCHIVE_ROOT", tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    text_block = MagicMock()
    text_block.type = "text"
    # 청크 #2, #4 동시 초과 (5/16 사고 재현 시나리오)
    text_block.text = _make_overflow_thread_markdown(overflow_indices=[2, 4])
    fake_response = MagicMock()
    fake_response.content = [text_block]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response

    with patch.object(col_mod.anthropic, "Anthropic", return_value=fake_client):
        with patch(
            "publishers.weekly_news_x.notifier.notify_draft_failure",
            return_value=True,
        ) as mock_fail:
            result = col_mod.main()

    assert result == 1
    kwargs = mock_fail.call_args.kwargs
    assert kwargs["stage"] == "length_exceeded"
    # error_msg에 "초과 청크" 키워드 + 2건 정보 포함되어야 함
    err = kwargs["error_msg"]
    assert "초과 청크" in err
    assert err.count("tweet #") >= 2  # 최소 2건 보고


@pytest.mark.unit
def test_collect_main_accepts_within_tweet_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """모든 청크가 TWEET_LIMIT 이내일 때 X 글자수 검증 통과 + archive 저장"""
    from publishers.weekly_news_x import collect as col_mod

    monkeypatch.setattr(col_mod, "ARCHIVE_ROOT", tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    text_block = MagicMock()
    text_block.type = "text"
    # _make_valid_thread_markdown은 모든 청크 짧음 → X 카운트도 < TWEET_LIMIT
    text_block.text = _make_valid_thread_markdown()
    fake_response = MagicMock()
    fake_response.content = [text_block]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response

    with patch.object(col_mod.anthropic, "Anthropic", return_value=fake_client):
        result = col_mod.main()

    assert result == 0
    saved_files = list(tmp_path.rglob("*.md"))
    assert len(saved_files) == 1


@pytest.mark.unit
def test_collect_main_weekday_in_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """정상 플로우 — GITHUB_OUTPUT에 weekday 필드 기록 (v1.2.0: 8청크 마크다운 사용)"""
    from publishers.weekly_news_x import collect as col_mod

    monkeypatch.setattr(col_mod, "ARCHIVE_ROOT", tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    output_file = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))

    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = _make_valid_thread_markdown()
    fake_response = MagicMock()
    fake_response.content = [text_block]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response

    with patch.object(col_mod.anthropic, "Anthropic", return_value=fake_client):
        assert col_mod.main() == 0

    content = output_file.read_text(encoding="utf-8")
    assert "weekday=" in content


# ────────────────────────────────────────────────────────
# publish — sidecar / 재발행 방지
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_sidecar_path_appends_meta_json(tmp_path: Path) -> None:
    """sidecar 경로는 archive 옆 '.meta.json'"""
    from publishers.weekly_news_x.publish import sidecar_path
    md = tmp_path / "2026-05-09-saturday.md"
    sc = sidecar_path(md)
    assert sc.name == "2026-05-09-saturday.md.meta.json"
    assert sc.parent == md.parent


@pytest.mark.unit
def test_is_already_published_false_when_no_sidecar(tmp_path: Path) -> None:
    """sidecar 없으면 False"""
    from publishers.weekly_news_x.publish import is_already_published
    md = tmp_path / "x.md"
    md.write_text("body", encoding="utf-8")
    assert is_already_published(md) is False


@pytest.mark.unit
def test_is_already_published_true_when_sidecar_exists(tmp_path: Path) -> None:
    """sidecar 있으면 True"""
    from publishers.weekly_news_x.publish import is_already_published, sidecar_path
    md = tmp_path / "x.md"
    md.write_text("body", encoding="utf-8")
    sidecar_path(md).write_text("{}", encoding="utf-8")
    assert is_already_published(md) is True


@pytest.mark.unit
def test_write_sidecar_contents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """sidecar JSON 필수 필드가 모두 포함됨"""
    import json

    from publishers.weekly_news_x import publish as pub_mod
    monkeypatch.setattr(pub_mod, "REPO_ROOT", tmp_path)

    md = tmp_path / "x.md"
    md.write_text("body", encoding="utf-8")

    sc = pub_mod.write_sidecar(
        archive_path=md,
        posted_ids=["111", "222", "333"],
        screen_name="testhandle",
        force_republished=False,
    )
    assert sc.exists()
    data = json.loads(sc.read_text(encoding="utf-8"))
    assert data["version"] == "1.0.0"
    assert data["tweet_count"] == 3
    assert data["thread_url"] == "https://x.com/testhandle/status/111"
    assert data["status"] == "published"
    assert data["force_republished"] is False
    assert len(data["tweets"]) == 3
    assert data["tweets"][0] == {"index": 1, "tweet_id": "111"}


@pytest.mark.unit
def test_write_sidecar_preserves_previous_on_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """force_republished=True이면 기존 sidecar가 'previous' 필드로 보존됨"""
    import json

    from publishers.weekly_news_x import publish as pub_mod
    monkeypatch.setattr(pub_mod, "REPO_ROOT", tmp_path)

    md = tmp_path / "x.md"
    md.write_text("body", encoding="utf-8")
    # 기존 sidecar
    pub_mod.write_sidecar(md, ["111"], "h", force_republished=False)

    # 강제 재발행
    pub_mod.write_sidecar(md, ["999"], "h", force_republished=True)

    data = json.loads(pub_mod.sidecar_path(md).read_text(encoding="utf-8"))
    assert data["status"] == "republished"
    assert data["force_republished"] is True
    assert "previous" in data
    assert data["previous"]["tweets"][0]["tweet_id"] == "111"


@pytest.mark.unit
def test_publish_main_skips_when_already_published(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """sidecar 존재 + FORCE_REPUBLISH 아님 → exit 0, 발행 호출 X"""
    from publishers.weekly_news_x import publish as pub_mod
    monkeypatch.setattr(pub_mod, "ARCHIVE_ROOT", tmp_path)
    md = tmp_path / "x.md"
    md.write_text("A\n\n---\n\nB", encoding="utf-8")
    # 기존 sidecar 시뮬레이션
    pub_mod.sidecar_path(md).write_text('{"version":"1.0.0"}', encoding="utf-8")

    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.delenv("FORCE_REPUBLISH", raising=False)

    fake_client = MagicMock()
    with patch.object(pub_mod, "get_x_client", return_value=fake_client):
        result = pub_mod.main()

    assert result == 0
    # tweepy 호출 자체가 없어야 함
    fake_client.create_tweet.assert_not_called()


@pytest.mark.unit
def test_publish_main_force_republish_proceeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """sidecar 존재 + FORCE_REPUBLISH=true → 발행 진행 + sidecar 덮어쓰기"""
    import json

    from publishers.weekly_news_x import publish as pub_mod
    monkeypatch.setattr(pub_mod, "ARCHIVE_ROOT", tmp_path)
    monkeypatch.setattr(pub_mod, "REPO_ROOT", tmp_path)

    md = tmp_path / "x.md"
    md.write_text("A\n\n---\n\nB", encoding="utf-8")
    # 기존 sidecar
    pub_mod.write_sidecar(md, ["999"], "old", force_republished=False)

    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("FORCE_REPUBLISH", "true")
    monkeypatch.setenv("X_SCREEN_NAME", "newhandle")
    monkeypatch.setenv("X_API_KEY", "k")
    monkeypatch.setenv("X_API_SECRET", "s")
    monkeypatch.setenv("X_ACCESS_TOKEN", "t")
    monkeypatch.setenv("X_ACCESS_TOKEN_SECRET", "ts")

    fake_client = MagicMock()
    fake_client.create_tweet.side_effect = [
        MagicMock(data={"id": "777"}),
        MagicMock(data={"id": "778"}),
    ]
    with patch.object(pub_mod, "get_x_client", return_value=fake_client):
        with patch.object(pub_mod, "upload_header_image", return_value=None):
            result = pub_mod.main()

    assert result == 0
    # 발행 호출됨
    assert fake_client.create_tweet.call_count == 2
    # sidecar 갱신됨 — 새 tweet_id 반영 + previous 보존
    data = json.loads(pub_mod.sidecar_path(md).read_text(encoding="utf-8"))
    assert data["status"] == "republished"
    assert data["tweets"][0]["tweet_id"] == "777"
    assert "previous" in data
    assert data["previous"]["tweets"][0]["tweet_id"] == "999"


@pytest.mark.unit
def test_write_sidecar_handles_corrupt_previous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """기존 sidecar가 깨진 JSON이면 'previous' 미포함하고 정상 작성"""
    import json

    from publishers.weekly_news_x import publish as pub_mod
    monkeypatch.setattr(pub_mod, "REPO_ROOT", tmp_path)

    md = tmp_path / "x.md"
    md.write_text("body", encoding="utf-8")
    # 손상된 JSON
    pub_mod.sidecar_path(md).write_text("not a json {{{", encoding="utf-8")

    sc = pub_mod.write_sidecar(md, ["111"], "h", force_republished=True)
    data = json.loads(sc.read_text(encoding="utf-8"))
    # previous 필드 없이도 정상 작성
    assert data["status"] == "republished"
    assert "previous" not in data


@pytest.mark.unit
def test_is_inside_helper(tmp_path: Path) -> None:
    """_is_inside 헬퍼: target이 root 하위면 True"""
    from publishers.weekly_news_x.publish import _is_inside

    sub = tmp_path / "a" / "b.md"
    sub.parent.mkdir(parents=True)
    sub.write_text("x", encoding="utf-8")
    assert _is_inside(sub, tmp_path) is True

    outside = Path("/tmp/elsewhere.md")
    assert _is_inside(outside, tmp_path) is False


# ────────────────────────────────────────────────────────
# collect.extract_text_from_response
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_extract_text_skips_non_text_blocks() -> None:
    """tool_use 등 비텍스트 블록 제외"""
    from publishers.weekly_news_x.collect import extract_text_from_response

    tool_block = MagicMock()
    tool_block.type = "server_tool_use"
    text_block_1 = MagicMock()
    text_block_1.type = "text"
    text_block_1.text = "Hello"
    text_block_2 = MagicMock()
    text_block_2.type = "text"
    text_block_2.text = "World"

    fake_response = MagicMock()
    fake_response.content = [tool_block, text_block_1, text_block_2]

    result = extract_text_from_response(fake_response)
    assert result == "Hello\nWorld"


# ────────────────────────────────────────────────────────
# collect.save_archive / build_user_message
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_save_archive_creates_nested_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """YYYY/MM 디렉토리가 없을 때도 생성됨"""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from publishers.weekly_news_x import collect as col_mod

    monkeypatch.setattr(col_mod, "ARCHIVE_ROOT", tmp_path)
    # 2026-05-09는 토요일
    fake_today = datetime(2026, 5, 9, 9, tzinfo=ZoneInfo("Asia/Seoul"))
    saved = col_mod.save_archive("# Test\n", fake_today)

    assert saved.exists()
    assert "2026" in str(saved)
    assert "05" in str(saved)
    assert "saturday" in saved.name


@pytest.mark.unit
def test_save_archive_writes_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """저장 내용이 일치"""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from publishers.weekly_news_x import collect as col_mod

    monkeypatch.setattr(col_mod, "ARCHIVE_ROOT", tmp_path)
    fake_today = datetime(2026, 5, 9, 9, tzinfo=ZoneInfo("Asia/Seoul"))
    saved = col_mod.save_archive("CONTENT_X", fake_today)
    assert saved.read_text(encoding="utf-8") == "CONTENT_X"


@pytest.mark.unit
def test_build_user_message_includes_date() -> None:
    """user message에 날짜 포함"""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from publishers.weekly_news_x.collect import build_user_message
    today = datetime(2026, 5, 9, tzinfo=ZoneInfo("Asia/Seoul"))
    msg = build_user_message(today)
    assert "2026" in msg
    assert "web_search" in msg


# ────────────────────────────────────────────────────────
# collect.main — 모든 분기
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_collect_main_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """ANTHROPIC_API_KEY 미설정 시 1 반환"""
    from publishers.weekly_news_x import collect as col_mod
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert col_mod.main() == 1


@pytest.mark.unit
def test_collect_main_api_exception_returns_1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claude API 호출 예외 시 1 반환"""
    from publishers.weekly_news_x import collect as col_mod
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    fake_client = MagicMock()
    fake_client.messages.create.side_effect = RuntimeError("network error")
    with patch.object(col_mod.anthropic, "Anthropic", return_value=fake_client):
        assert col_mod.main() == 1


@pytest.mark.unit
def test_collect_main_empty_response_returns_1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """응답 텍스트가 비어있으면 1 반환"""
    from publishers.weekly_news_x import collect as col_mod
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    fake_response = MagicMock()
    fake_response.content = []  # 비어있음
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response
    with patch.object(col_mod.anthropic, "Anthropic", return_value=fake_client):
        assert col_mod.main() == 1


@pytest.mark.unit
def test_collect_main_success_writes_github_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """정상 플로우 — archive 저장 + GITHUB_OUTPUT 기록 (v1.2.0: 8청크 마크다운 사용)"""
    from publishers.weekly_news_x import collect as col_mod

    monkeypatch.setattr(col_mod, "ARCHIVE_ROOT", tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    output_file = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))

    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = (
        "**🧵 헤더**\n\n---\n\n"
        "1\n\n---\n\n2\n\n---\n\n3\n\n---\n\n"
        "4\n\n---\n\n5\n\n---\n\n6\n\n---\n\n시사점"
    )
    fake_response = MagicMock()
    fake_response.content = [text_block]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response

    with patch.object(col_mod.anthropic, "Anthropic", return_value=fake_client):
        assert col_mod.main() == 0

    assert output_file.exists()
    content = output_file.read_text(encoding="utf-8")
    assert "archive_path=" in content
    assert "date=" in content


# ────────────────────────────────────────────────────────
# notion_sync
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_sync_to_notion_skips_when_secrets_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NOTION_TOKEN/DB_ID 미설정 시 False 반환"""
    from publishers.weekly_news_x.notion_sync import sync_to_notion

    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.delenv("NOTION_DB_ID", raising=False)

    result = sync_to_notion(
        title="t", date_iso="2026-05-09",
        tweet_url=None, content_md="x", source_path="x",
    )
    assert result is False


@pytest.mark.unit
def test_sync_to_notion_posts_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """시크릿 설정 시 requests.post 호출"""
    from publishers.weekly_news_x import notion_sync as ns

    monkeypatch.setenv("NOTION_TOKEN", "token")
    monkeypatch.setenv("NOTION_DB_ID", "dbid")

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"id": "page_abc"}

    with patch.object(ns.requests, "post", return_value=mock_resp) as mock_post:
        result = ns.sync_to_notion(
            title="t", date_iso="2026-05-09",
            tweet_url="https://x.com/u/status/1",
            content_md="x", source_path="x",
        )
    assert result is True
    assert mock_post.called
    # tweet_url이 payload에 포함되었는지
    payload = mock_post.call_args.kwargs["json"]
    assert "Tweet URL" in payload["properties"]


@pytest.mark.unit
def test_sync_to_notion_omits_tweet_url_when_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """tweet_url=None이면 properties에 Tweet URL 미포함"""
    from publishers.weekly_news_x import notion_sync as ns

    monkeypatch.setenv("NOTION_TOKEN", "token")
    monkeypatch.setenv("NOTION_DB_ID", "dbid")

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"id": "page_abc"}

    with patch.object(ns.requests, "post", return_value=mock_resp) as mock_post:
        ns.sync_to_notion(
            title="t", date_iso="2026-05-09",
            tweet_url=None, content_md="x", source_path="x",
        )
    payload = mock_post.call_args.kwargs["json"]
    assert "Tweet URL" not in payload["properties"]


@pytest.mark.unit
def test_sync_to_notion_handles_request_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """requests.post 예외 시 False 반환"""
    from publishers.weekly_news_x import notion_sync as ns

    monkeypatch.setenv("NOTION_TOKEN", "token")
    monkeypatch.setenv("NOTION_DB_ID", "dbid")

    with patch.object(ns.requests, "post", side_effect=RuntimeError("conn err")):
        result = ns.sync_to_notion(
            title="t", date_iso="2026-05-09",
            tweet_url=None, content_md="x", source_path="x",
        )
    assert result is False


@pytest.mark.unit
def test_notion_sync_main_skip_when_secrets_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NOTION_TOKEN/DB_ID 미설정 시 의도된 skip(exit 0) — 옵션 모듈 비활성"""
    from publishers.weekly_news_x import notion_sync as ns
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.delenv("NOTION_DB_ID", raising=False)
    # archive 비어있어도 시크릿 체크가 먼저이므로 0 반환
    assert ns.main() == 0


@pytest.mark.unit
def test_notion_sync_main_no_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """시크릿 있는데 archive 비어있으면 1 반환 (실제 실패)"""
    from publishers.weekly_news_x import notion_sync as ns
    monkeypatch.setenv("NOTION_TOKEN", "tok")
    monkeypatch.setenv("NOTION_DB_ID", "dbid")
    monkeypatch.setattr(ns, "ARCHIVE_ROOT", tmp_path)
    assert ns.main() == 1


@pytest.mark.unit
def test_notion_sync_main_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """정상 플로우 — 시크릿 + archive 존재 + sync 성공"""
    from publishers.weekly_news_x import notion_sync as ns
    monkeypatch.setenv("NOTION_TOKEN", "tok")
    monkeypatch.setenv("NOTION_DB_ID", "dbid")
    monkeypatch.setattr(ns, "ARCHIVE_ROOT", tmp_path)
    md = tmp_path / "x.md"
    md.write_text("body", encoding="utf-8")
    monkeypatch.setenv("THREAD_URL", "https://x.com/u/status/1")

    with patch.object(ns, "sync_to_notion", return_value=True):
        assert ns.main() == 0


@pytest.mark.unit
def test_notion_sync_main_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """시크릿 있고 archive 있는데 sync 실패 시 1 반환 (실제 실패)"""
    from publishers.weekly_news_x import notion_sync as ns
    monkeypatch.setenv("NOTION_TOKEN", "tok")
    monkeypatch.setenv("NOTION_DB_ID", "dbid")
    monkeypatch.setattr(ns, "ARCHIVE_ROOT", tmp_path)
    md = tmp_path / "x.md"
    md.write_text("body", encoding="utf-8")

    with patch.object(ns, "sync_to_notion", return_value=False):
        assert ns.main() == 1


# ────────────────────────────────────────────────────────
# comic_voice
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_generate_comic_voice_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """API 키 없으면 None"""
    from publishers.weekly_news_x.comic_voice import generate_comic_voice
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert generate_comic_voice("summary") is None


@pytest.mark.unit
def test_generate_comic_voice_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Claude API 성공 시 텍스트 반환"""
    from publishers.weekly_news_x import comic_voice as cv
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "**🎭 Max Bullhorn 한마디**\n..."
    fake_resp = MagicMock()
    fake_resp.content = [text_block]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_resp

    with patch.object(cv.anthropic, "Anthropic", return_value=fake_client):
        result = cv.generate_comic_voice("today summary")

    assert result is not None
    assert "Max Bullhorn" in result


@pytest.mark.unit
def test_generate_comic_voice_api_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """API 예외 발생 시 None"""
    from publishers.weekly_news_x import comic_voice as cv
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

    fake_client = MagicMock()
    fake_client.messages.create.side_effect = RuntimeError("503")
    with patch.object(cv.anthropic, "Anthropic", return_value=fake_client):
        assert cv.generate_comic_voice("summary") is None


@pytest.mark.unit
def test_append_to_archive_idempotent(tmp_path: Path) -> None:
    """이미 🎭가 있으면 skip"""
    from publishers.weekly_news_x.comic_voice import append_to_archive

    md = tmp_path / "today.md"
    md.write_text("**🎭 already there**\n", encoding="utf-8")

    before = md.read_text(encoding="utf-8")
    ok = append_to_archive(md, "**🎭 new voice**")
    assert ok is True
    assert md.read_text(encoding="utf-8") == before


@pytest.mark.unit
def test_append_to_archive_appends_new(tmp_path: Path) -> None:
    """🎭 없으면 추가"""
    from publishers.weekly_news_x.comic_voice import append_to_archive

    md = tmp_path / "today.md"
    md.write_text("# original\n", encoding="utf-8")

    ok = append_to_archive(md, "**🎭 NEW**")
    assert ok is True
    content = md.read_text(encoding="utf-8")
    assert "🎭 NEW" in content
    assert "---" in content


@pytest.mark.unit
def test_append_to_archive_handles_io_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """read 실패 시 False"""
    from publishers.weekly_news_x.comic_voice import append_to_archive

    md = tmp_path / "no.md"  # 존재하지 않음
    ok = append_to_archive(md, "**🎭 X**")
    assert ok is False


@pytest.mark.unit
def test_comic_voice_main_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """APPEND_COMIC_VOICE 미설정 시 0 반환 (skip)"""
    from publishers.weekly_news_x import comic_voice as cv
    monkeypatch.delenv("APPEND_COMIC_VOICE", raising=False)
    assert cv.main() == 0


@pytest.mark.unit
def test_comic_voice_main_no_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """APPEND_COMIC_VOICE=true이지만 archive 없으면 1"""
    from publishers.weekly_news_x import comic_voice as cv
    monkeypatch.setenv("APPEND_COMIC_VOICE", "true")
    monkeypatch.setattr(cv, "ARCHIVE_ROOT", tmp_path)
    assert cv.main() == 1


@pytest.mark.unit
def test_comic_voice_main_generation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """생성 실패 시 archive 무변경, 0 반환 (graceful skip)"""
    from publishers.weekly_news_x import comic_voice as cv
    monkeypatch.setenv("APPEND_COMIC_VOICE", "true")
    monkeypatch.setattr(cv, "ARCHIVE_ROOT", tmp_path)
    md = tmp_path / "x.md"
    md.write_text("orig", encoding="utf-8")

    with patch.object(cv, "generate_comic_voice", return_value=None):
        assert cv.main() == 0
    # archive 미변경
    assert md.read_text(encoding="utf-8") == "orig"


@pytest.mark.unit
def test_comic_voice_main_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """정상 플로우 — generate → append → 0"""
    from publishers.weekly_news_x import comic_voice as cv
    monkeypatch.setenv("APPEND_COMIC_VOICE", "true")
    monkeypatch.setattr(cv, "ARCHIVE_ROOT", tmp_path)
    md = tmp_path / "x.md"
    md.write_text("# brief\n", encoding="utf-8")

    with patch.object(cv, "generate_comic_voice", return_value="**🎭 V**\n"):
        assert cv.main() == 0
    assert "🎭 V" in md.read_text(encoding="utf-8")


# ────────────────────────────────────────────────────────
# image_gen
# ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_generate_header_image_no_openai_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """openai 패키지 미설치 시 None"""
    import sys

    from publishers.weekly_news_x.image_gen import generate_header_image

    # openai 임포트 실패 시뮬레이션
    monkeypatch.setitem(sys.modules, "openai", None)
    result = generate_header_image("summary", tmp_path / "x.png")
    assert result is None


@pytest.mark.unit
def test_generate_header_image_no_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OPENAI_API_KEY 미설정 시 None"""
    import sys
    import types

    # openai 모듈 mock 주입
    fake_openai = types.ModuleType("openai")
    fake_openai.OpenAI = MagicMock()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", fake_openai)

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    from publishers.weekly_news_x.image_gen import generate_header_image
    result = generate_header_image("summary", tmp_path / "x.png")
    assert result is None



@pytest.mark.unit
def test_generate_header_image_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """정상 플로우 — 이미지 파일 생성됨"""
    import base64
    import sys
    import types

    monkeypatch.setenv("OPENAI_API_KEY", "key")

    # fake OpenAI 모듈
    fake_client_inst = MagicMock()
    fake_image_data = MagicMock()
    fake_image_data.b64_json = base64.b64encode(b"PNGDATA").decode()
    fake_response = MagicMock()
    fake_response.data = [fake_image_data]
    fake_client_inst.images.generate.return_value = fake_response

    fake_openai_mod = types.ModuleType("openai")
    fake_openai_mod.OpenAI = MagicMock(return_value=fake_client_inst)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", fake_openai_mod)

    from publishers.weekly_news_x.image_gen import generate_header_image
    out = tmp_path / "x.png"
    result = generate_header_image("summary", out)

    assert result == out
    assert out.exists()
    assert out.read_bytes() == b"PNGDATA"


@pytest.mark.unit
def test_generate_header_image_api_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OpenAI API 예외 시 None"""
    import sys
    import types

    monkeypatch.setenv("OPENAI_API_KEY", "key")

    fake_client_inst = MagicMock()
    fake_client_inst.images.generate.side_effect = RuntimeError("rate limit")

    fake_openai_mod = types.ModuleType("openai")
    fake_openai_mod.OpenAI = MagicMock(return_value=fake_client_inst)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", fake_openai_mod)

    from publishers.weekly_news_x.image_gen import generate_header_image
    result = generate_header_image("summary", tmp_path / "x.png")
    assert result is None
