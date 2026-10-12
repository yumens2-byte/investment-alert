"""
제목: run_alert v1.4.0 canonical 파이프라인 테스트
내용: 창 판정(P1) / 창 claim(P2) / RunRecorder outcome·종료코드(P3) / DuplicateGuard(P7) /
      채널 실패 격리 / SYSTEM_DEGRADED / DRY_RUN / main() 출력 검증. 모든 외부 호출은 mock.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import run_alert
from core.alert_window import WindowDecision
from detection.duplicate_guard import DuplicateDecision
from detection.macro_news_layer import MacroNewsResult


def _result(level: str = "NONE") -> MacroNewsResult:
    return MacroNewsResult(
        score=0.0,
        level=level,
        news_events=[],
        youtube_events=[],
        news_score=0.0,
        youtube_bonus=0.0,
        reasoning="test",
        health_score=1.0,
    )


# ────────────────────────────────────────────────────────
# 기존 테스트 (v1.3.0 canonical run) — 유지
# ────────────────────────────────────────────────────────

def test_canonical_run_uses_repository_collectors(monkeypatch) -> None:
    monkeypatch.setattr(run_alert, "DRY_RUN", True)
    detect = MagicMock(return_value=_result())
    monkeypatch.setattr("detection.macro_news_layer.MacroNewsLayer.detect", detect)

    summary = run_alert.run()

    assert detect.call_count == 1
    assert summary["level"] == "NONE"
    assert summary["alerts_detected"] == 0
    assert summary["alerts_sent"] == 0


def test_canonical_run_no_longer_imports_legacy_modules(monkeypatch) -> None:
    monkeypatch.setattr(run_alert, "DRY_RUN", True)
    detect = MagicMock(return_value=_result())
    monkeypatch.setattr("detection.macro_news_layer.MacroNewsLayer.detect", detect)

    original_import = __import__

    def guarded_import(name, *args, **kwargs):
        assert name not in {"collectors.news_rss", "collectors.yahoo_finance", "engines.alert_engine"}
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", guarded_import)
    run_alert.run()


def test_canonical_run_isolates_channel_publish_failure(monkeypatch) -> None:
    monkeypatch.setattr(run_alert, "DRY_RUN", True)
    result = _result("L2")
    signal = MagicMock(
        level="L2", score=5.0, reasoning="test", health_score=1.0,
        alert_id="alert-id", top_news_titles=[], top_youtube_titles=[],
        publish_tg_free=True, publish_tg_paid=True, publish_tg_internal=False,
        publish_x=False, should_publish=True, dq_state_dict=None,
    )
    monkeypatch.setattr("detection.macro_news_layer.MacroNewsLayer.detect", lambda self: result)
    monkeypatch.setattr("detection.alert_engine.AlertEngine.process", lambda self, value: signal)
    monkeypatch.setattr(
        "publishers.telegram_publisher.TelegramPublisher.publish_free",
        MagicMock(side_effect=RuntimeError("free failed")),
    )
    paid = MagicMock(return_value="DRY_RUN")
    monkeypatch.setattr("publishers.telegram_publisher.TelegramPublisher.publish_paid", paid)

    summary = run_alert.run()

    assert summary["alerts_sent"] == 1
    assert summary["published"]["tg_free"] is False
    assert summary["published"]["tg_paid"] is True
    assert "free failed" in summary["errors"]["tg_free"]
    paid.assert_called_once()
    assert summary["outcome"] == "partial_fail"
    assert summary["exit_code"] == 0


# ────────────────────────────────────────────────────────
# v1.4.0 하네스
# ────────────────────────────────────────────────────────

INSIDE = WindowDecision(True, "09:30", "2026-10-13", "inside_window_et:2026-10-13T09:35 target=09:30")


def _signal(level: str = "L1", **overrides) -> SimpleNamespace:
    from detection.alert_engine import PUBLISH_POLICY

    policy = PUBLISH_POLICY[level]
    base = dict(
        alert_id="alert-0001-uuid", level=level, score=7.5, reasoning="r", health_score=1.0,
        top_news_titles=["n1"], top_youtube_titles=[],
        publish_x=policy["x"], publish_tg_free=policy["tg_free"],
        publish_tg_paid=policy["tg_paid"], publish_tg_internal=policy["tg_internal"],
        suppressed_reason=None, dq_state_dict={"health": 0.1} if level == "SYSTEM_DEGRADED" else None,
    )
    base.update(overrides)
    ns = SimpleNamespace(**base)
    ns.should_publish = (
        overrides.get("should_publish")
        if "should_publish" in overrides
        else (ns.publish_x or ns.publish_tg_free or ns.publish_tg_paid or ns.publish_tg_internal)
    )
    return ns


def _decision(action: str = "publish_new", suppress: bool = False, reason: str = "new_topic"):
    return DuplicateDecision(
        action=action, topic_key="topic-1", content_fingerprint=None,
        reason=reason, suppress_x=suppress, previous_alert_id="prev-1" if suppress else None,
    )


@pytest.fixture
def harness(monkeypatch, tmp_path):
    """run_alert 외부 의존성 전체 mock. h.store / h.tg / h.x_cls / h.guard / h.saved_rows 제공."""
    h = SimpleNamespace()
    monkeypatch.chdir(tmp_path)
    for key in ("GITHUB_STEP_SUMMARY", "GITHUB_OUTPUT", "DUP_OBSERVE_ONLY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GITHUB_RUN_ID", "run-42")
    monkeypatch.setattr(run_alert, "DRY_RUN", False)

    h.window = INSIDE
    monkeypatch.setattr(run_alert, "decide_from_env", lambda: h.window)
    h.sleep = MagicMock()
    monkeypatch.setattr(run_alert._time, "sleep", h.sleep)

    h.saved_rows = []
    h.store = MagicMock(name="AlertStore()")
    h.store.claim_window.return_value = "claimed"
    h.store.release_claim.return_value = True
    h.store.update_publish_result.return_value = True
    h.store.probe_duplicate_guard_tables.return_value = None
    h.store.save_run.side_effect = lambda row: h.saved_rows.append(dict(row)) or True
    h.store_cls = MagicMock(return_value=h.store)
    monkeypatch.setattr("db.alert_store.AlertStore", h.store_cls)

    h.result = _result("L1")
    layer_cls = MagicMock()
    layer_cls.return_value.detect.side_effect = lambda: h.result
    monkeypatch.setattr("detection.macro_news_layer.MacroNewsLayer", layer_cls)
    monkeypatch.setattr("collectors.news_collector.NewsCollector", MagicMock())
    monkeypatch.setattr("collectors.youtube_collector.YouTubeCollector", MagicMock())

    h.signal = _signal("L1")
    engine_cls = MagicMock()
    engine_cls.return_value.process.side_effect = lambda _r: h.signal
    monkeypatch.setattr("detection.alert_engine.AlertEngine", engine_cls)

    h.formatter = MagicMock()
    h.formatter.format_tg.return_value = "TG_TEXT"
    h.formatter.format_internal.return_value = "INTERNAL_TEXT"
    h.formatter.format_degraded.return_value = "DEGRADED_TEXT"
    h.formatter.format_x.return_value = "X_TEXT"
    monkeypatch.setattr("publishers.alert_formatter.AlertFormatter", MagicMock(return_value=h.formatter))

    h.tg = MagicMock()
    h.tg.publish_free.return_value = "m1"
    h.tg.publish_paid.return_value = "m2"
    h.tg.publish_internal.return_value = "m3"
    h.tg_cls = MagicMock(return_value=h.tg)
    monkeypatch.setattr("publishers.telegram_publisher.TelegramPublisher", h.tg_cls)

    h.x = MagicMock()
    h.x.publish.return_value = "tweet-999"
    h.x_cls = MagicMock(return_value=h.x)
    monkeypatch.setattr("publishers.x_publisher.XPublisher", h.x_cls)

    h.guard = MagicMock()
    h.guard.evaluate_pre_format.return_value = _decision()
    h.guard.evaluate_post_format.return_value = _decision(reason="content_unique")
    h.guard_cls = MagicMock(return_value=h.guard)
    monkeypatch.setattr("detection.duplicate_guard.DuplicateGuard", h.guard_cls)

    h.fallback = MagicMock(return_value=True)
    monkeypatch.setattr("core.audit_fallback.append_audit_fallback", h.fallback)
    return h


def _row(h) -> dict:
    assert len(h.saved_rows) == 1
    return h.saved_rows[0]


# ────────────────────────────────────────────────────────
# P1: 창 밖 / 휴장일
# ────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("reason,expected", [
    ("outside_window_et:2026-10-13T11:00 allowed=[...]", "outside_window"),
    ("weekend_et:2026-10-17T09:30", "outside_window"),
    ("closing_window_et:2026-10-13T09:42 target=09:30", "outside_window"),
    ("holiday_et:2026-11-26T09:30", "holiday"),
])
def test_window_skip_outcomes(harness, reason, expected) -> None:
    harness.window = WindowDecision(False, None, "2026-10-13", reason)

    summary = run_alert.run()

    assert summary["outcome"] == expected
    assert summary["exit_code"] == 0
    assert summary["reason"] == expected
    assert _row(harness)["outcome"] == expected
    assert _row(harness)["detail"]["window_reason"] == reason
    harness.store.claim_window.assert_not_called()
    harness.tg.publish_free.assert_not_called()


@pytest.mark.unit
def test_is_alert_window_wrapper_removed() -> None:
    """P5/P1: _is_alert_window 래퍼는 호출처(_run_legacy) 삭제와 함께 제거 (decide_from_env 직접 사용)"""
    assert not hasattr(run_alert, "_is_alert_window")
    assert not hasattr(run_alert, "_run_legacy")


# ────────────────────────────────────────────────────────
# P3: level_none / cooldown
# ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_level_none_skips_claim(harness) -> None:
    harness.result = _result("NONE")
    harness.signal = _signal("NONE")

    summary = run_alert.run()

    assert summary["outcome"] == "level_none"
    assert summary["exit_code"] == 0
    harness.store.claim_window.assert_not_called()
    row = _row(harness)
    assert row["level"] == "NONE"
    assert row["alert_id"] == "alert-0001-uuid"


@pytest.mark.unit
@pytest.mark.parametrize("reason", ["cooldown_level", "cooldown_topic"])
def test_suppressed_reason_becomes_outcome(harness, reason) -> None:
    harness.signal = _signal(
        "L1", publish_x=False, publish_tg_free=False, publish_tg_paid=False,
        publish_tg_internal=False, suppressed_reason=reason, should_publish=False,
    )

    summary = run_alert.run()

    assert summary["outcome"] == reason
    harness.store.claim_window.assert_not_called()


@pytest.mark.unit
def test_no_target_channel_is_level_none_with_note(harness) -> None:
    harness.signal = _signal(
        "L1", publish_x=False, publish_tg_free=False, publish_tg_paid=False,
        publish_tg_internal=False, should_publish=False,
    )

    summary = run_alert.run()

    assert summary["outcome"] == "level_none"
    assert _row(harness)["detail"]["note"] == "no_target_channel"


# ────────────────────────────────────────────────────────
# P2: claim
# ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_claimed_already_publishes_nothing(harness) -> None:
    harness.store.claim_window.return_value = "already_claimed"

    summary = run_alert.run()

    assert summary["outcome"] == "claimed_already"
    assert summary["exit_code"] == 0
    harness.store.claim_window.assert_called_once_with(
        "2026-10-13:09:30", "alert-0001-uuid", "run-42",
    )
    for method in (harness.tg.publish_free, harness.tg.publish_paid,
                   harness.tg.publish_internal, harness.x.publish):
        method.assert_not_called()
    harness.store.update_publish_result.assert_not_called()
    assert _row(harness)["detail"]["claim_key"] == "2026-10-13:09:30"


@pytest.mark.unit
def test_claim_error_fails_open(harness) -> None:
    harness.store.claim_window.return_value = "error"
    harness.tg.publish_free.side_effect = RuntimeError("down")
    harness.tg.publish_paid.side_effect = RuntimeError("down")
    harness.tg.publish_internal.side_effect = RuntimeError("down")
    harness.x.publish.side_effect = RuntimeError("down")

    summary = run_alert.run()

    assert summary["claim_error"] is True
    assert summary["outcome"] == "all_fail"
    assert _row(harness)["detail"]["claim_error"] is True
    harness.store.release_claim.assert_not_called()  # 소유 claim 없음


# ────────────────────────────────────────────────────────
# P3: published / partial_fail / all_fail
# ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_published_all_channels(harness) -> None:
    summary = run_alert.run()

    assert summary["outcome"] == "published"
    assert summary["exit_code"] == 0
    assert summary["alerts_sent"] == 4
    row = _row(harness)
    assert row["targets"] == ["tg_free", "tg_paid", "tg_internal", "x"]
    assert row["published_channels"] == ["tg_free", "tg_paid", "tg_internal", "x"]
    assert row["errors"] == {}
    kwargs = harness.store.update_publish_result.call_args.kwargs
    assert kwargs["published_channels"] == ["tg_free", "tg_paid", "tg_internal", "x"]
    assert kwargs["x_published"] is True
    harness.store.set_cooldown.assert_called_once_with("L1", "alert-0001-uuid")
    harness.store.release_claim.assert_not_called()
    harness.fallback.assert_not_called()
    # jitter: 4채널 → 두 번째 채널부터 3회
    assert harness.sleep.call_count == 3
    for call in harness.sleep.call_args_list:
        assert 2.0 <= call.args[0] <= 5.0


@pytest.mark.unit
def test_partial_fail(harness) -> None:
    harness.x.publish.side_effect = RuntimeError("x 503")

    summary = run_alert.run()

    assert summary["outcome"] == "partial_fail"
    assert summary["exit_code"] == 0
    row = _row(harness)
    assert row["published_channels"] == ["tg_free", "tg_paid", "tg_internal"]
    assert "x 503" in row["errors"]["x"]
    harness.store.release_claim.assert_not_called()
    harness.store.set_cooldown.assert_called_once()


@pytest.mark.unit
def test_all_fail_releases_claim(harness) -> None:
    for m in (harness.tg.publish_free, harness.tg.publish_paid,
              harness.tg.publish_internal, harness.x.publish):
        m.side_effect = RuntimeError("down")

    summary = run_alert.run()

    assert summary["outcome"] == "all_fail"
    assert summary["exit_code"] == 1
    harness.store.release_claim.assert_called_once_with("2026-10-13:09:30", "run-42")
    harness.store.set_cooldown.assert_not_called()
    assert _row(harness)["published_channels"] == []


@pytest.mark.unit
def test_update_publish_result_false_writes_audit_fallback(harness) -> None:
    harness.store.update_publish_result.return_value = False

    run_alert.run()

    harness.fallback.assert_called_once()
    record = harness.fallback.call_args.args[0]
    assert record["stage"] == "update_publish_result"
    assert record["alert_id"] == "alert-0001-uuid"


@pytest.mark.unit
def test_system_degraded_uses_format_degraded(harness) -> None:
    harness.result = _result("SYSTEM_DEGRADED")
    harness.signal = _signal("SYSTEM_DEGRADED")

    summary = run_alert.run()

    harness.formatter.format_degraded.assert_called_once_with({"health": 0.1}, "alert-0001-uuid")
    harness.formatter.format_internal.assert_not_called()
    harness.tg.publish_internal.assert_called_once_with("DEGRADED_TEXT")
    assert summary["outcome"] == "published"
    harness.sleep.assert_not_called()  # 단일 채널 → jitter 없음


@pytest.mark.unit
def test_pipeline_exception_records_error(harness) -> None:
    harness.store_cls.return_value.claim_window.side_effect = ValueError("boom")

    with pytest.raises(ValueError):
        run_alert.run()

    row = _row(harness)
    assert row["outcome"] == "error"
    assert "boom" in row["detail"]["exception"]


# ────────────────────────────────────────────────────────
# P7: DuplicateGuard
# ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_dup_guard_block_when_observe_off(harness, monkeypatch) -> None:
    monkeypatch.setenv("DUP_OBSERVE_ONLY", "false")
    harness.guard.evaluate_pre_format.return_value = _decision(
        "suppress_duplicate", True, "same_topic_within_suppress_window",
    )

    summary = run_alert.run()

    assert summary["outcome"] == "duplicate_blocked"
    assert summary["exit_code"] == 0
    harness.x.publish.assert_not_called()
    harness.tg.publish_free.assert_called_once()
    harness.tg.publish_paid.assert_called_once()
    # 정상 internal 1회 + 중복 억제 운영 알림 1회
    assert harness.tg.publish_internal.call_count == 2
    assert "[X 중복 억제]" in harness.tg.publish_internal.call_args_list[1].args[0]
    assert summary["published"]["x"] is False
    assert summary["alerts_sent"] == 3
    row = _row(harness)
    assert row["targets"] == ["tg_free", "tg_paid", "tg_internal"]
    assert row["detail"]["dup_action"] == "suppress_duplicate"
    assert row["detail"]["dup_observe_only"] is False
    harness.guard.evaluate_post_format.assert_not_called()
    harness.guard.record_x_fingerprint.assert_not_called()
    harness.guard.record_topic_observation.assert_called_once()
    assert harness.guard.record_topic_observation.call_args.kwargs["x_published"] is False


@pytest.mark.unit
def test_dup_guard_post_format_block(harness, monkeypatch) -> None:
    monkeypatch.setenv("DUP_OBSERVE_ONLY", "false")
    harness.guard.evaluate_post_format.return_value = _decision(
        "suppress_duplicate", True, "content_fingerprint_collision",
    )

    summary = run_alert.run()

    assert summary["outcome"] == "duplicate_blocked"
    harness.x.publish.assert_not_called()
    assert _row(harness)["detail"]["dup_reason"] == "content_fingerprint_collision"


@pytest.mark.unit
def test_dup_guard_observe_only_publishes_x(harness) -> None:
    harness.guard.evaluate_pre_format.return_value = _decision(
        "suppress_duplicate", True, "same_topic_within_suppress_window",
    )

    summary = run_alert.run()

    assert summary["outcome"] == "published"
    harness.x.publish.assert_called_once_with("X_TEXT")
    harness.guard.record_x_fingerprint.assert_called_once_with(
        "alert-0001-uuid", "topic-1", "X_TEXT", "tweet-999",
    )
    detail = _row(harness)["detail"]
    assert detail["dup_observe_only"] is True
    assert detail["dup_would_block"] is True
    assert harness.tg.publish_internal.call_count == 1


@pytest.mark.unit
def test_dup_guard_probe_failure_fails_open(harness, capsys) -> None:
    harness.store.probe_duplicate_guard_tables.return_value = "APIError: relation does not exist"

    summary = run_alert.run()

    assert summary["outcome"] == "published"
    harness.x.publish.assert_called_once()
    harness.guard.evaluate_pre_format.assert_not_called()
    harness.guard.record_x_fingerprint.assert_not_called()
    detail = _row(harness)["detail"]
    assert detail["dup_guard"] == "unavailable"
    assert "relation does not exist" in detail["dup_guard_error"]
    assert "::warning::DuplicateGuard unavailable" in capsys.readouterr().out


@pytest.mark.unit
def test_dup_guard_not_probed_when_x_not_targeted(harness) -> None:
    harness.result = _result("L2")
    harness.signal = _signal("L2")

    summary = run_alert.run()

    assert summary["outcome"] == "published"
    harness.store.probe_duplicate_guard_tables.assert_not_called()
    harness.x.publish.assert_not_called()


# ────────────────────────────────────────────────────────
# DRY_RUN
# ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_dry_run_skips_claim_and_db(harness) -> None:
    run_alert.DRY_RUN = True  # harness monkeypatch 가 복원

    summary = run_alert.run()

    assert summary["outcome"] == "published"
    harness.store_cls.assert_not_called()
    harness.guard_cls.assert_not_called()
    assert harness.saved_rows == []
    harness.sleep.assert_not_called()
    harness.tg_cls.assert_called_with(dry_run=True)
    assert harness.formatter.format_x.call_args.kwargs["force_template"] is True


# ────────────────────────────────────────────────────────
# main(): 종료코드 / Step Summary / GITHUB_OUTPUT / 실패 알림
# ────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("outcome,exit_code,notified,warned", [
    ("published", 0, False, False),
    ("duplicate_blocked", 0, False, False),
    ("claimed_already", 0, False, False),
    ("partial_fail", 0, True, True),
    ("all_fail", 1, True, False),
])
def test_main_exit_code_and_outputs(monkeypatch, tmp_path, capsys,
                                    outcome, exit_code, notified, warned) -> None:
    summary_file = tmp_path / "summary.md"
    output_file = tmp_path / "output.txt"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    errors = {"x": "RuntimeError: x 503"} if outcome in ("partial_fail", "all_fail") else {}
    monkeypatch.setattr(run_alert, "run", lambda: {
        "outcome": outcome, "exit_code": exit_code, "alerts_sent": 2, "level": "L1",
        "score": 7.5, "published": {"tg_free": True, "x": False}, "errors": errors,
    })
    notify = MagicMock()
    monkeypatch.setattr(run_alert, "_notify_internal_failure", notify)

    with pytest.raises(SystemExit) as exc:
        run_alert.main()

    assert exc.value.code == exit_code
    md = summary_file.read_text(encoding="utf-8")
    assert "### run_alert 결과" in md
    assert f"| outcome | {outcome} |" in md
    assert "| channels | tg_free |" in md
    assert output_file.read_text(encoding="utf-8") == f"result={outcome}\nalerts_sent=2\n"
    assert notify.called is notified
    assert ("::warning title=alert partial_fail::" in capsys.readouterr().out) is warned


@pytest.mark.unit
def test_main_exception_maps_to_error(monkeypatch, tmp_path) -> None:
    output_file = tmp_path / "output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(run_alert, "run", MagicMock(side_effect=RuntimeError("crash")))
    notify = MagicMock()
    monkeypatch.setattr(run_alert, "_notify_internal_failure", notify)

    with pytest.raises(SystemExit) as exc:
        run_alert.main()

    assert exc.value.code == 1
    assert "result=error" in output_file.read_text(encoding="utf-8")
    assert notify.call_args.args[0]["errors"]["run"] == "crash"


@pytest.mark.unit
def test_main_outputs_ignore_unwritable_path(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "nodir" / "s.md"))
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "nodir" / "o.txt"))
    monkeypatch.setattr(run_alert, "run", lambda: {"outcome": "published", "exit_code": 0})

    with pytest.raises(SystemExit) as exc:
        run_alert.main()

    assert exc.value.code == 0


@pytest.mark.unit
def test_notify_internal_failure_escapes_html(monkeypatch) -> None:
    tg = MagicMock()
    monkeypatch.setattr("publishers.telegram_publisher.TelegramPublisher", MagicMock(return_value=tg))
    monkeypatch.setenv("GITHUB_RUN_ID", "77")

    run_alert._notify_internal_failure({
        "outcome": "all_fail", "level": "L1", "alerts_sent": 0,
        "errors": {"x": "RuntimeError: <bad> & co"},
    })

    text = tg.publish_internal.call_args.args[0]
    assert "<b>[run_alert] all_fail</b>" in text
    assert "&lt;bad&gt; &amp; co" in text
    assert "/actions/runs/77" in text


@pytest.mark.unit
def test_notify_internal_failure_swallows_errors(monkeypatch) -> None:
    tg = MagicMock()
    tg.publish_internal.side_effect = RuntimeError("TG INTERNAL 환경변수 누락")
    monkeypatch.setattr("publishers.telegram_publisher.TelegramPublisher", MagicMock(return_value=tg))

    run_alert._notify_internal_failure({"outcome": "error", "errors": {}})  # 예외 전파 없음


@pytest.mark.unit
def test_publish_jitter_skipped_in_dry_run(monkeypatch) -> None:
    sleep = MagicMock()
    monkeypatch.setattr(run_alert._time, "sleep", sleep)
    monkeypatch.setattr(run_alert, "DRY_RUN", True)
    run_alert._publish_jitter()
    sleep.assert_not_called()
    monkeypatch.setattr(run_alert, "DRY_RUN", False)
    run_alert._publish_jitter()
    sleep.assert_called_once()


# ────────────────────────────────────────────────────────
# 리뷰 반영: M1 claim 대상 / M2 토큰 마스킹 / m2 예외 시 release / m3·m5 warning
# ────────────────────────────────────────────────────────

FAKE_TOKEN_URL = "https://api.telegram.org/bot123456789:AAH-fake_TOKEN-xyz/sendMessage"


@pytest.mark.unit
@pytest.mark.parametrize("level", ["L3", "SYSTEM_DEGRADED"])
def test_internal_only_skips_claim(harness, level) -> None:
    harness.result = _result(level)
    harness.signal = _signal(level)

    summary = run_alert.run()

    assert summary["outcome"] == "published"
    harness.store.claim_window.assert_not_called()
    harness.tg.publish_internal.assert_called_once()
    assert _row(harness)["detail"]["claim_skipped"] == "internal_only"


@pytest.mark.unit
@pytest.mark.parametrize("level", ["L1", "L2"])
def test_public_channel_levels_claim(harness, level) -> None:
    harness.result = _result(level)
    harness.signal = _signal(level)

    run_alert.run()

    harness.store.claim_window.assert_called_once()


@pytest.mark.unit
def test_redact_secrets() -> None:
    assert run_alert._redact_secrets(f"err {FAKE_TOKEN_URL}") == (
        "err https://api.telegram.org/bot<redacted>/sendMessage"
    )
    assert run_alert._redact_secrets("no token here") == "no token here"


@pytest.mark.unit
def test_bot_token_masked_in_all_error_sinks(harness, monkeypatch, tmp_path) -> None:
    harness.tg.publish_free.side_effect = RuntimeError(f"TG FREE 발행 실패: 401 for url: {FAKE_TOKEN_URL}")
    summary_file = tmp_path / "s.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))

    with pytest.raises(SystemExit) as exc:
        run_alert.main()

    assert exc.value.code == 0
    secret = "AAH-fake_TOKEN-xyz"
    row = _row(harness)
    assert secret not in json.dumps(row, ensure_ascii=False)
    assert "bot<redacted>" in row["errors"]["tg_free"]
    kwargs = harness.store.update_publish_result.call_args.kwargs
    assert secret not in str(kwargs["tg_free_error"])
    assert secret not in summary_file.read_text(encoding="utf-8")
    alert_texts = [c.args[0] for c in harness.tg.publish_internal.call_args_list]
    assert any("[run_alert] partial_fail" in t for t in alert_texts)
    assert all(secret not in t for t in alert_texts)


@pytest.mark.unit
def test_notify_internal_failure_redacts_raw_errors(monkeypatch) -> None:
    tg = MagicMock()
    monkeypatch.setattr("publishers.telegram_publisher.TelegramPublisher", MagicMock(return_value=tg))

    run_alert._notify_internal_failure({"outcome": "error", "errors": {"run": FAKE_TOKEN_URL}})

    assert "AAH-fake_TOKEN-xyz" not in tg.publish_internal.call_args.args[0]


@pytest.mark.unit
def test_main_exception_message_redacted(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setattr(run_alert, "run", MagicMock(side_effect=RuntimeError(FAKE_TOKEN_URL)))
    notify = MagicMock()
    monkeypatch.setattr(run_alert, "_notify_internal_failure", notify)

    with pytest.raises(SystemExit):
        run_alert.main()

    assert "AAH-fake_TOKEN-xyz" not in notify.call_args.args[0]["errors"]["run"]


@pytest.mark.unit
def test_exception_before_publish_releases_claim(harness) -> None:
    harness.formatter.format_tg.side_effect = RuntimeError("formatter crash")

    with pytest.raises(RuntimeError):
        run_alert.run()

    harness.store.release_claim.assert_called_once_with("2026-10-13:09:30", "run-42")
    assert _row(harness)["outcome"] == "error"


@pytest.mark.unit
def test_exception_after_partial_publish_keeps_claim(harness) -> None:
    harness.guard.record_topic_observation.side_effect = RuntimeError("late crash")

    with pytest.raises(RuntimeError):
        run_alert.run()

    harness.store.release_claim.assert_not_called()  # 성공 채널 있음 → claim 유지


@pytest.mark.unit
def test_claim_error_prints_warning(harness, capsys) -> None:
    harness.store.claim_window.return_value = "error"

    run_alert.run()

    assert "::warning::window claim 저장소 오류" in capsys.readouterr().out


@pytest.mark.unit
@pytest.mark.parametrize("event,reason,warned", [
    ("schedule", "closing_window_et:x target=09:30", True),
    ("schedule", "outside_window_et:x", True),
    ("schedule", "holiday_et:x", False),
    ("workflow_dispatch", "outside_window_et:x", False),
])
def test_gate_passed_but_window_left_warning(harness, monkeypatch, capsys,
                                            event, reason, warned) -> None:
    monkeypatch.setenv("GITHUB_EVENT_NAME", event)
    harness.window = WindowDecision(False, None, "2026-10-13", reason)

    run_alert.run()

    assert ("::warning::gate 통과 후 창 이탈" in capsys.readouterr().out) is warned


# ────────────────────────────────────────────────────────
# m8(a): save_run row 스키마 정합 (005 ia_alert_run 컬럼) + JSON 직렬화
# ────────────────────────────────────────────────────────

IA_ALERT_RUN_COLUMNS = frozenset({
    "gh_run_id", "gh_run_attempt", "gh_event", "started_at", "finished_at", "duration_ms",
    "dry_run", "outcome", "alert_id", "level", "score", "news_events", "youtube_events",
    "targets", "published_channels", "errors", "detail",
})


def test_ia_alert_run_columns_match_migration_005() -> None:
    import re
    from pathlib import Path

    sql_path = Path(__file__).parent.parent / "db/migrations/005_add_alert_run_and_window_claim.sql"
    sql = sql_path.read_text(encoding="utf-8")
    body = sql.split("CREATE TABLE IF NOT EXISTS ia_alert_run (")[1].split("CONSTRAINT")[0]
    cols = {m.group(1) for m in re.finditer(r"^\s+([a-z_]+)\s+[A-Z]", body, re.MULTILINE)}
    assert cols - {"id", "created_at"} == IA_ALERT_RUN_COLUMNS


@pytest.mark.unit
@pytest.mark.parametrize("scenario", [
    "published", "partial", "allfail", "claimerr", "dupblock", "probefail", "exc", "already",
    "none", "outside", "internal_only",
])
def test_save_run_row_schema_and_json(harness, monkeypatch, scenario) -> None:
    h = harness
    if scenario == "partial":
        h.tg.publish_free.side_effect = RuntimeError("x")
    if scenario == "allfail":
        for m in (h.tg.publish_free, h.tg.publish_paid, h.tg.publish_internal, h.x.publish):
            m.side_effect = RuntimeError("x")
    if scenario == "claimerr":
        h.store.claim_window.return_value = "error"
    if scenario == "already":
        h.store.claim_window.return_value = "already_claimed"
    if scenario == "dupblock":
        monkeypatch.setenv("DUP_OBSERVE_ONLY", "false")
        h.guard.evaluate_pre_format.return_value = _decision("suppress_duplicate", True, "same")
    if scenario == "probefail":
        h.store.probe_duplicate_guard_tables.return_value = "Err: boom"
    if scenario == "none":
        h.result = _result("NONE")
        h.signal = _signal("NONE")
    if scenario == "outside":
        h.window = WindowDecision(False, None, "2026-10-13", "outside_window_et:x")
    if scenario == "internal_only":
        h.result = _result("L3")
        h.signal = _signal("L3")
    if scenario == "exc":
        h.formatter.format_tg.side_effect = RuntimeError("fmt")
        with pytest.raises(RuntimeError):
            run_alert.run()
    else:
        run_alert.run()

    row = _row(h)
    assert set(row) <= IA_ALERT_RUN_COLUMNS, set(row) - IA_ALERT_RUN_COLUMNS
    json.dumps(row, allow_nan=False)
    assert row["outcome"] is not None
