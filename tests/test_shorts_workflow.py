from __future__ import annotations


def _text() -> str:
    with open(".github/workflows/shorts_pilot.yml", encoding="utf-8") as workflow_file:
        return workflow_file.read()
from pathlib import Path

WORKFLOW = Path(".github/workflows/shorts_pilot.yml")


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_workflow_is_isolated_and_safe_by_default() -> None:
    text = _text()
    assert "group: youtube-shorts-pilot" in text
    assert 'SHORTS_ENABLED: "false"' in text
    assert 'SHORTS_UPLOAD_ENABLED: "false"' in text
    assert 'SHORTS_PUBLIC_ENABLED: "false"' in text
    assert "secrets." not in text
    assert "run_alert.py" not in text
    assert "run_sector_alert.py" not in text


def test_workflow_uses_dispatcher_for_schedule() -> None:
    text = _text()
    assert 'cron: "7 * * * *"' in text
    assert "python run_shorts.py --dispatch" in text
    assert "github.event_name == 'schedule'" in text


def test_workflow_tests_and_verifies_media_before_artifact() -> None:
    text = _text()
    assert "tests/test_shorts_pilot.py" in text
    assert "ffprobe -v error" in text
    assert "actions/upload-artifact@v4" in text
    assert "retention-days: 14" in text
