"""
제목: YouTube 채널 RSS 수집 모듈
내용: GitHub Secret YOUTUBE_CHANNELS에 설정된 채널의 RSS를 수집하고
      한글 키워드 매칭 및 채널 가중치를 적용하여 CollectorEvent 리스트를 반환합니다.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

import feedparser

from collectors.base import BaseCollector, CollectorEvent
from config.settings import CHANNEL_WEIGHTS, YOUTUBE_TODAY_ONLY, YOUTUBE_WINDOW_HOURS
from core.logger import get_logger

VERSION = "1.0.0"

logger = get_logger(__name__)

YOUTUBE_URGENT_KEYWORDS_KR: dict[str, float] = {
    "긴급": 3.0,
    "속보": 3.5,
    "대폭락": 4.0,
    "폭락장": 3.5,
    "급등": 2.5,
    "급락": 2.5,
    "위기": 3.0,
    "경고": 2.5,
    "주의보": 2.0,
    "서킷브레이커": 4.0,
    "거래정지": 4.0,
}

YOUTUBE_EXCLUSION_PATTERNS: list[str] = [
    "시장 마감",
    "오늘의 시황",
    "오늘의 요약",
    "오늘의 정리",
    "[26년",
    "[25년",
    "예상",
    "전망",
    "정리",
    "recap",
]

KEYWORD_SCORE_CAP: float = 10.0
KEYWORD_THRESHOLD: float = 2.0
YOUTUBE_RSS_TEMPLATE: str = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


class YouTubeCollector(BaseCollector):
    """
    제목: YouTube 채널 RSS 수집 및 분석
    내용: YOUTUBE_CHANNELS 환경변수에 설정된 채널의 RSS를 수집하고
          한글 키워드 필터링 및 채널 가중치를 적용합니다.
    """

    def __init__(
        self,
        channels_str: str | None = None,
        window_hours: int = YOUTUBE_WINDOW_HOURS,
        today_only: bool = YOUTUBE_TODAY_ONLY,
    ) -> None:
        """
        Args:
            channels_str: 채널 목록 문자열 ("이름:ID,이름:ID").
            window_hours: 수집 시간 범위 (레거시, today_only=False 시 사용)
            today_only: True이면 UTC 당일 00:00 이후 영상만 수집
        """
        super().__init__(source_name="youtube_collector", timeout=10, max_retries=2)

        raw = channels_str if channels_str is not None else os.getenv("YOUTUBE_CHANNELS", "")
        self.channels: list[dict[str, Any]] = self._parse_channels(raw)
        self.window_hours = window_hours
        self.today_only = today_only

        mode = "당일(UTC)" if today_only else f"{window_hours}h"
        logger.info(
            f"[YouTubeCollector] v{VERSION} 초기화 "
            f"(채널={len(self.channels)}개, window={mode})"
        )

    def collect(self) -> list[CollectorEvent]:
        logger.info("[YouTubeCollector] YouTube 수집 시작")

        if not self.channels:
            logger.warning("[YouTubeCollector] 등록된 채널이 없습니다. YOUTUBE_CHANNELS 확인 필요")
            return []

        raw_events: list[CollectorEvent] = []
        for channel in self.channels:
            channel_events = self._collect_channel(channel)
            raw_events.extend(channel_events)

        logger.info(f"[YouTubeCollector] RSS 수집 완료: {len(raw_events)}건")

        filtered = self._filter_by_keywords(raw_events)
        logger.info(f"[YouTubeCollector] 키워드 필터 후: {len(filtered)}건")

        filtered.sort(key=lambda e: e.keyword_score * e.channel_weight, reverse=True)

        logger.info(f"[YouTubeCollector] 수집 완료: 최종 {len(filtered)}건")
        return filtered

    def _collect_channel(self, channel: dict[str, Any]) -> list[CollectorEvent]:
        events: list[CollectorEvent] = []
        channel_name = channel.get("name", "unknown")
        channel_id = channel.get("id", "")
        channel_weight = channel.get("weight", 1.0)

        rss_url = YOUTUBE_RSS_TEMPLATE.format(channel_id=channel_id)

        try:
            feed = self._retry_request(feedparser.parse, rss_url)
            entries = getattr(feed, "entries", [])

            for entry in entries:
                published_at = self._parse_entry_date(entry)

                if not self._is_within_window(published_at):
                    continue

                video_id = entry.get("yt_videoid", "")
                title = entry.get("title", "").strip()
                description = entry.get("summary", "")[:500]
                url = f"https://www.youtube.com/watch?v={video_id}"

                if not title or not video_id:
                    continue

                event_id = CollectorEvent.compute_event_id(channel_name, url, title)

                event = CollectorEvent(
                    source_type="youtube",
                    source_name=channel_name,
                    event_id=event_id,
                    title=title,
                    summary=description,
                    url=url,
                    published_at=published_at,
                    tier=None,
                    channel_weight=channel_weight,
                    auto_l1=False,
                )

                events.append(event)

        except Exception as e:
            logger.warning(
                f"[YouTubeCollector] {channel_name} 수집 실패: "
                f"{type(e).__name__}: {e}"
            )

        return events

    def _filter_by_keywords(self, events: list[CollectorEvent]) -> list[CollectorEvent]:
        filtered: list[CollectorEvent] = []

        for event in events:
            combined = f"{event.title} {event.summary}"

            if self._has_exclusion_pattern(combined):
                continue

            score, matched = self._match_keywords_kr(combined)

            if score >= KEYWORD_THRESHOLD:
                event.keyword_score = score
                event.matched_keywords = matched
                filtered.append(event)

        return filtered

    def _match_keywords_kr(self, text: str) -> tuple[float, list[str]]:
        score = 0.0
        matched: list[str] = []

        for keyword, weight in YOUTUBE_URGENT_KEYWORDS_KR.items():
            if keyword in text:
                score += weight
                matched.append(keyword)

        score = min(score, KEYWORD_SCORE_CAP)
        return score, matched

    def _has_exclusion_pattern(self, text: str) -> bool:
        return any(pattern in text for pattern in YOUTUBE_EXCLUSION_PATTERNS)

    def _parse_channels(self, channels_str: str) -> list[dict[str, Any]]:
        channels: list[dict[str, Any]] = []

        if not channels_str or not channels_str.strip():
            return channels

        for pair in channels_str.split(","):
            pair = pair.strip()
            if ":" not in pair:
                logger.warning(f"[YouTubeCollector] 채널 파싱 실패 (포맷 오류): '{pair}'")
                continue

            name, channel_id = pair.split(":", 1)
            name = name.strip()
            channel_id = channel_id.strip()

            if not name or not channel_id:
                continue

            weight = CHANNEL_WEIGHTS.get(name, 1.0)
            channels.append({"name": name, "id": channel_id, "weight": weight})

        return channels

    def _parse_entry_date(self, entry: Any) -> datetime:
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            import calendar
            ts = calendar.timegm(entry.published_parsed)
            return datetime.fromtimestamp(ts, tz=UTC)

        published_str = entry.get("published", "")
        if published_str:
            try:
                from dateutil.parser import parse as dateutil_parse
                dt = dateutil_parse(published_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                return dt
            except Exception:
                pass

        return datetime.now(UTC)

    def _is_within_window(self, published_at: datetime) -> bool:
        """
        today_only=True이면 UTC 당일 00:00 이후 영상만 허용.
        today_only=False이면 window_hours 기준 레거시 동작.
        """
        now = datetime.now(UTC)
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=UTC)

        if self.today_only:
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            return published_at >= today_start

        return published_at >= now - timedelta(hours=self.window_hours)

    def _normalize_datetime(self, published_str: str) -> str:
        try:
            from dateutil.parser import parse as dateutil_parse
            dt = dateutil_parse(published_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.isoformat()
        except Exception:
            return datetime.now(UTC).isoformat()
