"""
제목: Macro-News 통합 감지 레이어
내용: NewsCollector + YouTubeCollector의 결과를 통합하여 Macro-News Score를 산출하고
      L1(CRITICAL)/L2(HIGH)/L3(MEDIUM)/NONE 레벨을 판정합니다.
      금융권 실무 기준의 감사 추적성(reasoning 필드)과 건강도(health_score)를 함께 반환합니다.

주요 클래스:
  - MacroNewsResult: detect() 반환 결과 dataclass
  - MacroNewsLayer: 통합 감지 실행 클래스

주요 함수:
  - MacroNewsLayer.detect(): 전체 감지 파이프라인 실행
  - MacroNewsLayer._compute_news_score(events): Tier×Source 보너스 점수 산출
  - MacroNewsLayer._compute_youtube_bonus(news, yt): YouTube 확인 보너스 산출
  - MacroNewsLayer._judge_level(score, news, health): L1/L2/L3/NONE 판정
  - MacroNewsLayer._compute_health_score(news, yt): 데이터 건강도 산출
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from collectors.base import CollectorEvent
from collectors.news_collector import NewsCollector
from collectors.youtube_collector import YouTubeCollector
from config.market_calendar import get_market_profile, get_threshold_for_profile
from config.settings import LEVEL_THRESHOLDS, TIER_WEIGHTS
from core.logger import get_logger
from db.dq_store import DataQualityStore
from detection.dq_monitor import DataQualityMonitor, DataQualityState
from detection.reasoning_builder import ReasoningBuilder

VERSION = "1.0.0"

logger = get_logger(__name__)

# ────────────────────────────────────────────────────────
# 타입 정의
# ────────────────────────────────────────────────────────
AlertLevel = Literal["L1", "L2", "L3", "NONE", "SYSTEM_DEGRADED"]


# ────────────────────────────────────────────────────────
# 결과 데이터 클래스
# ────────────────────────────────────────────────────────
@dataclass
class MacroNewsResult:
    """
    제목: Macro-News 감지 결과
    내용: MacroNewsLayer.detect()의 반환 타입. 금융권 실무 기준으로
          감사 추적성(reasoning)과 데이터 건강도(health_score)를 포함합니다.

    책임:
      - 최종 Score/Level 결과 보관
      - 뉴스/YouTube 이벤트 원본 보관
      - 점수 분해 (news_score, youtube_bonus) 보관
      - 상위 3건 요약 보관 (발행 콘텐츠 생성용)
      - 레벨 판정 근거(reasoning) 보관 (감사 추적용)
      - 데이터 건강도(health_score) 보관 (신뢰도 지표)
    """

    score: float  # 최종 Macro-News Score (0.0 ~ 10.0)
    level: AlertLevel  # 판정 레벨

    news_events: list[CollectorEvent]  # 뉴스 이벤트 전체
    youtube_events: list[CollectorEvent]  # YouTube 이벤트 전체

    news_score: float  # 뉴스 부분 점수
    youtube_bonus: float  # YouTube 확인 보너스

    top_news: list[CollectorEvent] = field(default_factory=list)  # 상위 3건 뉴스
    top_youtube: list[CollectorEvent] = field(default_factory=list)  # 상위 3건 YouTube

    reasoning: str = ""  # 레벨 판정 근거 (감사 추적용)
    health_score: float = 1.0  # 데이터 건강도 (0.0 ~ 1.0)
    reasoning_json: dict = field(default_factory=dict)  # FR-05 표준화 reasoning (JSONB)
    policy_version: str = "v1.0.0"  # 적용된 정책 버전 (semver)
    dq_state: DataQualityState | None = None  # FR-03 데이터 품질 평가 결과
    # 제목: 운영 경고 버킷
    # 내용: Alert 레벨(발행 정책)에 직접 영향 주지 않는 관측성 신호를 누적 보관.
    #       예) 수집은 정상이나 키워드 필터로 전량 탈락한 희소 상태(event_scarcity).
    #       런타임 로그/대시보드/운영 채널 전송기의 입력으로 사용한다.
    ops_warnings: list[str] = field(default_factory=list)


# ────────────────────────────────────────────────────────
# 상수
# ────────────────────────────────────────────────────────
# 제목: 복수 소스 보너스 상한
# 내용: source_count가 아무리 커도 1.5배까지만 보너스
SOURCE_BONUS_CAP: float = 1.5

# 제목: YouTube 단독 긴급 판정 최소 AI 점수
# 내용: 뉴스 없이 YouTube 단독으로 L2 판정 시 필요한 ai_score 하한
YOUTUBE_SOLO_L2_MIN_SCORE: float = 6.0

# 제목: 최소 소스 수 (L1 복수소스 조건)
L1_MIN_SOURCE_COUNT: int = 2

# 제목: 휴장일 저신호 완화 기준
# 내용: 주말/휴장일에는 매크로 긴급 뉴스가 적은 것이 일반적이므로
#       raw 이벤트가 소량일 때는 경고 강도를 낮춰 노이즈를 줄인다.
HOLIDAY_LOW_SIGNAL_RAW_NEWS_MAX: int = 4


class MacroNewsLayer:
    """
    제목: Macro-News 통합 감지 레이어
    내용: NewsCollector와 YouTubeCollector를 통합하여 시장 충격 점수와 Alert 레벨을
          자동 판정합니다. 의존성 주입 방식으로 테스트 용이성을 확보합니다.

    책임:
      - 뉴스/YouTube 수집 오케스트레이션
      - Macro-News Score 산출 (3인 전문가 협의 설계서 공식)
      - L1/L2/L3/NONE 레벨 판정 + reasoning 기록
      - 건강도(health_score) 계산 및 레벨 강등 처리
    """

    def __init__(
        self,
        news_collector: NewsCollector,
        youtube_collector: YouTubeCollector,
        tier_weights: dict[str, float] | None = None,
        level_thresholds: dict[str, float] | None = None,
        dq_monitor: DataQualityMonitor | None = None,
        reasoning_builder: ReasoningBuilder | None = None,
        dq_store: DataQualityStore | None = None,
        policy_version: str = "v1.0.0",
    ) -> None:
        """
        제목: MacroNewsLayer 초기화

        Args:
            news_collector: NewsCollector 인스턴스 (의존성 주입)
            youtube_collector: YouTubeCollector 인스턴스 (의존성 주입)
            tier_weights: Tier별 가중치 오버라이드 (None이면 settings 기본값)
            level_thresholds: 레벨 임계값 오버라이드 (None이면 settings 기본값)
            dq_monitor: DataQualityMonitor 인스턴스 (None이면 자동 생성)
            reasoning_builder: ReasoningBuilder 인스턴스 (None이면 자동 생성)
            dq_store: DataQualityStore 인스턴스 (None이면 자동 생성, DB 적재용)
            policy_version: 적용된 정책 버전 (env POLICY_VERSION 또는 'v1.0.0')
        """
        self.news_collector = news_collector
        self.youtube_collector = youtube_collector
        self.tier_weights = tier_weights or TIER_WEIGHTS
        self.thresholds = level_thresholds or LEVEL_THRESHOLDS

        # 신규 의존성 — 미주입 시 자동 생성 (기존 호출자 호환성)
        self.dq_monitor = dq_monitor or DataQualityMonitor()
        self.reasoning_builder = reasoning_builder or ReasoningBuilder()
        self.dq_store = dq_store or DataQualityStore()
        self.policy_version = policy_version

        logger.info(
            f"[MacroNewsLayer] v{VERSION} 초기화 (policy={self.policy_version})"
        )

    def detect(self) -> MacroNewsResult:
        """
        제목: Macro-News 감지 메인 파이프라인
        내용: 뉴스/YouTube 수집 → 점수 산출 → 건강도 계산 → 레벨 판정 순서로 실행.
              각 단계의 실패는 격리되며, 빈 결과로 계속 진행합니다.

        처리 플로우:
          1. NewsCollector.collect() 실행
          2. YouTubeCollector.collect() 실행
          3. Macro-News Score 산출 (news_score + youtube_bonus)
          4. 데이터 건강도 계산
          5. L1/L2/L3/NONE 판정 (건강도 강등 포함)
          6. MacroNewsResult 조립 및 반환

        Returns:
            MacroNewsResult: 감지 결과 전체
        """
        from datetime import UTC, datetime
        cycle_started_at = datetime.now(UTC)
        logger.info("[MacroNewsLayer] 감지 시작")

        # Step 1: 뉴스 수집 (source 성공/실패 추적용 dict 준비)
        source_results: dict[str, bool] = {}
        news_events: list[CollectorEvent] = []
        try:
            news_events = self.news_collector.collect()
            source_results["news_collector"] = True
        except Exception as e:
            logger.error(f"[MacroNewsLayer] 뉴스 수집 실패 (계속 진행): {type(e).__name__}: {e}")
            source_results["news_collector"] = False

        # Step 2: YouTube 수집
        youtube_events: list[CollectorEvent] = []
        try:
            youtube_events = self.youtube_collector.collect()
            source_results["youtube_collector"] = True
        except Exception as e:
            logger.error(f"[MacroNewsLayer] YouTube 수집 실패 (계속 진행): {type(e).__name__}: {e}")
            source_results["youtube_collector"] = False

        # Step 2.4: 시장 프로파일 사전 계산 (경고 강도/임계값 공통 사용)
        market_profile = get_market_profile()

        # Step 2.5 (신규): 데이터 품질 평가 (DQ Monitor)
        cycle_finished_at = datetime.now(UTC)
        # B-fix: 키워드 필터 *전*의 raw events를 DQ에 전달 (수집 시스템 건강성 측정용)
        # collect()의 결과(news_events)는 이미 키워드 필터 후 → DQ 입력으로 부적합
        raw_news_events = list(getattr(self.news_collector, "last_raw_events", []) or [])
        raw_youtube_events = list(getattr(self.youtube_collector, "last_raw_events", []) or [])
        # Step 2.6: Event Scarcity Guard (사전 고도화 1차 적용)
        # 목적: 수집(raw)은 있으나 키워드 필터 후 이벤트가 0건인 상태를 운영 경고로 표면화.
        # 주의: 본 경고는 발행 레벨을 강제 승격/강등하지 않고, 운영 관측용으로만 사용.
        ops_warnings: list[str] = []
        # 판정식 설명:
        #  - 좌변(raw 합계)>0: HTTP/RSS 수집 자체는 실패하지 않았음을 의미
        #  - 우변(filtered 합계)==0: 정책 키워드 기준으로는 유효 이벤트가 0건
        #  => 장애 알림이 아닌 운영 경고로 분류하여 관측성만 강화한다.
        if (len(raw_news_events) + len(raw_youtube_events)) > 0 and (len(news_events) + len(youtube_events)) == 0:

            # 휴장일 + 저신호 구간(소량 raw, 유튜브 raw 없음)은 예상 가능한 패턴으로 간주해
            # 경고를 완전히 제거하지 않고 severity=info로 낮춰 운영 피로도를 줄인다.
            is_holiday_low_signal = (
                market_profile == "holiday"
                and len(raw_news_events) <= HOLIDAY_LOW_SIGNAL_RAW_NEWS_MAX
                and len(raw_youtube_events) == 0
            )
            severity = "info" if is_holiday_low_signal else "warn"

            # warning 문자열은 후속 파서(로그 검색/알림 라우터)에서 안정적으로
            # 식별할 수 있도록 prefix(event_scarcity) + key=value 포맷을 유지한다.
            warning = (
                f"event_scarcity[{severity}]: raw_events_present_but_all_filtered "
                f"(profile={market_profile}, raw_news={len(raw_news_events)}, raw_yt={len(raw_youtube_events)}, "
                f"filtered_news={len(news_events)}, filtered_yt={len(youtube_events)})"
            )
            ops_warnings.append(warning)
            if is_holiday_low_signal:
                logger.info(f"[MacroNewsLayer] 운영 참고 — {warning}")
            else:
                logger.warning(f"[MacroNewsLayer] 운영 경고 — {warning}")

        try:
            dq_state = self.dq_monitor.evaluate(
                cycle_started_at=cycle_started_at,
                cycle_finished_at=cycle_finished_at,
                source_results=source_results,
                news_events=raw_news_events,
                youtube_events=raw_youtube_events,
                baseline_volume_avg=None,  # Phase 2에서 weekly_tracker 연동 예정
            )
            # DQ 결과를 Supabase에 INSERT (실패해도 파이프라인 계속)
            self.dq_store.save_dq_state(dq_state)
        except Exception as e:
            logger.error(f"[MacroNewsLayer] DQ 평가 실패 (안전 모드 — degraded=False 가정): {e}")
            dq_state = DataQualityState(
                fresh_event_ratio=0.0,
                source_success_rate=0.0,
                lag_seconds_p95=0.0,
                volume_zscore=None,
                degraded_flag=False,  # 안전: 평가 실패 시 정상 판정 유지
                degraded_reasons=[],
                cycle_started_at=cycle_started_at,
                cycle_finished_at=cycle_finished_at,
                source_results=source_results,
            )

        # Step 3: 점수 산출
        news_score = self._compute_news_score(news_events)
        youtube_bonus = self._compute_youtube_bonus(news_events, youtube_events)
        raw_score = news_score + youtube_bonus
        final_score = min(raw_score, 10.0)

        # Step 4: 건강도 계산
        health_score = self._compute_health_score(news_events, youtube_events)

        # Step 4.5: FR-02 장중/장외 프로파일 자동 전환
        dynamic_thresholds = get_threshold_for_profile(market_profile)
        logger.info(
            f"[MacroNewsLayer] 시장 프로파일: {market_profile} "
            f"(L1임계={dynamic_thresholds['l1_score']}, L2임계={dynamic_thresholds['l2_score']})"
        )

        # Step 5: 레벨 판정 (동적 임계값 적용 + DQ 우선 단락)
        level, reasoning, contributing_factors = self._judge_level(
            final_score, news_events, health_score, youtube_events,
            dynamic_thresholds, dq_state=dq_state,
        )

        logger.info(
            f"[MacroNewsLayer] 완료: score={final_score:.2f} "
            f"(news={news_score:.2f} + yt={youtube_bonus:.2f}) "
            f"level={level} health={health_score:.2f}"
        )
        logger.info(f"[MacroNewsLayer] 판정 근거: {reasoning}")

        # Step 6: FR-05 표준화 reasoning 조립
        reasoning_text, reasoning_json = self.reasoning_builder.build(
            level=level,
            score=final_score,
            news_score=news_score,
            yt_bonus=youtube_bonus,
            semantic_bonus=0.0,  # Phase 2 임베딩 매칭 시 채움
            thresholds_used=dynamic_thresholds,
            market_profile=market_profile,
            contributing_factors=contributing_factors,
            health_components={
                "health_score": health_score,
            },
            dq_state=dq_state,
            policy_version=self.policy_version,
        )

        return MacroNewsResult(
            score=round(final_score, 2),
            level=level,
            news_events=news_events,
            youtube_events=youtube_events,
            news_score=round(news_score, 2),
            youtube_bonus=round(youtube_bonus, 2),
            top_news=news_events[:3],
            top_youtube=youtube_events[:3],
            reasoning=reasoning_text,
            health_score=round(health_score, 2),
            reasoning_json=reasoning_json,
            policy_version=self.policy_version,
            dq_state=dq_state,
            ops_warnings=ops_warnings,
        )

    def _compute_news_score(self, events: list[CollectorEvent]) -> float:
        """
        제목: 뉴스 부분 점수 산출
        내용: 3인 전문가 협의 설계서의 Score 공식을 구현합니다.
              score = Σ(base × tier_weight × source_bonus)
              source_bonus = min(1.0 + (source_count-1) × 0.15, 1.5)

        처리 플로우:
          1. 이벤트별 tier_weight 조회
          2. source_count 기반 복수 소스 보너스 계산
          3. effective_score(ai 우선, fallback keyword) × tier_weight × source_bonus 합산

        Args:
            events: 뉴스 CollectorEvent 리스트

        Returns:
            float: 뉴스 부분 점수
        """
        if not events:
            return 0.0

        total = 0.0
        for event in events:
            tier = event.tier or "B"
            tier_weight = self.tier_weights.get(tier, 1.0)

            # 제목: 복수 소스 보너스
            # 내용: 동일 주제를 2개 이상 소스가 보도하면 신뢰도 상승
            source_count = max(event.source_count, 1)
            source_bonus = min(1.0 + (source_count - 1) * 0.15, SOURCE_BONUS_CAP)

            base = event.effective_score
            total += base * tier_weight * source_bonus

        return total

    def _compute_youtube_bonus(
        self,
        news_events: list[CollectorEvent],
        youtube_events: list[CollectorEvent],
    ) -> float:
        """
        제목: YouTube 확인 보너스 산출
        내용: 뉴스와 주제가 일치하는 YouTube 이벤트에 대해 channel_weight 보너스를 부여합니다.
              YouTube 단독은 L1 불가 (설계 원칙). 뉴스 확인 시에만 보너스 적용.

        처리 플로우:
          1. YouTube 이벤트별 _is_topic_match 확인
          2. 일치 시 1.0 × channel_weight 보너스
          3. 불일치 시 보너스 없음

        Args:
            news_events: 뉴스 이벤트 리스트
            youtube_events: YouTube 이벤트 리스트

        Returns:
            float: YouTube 확인 보너스 합계
        """
        if not youtube_events or not news_events:
            return 0.0

        bonus = 0.0
        for yt_event in youtube_events:
            if self._is_topic_match(news_events, yt_event):
                bonus += 1.0 * yt_event.channel_weight

        return bonus

    def _is_topic_match(
        self,
        news_events: list[CollectorEvent],
        yt_event: CollectorEvent,
    ) -> bool:
        """
        제목: 뉴스-YouTube 주제 일치 판정
        내용: YouTube 이벤트의 matched_keywords와 뉴스 이벤트들의 matched_keywords 사이
              교집합이 2개 이상이면 동일 주제로 판정합니다.

        Args:
            news_events: 비교 대상 뉴스 이벤트 리스트
            yt_event: YouTube 이벤트

        Returns:
            bool: 동일 주제 판정 시 True
        """
        yt_keywords = set(yt_event.matched_keywords)
        if not yt_keywords:
            return False

        for news_event in news_events:
            news_keywords = set(news_event.matched_keywords)
            # 제목: 교집합 2개 이상이면 동일 주제
            if len(yt_keywords & news_keywords) >= 2:
                return True

        return False

    def _compute_health_score(
        self,
        news_events: list[CollectorEvent],
        youtube_events: list[CollectorEvent],
    ) -> float:
        """
        제목: 데이터 건강도 계산 v2 (FR-03 다요인화)
        내용: 4개 요소 가중 합산으로 수집 품질을 평가합니다.

        처리 플로우:
          - source_diversity (0.35): 뉴스 Tier 다양성 (S/A/B 혼합도)
          - recency        (0.25): 최신성 (1시간 내 이벤트 비율)
          - cross_val      (0.20): 복수소스 교차검증 비율
          - dedup          (0.20): 중복 제거 품질 (source_count 분포)

        Args:
            news_events: 수집된 뉴스 이벤트
            youtube_events: 수집된 YouTube 이벤트

        Returns:
            float: 0.0 ~ 1.0 건강도 점수 v2
        """
        from datetime import UTC, datetime, timedelta

        all_events = news_events + youtube_events
        if not all_events:
            return 0.0

        # ── 요소 1: 소스 다양성 (0.35) ─────────────────────────
        # 내용: 뉴스 Tier S/A/B가 혼합될수록 높은 점수
        tiers = {e.tier for e in news_events if e.tier}
        diversity_map = {frozenset(): 0.0, frozenset(["S"]): 0.8,
                         frozenset(["A"]): 0.5, frozenset(["B"]): 0.3,
                         frozenset(["S", "A"]): 1.0, frozenset(["A", "B"]): 0.7,
                         frozenset(["S", "B"]): 0.8, frozenset(["S", "A", "B"]): 1.0}
        source_diversity = diversity_map.get(frozenset(tiers), 0.5)
        # YouTube가 있으면 다양성 보너스 +0.1 (상한 1.0)
        if youtube_events:
            source_diversity = min(source_diversity + 0.1, 1.0)

        # ── 요소 2: 최신성 (0.25) ──────────────────────────────
        # 내용: 1시간 이내 이벤트 비율
        now = datetime.now(UTC)
        cutoff_1h = now - timedelta(hours=1)
        recent_count = sum(
            1 for e in all_events
            if e.published_at.tzinfo is not None and e.published_at >= cutoff_1h
        )
        recency = recent_count / len(all_events) if all_events else 0.0

        # ── 요소 3: 교차검증 비율 (0.20) ───────────────────────
        # 내용: source_count > 1인 이벤트 비율
        cross_count = sum(1 for e in news_events if e.source_count > 1)
        cross_val = cross_count / len(news_events) if news_events else 0.0

        # ── 요소 4: 중복 제거 품질 (0.20) ─────────────────────
        # 내용: 동일 topic_hash 내 source_count가 높을수록 품질 높음
        # source_count 평균이 높으면 교차검증이 잘 된 것 → 품질 높음
        if news_events:
            avg_source_count = sum(e.source_count for e in news_events) / len(news_events)
            dedup = min(avg_source_count / 3.0, 1.0)  # 3소스 이상이면 만점
        else:
            dedup = 0.3  # YouTube만 있어도 기본 점수

        health_v2 = (
            0.35 * source_diversity
            + 0.25 * recency
            + 0.20 * cross_val
            + 0.20 * dedup
        )
        return round(min(health_v2, 1.0), 4)

    def _judge_level(
        self,
        score: float,
        news_events: list[CollectorEvent],
        health_score: float,
        youtube_events: list[CollectorEvent] | None = None,
        thresholds: dict[str, float] | None = None,
        dq_state: DataQualityState | None = None,
    ) -> tuple[AlertLevel, str, list[dict]]:
        """
        제목: Alert 레벨 판정
        내용: 3인 전문가 협의 설계서의 최종 레벨 판정 규칙을 구현합니다.
              건강도가 임계값 미달이면 레벨을 한 단계 강등합니다.

        처리 플로우:
          1. Tier S auto_l1 이벤트 존재 여부 확인 → L1 (건강도 강등 없음)
          2. score + source_count + health ≥ L1 기준 → L1
          3. 건강도 L1 미달 → L2로 강등
          4. score ≥ L2 기준 → L2
          5. YouTube 단독 긴급 (뉴스 없음 + YT ai_score ≥ 6.0) → L2
          6. score ≥ L3 기준 → L3
          7. 미달 → NONE

        Args:
            score: Macro-News Score
            news_events: 뉴스 이벤트 리스트 (Tier S 존재 여부, source_count 확인)
            health_score: 데이터 건강도

        Returns:
            tuple[AlertLevel, str]: (레벨, 판정 근거)
        """
        # 제목: 동적 임계값 적용 (FR-02)
        th = thresholds if thresholds is not None else self.thresholds
        contributing_factors: list[dict] = []

        # ── 0. SYSTEM_DEGRADED 우선 단락 (FR-03) ────────────────
        if dq_state is not None and dq_state.degraded_flag:
            contributing_factors.append({
                "factor": "data_quality_degraded",
                "weight": None,
                "reasons": dq_state.degraded_reasons[:5],
            })
            reason = (
                f"SYSTEM_DEGRADED: 수집 시스템 이상 감지 "
                f"(success={dq_state.source_success_rate:.2f}, "
                f"reasons={','.join(dq_state.degraded_reasons[:2])})"
            )
            return "SYSTEM_DEGRADED", reason, contributing_factors

        # ── 1. Tier S auto_l1 이벤트 → 무조건 L1 ─────────────
        tier_s_events = [e for e in news_events if e.auto_l1]
        if tier_s_events:
            sample = tier_s_events[0]
            contributing_factors.append({
                "factor": "tier_s_auto_l1",
                "weight": None,
                "matched_source": sample.source_name,
                "title": sample.title[:60],
            })
            reason = (
                f"L1: Tier S auto_l1 이벤트 감지 "
                f"(source={sample.source_name}, title='{sample.title[:40]}')"
            )
            return "L1", reason, contributing_factors

        # ── 2. Score 기반 L1 판정 ──────────────────────────────
        max_source_count = max((e.source_count for e in news_events), default=0)
        if (
            score >= th["l1_score"]
            and max_source_count >= L1_MIN_SOURCE_COUNT
            and health_score >= th["health_l1"]
        ):
            contributing_factors.append({
                "factor": "score_threshold_l1",
                "weight": float(score),
                "source_count": int(max_source_count),
                "health_score": float(health_score),
            })
            reason = (
                f"L1: score={score:.2f} ≥ {th['l1_score']}, "
                f"source_count={max_source_count}, health={health_score:.2f}"
            )
            return "L1", reason, contributing_factors

        # ── 3. L1 건강도 미달 → L2 강등 ──────────────────────
        if (
            score >= th["l1_score"]
            and max_source_count >= L1_MIN_SOURCE_COUNT
            and health_score < th["health_l1"]
        ):
            contributing_factors.append({
                "factor": "health_demotion_l1_to_l2",
                "weight": float(score),
                "health_score": float(health_score),
                "health_l1_threshold": float(th["health_l1"]),
            })
            reason = (
                f"L2 (강등): score={score:.2f} L1 충족이나 "
                f"health={health_score:.2f} < {th['health_l1']}"
            )
            return "L2", reason, contributing_factors

        # ── 4. Score 기반 L2 판정 ──────────────────────────────
        if score >= th["l2_score"] and health_score >= th["health_l2"]:
            contributing_factors.append({
                "factor": "score_threshold_l2",
                "weight": float(score),
                "health_score": float(health_score),
            })
            reason = (
                f"L2: score={score:.2f} ≥ {th['l2_score']}, "
                f"health={health_score:.2f}"
            )
            return "L2", reason, contributing_factors

        # ── 5. YouTube 단독 긴급 → L2 ─────────────────────────
        # 뉴스 미감지 + YouTube 이벤트 중 ai_score or keyword×weight 가 임계 이상
        if not news_events and youtube_events:
            yt_max_score = max(
                (e.ai_score or e.keyword_score * e.channel_weight)
                for e in youtube_events
            )
            if yt_max_score >= YOUTUBE_SOLO_L2_MIN_SCORE and health_score >= th["health_l2"]:
                contributing_factors.append({
                    "factor": "youtube_solo_urgent",
                    "weight": float(yt_max_score),
                    "health_score": float(health_score),
                })
                reason = (
                    f"L2 (YouTube 단독): 뉴스 미감지, "
                    f"YouTube 최고점수={yt_max_score:.2f} ≥ {YOUTUBE_SOLO_L2_MIN_SCORE}"
                )
                return "L2", reason, contributing_factors

        # ── 6. Score 기반 L3 판정 ──────────────────────────────
        if score >= th["l3_score"] and health_score >= th["health_l3"]:
            contributing_factors.append({
                "factor": "score_threshold_l3",
                "weight": float(score),
                "health_score": float(health_score),
            })
            reason = (
                f"L3: score={score:.2f} ≥ {th['l3_score']}, "
                f"health={health_score:.2f}"
            )
            return "L3", reason, contributing_factors

        # ── 7. 미달 → NONE ─────────────────────────────────────
        contributing_factors.append({
            "factor": "below_threshold",
            "weight": float(score),
            "health_score": float(health_score),
        })
        reason = (
            f"NONE: score={score:.2f} < {th['l3_score']} 또는 "
            f"health={health_score:.2f} 미달"
        )
        return "NONE", reason, contributing_factors
