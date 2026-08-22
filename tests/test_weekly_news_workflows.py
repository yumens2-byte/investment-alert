"""Regression checks for weekly-news artifact persistence workflows."""

from __future__ import annotations

from pathlib import Path


WORKFLOWS = (
    Path(".github/workflows/weekly_news_draft.yml"),
    Path(".github/workflows/weekly_news_draft_sunday.yml"),
    Path(".github/workflows/weekly_news_publish.yml"),
)


def test_ignored_sidecars_are_force_staged_from_environment() -> None:
    """Ignored metadata must be staged without interpolating output into shell code."""
    for workflow in WORKFLOWS:
        text = workflow.read_text(encoding="utf-8")
        assert "SIDECAR_PATH_INPUT:" in text
        assert 'git add --force -- "${SIDECAR_PATH_INPUT}"' in text
        assert 'git add --force -- "${{ steps.' not in text


def test_sidecar_is_restricted_to_the_selected_archive() -> None:
    """A malformed publisher output must not allow an unrelated file to be committed."""
    for workflow in WORKFLOWS:
        text = workflow.read_text(encoding="utf-8")
        assert 'EXPECTED_SIDECAR="$(realpath -m -- ' in text
        assert 'SIDECAR_ABS="$(realpath -e -- ' in text
        assert 'if [ "${SIDECAR_ABS}" != "${EXPECTED_SIDECAR}" ]; then' in text


def test_archive_paths_are_canonicalized_before_use() -> None:
    """Archive path validation must reject traversal and symlink escapes."""
    for workflow in WORKFLOWS:
        text = workflow.read_text(encoding="utf-8")
        assert 'ARCHIVE_ROOT="$(realpath -- "logs/weekly_news")"' in text
        assert '"${ARCHIVE_ROOT}"/*.md)' in text

