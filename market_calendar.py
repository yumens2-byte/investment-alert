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

# ────────────────────────────────────────────────────────
# 시장 프로파일 상수
# ────────────────────────────────────────────────────────
PROFILE_INTRADAY = "intraday"    # 장중 09:30~16:00 ET
PROFILE_EXTENDED = "extended"   # 장외 (프리마켓/애프터마켓)
PROFILE_HOLIDAY  = "holiday"    # 미국 증시 휴장일

# ────────────────────────────────────────────────────────
# ET 타임존 정의
# ────────────────────────────────────────────────────────
# 제목: 미국 동부시간 타임존
# 내용: 4~10월 EDT(UTC-4), 11~3월 EST(UTC-5)
#       Python 3.9+의 zoneinfo 없이 간단 계산으로 처리
def _get_et_offset_hours(dt_utc: datetime) -> int:
    """
    제목: UTC 기준 ET 오프셋 계산
    내용: EDT(4월~10월 두 번째 일요일): -4
          EST(11월 첫 번째 일요일~3월): -5

    Args:
        dt_utc: UTC datetime

    Returns:
        int: ET 오프셋 (-4 또는 -5)
    """
    year = dt_utc.year
    month = dt_utc.month

    # DST 시작: 3월 두 번째 일요일 02:00 EST
    # DST 종료: 11월 첫 번째 일요일 02:00 EDT
    if 4 <= month <= 10:
        return -4  # EDT
    elif month == 3:
        # 3월 두 번째 일요일 이후이면 EDT
        second_sunday = _nth_weekday(year, 3, 6, 2)  # 6=일요일, 2=두 번째
        if dt_utc.date() > second_sunday:
            return -4
        return -5
    elif month == 11:
        # 11월 첫 번째 일요일 이전이면 EDT
        first_sunday = _nth_weekday(year, 11, 6, 1)
        if dt_utc.date() < first_sunday:
            return -4
        return -5
    return -5  # EST


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """
    제목: n번째 요일 날짜 계산
    내용: 예: 3월 두 번째 일요일 → _nth_weekday(2026, 3, 6, 2)

    Args:
        year: 연도
        month: 월
        weekday: 요일 (0=월요일, 6=일요일)
        n: n번째

    Returns:
        date: 해당 날짜
    """
    first_day = date(year, month, 1)
    days_ahead = weekday - first_day.weekday()
    if days_ahead < 0:
        days_ahead += 7
    first_occurrence = first_day + timedelta(days=days_ahead)
    return first_occurrence + timedelta(weeks=n - 1)


# ────────────────────────────────────────────────────────
# 미국 증시 주요 휴장일 (2026)
# ────────────────────────────────────────────────────────
# 제목: 2026년 NYSE 휴장일
# 내용: 연간 업데이트 필요 (config/us_market_holidays.py와 통합 가능)
US_MARKET_HOLIDAYS_2026: set[date] = {
    date(2026, 1, 1),   # 신정
    date(2026, 1, 19),  # MLK Day
    date(2026, 2, 16),  # Presidents Day
    date(2026, 4, 3),   # Good Friday
    date(2026, 5, 25),  # Memorial Day
    date(2026, 6, 19),  # Juneteenth
    date(2026, 7, 3),   # Independence Day (관측일)
    date(2026, 9, 7),   # Labor Day
    date(2026, 11, 26), # Thanksgiving
    date(2026, 12, 25), # Christmas
}


def get_market_profile(dt_utc: datetime | None = None) -> str:
    """
    제목: 현재 시장 시간대 프로파일 반환
    내용: UTC datetime을 ET로 변환 후 장중/장외/휴장 판정.
          주말 및 NYSE 휴장일은 HOLIDAY로 반환.

    처리 플로우:
      1. UTC → ET 변환
      2. 주말/휴장일 체크 → PROFILE_HOLIDAY
      3. 09:30~16:00 ET → PROFILE_INTRADAY
      4. 나머지 → PROFILE_EXTENDED

    Args:
        dt_utc: 판정 기준 UTC datetime (None이면 현재 시각)

    Returns:
        str: PROFILE_INTRADAY | PROFILE_EXTENDED | PROFILE_HOLIDAY
    """
    if dt_utc is None:
        dt_utc = datetime.now(UTC)

    et_offset = _get_et_offset_hours(dt_utc)
    dt_et = dt_utc + timedelta(hours=et_offset)
    et_date = dt_et.date()

    # 제목: 주말 체크
    if dt_et.weekday() >= 5:  # 5=토, 6=일
        return PROFILE_HOLIDAY

    # 제목: 휴장일 체크
    if et_date in US_MARKET_HOLIDAYS_2026:
        return PROFILE_HOLIDAY

    # 제목: 장중 체크 (09:30 ~ 16:00 ET)
    et_minutes = dt_et.hour * 60 + dt_et.minute
    market_open  = 9 * 60 + 30   # 570
    market_close = 16 * 60        # 960

    if market_open <= et_minutes < market_close:
        return PROFILE_INTRADAY

    return PROFILE_EXTENDED


def get_threshold_for_profile(profile: str) -> dict[str, float]:
    """
    제목: 프로파일별 임계값 반환
    내용: FR-02 장중/장외 민감도 자동 전환.
          장중은 민감도 상향(임계값 낮춤), 장외는 강화(임계값 높임).

    프로파일별 임계값:
      INTRADAY: 민감도 상향 — L1:6.5, L2:4.0
      EXTENDED: 기본값      — L1:7.0, L2:5.0
      HOLIDAY:  민감도 최소 — L1:8.0, L2:7.0

    Args:
        profile: PROFILE_INTRADAY | PROFILE_EXTENDED | PROFILE_HOLIDAY

    Returns:
        dict: LEVEL_THRESHOLDS 형식의 임계값 딕셔너리
    """
    _PROFILES: dict[str, dict[str, float]] = {
        PROFILE_INTRADAY: {
            "l1_score":  6.5,   # 장중 민감도 상향 (7.0 → 6.5)
            "l2_score":  4.0,   # 장중 민감도 상향 (5.0 → 4.0)
            "l3_score":  2.5,   # 장중 민감도 상향 (3.0 → 2.5)
            "health_l1": 0.85,  # 장중 health 완화 (0.90 → 0.85)
            "health_l2": 0.70,
            "health_l3": 0.60,
        },
        PROFILE_EXTENDED: {
            "l1_score":  7.0,   # 기본값
            "l2_score":  5.0,
            "l3_score":  3.0,
            "health_l1": 0.90,
            "health_l2": 0.80,
            "health_l3": 0.70,
        },
        PROFILE_HOLIDAY: {
            "l1_score":  8.0,   # 휴장일 민감도 최소 (오탐 방지)
            "l2_score":  7.0,
            "l3_score":  5.0,
            "health_l1": 0.95,
            "health_l2": 0.90,
            "health_l3": 0.85,
        },
    }
    return _PROFILES.get(profile, _PROFILES[PROFILE_EXTENDED])


def is_market_hours(dt_utc: datetime | None = None) -> bool:
    """
    제목: 장중 여부 단순 확인

    Args:
        dt_utc: 기준 시각 (None이면 현재)

    Returns:
        bool: 장중이면 True
    """
    return get_market_profile(dt_utc) == PROFILE_INTRADAY
