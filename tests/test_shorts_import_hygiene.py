from __future__ import annotations

import ast
from pathlib import Path

IMPORT_HYGIENE_FILES = (
    Path("run_youtube_shorts.py"),
    Path("run_shorts.py"),
    Path("tests/test_shorts_runtime.py"),
    Path("tests/test_shorts_action_config.py"),
    Path("tests/test_shorts_import_hygiene.py"),
)
DEPRECATED_COLLISION_PATHS = (
    Path("tests/test_shorts_pilot.py"),
    Path("tests/test_shorts_workflow.py"),
)


def _assert_import_hygiene(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    seen: set[tuple[str, str, str | None]] = set()
    import_section_open = True
    for index, node in enumerate(tree.body):
        if isinstance(node, ast.Expr) and index == 0 and isinstance(node.value, ast.Constant):
            continue  # module docstring
        if isinstance(node, ast.ImportFrom):
            assert import_section_open, f"{path}:{node.lineno} module-level late import"
            aliases = ((node.module or "", alias.name, alias.asname) for alias in node.names)
        elif isinstance(node, ast.Import):
            assert import_section_open, f"{path}:{node.lineno} module-level late import"
            aliases = (("", alias.name, alias.asname) for alias in node.names)
        else:
            import_section_open = False
            continue
        for key in aliases:
            assert key not in seen, f"{path}:{node.lineno} duplicate import {key}"
            seen.add(key)


def test_shorts_source_imports_are_unique_and_top_level() -> None:
    """중복 patch로 발생한 E402/F811 import 회귀를 ruff 이전에도 탐지한다."""
    for path in IMPORT_HYGIENE_FILES:
        _assert_import_hygiene(path)


def test_deprecated_collision_paths_do_not_reappear() -> None:
    for path in DEPRECATED_COLLISION_PATHS:
        assert not path.exists(), f"stale patch collision path reappeared: {path}"
