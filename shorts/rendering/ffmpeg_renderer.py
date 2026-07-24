"""Pilot용 결정론적 9:16 MP4 렌더러."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from shorts.domain.models import Script


class RenderError(RuntimeError):
    """FFmpeg 렌더 또는 기술 검증 실패."""


def render_pilot(script: Script, output_path: Path) -> Path:
    """외부/라이선스 자산 없이 무음 motion-card pilot를 렌더한다."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RenderError("ffmpeg 실행 파일을 찾을 수 없습니다")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    duration = script.duration_ms / 1000
    command = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=0x10243A:s=1080x1920:r=30:d={duration}",
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=channel_layout=stereo:sample_rate=48000:d={duration}",
        "-shortest",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    if result.returncode:
        raise RenderError(result.stderr[-1000:])
    validate_media(output_path, expected_duration=duration)
    return output_path


def validate_media(path: Path, expected_duration: float) -> dict[str, object]:
    """ffprobe로 Shorts 핵심 기술 사양을 확인한다."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RenderError("ffprobe 실행 파일을 찾을 수 없습니다")
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    if result.returncode:
        raise RenderError(result.stderr[-1000:])
    probe = json.loads(result.stdout)
    streams = probe.get("streams", [])
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    duration = float(probe["format"]["duration"])
    if not video or (video.get("width"), video.get("height")) != (1080, 1920):
        raise RenderError("영상 해상도는 1080x1920이어야 합니다")
    if video.get("codec_name") != "h264" or video.get("pix_fmt") != "yuv420p":
        raise RenderError("영상은 H.264/yuv420p이어야 합니다")
    if not audio or audio.get("codec_name") != "aac":
        raise RenderError("AAC 오디오 스트림이 필요합니다")
    if abs(duration - expected_duration) > 0.25:
        raise RenderError(f"영상 길이 불일치: actual={duration}, expected={expected_duration}")
    return probe
