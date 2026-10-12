"""
core/alert_window.py
====================
역할: Alert 실행 창(window) 판정 — stdlib only
사용:
  - GitHub Actions gate job : python -m core.alert_window --margin-sec 210
  - run_alert.run()         : decide_from_env()

규칙(현행 run_alert._is_alert_window v1.3.0 의미 유지):
  - ALERT_TZ 로컬 시각 기준, 분 단위 diff = now_min - target_min
  - -grace <= diff <= +grace 이면 창 안 (예: 09:30, grace 12 → 09:18:00 ~ 09:42:59)
  - 주말(로컬) 제외
신규:
  - NYSE 휴장일 제외 (config.market_calendar.US_MARKET_HOLIDAYS_2026)
  - margin_sec: 창 마감까지 남은 초 < margin_sec 이면 창 밖(closing_window) 판정
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

VERSION = "1.0.0"

logger = logging.getLogger("alert_window")

DEFAULT_WINDOWS = "09:30,10:30,15:30"
DEFAULT_GRACE_MINUTES = 12
DEFAULT_TZ = "America/New_York"


@dataclass(frozen=True)
class WindowDecision:
    inside: bool
    target: str | None  # "09:30" — 창 밖이면 None (closing_window 는 target 유지)
    et_date: str        # ALERT_TZ 기준 로컬 날짜 "YYYY-MM-DD"
    reason: str


def parse_windows(raw: str) -> list[str]:
    """'09:30, 10:30' → ['09:30', '10:30'] (HH:MM 정규화, 형식 오류는 ValueError)"""
    windows: list[str] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        hh, mm = item.split(":")
        hour, minute = int(hh), int(mm)
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise ValueError(f"invalid ALERT_WINDOWS item: {item!r}")
        windows.append(f"{hour:02d}:{minute:02d}")
    if not windows:
        raise ValueError("ALERT_WINDOWS is empty")
    return windows


def decide(
    now_utc: datetime,
    windows: Iterable[str],
    grace_min: int,
    tz: str,
    holidays: Iterable[date],
    margin_sec: int = 0,
) -> WindowDecision:
    """순수 함수: 입력만으로 창 판정 (I/O·환경변수·현재시각 접근 없음)"""
    if now_utc.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware")
    holiday_set = frozenset(holidays)
    now_local = now_utc.astimezone(ZoneInfo(tz))
    local_date = now_local.date()
    et_date = local_date.isoformat()
    stamp = now_local.isoformat()

    if now_local.weekday() >= 5:
        return WindowDecision(False, None, et_date, f"weekend_et:{stamp}")
    if local_date in holiday_set:
        return WindowDecision(False, None, et_date, f"holiday_et:{stamp}")

    calendar_note = (
        "" if any(h.year == local_date.year for h in holiday_set)
        else f" calendar_missing_year={local_date.year}"
    )
    now_min = now_local.hour * 60 + now_local.minute
    now_sec = now_min * 60 + now_local.second
    allowed = list(windows)

    for hhmm in allowed:
        hour, minute = map(int, hhmm.split(":"))
        target_min = hour * 60 + minute
        diff = now_min - target_min
        if -grace_min <= diff <= grace_min:
            # 마지막 허용 분(diff=+grace)이 끝나는 시점 = target + grace + 1분
            remaining = (target_min + grace_min + 1) * 60 - now_sec
            if remaining < margin_sec:
                return WindowDecision(
                    False, hhmm, et_date,
                    f"closing_window_et:{stamp} target={hhmm} "
                    f"remaining={remaining}s margin={margin_sec}s",
                )
            return WindowDecision(
                True, hhmm, et_date,
                f"inside_window_et:{stamp} target={hhmm} grace={grace_min}m "
                f"diff={diff}m remaining={remaining}s{calendar_note}",
            )

    return WindowDecision(
        False, None, et_date,
        f"outside_window_et:{stamp} allowed={allowed} grace={grace_min}m{calendar_note}",
    )


def load_holidays() -> frozenset[date]:
    """config.market_calendar 의 2026 휴장일 (import 체인 stdlib only)"""
    from config.market_calendar import US_MARKET_HOLIDAYS_2026
    return frozenset(US_MARKET_HOLIDAYS_2026)


def decide_from_env(now_utc: datetime | None = None, margin_sec: int = 0) -> WindowDecision:
    """환경변수(ALERT_TZ/ALERT_WINDOWS/ALERT_GRACE_MINUTES) 기반 판정. 기본값은 v1.3.0 과 동일"""
    return decide(
        now_utc=now_utc or datetime.now(UTC),
        windows=parse_windows(os.getenv("ALERT_WINDOWS", DEFAULT_WINDOWS)),
        grace_min=int(os.getenv("ALERT_GRACE_MINUTES", str(DEFAULT_GRACE_MINUTES))),
        tz=os.getenv("ALERT_TZ", DEFAULT_TZ),
        holidays=load_holidays(),
        margin_sec=margin_sec,
    )


def _write_github_output(decision: WindowDecision) -> None:
    path = os.getenv("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"inside={'true' if decision.inside else 'false'}\n")
        f.write(f"target={decision.target or ''}\n")
        f.write(f"et_date={decision.et_date}\n")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
    parser = argparse.ArgumentParser(description="Alert window gate")
    parser.add_argument("--margin-sec", type=int, default=0)
    args = parser.parse_args(argv)

    logger.info(f"[alert_window] v{VERSION} 시작 | margin={args.margin_sec}s")
    try:
        decision = decide_from_env(margin_sec=args.margin_sec)
    except (ValueError, ImportError) as exc:  # 설정 오류는 gate 실패로 노출
        logger.error(f"[alert_window] 설정 오류: {type(exc).__name__}: {exc}")
        return 1
    logger.info(f"[alert_window] inside={decision.inside} target={decision.target} "
                f"et_date={decision.et_date} reason={decision.reason}")
    if "calendar_missing_year" in decision.reason:
        print(f"::warning::market_calendar 휴장일 미등록 연도 — {decision.et_date}")
    _write_github_output(decision)
    return 0


if __name__ == "__main__":
    sys.exit(main())
