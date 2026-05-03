"""
제목: 뉴스 소스 RSS 수집 모듈
내용: Tier S/A/B 뉴스 소스로부터 RSS를 수집하고 키워드 필터링·AI 분석·교차검증을
      수행하여 CollectorEvent 리스트를 반환합니다.

주요 클래스:
  - NewsCollector: Tier별 RSS 수집, 키워드 필터링, AI 스코어링, 교차검증

주요 함수:
  - NewsCollector.collect(): 전체 수집 파이프라인 실행
  - NewsCollector._collect_tier(tier): 단일 Tier RSS 수집
  - NewsCollector._filter_by_keywords(events): 키워드 1차 필터
  - NewsCollector._apply_ai_scoring(events): AI Impact 분석 (선택적)
  - NewsCollector._apply_cross_validation(events): 복수 소스 교차검증
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

import feedparser

from collectors.base import BaseCollector, CollectorEvent
from config.settings import NEWS_SOURCE_REGISTRY, NEWS_WINDOW_HOURS
from core.logger import get_logger
from validators.news_validator import NewsValidator

VERSION = "1.1.0"

logger = get_logger(__name__)


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
# 긴급 키워드 정의
# ────────────────────────────────────────────────────────
# 제목: L1 긴급 키워드
# 내용: 3인 전문가 협의 설계서 Round 1 확정값
URGENT_KEYWORDS_L1: dict[str, float] = {
    "emergency": 5.0,
    "crisis": 4.5,
    "unprecedented": 4.0,
    "extraordinary measures": 5.0,
    "circuit breaker": 5.0,
    "trading halt": 4.5,
    "market closed": 5.0,
    "flash crash": 4.5,
    "federal reserve announces": 5.0,
}

# 제목: L2 긴급 키워드
# 내용: L1보다 낮은 충격, 세부 분석 필요
URGENT_KEYWORDS_L2: dict[str, float] = {
    "significant": 2.5,
    "concern": 2.0,
    "volatility spike": 3.0,
    "sell-off": 2.5,
    "plunge": 3.0,
    "tumble": 2.5,
    "surge": 2.5,
    "rate hike": 3.0,
    "rate cut": 3.0,
    "recession": 3.5,
}

# ────────────────────────────────────────────────────────
# 확장 키워드 v1.1.0 (2026-05-03)
# 추가 영역: 관세·무역 / 지정학 / 미국 신용·재정 / AI·반도체 규제
# 변경 없음: URGENT_KEYWORDS_L1, URGENT_KEYWORDS_L2 (기존 유지)
# ────────────────────────────────────────────────────────

# 관세·무역정책 충격
URGENT_KEYWORDS_TARIFF: dict[str, float] = {
    # L1급 — 즉각 충격 표현 (단독 Tier A 1건으로 keyword_score >= 3.5)
    "tariff war": 4.0,
    "trade war": 3.5,
    "trade war escalates": 4.5,
    "export ban": 4.0,
    "import embargo": 4.5,
    "reciprocal tariff": 4.0,
    "trade deal collapse": 4.5,
    # L2급 — 단독 threshold 초과하나 Score 낮음 (복합 시 상승)
    "tariff": 2.0,
    "new tariff": 2.5,
    "export control": 2.5,
    "trade sanction": 3.0,
    "section 301": 3.5,
    "section 232": 3.5,
    "trade restriction": 2.5,
}

# 지정학적 긴급사태
URGENT_KEYWORDS_GEO: dict[str, float] = {
    # L1급
    "nuclear threat": 5.0,
    "nuclear attack": 5.0,
    "nuclear weapon": 4.5,
    "military strike": 5.0,
    "oil embargo": 4.5,
    "strait of hormuz": 4.5,
    "war declared": 5.0,
    # L2급 — 복합 표현만 수록 (단독 일반어 제외)
    "invasion": 3.5,
    "airstrike": 3.0,
    "missile attack": 3.5,
    "military escalation": 3.5,
    "geopolitical crisis": 3.5,
    "conflict escalation": 3.0,
}

# 미국 신용·재정 위기
URGENT_KEYWORDS_FISCAL: dict[str, float] = {
    # L1급
    "us credit downgrade": 5.0,
    "credit downgrade us": 5.0,
    "debt default": 5.0,
    "debt ceiling crisis": 4.5,
    "government shutdown begins": 4.5,
    "moody's downgrades": 4.0,
    "fitch downgrades": 4.0,
    "treasury auction fails": 4.5,
    "treasury auction tail": 4.0,
    # L2급
    "debt ceiling": 3.5,
    "government shutdown": 3.0,
    "credit downgrade": 3.0,
    "fiscal cliff": 3.0,
    "bond auction": 2.5,
    "deficit warning": 2.5,
}

# AI·반도체 규제 충격
URGENT_KEYWORDS_TECH_REG: dict[str, float] = {
    # L1급
    "chip export ban": 4.5,
    "semiconductor ban": 4.5,
    "semiconductor export ban": 4.5,
    "nvidia ban": 4.5,
    "tsmc restriction": 4.0,
    "chip export control": 4.0,
    "ai executive order": 4.0,
    # L2급
    "semiconductor restriction": 3.5,
    "chip restriction": 3.0,
    "entity list": 2.5,
    "antitrust ruling tech": 3.5,
    "big tech breakup": 3.5,
    "ai regulation": 2.0,
    "export control ai": 3.0,
}

# 제목: 키워드 매칭 최소 임계값
# 내용: 이 점수 미만의 이벤트는 제외
KEYWORD_THRESHOLD: float = 2.0

# 제목: AI 분석 임계값
# 내용: keyword_score가 이 이상인 이벤트만 AI 분석 수행 (쿼터 절약)
AI_SCORE_MIN_KEYWORD: float = 2.5


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

            try:
                feed = self._retry_request(feedparser.parse, url)

                #주석
                http_status = getattr(feed, "status", "N/A")
                source_count_before = len(events)
                entries = getattr(feed, "entries", [])

                for entry in entries[:10]:  # 소스당 최근 10건
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

                    events.append(event)




                # 제목: 소스별 수집 결과 로그
                collected = len(events) - source_count_before
                logger.info(
                    f"[NewsCollector] {source_name} (Tier {tier}): "
                    f"HTTP={http_status}, RSS={len(entries)}건, "
                    f"윈도우내={collected}건"
                )

            except Exception as e:
                logger.warning(f"[NewsCollector] {source_name} 수집 실패: {type(e).__name__}: {e}")

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
            }
              
            for keyword, weight in all_keywords.items():
                if keyword in combined:
                    score += weight
                    matched.append(keyword)

            if score >= KEYWORD_THRESHOLD:
                event.keyword_score = score
                event.matched_keywords = matched
                filtered.append(event)

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
              복수 소스에서 보도된 이벤트는 신뢰도가 높아집니다.

        처리 플로우:
          1. 각 이벤트의 topic_hash 계산
          2. topic_hash 기준으로 그룹화
          3. 그룹 내 소스 수 카운트 → source_count 업데이트

        Args:
            events: 교차검증 대상 이벤트

        Returns:
            list[CollectorEvent]: source_count가 업데이트된 이벤트
        """
        # 제목: topic_hash 계산
        for event in events:
            event.topic_hash = self._compute_topic_hash(event.title)

        # 제목: 그룹별 소스 카운트
        from collections import defaultdict
        groups: dict[str, list[str]] = defaultdict(list)
        for event in events:
            if event.topic_hash:
                groups[event.topic_hash].append(event.source_name)

        # 제목: source_count 업데이트
        for event in events:
            if event.topic_hash and event.topic_hash in groups:
                unique_sources = len(set(groups[event.topic_hash]))
                event.source_count = unique_sources

        return events

    def _compute_topic_hash(self, title: str) -> str:
        """
        제목: 주제 해시 계산
        내용: 제목의 불용어를 제거한 뒤 상위 5 단어의 정렬된 조합으로 MD5 해시 생성.
              유사한 제목끼리 동일 해시를 공유하여 교차검증에 활용됩니다.

        Args:
            title: 기사 제목

        Returns:
            str: 8자리 MD5 해시
        """
        stopwords = {"the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "is", "are", "was"}
        words = [w.lower().strip(".,!?") for w in title.split() if w.lower() not in stopwords]
        key = " ".join(sorted(words[:5]))
        return hashlib.md5(key.encode()).hexdigest()[:8]  # noqa: S324

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
