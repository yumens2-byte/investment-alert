from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from shorts.pilot import run_pilot
from shorts.rendering.ffmpeg_renderer import validate_media


def test_pilot_without_render_writes_safe_manifest(tmp_path: Path) -> None:
    manifest_path = run_pilot(tmp_path, render_video=False)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["validation"]["passed"] is True
    assert payload["metadata"]["dry_run"] is True
    assert payload["metadata"]["upload_attempted"] is False
    assert payload["metadata"]["bgm"] == "none"
    assert payload["video_path"] is None


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg 필요")
def test_rendered_pilot_meets_media_contract(tmp_path: Path) -> None:
    manifest_path = run_pilot(tmp_path, render_video=True)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    video_path = Path(payload["video_path"])
    assert video_path.is_file()
    probe = validate_media(video_path, expected_duration=30.0)
    assert float(probe["format"]["duration"]) == pytest.approx(30.0, abs=0.25)
