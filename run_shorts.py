"""YouTube Shorts 파이프라인 진입점.

현재 릴리스는 외부 생성 API와 YouTube를 호출하지 않는 pilot만 제공한다.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from shorts.pilot import run_pilot


def main() -> int:
    parser = argparse.ArgumentParser(description="Investment Alert Shorts pilot")
    parser.add_argument("--pilot", action="store_true", help="dry-run pilot 실행")
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
