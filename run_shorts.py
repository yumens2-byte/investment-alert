"""Backward-compatible entrypoint for the YouTube Shorts pilot CLI.

The canonical implementation lives in ``run_youtube_shorts.py``.  This shim keeps
older CI commands and local runbooks working while avoiding duplicate inline
imports that previously caused ruff E402/F811 failures.
"""

from __future__ import annotations

from run_youtube_shorts import main

if __name__ == "__main__":
    raise SystemExit(main())
