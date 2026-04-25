"""
제목: 미국 시장 시간대 및 휴장일 관리 모듈
내용: FR-02 장중/장외 프로파일 자동 전환을 위한
      미국 동부시간(ET) 기준 시장 상태를 판정합니다.

주요 함수:
  - get_market_profile(): 현재 시장 시간대 프로파일 반환
  - get_threshold_for_profile(profile): 프로파일별 임계값 반환
  - is_market_hours(): 장중 여부 단순 확인
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

VERSION = "1.0.0"

PROFILE_INTRADAY = "intraday"
PROFILE_EXTENDED = "extended"
PROFILE_HOLIDAY = "holiday"


def _get_et_offset_hours(dt_utc: datetime) -> int:
    year = dt_utc.year
    month = dt_utc.month
    if 4 <= month <= 10:
        return -4
    elif month == 3:
        second_sunday = _nth_weekday(year, 3, 6, 2)
        if dt_utc.date() > second_sunday:
            return -4
        return -5
    elif month == 11:
        first_sunday = _nth_weekday(year, 11, 6, 1)
        if dt_utc.date() < first_sunday:
            return -4
        return -5
    return -5


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first_day = date(year, month, 1)
    days_ahead = weekday - first_day.weekday()
    if days_ahead < 0:
        days_ahead += 7
    first_occurrence = first_day + timedelta(days=days_ahead)
    return first_occurrence + timedelta(weeks=n - 1)


US_MARKET_HOLIDAYS_2026: set[date] = {
    date(2026, 1, 1),
    date(2026, 1, 19),
    date(2026, 2, 16),
    date(2026, 4, 3),
    date(2026, 5, 25),
    date(2026, 6, 19),
    date(2026, 7, 3),
    date(2026, 9, 7),
    date(2026, 11, 26),
    date(2026, 12, 25),
}


def get_market_profile(dt_utc: datetime | None = None) -> str:
    """
    제목: 현재 시장 시간대 프로파일 반환
    내용: UTC datetime을 ET로 변환 후 장중/장외/휴장 판정.

    Returns:
        str: PROFILE_INTRADAY | PROFILE_EXTENDED | PROFILE_HOLIDAY
    """
    if dt_utc is None:
        dt_utc = datetime.now(UTC)

    et_offset = _get_et_offset_hours(dt_utc)
    dt_et = dt_utc + timedelta(hours=et_offset)
    et_date = dt_et.date()

    if dt_et.weekday() >= 5:
        return PROFILE_HOLIDAY

    if et_date in US_MARKET_HOLIDAYS_2026:
        return PROFILE_HOLIDAY

    et_minutes = dt_et.hour * 60 + dt_et.minute
    market_open = 9 * 60 + 30
    market_close = 16 * 60

    if market_open <= et_minutes < market_close:
        return PROFILE_INTRADAY

    return PROFILE_EXTENDED


def get_threshold_for_profile(profile: str) -> dict[str, float]:
    """
    제목: 프로파일별 임계값 반환
    내용: FR-02 장중/장외 민감도 자동 전환.

    Returns:
        dict: LEVEL_THRESHOLDS 형식 임계값
    """
    _PROFILES: dict[str, dict[str, float]] = {
        PROFILE_INTRADAY: {
            "l1_score": 6.5,
            "l2_score": 4.0,
            "l3_score": 2.5,
            "health_l1": 0.85,
            "health_l2": 0.70,
            "health_l3": 0.60,
        },
        PROFILE_EXTENDED: {
            "l1_score": 7.0,
            "l2_score": 5.0,
            "l3_score": 3.0,
            "health_l1": 0.90,
            "health_l2": 0.80,
            "health_l3": 0.70,
        },
        PROFILE_HOLIDAY: {
            "l1_score": 8.0,
            "l2_score": 7.0,
            "l3_score": 5.0,
            "health_l1": 0.95,
            "health_l2": 0.90,
            "health_l3": 0.85,
        },
    }
    return _PROFILES.get(profile, _PROFILES[PROFILE_EXTENDED])


def is_market_hours(dt_utc: datetime | None = None) -> bool:
    """장중 여부 단순 확인"""
    return get_market_profile(dt_utc) == PROFILE_INTRADAY
