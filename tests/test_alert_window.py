"""
tests/test_alert_window.py
==========================
core.alert_window v1.0.0 — 창 판정(경계/margin/DST/휴장일/주말/연도누락), main() 동작, stdlib import 검증
"""
from __future__ import annotations

import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from config.market_calendar import US_MARKET_HOLIDAYS_2026
from core import alert_window
from core.alert_window import (
    DEFAULT_GRACE_MINUTES,
    DEFAULT_TZ,
    DEFAULT_WINDOWS,
    WindowDecision,
    decide,
    decide_from_env,
    main,
    parse_windows,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
WINDOWS = parse_windows(DEFAULT_WINDOWS)


def _decide(now_utc: datetime, margin_sec: int = 0) -> WindowDecision:
    return decide(
        now_utc=now_utc,
        windows=WINDOWS,
        grace_min=DEFAULT_GRACE_MINUTES,
        tz=DEFAULT_TZ,
        holidays=US_MARKET_HOLIDAYS_2026,
        margin_sec=margin_sec,
    )


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for key in ("ALERT_WINDOWS", "ALERT_GRACE_MINUTES", "ALERT_TZ", "GITHUB_OUTPUT"):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


# ── grace 경계 (2026-10-13 화, EDT: ET = UTC-4) ───────────────────────────
class TestGraceBoundary:
    def test_091759_outside(self):
        d = _decide(_utc(2026, 10, 13, 13, 17, 59))
        assert d.inside is False
        assert d.target is None
        assert d.reason.startswith("outside_window_et:")

    def test_091800_inside(self):
        d = _decide(_utc(2026, 10, 13, 13, 18, 0))
        assert d.inside is True
        assert d.target == "09:30"
        assert d.et_date == "2026-10-13"
        assert "diff=-12m" in d.reason

    def test_094259_inside(self):
        d = _decide(_utc(2026, 10, 13, 13, 42, 59))
        assert d.inside is True
        assert d.target == "09:30"
        assert "remaining=1s" in d.reason

    def test_094300_outside(self):
        d = _decide(_utc(2026, 10, 13, 13, 43, 0))
        assert d.inside is False
        assert d.target is None
        assert d.reason.startswith("outside_window_et:")


# ── margin 경계 (창 마감 09:43:00, margin 210s → 09:39:30 까지 inside) ─────
class TestMarginBoundary:
    def test_093930_inside_at_exact_margin(self):
        d = _decide(_utc(2026, 10, 13, 13, 39, 30), margin_sec=210)
        assert d.inside is True
        assert "remaining=210s" in d.reason

    def test_093931_closing(self):
        d = _decide(_utc(2026, 10, 13, 13, 39, 31), margin_sec=210)
        assert d.inside is False
        assert d.target == "09:30"  # closing_window 는 target 유지
        assert d.reason.startswith("closing_window_et:")
        assert "remaining=209s margin=210s" in d.reason


# ── DST: EDT 13:21Z / EST 14:21Z 모두 ET 09:21 → 09:30 창 ──────────────────
class TestDst:
    def test_edt_1321z_inside_0930(self):
        d = _decide(_utc(2026, 10, 13, 13, 21, 0), margin_sec=210)
        assert d.inside is True
        assert d.target == "09:30"
        assert "remaining=1320s" in d.reason

    def test_est_1421z_inside_0930(self):
        # 2026-11-02(월) — 11-01 EST 전환 다음 평일
        d = _decide(_utc(2026, 11, 2, 14, 21, 0), margin_sec=210)
        assert d.inside is True
        assert d.target == "09:30"
        assert d.et_date == "2026-11-02"

    def test_est_1321z_outside(self):
        # EST 에서 13:21Z = 08:21 ET → 창 밖 (gate skip 슬롯)
        d = _decide(_utc(2026, 11, 2, 13, 21, 0), margin_sec=210)
        assert d.inside is False


# ── 휴장일 / 주말 / 연도 누락 ────────────────────────────────────────────
class TestCalendar:
    def test_thanksgiving_holiday(self):
        d = _decide(_utc(2026, 11, 26, 14, 21, 0), margin_sec=210)
        assert d.inside is False
        assert d.target is None
        assert d.et_date == "2026-11-26"
        assert d.reason.startswith("holiday_et:")

    def test_saturday_weekend(self):
        d = _decide(_utc(2026, 10, 17, 13, 21, 0), margin_sec=210)
        assert d.inside is False
        assert d.reason.startswith("weekend_et:")

    def test_2027_calendar_missing_year(self):
        d = _decide(_utc(2027, 1, 5, 14, 21, 0), margin_sec=210)
        assert d.inside is True
        assert d.target == "09:30"
        assert "calendar_missing_year=2027" in d.reason

    def test_2026_no_calendar_note(self):
        d = _decide(_utc(2026, 10, 13, 13, 21, 0))
        assert "calendar_missing_year" not in d.reason

    def test_naive_datetime_rejected(self):
        with pytest.raises(ValueError):
            decide(datetime(2026, 10, 13, 13, 21), WINDOWS, 12, DEFAULT_TZ, [date(2026, 1, 1)])


# ── parse_windows ─────────────────────────────────────────────────────────
class TestParseWindows:
    def test_normalize(self):
        assert parse_windows(" 9:30, 10:30 ,,15:30") == ["09:30", "10:30", "15:30"]

    @pytest.mark.parametrize("raw", ["", " , ", "25:00", "09:60", "abc", "09-30"])
    def test_invalid(self, raw: str):
        with pytest.raises(ValueError):
            parse_windows(raw)


# ── decide_from_env / main ───────────────────────────────────────────────
class TestEnvAndMain:
    def test_decide_from_env_defaults(self, clean_env):
        d = decide_from_env(now_utc=_utc(2026, 10, 13, 13, 21, 0))
        assert d.inside is True
        assert d.target == "09:30"

    def test_decide_from_env_custom(self, clean_env):
        clean_env.setenv("ALERT_WINDOWS", "11:00")
        clean_env.setenv("ALERT_GRACE_MINUTES", "5")
        d = decide_from_env(now_utc=_utc(2026, 10, 13, 15, 4, 0))
        assert d.inside is True
        assert d.target == "11:00"

    def test_main_invalid_windows_returns_1(self, clean_env, tmp_path):
        out = tmp_path / "gh_output"
        clean_env.setenv("ALERT_WINDOWS", "9h30")
        clean_env.setenv("GITHUB_OUTPUT", str(out))
        assert main(["--margin-sec", "210"]) == 1
        assert not out.exists()

    def test_main_writes_github_output(self, clean_env, tmp_path, monkeypatch):
        out = tmp_path / "gh_output"
        clean_env.setenv("GITHUB_OUTPUT", str(out))
        fixed = WindowDecision(True, "09:30", "2026-10-13", "inside_window_et:test")
        monkeypatch.setattr(alert_window, "decide_from_env", lambda margin_sec=0: fixed)
        assert main(["--margin-sec", "210"]) == 0
        assert out.read_text(encoding="utf-8").splitlines() == [
            "inside=true",
            "target=09:30",
            "et_date=2026-10-13",
        ]

    def test_main_outside_writes_empty_target(self, clean_env, tmp_path, monkeypatch):
        out = tmp_path / "gh_output"
        clean_env.setenv("GITHUB_OUTPUT", str(out))
        fixed = WindowDecision(False, None, "2026-10-17", "weekend_et:test")
        monkeypatch.setattr(alert_window, "decide_from_env", lambda margin_sec=0: fixed)
        assert main([]) == 0
        assert out.read_text(encoding="utf-8").splitlines() == [
            "inside=false",
            "target=",
            "et_date=2026-10-17",
        ]

    def test_main_calendar_missing_warning(self, clean_env, monkeypatch, capsys):
        fixed = WindowDecision(True, "09:30", "2027-01-05", "inside ... calendar_missing_year=2027")
        monkeypatch.setattr(alert_window, "decide_from_env", lambda margin_sec=0: fixed)
        assert main([]) == 0
        assert "::warning::" in capsys.readouterr().out


# ── gate 는 pip install 없이 동작해야 함 (stdlib only) ─────────────────────
def test_import_is_stdlib_only():
    code = (
        "import core.alert_window, sys; "
        "print('dotenv' in sys.modules, 'supabase' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert result.stdout.strip() == "False False"


def test_load_holidays_does_not_import_dotenv():
    code = (
        "from core.alert_window import load_holidays; import sys; "
        "h = load_holidays(); print(len(h), 'dotenv' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert result.stdout.strip() == "10 False"
