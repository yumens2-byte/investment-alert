"""
제목: 뉴스 소스 RSS 수집 모듈
내용: Tier S/A/B 뉴스 소스로부터 RSS를 수집하고 키워드 필터링·AI 분석·교차검증을
      수행하여 CollectorEvent 리스트를 반환합니다.

주요 클래스:
  - SourceResult: 소스별 수집 결과 (Q1, DQ 소스 단위 성공률·운영 경고 입력)
  - NewsCollector: Tier별 RSS 수집, 키워드 필터링, AI 스코어링, 교차검증

주요 함수:
  - NewsCollector.collect(): 전체 수집 파이프라인 실행
  - NewsCollector._collect_tier(tier): 단일 Tier RSS 수집
  - NewsCollector._filter_by_keywords(events): 키워드 1차 필터
  - NewsCollector._apply_ai_scoring(events): AI Impact 분석 (선택적)
  - NewsCollector._apply_cross_validation(events): 복수 소스 교차검증
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import socket as _socket
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

import feedparser

from collectors.base import BaseCollector, CollectorEvent
from config.keyword_rules import (  # noqa: F401 — v1 사전 재노출 (tests·외부 import 호환)
    AI_SCORE_MIN_KEYWORD,
    KEYWORD_THRESHOLD,
    URGENT_KEYWORDS_CENTRAL_BANK,
    URGENT_KEYWORDS_EARNINGS,
    URGENT_KEYWORDS_FISCAL,
    URGENT_KEYWORDS_GEO,
    URGENT_KEYWORDS_L1,
    URGENT_KEYWORDS_L2,
    URGENT_KEYWORDS_MACRO_DATA,
    URGENT_KEYWORDS_POLICY,
    URGENT_KEYWORDS_TARIFF,
    URGENT_KEYWORDS_TECH_REG,
)
from config.settings import NEWS_SOURCE_REGISTRY, NEWS_WINDOW_HOURS
from core.logger import get_logger
from validators.news_validator import NewsValidator

# A1 패치 (v1.3.0): feedparser는 timeout 인자가 없으므로 socket level로 강제.
# 운영에서 RSS 서버 hang 시 GitHub Actions timeout(10분) 도달 위험 차단.
_socket.setdefaulttimeout(15)

VERSION = "1.4.0"  # Q1: SourceResult / last_source_results (소스별 성공 판정)

logger = get_logger(__name__)


# ────────────────────────────────────────────────────────
# 소스별 수집 결과 (Q1)
# ────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SourceResult:
    """
    제목: 뉴스 소스 1개의 수집 결과
    내용: DQ 소스 단위 성공률과 운영 경고(failed_sources)의 입력.

    성공(ok) 규칙 (2026-10-12 마스터 확정 Q1-1):
      - 예외 없음 AND entries_total >= 1 AND
        (HTTP status 2xx/3xx  또는  status 속성 없음 + bozo 아님)
      - 윈도우 내 0건(in_window=0)은 성공 (예: fed_rss 평시)
    """

    name: str
    tier: str
    http_status: int | None  # feed.status 가 int 일 때만, 아니면 None
    entries_total: int  # len(feed.entries) (소스당 절단 전)
    in_window: int  # 중복 제거·윈도우 필터 후 채택 건수
    ok: bool
    error: str | None = None  # 실패 사유 (ok=True 이면 None)
    bozo: bool = False  # 파서 경고 관측용

    def to_dict(self) -> dict:
        """JSONB 적재용 dict."""
        return asdict(self)


def build_source_result(
    name: str,
    tier: str,
    feed: Any,
    in_window: int,
    exc: BaseException | None = None,
) -> SourceResult:
    """
    제목: 소스별 수집 결과 판정
    내용: feedparser 결과(또는 예외)로 SourceResult 를 만든다. 부수효과 없음.

    Args:
        name: 소스명
        tier: 'S'|'A'|'B'
        feed: feedparser.parse 반환값 (예외 시 None)
        in_window: 윈도우 내 채택 건수
        exc: 수집 중 발생 예외

    Returns:
        SourceResult
    """
    if exc is not None:
        return SourceResult(name, tier, None, 0, 0, False, f"exception:{type(exc).__name__}")

    raw_status = getattr(feed, "status", None)
    status = raw_status if isinstance(raw_status, int) and not isinstance(raw_status, bool) else None
    entries = getattr(feed, "entries", None)
    total = len(entries) if isinstance(entries, list) else 0
    raw_bozo = getattr(feed, "bozo", False)
    bozo = bool(raw_bozo) if isinstance(raw_bozo, int) else False  # bool ⊂ int

    if status is not None:
        if not 200 <= status < 400:
            return SourceResult(name, tier, status, total, in_window, False, f"http_{status}", bozo)
    elif bozo:
        # status 없음 + bozo → 네트워크 오류(URLError) 등 (feedparser api.py)
        exc_obj = getattr(feed, "bozo_exception", None)
        reason = type(exc_obj).__name__ if isinstance(exc_obj, BaseException) else "bozo"
        return SourceResult(name, tier, None, total, in_window, False, f"no_status:{reason}", bozo)

    if total < 1:
        return SourceResult(name, tier, status, total, in_window, False, "empty_feed", bozo)
    return SourceResult(name, tier, status, total, in_window, True, None, bozo)


# ────────────────────────────────────────────────────────
# AI 클라이언트 프로토콜 (의존성 역전)
# ────────────────────────────────────────────────────────
@runtime_checkable
class AIClientProtocol(Protocol):
    """
    제목: AI 클라이언트 프로토콜
    내용: Gemini·Claude 등 AI 클라이언트의 공통 인터페이스 정의.
          테스트에서는 Mock으로 교체 가능. 프로덕션에서는 GeminiGateway 주입.

    책임:
      - generate(prompt): 텍스트 생성 요청 단일 진입점
    """

    def generate(self, prompt: str) -> str:
        """
        제목: 텍스트 생성
        내용: 프롬프트를 입력받아 AI 응답 텍스트를 반환합니다.

        Args:
            prompt: AI에 전달할 프롬프트

        Returns:
            str: AI 응답 텍스트
        """
        ...


# ────────────────────────────────────────────────────────
# 긴급 키워드 정의 — Q2: config/keyword_rules.py 로 값·순서 그대로 이동 (재노출, 호환 유지)
# ────────────────────────────────────────────────────────

# 제목: 소스당 엔트리 처리 상한 (Q2)
# 내용: v1 판정은 소스당 앞 10건(기존 entries[:10])만 사용 — 결과 불변.
#       v2 후보는 소스당 앞 30건까지 수집(1회 수집, 같은 피드 재사용).
NEWS_ENTRIES_PER_SOURCE_V1: int = 10
NEWS_ENTRIES_PER_SOURCE: int = 30


# ────────────────────────────────────────────────────────
# 교차검증 (무상태 — NewsCollector 와 v2 정책 공용, Q2 리뷰 m4)
# ────────────────────────────────────────────────────────
_TOPIC_STOPWORDS: frozenset[str] = frozenset(
    {"the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "is", "are", "was"}
)


def compute_topic_hash(title: str) -> str:
    """
    제목: 주제 해시 계산
    내용: 제목의 불용어를 제거한 뒤 상위 5 단어의 정렬된 조합으로 MD5 해시 생성.
          유사한 제목끼리 동일 해시를 공유하여 교차검증에 활용됩니다.
    """
    words = [w.lower().strip(".,!?") for w in title.split() if w.lower() not in _TOPIC_STOPWORDS]
    key = " ".join(sorted(words[:5]))
    return hashlib.md5(key.encode()).hexdigest()[:8]  # noqa: S324


def apply_cross_validation(events: list[CollectorEvent]) -> list[CollectorEvent]:
    """
    제목: 복수 소스 교차검증
    내용: topic_hash 계산 → 그룹별 고유 소스 수를 source_count 에 기록(입력 이벤트 in-place).
    """
    for event in events:
        event.topic_hash = compute_topic_hash(event.title)
    groups: dict[str, list[str]] = defaultdict(list)
    for event in events:
        if event.topic_hash:
            groups[event.topic_hash].append(event.source_name)
    for event in events:
        if event.topic_hash and event.topic_hash in groups:
            event.source_count = len(set(groups[event.topic_hash]))
    return events


class NewsCollector(BaseCollector):
    """
    제목: Tier별 뉴스 RSS 수집 및 분석
    내용: settings.NEWS_SOURCE_REGISTRY에 정의된 Tier S/A/B 소스에서
          RSS를 수집하고, NewsValidator·키워드 필터·AI 분석·교차검증을 순서대로 적용합니다.

    책임:
      - Tier S/A/B RSS 수집 (BaseCollector 재시도 로직 활용)
      - 24시간 필터링 + SHA256 중복 제거
      - 키워드 1차 필터 → AI Impact Scoring → 교차검증
      - NewsValidator 적용 (추측성·재탕 제거)
      - 최종 결과 effective_score 내림차순 정렬
    """

    def __init__(
        self,
        ai_client: AIClientProtocol | None = None,
        validator: NewsValidator | None = None,
        source_registry: dict | None = None,
        window_hours: int = NEWS_WINDOW_HOURS,
    ) -> None:
        """
        제목: NewsCollector 초기화

        Args:
            ai_client: AI 클라이언트 (None이면 keyword_score fallback)
            validator: 뉴스 검증기 (None이면 기본 NewsValidator 사용)
            source_registry: 소스 레지스트리 (None이면 settings 기본값 사용)
            window_hours: 수집 시간 범위 (기본: 24시간)
        """
        super().__init__(source_name="news_collector", timeout=15, max_retries=3)
        self.ai_client = ai_client
        self.validator = validator or NewsValidator(window_hours=window_hours)
        self.source_registry = source_registry or NEWS_SOURCE_REGISTRY
        self.window_hours = window_hours

        # 제목: 세션 내 중복 제거 ID 집합
        # 내용: collect() 호출마다 초기화
        self._seen_event_ids: set[str] = set()

        # 제목: 소스별 수집 결과 (Q1) — collect() 호출마다 초기화
        self.last_source_results: list[SourceResult] = []

        # 제목: v2 후보 (Q2) — collect() 호출마다 초기화
        # 내용: last_candidates = 키워드 필터 *전* 검증 통과 후보 복사본 (소스당 최대 30건).
        #       _extra_events = 소스당 10번째 이후(엔트리 index ≥ 10) 윈도우 내 이벤트.
        self.last_candidates: list[CollectorEvent] = []
        self._extra_events: list[CollectorEvent] = []

        logger.info(f"[NewsCollector] v{VERSION} 초기화 (window={window_hours}h, ai={ai_client is not None})")

    def collect(self) -> list[CollectorEvent]:
        """
        제목: 뉴스 수집 전체 파이프라인
        내용: RSS 수집 → 검증 → 키워드 필터 → AI 분석 → 교차검증 → 정렬 순서로 실행.

        처리 플로우:
          1. 중복 제거 집합 초기화
          2. Tier S → A → B 순서로 RSS 수집
          3. NewsValidator 적용
          4. 키워드 1차 필터
          5. AI Impact Scoring (ai_client 있는 경우)
          6. 교차검증 (source_count 계산)
          7. effective_score 내림차순 정렬

        Returns:
            list[CollectorEvent]: 최종 필터링된 이벤트 (점수 내림차순)
        """
        logger.info("[NewsCollector] 뉴스 수집 시작")

        # 제목: 중복 제거 집합 초기화
        self._seen_event_ids = set()
        self.last_source_results = []
        self._extra_events = []
        self.last_candidates = []

        # Step 1: Tier별 수집
        raw_events: list[CollectorEvent] = []
        for tier in ("S", "A", "B"):
            tier_events = self._collect_tier(tier)
            raw_events.extend(tier_events)

        logger.info(f"[NewsCollector] RSS 수집 완료: {len(raw_events)}건")

        # Step 2: NewsValidator 적용
        validated = self.validator.validate_all(raw_events)
        logger.info(f"[NewsCollector] 검증 후: {len(validated)}건")

        # B-fix: DQ Monitor용 raw events 보관 (키워드 필터 전, 검증 통과 후)
        # 이 시점이 "수집 시스템 건강성" 측정의 정확한 입력
        self.last_raw_events = list(validated)

        # Q2: v2 후보 스냅샷 — 키워드 필터(in-place 점수 기록) *전*에 복사.
        # 소스당 10번째 이후·v2_only 소스 이벤트는 v1 경로에 넣지 않고 validate() 만 적용.
        # 리뷰 m3: 실패해도 v1 경로 무영향 (빈 후보 + 경고).
        self._snapshot_candidates(validated)

        # Step 3: 키워드 1차 필터
        filtered = self._filter_by_keywords(validated)
        logger.info(f"[NewsCollector] 키워드 필터 후: {len(filtered)}건")

        # Step 4: AI 분석 (선택적)
        scored = self._apply_ai_scoring(filtered)

        # Step 5: 교차검증
        cross_validated = self._apply_cross_validation(scored)

        # Step 6: 정렬
        cross_validated.sort(key=lambda e: e.effective_score, reverse=True)

        logger.info(f"[NewsCollector] 수집 완료: 최종 {len(cross_validated)}건")
        return cross_validated

    def _snapshot_candidates(self, validated: list[CollectorEvent]) -> None:
        """
        제목: v2 후보 스냅샷 (Q2)
        내용: last_candidates = 검증 통과 v1 이벤트 + 검증 통과 추가 이벤트의 복사본.
              예외 시 last_candidates=[] + WARNING (v1 수집 결과에 영향 없음).
        """
        try:
            extras_ok = [e for e in self._extra_events if self.validator.validate(e)]
            self.last_candidates = [dataclasses.replace(e) for e in [*validated, *extras_ok]]
            if self._extra_events:
                logger.info(
                    f"[NewsCollector] v2 후보: {len(self.last_candidates)}건 "
                    f"(v1 {len(validated)} + 추가 {len(extras_ok)}/{len(self._extra_events)})"
                )
        except Exception as e:
            self.last_candidates = []
            logger.warning(
                f"[NewsCollector] v2 후보 스냅샷 실패 (v1 무영향, 빈 후보): {type(e).__name__}: {e}"
            )

    def _collect_tier(self, tier: str) -> list[CollectorEvent]:
        """
        제목: 단일 Tier 소스 RSS 수집
        내용: 해당 Tier의 모든 소스를 순회하며 RSS를 파싱합니다.
              개별 소스 실패는 경고 로깅 후 다음 소스로 계속 진행합니다.

        Args:
            tier: 'S', 'A', 'B' 중 하나

        Returns:
            list[CollectorEvent]: 해당 Tier에서 수집된 이벤트
        """
        events: list[CollectorEvent] = []
        sources = self.source_registry.get(tier, {})

        for source_name, config in sources.items():
            url = config.get("url")
            if not url:
                continue

            source_count_before = len(events)
            # Q2 리뷰 M1: v2_only 소스(신규 fed_speeches/marketwatch)는 v1 경로(events) 미포함.
            # 수집·SourceResult·v2 후보에는 포함. in_window 는 v1 소스와 같은 기준(index<10)으로 집계.
            v2_only = bool(config.get("v2_only", False))
            v2_only_in_window = 0
            feed: Any = None
            try:
                feed = self._retry_request(feedparser.parse, url)
                entries = getattr(feed, "entries", [])

                # Q2: 소스당 30건 처리. index < 10 → v1 경로(events), 이후 → v2 전용(_extra_events).
                # 중복 제거는 index 순서대로 동일하게 적용되므로 v1 결과는 entries[:10] 과 같다.
                for idx, entry in enumerate(entries[:NEWS_ENTRIES_PER_SOURCE]):
                    event = self._entry_to_event(entry, source_name, tier, config)
                    if event is None:
                        continue

                    # 제목: SHA256 중복 제거
                    # 내용: 동일 이벤트 ID가 이미 수집된 경우 스킵
                    if event.event_id in self._seen_event_ids:
                        continue
                    self._seen_event_ids.add(event.event_id)

                    # 제목: 24시간 이내 필터링
                    # 내용: 수집 윈도우 초과 이벤트 조기 제거
                    if not self._is_within_window(event.published_at):
                        continue

                    if v2_only:
                        if idx < NEWS_ENTRIES_PER_SOURCE_V1:
                            v2_only_in_window += 1
                        self._extra_events.append(event)
                        continue
                    if idx >= NEWS_ENTRIES_PER_SOURCE_V1:
                        self._extra_events.append(event)
                        continue
                    events.append(event)

            except Exception as e:
                logger.warning(f"[NewsCollector] {source_name} 수집 실패: {type(e).__name__}: {e}")
                self.last_source_results.append(
                    build_source_result(source_name, tier, None, 0, exc=e)
                )
                continue

            # 제목: 소스별 수집 결과 판정·로그 (Q1)
            collected = len(events) - source_count_before + v2_only_in_window
            result = build_source_result(source_name, tier, feed, collected)
            self.last_source_results.append(result)
            http_label = result.http_status if result.http_status is not None else "N/A"
            if result.ok:
                logger.info(
                    f"[NewsCollector] {source_name} (Tier {tier}): "
                    f"HTTP={http_label}, RSS={result.entries_total}건, "
                    f"윈도우내={collected}건" + (" (bozo)" if result.bozo else "")
                )
            else:
                logger.warning(
                    f"[NewsCollector] {source_name} (Tier {tier}) 소스 실패: {result.error} "
                    f"HTTP={http_label}, RSS={result.entries_total}건, 윈도우내={collected}건"
                )

        return events

    def _entry_to_event(
        self,
        entry: Any,
        source_name: str,
        tier: str,
        config: dict,
    ) -> CollectorEvent | None:
        """
        제목: feedparser 엔트리를 CollectorEvent로 변환
        내용: feedparser 엔트리 딕셔너리에서 필수 필드를 추출하고
              CollectorEvent 인스턴스를 생성합니다. 오류 시 None 반환.

        Args:
            entry: feedparser 엔트리 객체
            source_name: 소스 식별자
            tier: 뉴스 Tier
            config: 소스 설정 딕셔너리

        Returns:
            CollectorEvent | None: 변환 성공 시 이벤트, 실패 시 None
        """
        try:
            title = entry.get("title", "").strip()
            url = entry.get("link", "").strip()
            summary = entry.get("summary", "")[:500]

            if not title or not url:
                return None

            published_at = self._parse_entry_date(entry)
            event_id = CollectorEvent.compute_event_id(source_name, url, title)
            auto_l1 = config.get("auto_l1", False)

            return CollectorEvent(
                source_type="news",
                source_name=source_name,
                event_id=event_id,
                title=title,
                summary=summary,
                url=url,
                published_at=published_at,
                tier=tier,
                channel_weight=1.0,
                auto_l1=auto_l1,
                # auto_l1이면 keyword_score 5.0 선설정
                keyword_score=5.0 if auto_l1 else 0.0,
                matched_keywords=["auto_l1"] if auto_l1 else [],
            )
        except Exception as e:
            logger.debug(f"[NewsCollector] 엔트리 변환 실패: {e}")
            return None

    def _parse_entry_date(self, entry: Any) -> datetime:
        """
        제목: feedparser 엔트리 날짜 파싱
        내용: published_parsed → published 순으로 시도.
              파싱 실패 시 현재 UTC 시각 반환.

        Args:
            entry: feedparser 엔트리 객체

        Returns:
            datetime: UTC timezone-aware 발행 시각
        """
        # 제목: published_parsed 우선 시도
        # 내용: feedparser가 time.struct_time으로 파싱한 결과
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            import calendar
            ts = calendar.timegm(entry.published_parsed)
            return datetime.fromtimestamp(ts, tz=UTC)

        # 제목: published 문자열 fallback
        # 내용: dateutil로 직접 파싱
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

        # 제목: 최종 fallback
        # 내용: 현재 시각 반환 (최소 수집 보장)
        return datetime.now(UTC)

    def _filter_by_keywords(self, events: list[CollectorEvent]) -> list[CollectorEvent]:
        """
        제목: 키워드 1차 필터
        내용: auto_l1 이벤트는 통과, 나머지는 URGENT_KEYWORDS로 점수를 매겨
              KEYWORD_THRESHOLD 이상인 이벤트만 반환합니다.

        Args:
            events: 필터 대상 이벤트 리스트

        Returns:
            list[CollectorEvent]: 임계값 초과 이벤트
        """
        filtered: list[CollectorEvent] = []

        for event in events:
            # 제목: auto_l1 소스는 키워드 필터 우회
            if event.auto_l1:
                filtered.append(event)
                continue

            combined = f"{event.title} {event.summary}".lower()
            score = 0.0
            matched: list[str] = []

            # 변경
            all_keywords = {
                **URGENT_KEYWORDS_L1,
                **URGENT_KEYWORDS_L2,
                **URGENT_KEYWORDS_TARIFF,
                **URGENT_KEYWORDS_GEO,
                **URGENT_KEYWORDS_FISCAL,
                **URGENT_KEYWORDS_TECH_REG,
                # v1.2.0 추가: B시스템 보완 영역 (A가 놓치는 선행 신호)
                **URGENT_KEYWORDS_MACRO_DATA,
                **URGENT_KEYWORDS_CENTRAL_BANK,
                **URGENT_KEYWORDS_EARNINGS,
                **URGENT_KEYWORDS_POLICY,
            }

            for keyword, weight in all_keywords.items():
                if keyword in combined:
                    score += weight
                    matched.append(keyword)

            if score >= KEYWORD_THRESHOLD:
                event.keyword_score = score
                event.matched_keywords = matched
                filtered.append(event)

        # v1.2.0: 탈락 뉴스 디버그 로그 (운영 튜닝용)
        # 모든 뉴스가 키워드 필터에서 탈락한 경우, 상위 3건의 제목/소스를 기록.
        # → 키워드 사전 보완 시 어떤 단어를 추가해야 하는지 판단 자료
        if events and not filtered:
            samples = [
                f"{e.source_name}|{e.title[:80]}"
                for e in events[:3]
            ]
            logger.info(
                f"[NewsCollector] 운영 참고 — 키워드 필터 0건 통과. "
                f"검증 통과 입력={len(events)}건. 상위 샘플: {samples}"
            )

        return filtered

    def _apply_ai_scoring(self, events: list[CollectorEvent]) -> list[CollectorEvent]:
        """
        제목: AI Impact Scoring (선택적)
        내용: ai_client가 주입된 경우, keyword_score가 AI_SCORE_MIN_KEYWORD 이상인
              이벤트만 Gemini에 분석을 요청합니다. 실패 시 keyword_score로 fallback.

        처리 플로우:
          1. ai_client 미주입 시 즉시 반환
          2. keyword_score < AI_SCORE_MIN_KEYWORD이면 스킵
          3. Gemini 호출 → JSON {score, reasoning} 파싱
          4. 파싱 실패 시 keyword_score를 ai_score로 설정 (fallback)

        Args:
            events: AI 분석 대상 이벤트

        Returns:
            list[CollectorEvent]: ai_score가 채워진 이벤트
        """
        if not self.ai_client:
            return events

        for event in events:
            # 제목: auto_l1은 AI 분석 불필요
            if event.auto_l1:
                event.ai_score = 5.0
                event.ai_reasoning = "Tier S auto_l1 source"
                continue

            # 제목: keyword 점수 낮은 이벤트 스킵 (쿼터 절약)
            if event.keyword_score < AI_SCORE_MIN_KEYWORD:
                event.ai_score = event.keyword_score
                continue

            try:
                prompt = (
                    f"Rate the US market impact of this news on a scale of 0-10.\n"
                    f"Output ONLY valid JSON: {{\"score\": <number>, \"reasoning\": \"<text>\"}}\n\n"
                    f"Title: {event.title}\n"
                    f"Summary: {event.summary[:200]}"
                )
                response = self.ai_client.generate(prompt)
                result = json.loads(response.strip())
                event.ai_score = float(result.get("score", 0.0))
                event.ai_reasoning = str(result.get("reasoning", ""))

            except Exception as e:
                logger.warning(
                    f"[NewsCollector] AI 분석 실패 (source={event.source_name}): "
                    f"{type(e).__name__}: {e} → keyword_score fallback"
                )
                event.ai_score = event.keyword_score
                event.ai_reasoning = "AI fallback to keyword score"

        return events

    def _apply_cross_validation(self, events: list[CollectorEvent]) -> list[CollectorEvent]:
        """
        제목: 복수 소스 교차검증
        내용: topic_hash가 같은 이벤트를 그룹화하여 source_count를 업데이트합니다.
              Q2 리뷰 m4: 무상태 모듈 함수 apply_cross_validation 위임 (동작 동일).

        Args:
            events: 교차검증 대상 이벤트

        Returns:
            list[CollectorEvent]: source_count가 업데이트된 이벤트
        """
        return apply_cross_validation(events)

    def _compute_topic_hash(self, title: str) -> str:
        """
        제목: 주제 해시 계산 (모듈 함수 compute_topic_hash 위임)

        Args:
            title: 기사 제목

        Returns:
            str: 8자리 MD5 해시
        """
        return compute_topic_hash(title)

    def _is_within_window(self, published_at: datetime) -> bool:
        """
        제목: 수집 윈도우 범위 검증

        Args:
            published_at: 이벤트 발행 시각

        Returns:
            bool: window_hours 이내이면 True
        """
        now = datetime.now(UTC)
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=UTC)
        return published_at >= now - timedelta(hours=self.window_hours)
