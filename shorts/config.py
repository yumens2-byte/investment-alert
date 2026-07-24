"""Shorts 파이프라인의 환경변수 기반 설정."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ShortsConfig:
    """안전한 기본값을 가진 Shorts 운영 설정."""

    enabled: bool = False
    generation_enabled: bool = True
    upload_enabled: bool = False
    public_enabled: bool = False
    timezone: str = "America/New_York"
    slot_times: tuple[str, ...] = ("08:00", "22:00")
    daily_limit: int = 2
    min_duration_seconds: float = 27.0
    max_duration_seconds: float = 32.0
    bgm_max_cost_usd: float = 0.0
    company_marks_enabled: bool = True

    @classmethod
    def from_env(cls) -> ShortsConfig:
        slots = tuple(
            item.strip()
            for item in os.getenv("SHORTS_SLOT_TIMES", "08:00,22:00").split(",")
            if item.strip()
        )
        config = cls(
            enabled=_bool_env("SHORTS_ENABLED", False),
            generation_enabled=_bool_env("SHORTS_GENERATION_ENABLED", True),
            upload_enabled=_bool_env("SHORTS_UPLOAD_ENABLED", False),
            public_enabled=_bool_env("SHORTS_PUBLIC_ENABLED", False),
            timezone=os.getenv("SHORTS_TIMEZONE", "America/New_York"),
            slot_times=slots,
            daily_limit=int(os.getenv("SHORTS_DAILY_LIMIT", "2")),
            min_duration_seconds=float(os.getenv("SHORTS_MIN_DURATION_SEC", "27")),
            max_duration_seconds=float(os.getenv("SHORTS_MAX_DURATION_SEC", "32")),
            bgm_max_cost_usd=float(os.getenv("BGM_GENERATION_MAX_COST_USD", "0")),
            company_marks_enabled=_bool_env("COMPANY_MARKS_ENABLED", True),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.daily_limit != 2 or self.slot_times != ("08:00", "22:00"):
            raise ValueError("v1은 미국 동부 현지시각 08:00/22:00, 일 2건만 허용합니다")
        if self.timezone != "America/New_York":
            raise ValueError("SHORTS_TIMEZONE은 America/New_York이어야 합니다")
        if self.bgm_max_cost_usd != 0:
            raise ValueError("BGM 생성/취득 비용 상한은 0 USD이어야 합니다")
        if self.min_duration_seconds >= self.max_duration_seconds:
            raise ValueError("영상 최소 길이는 최대 길이보다 짧아야 합니다")

    @property
    def can_publish(self) -> bool:
        """세 개의 kill switch가 모두 켜진 경우에만 공개를 허용한다."""
        return self.enabled and self.upload_enabled and self.public_enabled
