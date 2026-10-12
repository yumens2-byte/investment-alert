"""
제목: RunRecorder 단위 테스트
내용: outcome 확정 규칙, 예외 처리, fail-open 기록, exit_code 매핑을 Mock 기반으로 검증합니다.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.run_recorder import FAIL_EXIT_OUTCOMES, OUTCOMES, RunRecorder


@pytest.fixture
def store() -> MagicMock:
    s = MagicMock()
    s.save_run.return_value = True
    return s


@pytest.mark.unit
def test_outcomes_has_11_values() -> None:
    assert len(OUTCOMES) == 11
    assert FAIL_EXIT_OUTCOMES == {"all_fail", "error"}
    assert FAIL_EXIT_OUTCOMES <= OUTCOMES


@pytest.mark.unit
def test_finish_twice_keeps_first_outcome(store: MagicMock) -> None:
    rec = RunRecorder(alert_store=store)
    rec.finish("level_none")
    rec.finish("published", extra=1)
    assert rec.row["outcome"] == "level_none"
    assert rec.row["detail"]["extra"] == 1


@pytest.mark.unit
def test_finish_undefined_outcome_becomes_error(store: MagicMock) -> None:
    rec = RunRecorder(alert_store=store)
    rec.finish("bogus")
    assert rec.row["outcome"] == "error"
    assert rec.row["detail"]["invalid_outcome"] == "bogus"
    assert rec.exit_code == 1


@pytest.mark.unit
def test_with_exception_records_error_and_reraises(store: MagicMock) -> None:
    with pytest.raises(ValueError, match="boom"):
        with RunRecorder(alert_store=store) as rec:
            raise ValueError("boom")
    assert rec.row["outcome"] == "error"
    assert rec.row["detail"]["exception"] == "ValueError: boom"
    assert "finished_at" in rec.row
    assert isinstance(rec.row["duration_ms"], int)
    store.save_run.assert_called_once_with(rec.row)


@pytest.mark.unit
def test_with_without_finish_records_outcome_not_set(store: MagicMock) -> None:
    with RunRecorder(alert_store=store) as rec:
        pass
    assert rec.row["outcome"] == "error"
    assert rec.row["detail"]["exception"] == "outcome_not_set"


@pytest.mark.unit
def test_save_run_false_does_not_raise(store: MagicMock) -> None:
    store.save_run.return_value = False
    with RunRecorder(alert_store=store) as rec:
        rec.finish("published")
    store.save_run.assert_called_once()
    assert rec.row["outcome"] == "published"


@pytest.mark.unit
def test_save_run_exception_is_swallowed(store: MagicMock) -> None:
    store.save_run.side_effect = RuntimeError("DB down")
    with RunRecorder(alert_store=store) as rec:
        rec.finish("published")
    assert rec.exit_code == 0


@pytest.mark.unit
def test_dry_run_does_not_persist(store: MagicMock) -> None:
    with RunRecorder(alert_store=store, dry_run=True) as rec:
        rec.finish("published")
    store.save_run.assert_not_called()
    assert rec.row["dry_run"] is True


@pytest.mark.unit
def test_store_none_does_not_persist() -> None:
    with RunRecorder(alert_store=None) as rec:
        rec.finish("level_none")
    assert rec.row["outcome"] == "level_none"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("outcome", "expected"),
    [(o, 1 if o in ("all_fail", "error") else 0) for o in sorted(OUTCOMES)],
)
def test_exit_code_mapping(outcome: str, expected: int) -> None:
    rec = RunRecorder()
    rec.finish(outcome)
    assert rec.exit_code == expected


@pytest.mark.unit
def test_set_and_add_detail(store: MagicMock) -> None:
    with RunRecorder(alert_store=store) as rec:
        rec.set(level="L1", score=8.0, targets=["x"])
        rec.add_detail(dup_guard="unavailable")
        rec.add_detail(dup_guard_error="RuntimeError: x")
        rec.finish("published")
    assert rec.row["level"] == "L1"
    assert rec.row["targets"] == ["x"]
    assert rec.row["detail"] == {
        "dup_guard": "unavailable",
        "dup_guard_error": "RuntimeError: x",
    }


@pytest.mark.unit
def test_github_env_captured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_RUN_ID", "999")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    rec = RunRecorder()
    assert rec.row["gh_run_id"] == "999"
    assert rec.row["gh_run_attempt"] == "2"
    assert rec.row["gh_event"] == "schedule"
