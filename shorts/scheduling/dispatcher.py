"""미국 동부 현지시각 기준 08:00/22:00 슬롯 판정."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from config.market_calendar import US_MARKET_HOLIDAYS_2026
from shorts.domain.models import ContentMode, SlotName

_SLOTS = {SlotName.MORNING: time(8, 0), SlotName.NIGHT: time(22, 0)}


@dataclass(frozen=True)
class SlotClaim:
    content_id: str
    slot: SlotName
    mode: ContentMode
    local_time: datetime


def content_mode(local_time: datetime) -> ContentMode:
    local_date = local_time.date()
    if local_time.weekday() >= 5 or local_date in US_MARKET_HOLIDAYS_2026:
        return ContentMode.EVERGREEN
    return ContentMode.MARKET_DAY


def due_slot(now_utc: datetime, window_minutes: int = 30) -> SlotClaim | None:
    """현재 UTC 시각이 슬롯 허용 window 안이면 멱등 content ID를 반환한다."""
    if now_utc.tzinfo is None:
        raise ValueError("now_utc는 timezone-aware datetime이어야 합니다")
    if window_minutes <= 0:
        raise ValueError("window_minutes는 양수여야 합니다")

    local = now_utc.astimezone(ZoneInfo("America/New_York"))
    for slot, slot_time in _SLOTS.items():
        start = datetime.combine(local.date(), slot_time, tzinfo=local.tzinfo)
        if start <= local < start + timedelta(minutes=window_minutes):
            return SlotClaim(
                content_id=f"{local.date().isoformat()}:{slot.value}",
                slot=slot,
                mode=content_mode(local),
                local_time=local,
            )
    return None


def current_utc() -> datetime:
    return datetime.now(UTC)
