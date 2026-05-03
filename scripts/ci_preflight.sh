#!/usr/bin/env bash
set -euo pipefail

# 배포 전 최소 점검: 컨플릭트 마커/문법/린트/핵심 테스트
if rg -n "^(<<<<<<< |>>>>>>> |=======$)" .; then
  echo "[preflight] merge conflict markers detected" >&2
  exit 1
fi

# 배포 전 최소 점검: 문법/린트/핵심 테스트
python -m py_compile collectors/youtube_collector.py
ruff check . --line-length=100 --fix
pytest -q tests/test_collector_internals.py::test_collect_channel_rss_fail_then_api_fallback_returns_events tests/test_event_scarcity_guard.py --no-cov
