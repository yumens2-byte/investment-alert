"""
제목: YouTube 채널 RSS 수집 모듈
내용: GitHub Secret YOUTUBE_CHANNELS에 설정된 4개 채널의 RSS를 수집하고
      한글 키워드 매칭 및 채널 가중치를 적용하여 CollectorEvent 리스트를 반환합니다.

주요 클래스:
  - YouTubeCollector: YouTube RSS 수집, 키워드 필터, 채널 가중치 적용

주요 함수:
  - YouTubeCollector.collect(): 전체 수집 파이프라인 실행
  - YouTubeCollector._collect_channel(channel): 단일 채널 RSS 수집
  - YouTubeCollector._filter_by_keywords(events): 한글 키워드 1차 필터
  - YouTubeCollector._match_keywords_kr(text): 키워드 점수 산출
  - YouTubeCollector._parse_channels(channels_str): Secret 문자열 파싱
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

import feedparser
import requests

from collectors.base import BaseCollector, CollectorEvent
from config.settings import CHANNEL_WEIGHTS, YOUTUBE_TODAY_ONLY, YOUTUBE_WINDOW_HOURS
from core.logger import get_logger

VERSION = "1.0.0"

logger = get_logger(__name__)


# ────────────────────────────────────────────────────────
# YouTube 긴급 키워드 (한글)
# ────────────────────────────────────────────────────────
# 제목: 한글 긴급 키워드
# 내용: 3인 전문가 협의 설계서 Round 2 확정값
YOUTUBE_URGENT_KEYWORDS_KR: dict[str, float] = {
    "긴급": 3.0,
    "속보": 3.5,
    "대폭락": 4.0,
    "폭락장": 3.5,
    "급등": 2.5,
    "급락": 2.5,
    "위기": 3.0,
    "경고": 2.5,
    "위협": 2.0,
    "버블": 1,
    "전쟁": 1,
    "북한": 1,
    "조선": 1,
    "주의보": 2.0,
    "서킷브레이커": 4.0,
    "거래정지": 4.0,
}

# 제목: 제외 패턴
# 내용: 일상 브리핑/시황 콘텐츠는 긴급 Alert 대상 아님
#       2026-04-25 추가: 오늘의 요약, 날짜 브리핑 형식 ([26년/[25년)
YOUTUBE_EXCLUSION_PATTERNS: list[str] = [
    "시장 마감",
    "오늘의 시황",
    "오늘의 요약",    # 일상 요약 브리핑 제외
    "오늘의 정리",    # 일상 정리 브리핑 제외
    "[26년",          # 날짜 형식 브리핑 제외 ([26년 04월 xx일)
    "[25년",          # 날짜 형식 브리핑 제외 ([25년 xx월 xx일)
    "예상",
    "전망",
    "정리",
    "recap",
]

# 제목: 키워드 점수 상한
# 내용: 복수 키워드 중복 매칭 시 점수 상한
KEYWORD_SCORE_CAP: float = 10.0

# 제목: 키워드 매칭 최소 임계값
KEYWORD_THRESHOLD: float = 2.0

# 제목: YouTube RSS URL 템플릿
YOUTUBE_RSS_TEMPLATE: str = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


class YouTubeCollector(BaseCollector):
    """
    제목: YouTube 채널 RSS 수집 및 분석
    내용: YOUTUBE_CHANNELS 환경변수에 설정된 채널의 RSS를 수집하고
          한글 키워드 필터링 및 채널 가중치를 적용합니다.
          유튜브는 "보강 레이어" 역할이므로 L1 단독 트리거 불가.

    책임:
      - YOUTUBE_CHANNELS Secret 파싱 (이름:ID,이름:ID 포맷)
      - 4개 채널 RSS 수집 (48시간 이내)
      - 한글 긴급 키워드 11종 매칭
      - 채널 가중치 적용 (weighted_score = keyword_score × channel_weight)
      - 일상 브리핑 제외 패턴 필터링
    """

    def __init__(
        self,
        channels_str: str | None = None,
        window_hours: int = YOUTUBE_WINDOW_HOURS,
        today_only: bool = YOUTUBE_TODAY_ONLY,
    ) -> None:
        """
        제목: YouTubeCollector 초기화

        Args:
            channels_str: 채널 목록 문자열 ("이름:ID,이름:ID").
                         None이면 YOUTUBE_CHANNELS 환경변수에서 로드.
            window_hours: 수집 시간 범위 (레거시, today_only=False 시 사용)
            today_only: True이면 UTC 당일 00:00 이후 영상만 수집
        """
        super().__init__(source_name="youtube_collector", timeout=10, max_retries=2)

        raw = channels_str if channels_str is not None else os.getenv("YOUTUBE_CHANNELS", "")
        raw = self._normalize_channels_str(raw)
        self.channels: list[dict[str, Any]] = self._parse_channels(raw)
        self.window_hours = window_hours
        self.today_only = today_only

        # 운영 관측성: 채널별 수집 실패 목록(run 단위)
        self.last_failed_channels: list[str] = []
        # RSS 실패 시 2차 대체 수집용 YouTube Data API Key
        self.youtube_api_key: str = os.getenv("YOUTUBE_API_KEY", "").strip()

        mode = "당일(UTC)" if today_only else f"{window_hours}h"
        logger.info(
            f"[YouTubeCollector] v{VERSION} 초기화 "
            f"(채널={len(self.channels)}개, window={mode})"
        )

    def collect(self) -> list[CollectorEvent]:
        """
        제목: YouTube 수집 전체 파이프라인
        내용: 채널 RSS 수집 → 키워드 필터 순으로 실행.
              각 채널 실패는 경고 후 다음 채널로 계속 진행합니다.

        처리 플로우:
          1. 등록된 채널별 RSS 수집 (48시간 이내)
          2. 한글 키워드 1차 필터
          3. weighted_score(= keyword_score × channel_weight) 내림차순 정렬

        Returns:
            list[CollectorEvent]: 최종 필터링된 이벤트 (weighted_score 내림차순)
        """
        logger.info("[YouTubeCollector] YouTube 수집 시작")

        if not self.channels:
            logger.warning("[YouTubeCollector] 등록된 채널이 없습니다. YOUTUBE_CHANNELS 확인 필요")
            return []

        # Step 1: 채널별 수집
        raw_events: list[CollectorEvent] = []
        self.last_failed_channels = []
        for channel in self.channels:
            channel_events = self._collect_channel(channel)
            raw_events.extend(channel_events)

        logger.info(f"[YouTubeCollector] RSS 수집 완료: {len(raw_events)}건")

        # B-fix: DQ Monitor용 raw events 보관 (키워드 필터 전)
        # YouTube는 별도 validator가 없으므로 RSS 수집 결과 자체가 raw
        self.last_raw_events = list(raw_events)

        # Step 2: 키워드 필터
        filtered = self._filter_by_keywords(raw_events)
        logger.info(f"[YouTubeCollector] 키워드 필터 후: {len(filtered)}건")
        if raw_events and not filtered:
            filter_stats = self._summarize_filter_reasons(raw_events)
            logger.warning(
                "[YouTubeCollector] 운영 참고 — raw는 존재하나 전부 필터됨: "
                f"excluded={filter_stats['excluded_count']}, "
                f"below_threshold={filter_stats['below_threshold_count']}, "
                f"threshold={KEYWORD_THRESHOLD}"
            )

        # Step 3: weighted_score 내림차순 정렬
        filtered.sort(key=lambda e: e.keyword_score * e.channel_weight, reverse=True)

        logger.info(f"[YouTubeCollector] 수집 완료: 최종 {len(filtered)}건")
        return filtered

    def _collect_channel(self, channel: dict[str, Any]) -> list[CollectorEvent]:
        """
        제목: 단일 채널 RSS 수집
        내용: 1차 RSS, 실패 시 2차 YouTube Data API v3로 대체 수집합니다.

        Args:
            channel: {'name': str, 'id': str, 'weight': float}

        Returns:
            list[CollectorEvent]: 수집된 이벤트
        """
        events: list[CollectorEvent] = []
        channel_name = channel.get("name", "unknown")
        channel_id = channel.get("id", "")
        channel_weight = channel.get("weight", 1.0)

        rss_url = YOUTUBE_RSS_TEMPLATE.format(channel_id=channel_id)

        try:
            feed = self._retry_request(feedparser.parse, rss_url)
            status = getattr(feed, "status", None)
            # RSS 파서가 예외를 던지지 않아도 HTTP 4xx/5xx를 status로 제공할 수 있으므로
            # 채널 실패로 분류하여 운영 경고(last_failed_channels)에 반영한다.
            if isinstance(status, int) and status >= 400:
                self.last_failed_channels.append(channel_name)
                logger.warning(
                    f"[YouTubeCollector] {channel_name} RSS HTTP 비정상: status={status}, url={rss_url}"
                )
                return self._collect_channel_via_api(channel_name, channel_id, channel_weight)
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
                events.append(CollectorEvent(
                    source_type="youtube", source_name=channel_name, event_id=event_id,
                    title=title, summary=description, url=url, published_at=published_at,
                    tier=None, channel_weight=channel_weight, auto_l1=False,
                ))

        except Exception as e:
            # 방어코드: 구버전 객체/테스트 더블에서도 AttributeError 없이 동작
            if not hasattr(self, "last_failed_channels"):
                self.last_failed_channels = []
            self.last_failed_channels.append(channel_name)
            logger.warning(
                f"[YouTubeCollector] {channel_name} 수집 실패: "
                f"{type(e).__name__}: {e}, url={rss_url}"
            )
            return self._collect_channel_via_api(channel_name, channel_id, channel_weight)

        return events

    def _collect_channel_via_api(self, channel_name: str, channel_id: str, channel_weight: float) -> list[CollectorEvent]:
        """RSS 실패 시 YouTube Data API v3 search endpoint로 대체 수집."""
        if not self.youtube_api_key:
            logger.warning(f"[YouTubeCollector] {channel_name} API 대체 수집 스킵: YOUTUBE_API_KEY 미설정")
            return []
        url = (
            "https://www.googleapis.com/youtube/v3/search"
            f"?channelId={channel_id}&type=video&order=date&maxResults=10&key={self.youtube_api_key}"
        )
        try:
            resp = requests.get(url, timeout=self.timeout)
            if resp.status_code >= 400:
                logger.warning(f"[YouTubeCollector] {channel_name} API HTTP 비정상: status={resp.status_code}, url={url}")
                return []
            data = resp.json()
            items = data.get("items", [])
            events: list[CollectorEvent] = []
            for item in items:
                vid = (((item.get("id") or {}).get("videoId")) or "").strip()
                sn = item.get("snippet") or {}
                title = (sn.get("title") or "").strip()
                if not vid or not title:
                    continue
                published_raw = str(sn.get("publishedAt") or "")
                # API publishedAt는 ISO8601 문자열이므로 datetime으로 변환해 window 검증
                try:
                    published_at = datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
                except Exception:
                    published_at = datetime.now(UTC)
                if published_at.tzinfo is None:
                    published_at = published_at.replace(tzinfo=UTC)
                if not self._is_within_window(published_at):
                    continue
                vurl = f"https://www.youtube.com/watch?v={vid}"
                event_id = CollectorEvent.compute_event_id(channel_name, vurl, title)
                events.append(CollectorEvent(
                    source_type="youtube", source_name=channel_name, event_id=event_id,
                    title=title, summary=(sn.get("description") or "")[:500],
                    url=vurl, published_at=published_at, tier=None,
                    channel_weight=channel_weight, auto_l1=False,
                ))
            logger.info(f"[YouTubeCollector] {channel_name} API 대체 수집 성공: {len(events)}건")
            return events
        except Exception as e:
            logger.warning(f"[YouTubeCollector] {channel_name} API 대체 수집 실패: {type(e).__name__}: {e}, url={url}")
            return []

    def _filter_by_keywords(self, events: list[CollectorEvent]) -> list[CollectorEvent]:
        """
        제목: 한글 키워드 1차 필터
        내용: 제외 패턴에 해당하는 이벤트를 먼저 제거하고,
              긴급 키워드 점수가 KEYWORD_THRESHOLD 이상인 이벤트만 반환합니다.

        Args:
            events: 필터 대상 이벤트 리스트

        Returns:
            list[CollectorEvent]: 임계값 초과 이벤트
        """
        filtered: list[CollectorEvent] = []

        for event in events:
            combined = f"{event.title} {event.summary}"

            # 제목: 제외 패턴 검사
            # 내용: 일상 브리핑은 긴급 Alert 대상 아님
            if self._has_exclusion_pattern(combined):
                continue

            # 제목: 긴급 키워드 점수 산출
            score, matched = self._match_keywords_kr(combined)

            if score >= KEYWORD_THRESHOLD:
                event.keyword_score = score
                event.matched_keywords = matched
                filtered.append(event)

        return filtered

    def _summarize_filter_reasons(self, events: list[CollectorEvent]) -> dict[str, int]:
        """키워드 필터 결과가 0건일 때 운영 경고용 카운트를 산출."""
        excluded_count = 0
        below_threshold_count = 0
        for event in events:
            combined = f"{event.title} {event.summary}"
            if self._has_exclusion_pattern(combined):
                excluded_count += 1
                continue
            score, _matched = self._match_keywords_kr(combined)
            if score < KEYWORD_THRESHOLD:
                below_threshold_count += 1
        return {
            "excluded_count": excluded_count,
            "below_threshold_count": below_threshold_count,
        }

    def _match_keywords_kr(self, text: str) -> tuple[float, list[str]]:
        """
        제목: 한글 긴급 키워드 점수 산출
        내용: YOUTUBE_URGENT_KEYWORDS_KR의 모든 키워드를 텍스트에서 검색하고
              누적 점수와 매칭된 키워드 목록을 반환합니다.
              점수 상한은 KEYWORD_SCORE_CAP(10.0).

        Args:
            text: 검사할 텍스트 (제목 + 설명 결합)

        Returns:
            tuple[float, list[str]]: (점수, 매칭된 키워드 목록)
        """
        score = 0.0
        matched: list[str] = []

        for keyword, weight in YOUTUBE_URGENT_KEYWORDS_KR.items():
            if keyword in text:
                score += weight
                matched.append(keyword)

        # 제목: 점수 상한 적용
        score = min(score, KEYWORD_SCORE_CAP)
        return score, matched

    def _has_exclusion_pattern(self, text: str) -> bool:
        """
        제목: 제외 패턴 탐지
        내용: YOUTUBE_EXCLUSION_PATTERNS 중 하나라도 텍스트에 포함되면 True 반환.

        Args:
            text: 검사할 텍스트

        Returns:
            bool: 제외 대상이면 True
        """
        return any(pattern in text for pattern in YOUTUBE_EXCLUSION_PATTERNS)

    def _parse_channels(self, channels_str: str) -> list[dict[str, Any]]:
        """
        제목: 채널 문자열 파싱
        내용: "이름:채널ID,이름:채널ID" 형식의 문자열을 파싱합니다.
              알 수 없는 채널 이름은 CHANNEL_WEIGHTS 기본값 1.0 적용.

        Args:
            channels_str: "이름:ID,이름:ID" 형식 문자열

        Returns:
            list[dict]: [{'name': str, 'id': str, 'weight': float}, ...]
        """
        channels: list[dict[str, Any]] = []

        if not channels_str or not channels_str.strip():
            return channels

        for pair in channels_str.split(","):
            pair = pair.strip()
            if ":" not in pair:
                logger.warning(f"[YouTubeCollector] 채널 파싱 실패 (포맷 오류): '{pair}'")
                continue

            # 제목: 이름:ID 분리
            # 내용: 첫 번째 ':' 기준으로 분리 (채널명에 ':' 포함 가능)
            name, channel_id = pair.split(":", 1)
            name = name.strip()
            channel_id = channel_id.strip()

            if not name or not channel_id:
                continue

            # 제목: 채널 가중치 조회
            # 내용: 미등록 채널은 기본값 1.0
            weight = CHANNEL_WEIGHTS.get(name, 1.0)

            channels.append({"name": name, "id": channel_id, "weight": weight})

        return channels

    @staticmethod
    def _normalize_channels_str(channels_str: str) -> str:
        """
        제목: 채널 문자열 정규화
        내용: 운영 환경에서 실수로 "YOUTUBE_CHANNELS=..." 형태가 들어오는 경우를
              자동으로 보정합니다.
        """
        value = (channels_str or "").strip()
        prefix = "YOUTUBE_CHANNELS="
        if value.startswith(prefix):
            return value[len(prefix):].strip()
        return value

    def _parse_entry_date(self, entry: Any) -> datetime:
        """
        제목: feedparser 엔트리 날짜 파싱
        내용: published → published_parsed → 현재 시각 순으로 fallback.

        Args:
            entry: feedparser 엔트리 객체

        Returns:
            datetime: UTC timezone-aware 발행 시각
        """
        # 제목: published_parsed 우선 시도
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            import calendar
            ts = calendar.timegm(entry.published_parsed)
            return datetime.fromtimestamp(ts, tz=UTC)

        # 제목: 문자열 fallback
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
        제목: 수집 윈도우 범위 검증
        내용: today_only=True이면 UTC 당일 00:00 이후 영상만 허용.
              today_only=False이면 window_hours 기준 레거시 동작.

        Args:
            published_at: 이벤트 발행 시각

        Returns:
            bool: 수집 범위 내이면 True
        """
        now = datetime.now(UTC)
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=UTC)

        if self.today_only:
            # 제목: UTC 당일 00:00 기준
            # 내용: 오늘 날짜(UTC) 자정 이후 발행된 영상만 수집
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            return published_at >= today_start

        return published_at >= now - timedelta(hours=self.window_hours)

    def _normalize_datetime(self, published_str: str) -> str:
        """
        제목: ISO 8601 형식으로 날짜 정규화 (테스트 호환)
        내용: 날짜 문자열을 UTC ISO 8601 형식으로 변환합니다.

        Args:
            published_str: 입력 날짜 문자열

        Returns:
            str: UTC ISO 8601 형식 문자열
        """
        try:
            from dateutil.parser import parse as dateutil_parse
            dt = dateutil_parse(published_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.isoformat()
        except Exception:
            return datetime.now(UTC).isoformat()
