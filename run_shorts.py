"""Backward-compatible entrypoint for the YouTube Shorts pilot CLI.

The canonical implementation lives in ``run_youtube_shorts.py``.  This shim keeps
older CI commands and local runbooks working while avoiding duplicate inline
imports that previously caused ruff E402/F811 failures.
"""YouTube Shorts 파이프라인 진입점.

현재 릴리스는 외부 생성 API와 YouTube를 호출하지 않는 pilot만 제공한다.
"""

from __future__ import annotations

from run_youtube_shorts import main
import argparse
from datetime import UTC, datetime
from pathlib import Path

from shorts.pilot import run_due_pilot, run_pilot


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("--now는 timezone-aware ISO-8601이어야 합니다")
    return parsed.astimezone(UTC)
from pathlib import Path

from shorts.pilot import run_pilot


def main() -> int:
    parser = argparse.ArgumentParser(description="Investment Alert Shorts pilot")
    parser.add_argument("--pilot", action="store_true", help="dry-run pilot 실행")
    parser.add_argument("--dispatch", action="store_true", help="현재 시각이 슬롯일 때만 pilot 실행")
    parser.add_argument("--no-render", action="store_true", help="FFmpeg 렌더 생략")
    parser.add_argument("--output-dir", default="logs/shorts/pilot", help="산출물 디렉터리")
    parser.add_argument("--now", type=_parse_utc, help="테스트용 UTC ISO-8601 시각")
    args = parser.parse_args()
    if args.pilot == args.dispatch:
        parser.error("--pilot 또는 --dispatch 중 정확히 하나를 지정해야 합니다")
    if args.dispatch:
        manifest_path = run_due_pilot(
            Path(args.output_dir), now=args.now, render_video=not args.no_render
        )
        if manifest_path is None:
            print("Shorts pilot SKIP: 현재 시각은 08:00/22:00 ET 슬롯이 아닙니다")
            return 0
    else:
        manifest_path = run_pilot(Path(args.output_dir), render_video=not args.no_render)
    parser.add_argument("--no-render", action="store_true", help="FFmpeg 렌더 생략")
    parser.add_argument("--output-dir", default="logs/shorts/pilot", help="산출물 디렉터리")
    args = parser.parse_args()
    if not args.pilot:
        parser.error("현재는 안전한 --pilot 모드만 지원합니다")
    manifest_path = run_pilot(Path(args.output_dir), render_video=not args.no_render)
    print(f"Shorts pilot PASS: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
