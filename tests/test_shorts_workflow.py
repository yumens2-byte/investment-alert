from __future__ import annotations

import ast
from pathlib import Path

WORKFLOW = Path(".github/workflows/shorts_pilot.yml")
IMPORT_HYGIENE_FILES = (
    Path("run_shorts.py"),
    Path("tests/test_shorts_pilot.py"),
    Path("tests/test_shorts_workflow.py"),
)
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


def test_shorts_source_imports_are_unique_and_top_level() -> None:
    """중복 patch로 발생했던 E402/F811 import 회귀를 ruff 이전에도 탐지한다."""
    for path in IMPORT_HYGIENE_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        seen: set[tuple[str, str, str | None]] = set()
        import_section_open = True
        for index, node in enumerate(tree.body):
            if isinstance(node, ast.Expr) and index == 0 and isinstance(node.value, ast.Constant):
                continue  # module docstring
            if isinstance(node, ast.ImportFrom):
                assert import_section_open, f"{path}:{node.lineno} module-level late import"
                for alias in node.names:
                    key = (node.module or "", alias.name, alias.asname)
                    assert key not in seen, f"{path}:{node.lineno} duplicate import {key}"
                    seen.add(key)
            elif isinstance(node, ast.Import):
                assert import_section_open, f"{path}:{node.lineno} module-level late import"
                for alias in node.names:
                    key = ("", alias.name, alias.asname)
                    assert key not in seen, f"{path}:{node.lineno} duplicate import {key}"
                    seen.add(key)
            else:
                import_section_open = False
