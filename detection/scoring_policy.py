"""
제목: Macro-News 판정 정책 (v1 현행 위임 / v2 규칙 기반) + shadow 지원
내용: MacroNewsLayer.detect() 의 1회 수집 결과(DetectionInput)를 받아 정책별 판정(PolicyOutcome)을
      산출한다. v1 은 MacroNewsLayer 의 기존 메서드를 그대로 호출(결과 불변),
      v2 는 KeywordMatcherV2 + YouTube 토큰 교집합 + health v2(수집 품질) + 점수 신뢰계수.

주요 클래스:
  - DetectionInput / PolicyOutcome: 정책 입출력
  - ScoringPolicy: 정책 Protocol
  - KeywordMatcherV2: \b 정규식 규칙 매처
  - ScoringPolicyV1 / ScoringPolicyV2

주요 함수:
  - resolve_detection_policy(raw): DETECTION_POLICY env 해석 (기본 v1, 잘못된 값 → v1 + warning)
  - get_fresh_window_seconds(): v2 recency 윈도우 (DQ 공통 기본 86400)
  - outcome_to_shadow(outcome): ia_alert_run.detail.shadow 기록용 dict
"""

from __future__ import annotations

import dataclasses
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal, Protocol

from collectors.base import CollectorEvent
from collectors.news_collector import apply_cross_validation
from config.keyword_rules import (
    GENERIC_SOLO_FACTOR,
    GENERIC_TOPIC,
    GEO_COMBO_BONUS,
    KEYWORD_THRESHOLD_V2,
    V2_RULE_EXCLUDED_BY,
    V2_RULE_EXCLUSION_OVERRIDE,
    V2_RULE_GROUP,
    V2_RULE_REQUIRES,
    V2_RULE_VETO,
    V2_RULES,
    YT_TOKEN_MAP,
)
from config.settings import DQ_FRESH_WINDOW_DEFAULT_SECONDS, get_env_int
from core.logger import get_logger
from detection.dq_monitor import DataQualityState

if TYPE_CHECKING:
    from detection.macro_news_layer import MacroNewsLayer

VERSION = "1.0.0"

logger = get_logger(__name__)

AlertLevel = Literal["L1", "L2", "L3", "NONE", "SYSTEM_DEGRADED"]

# 제목: 정책 식별자 / 버전
VALID_POLICIES: tuple[str, ...] = ("v1", "v2")
DEFAULT_POLICY: str = "v1"
POLICY_V2_VERSION: str = "v2.0.0"

# 제목: v2 health 가중치 (현행 v1 diversity 0.35 : recency 0.25 비율 유지, 합 1.0)
HEALTH_V2_W_DIVERSITY: float = 0.35 / 0.60
HEALTH_V2_W_RECENCY: float = 0.25 / 0.60

# 제목: v2 점수 신뢰계수 = TRUST_BASE + TRUST_SPAN × (0.5·cross_val + 0.5·dedup)  → [0.6, 1.0]
TRUST_BASE: float = 0.6
TRUST_SPAN: float = 0.4

# 제목: v1 health diversity_map 과 동일 (macro_news_layer._compute_health_score)
_DIVERSITY_MAP: dict[frozenset, float] = {
    frozenset(): 0.0, frozenset(["S"]): 0.8, frozenset(["A"]): 0.5, frozenset(["B"]): 0.3,
    frozenset(["S", "A"]): 1.0, frozenset(["A", "B"]): 0.7, frozenset(["S", "B"]): 0.8,
    frozenset(["S", "A", "B"]): 1.0,
}

# 제목: shadow 기록 절단 한도
SHADOW_TOP_NEWS: int = 3
SHADOW_MAX_FACTORS: int = 5
SHADOW_TITLE_MAX: int = 120
SHADOW_REASON_MAX: int = 200


# ────────────────────────────────────────────────────────
# 입출력
# ────────────────────────────────────────────────────────
@dataclass(frozen=True)
class DetectionInput:
    """1회 수집 결과 묶음 (모든 정책 공통 입력)."""

    news_v1: list[CollectorEvent]  # NewsCollector.collect() 반환 (v1 필터·교차검증 완료)
    news_candidates: list[CollectorEvent]  # 키워드 필터 전 검증 통과 후보 (복사본)
    youtube_events: list[CollectorEvent]  # YouTube 키워드 필터 후
    youtube_raw: list[CollectorEvent]  # YouTube 필터 전
    market_profile: str
    thresholds: dict[str, float]
    dq_state: DataQualityState | None


@dataclass
class PolicyOutcome:
    """정책 1개의 판정 결과."""

    policy: str
    level: AlertLevel
    reason: str
    factors: list[dict]
    score: float
    news_score: float
    youtube_bonus: float
    health_score: float | None
    health_components: dict[str, float | None] = field(default_factory=dict)
    news_events: list[CollectorEvent] = field(default_factory=list)
    trust: float | None = None


class ScoringPolicy(Protocol):
    """판정 정책 인터페이스."""

    name: str

    def evaluate(self, inp: DetectionInput) -> PolicyOutcome:
        """DetectionInput → PolicyOutcome (부수효과: 입력 이벤트 변경 금지)."""
        ...


# ────────────────────────────────────────────────────────
# env / 윈도우
# ────────────────────────────────────────────────────────
def resolve_detection_policy(raw: str | None = None) -> str:
    """
    제목: DETECTION_POLICY 해석
    내용: raw 가 None 이면 env DETECTION_POLICY. 빈 문자열(미설정 vars) → v1.
          지원하지 않는 값 → WARNING 후 v1. 대소문자·공백 무시.
    """
    value = (os.getenv("DETECTION_POLICY", "") if raw is None else raw).strip().lower()
    if not value:
        return DEFAULT_POLICY
    if value not in VALID_POLICIES:
        logger.warning(f"[ScoringPolicy] DETECTION_POLICY='{value}' 미지원 → {DEFAULT_POLICY}")
        return DEFAULT_POLICY
    return value


def other_policy(name: str) -> str:
    """active 가 아닌 정책명 (shadow 대상)."""
    return "v2" if name == "v1" else "v1"


def get_fresh_window_seconds() -> int:
    """v2 recency 윈도우: DQ_FRESH_WINDOW_SECONDS (빈 문자열/비정수 → 86400, DQ 와 동일)."""
    return get_env_int("DQ_FRESH_WINDOW_SECONDS", DQ_FRESH_WINDOW_DEFAULT_SECONDS)


# ────────────────────────────────────────────────────────
# v2 키워드 매처
# ────────────────────────────────────────────────────────
@dataclass(frozen=True)
class MatchResult:
    """KeywordMatcherV2.match 결과."""

    score: float
    rule_ids: list[str]
    topics: list[str]  # generic 제외, 정렬


class KeywordMatcherV2:
    """
    제목: v2 키워드 매처
    내용: V2_RULES 를 IGNORECASE 정규식으로 컴파일. 규칙별 1회 매칭 가중치 합산.
          앵커 규칙 미매칭 시 generic 규칙 가중치 × GENERIC_SOLO_FACTOR.
          규칙 의존성(keyword_rules): VETO(문맥 배제) → EXCLUDED_BY → REQUIRES → GROUP(1회 가산).
          지정학 공격어(geo_attack 그룹) + 시설어(geo_target) 동시 매칭 시 + GEO_COMBO_BONUS.
    """

    def __init__(self, rules: list[tuple[str, str, float, bool, str]] | None = None) -> None:
        self._rules = [
            (rid, topic, weight, anchor, re.compile(pattern, re.IGNORECASE))
            for rid, topic, weight, anchor, pattern in (rules if rules is not None else V2_RULES)
        ]
        self._veto = {rid: re.compile(p, re.IGNORECASE) for rid, p in V2_RULE_VETO.items()}

    def _effective_hits(self, text: str) -> list[tuple[str, str, float, bool]]:
        """정규식 매칭 → 의존성 규칙 적용 후 유효 hit (규칙 정의 순서 유지)."""
        hits = [(rid, topic, weight, anchor) for rid, topic, weight, anchor, rx in self._rules
                if rx.search(text)]
        hits = [h for h in hits if not (h[0] in self._veto and self._veto[h[0]].search(text))]
        ids = {h[0] for h in hits}
        hits = [h for h in hits
                if not any(x in ids for x in V2_RULE_EXCLUDED_BY.get(h[0], ()))
                or any(o in ids for o in V2_RULE_EXCLUSION_OVERRIDE.get(h[0], ()))]
        ids = {h[0] for h in hits}
        hits = [h for h in hits
                if h[0] not in V2_RULE_REQUIRES or any(r in ids for r in V2_RULE_REQUIRES[h[0]])]
        # 그룹: 같은 그룹은 최대 가중치 1건만 (동률이면 먼저 정의된 규칙)
        best: dict[str, tuple[str, str, float, bool]] = {}
        for h in hits:
            group = V2_RULE_GROUP.get(h[0], h[0])
            if group not in best or h[2] > best[group][2]:
                best[group] = h
        keep = {id(h) for h in best.values()}
        return [h for h in hits if id(h) in keep]

    def match(self, text: str) -> MatchResult:
        hits = self._effective_hits(text)
        has_anchor = any(anchor for _, _, _, anchor in hits)
        score = 0.0
        for _, topic, weight, _ in hits:
            factor = GENERIC_SOLO_FACTOR if (topic == GENERIC_TOPIC and not has_anchor) else 1.0
            score += weight * factor
        rule_ids = [rid for rid, _, _, _ in hits]
        if "geo_target" in rule_ids:  # REQUIRES 로 geo_attack 그룹 동반이 보장됨
            score += GEO_COMBO_BONUS
        topics = sorted({topic for _, topic, _, _ in hits if topic != GENERIC_TOPIC})
        return MatchResult(round(score, 4), rule_ids, topics)

    def filter_events(self, events: list[CollectorEvent]) -> list[tuple[CollectorEvent, list[str]]]:
        """
        제목: v2 키워드 필터
        내용: auto_l1 은 우회(점수·키워드 원본 유지). 그 외 score ≥ KEYWORD_THRESHOLD_V2 통과.
              반환 이벤트는 복사본(dataclasses.replace) — 입력 불변.

        Returns:
            list[(이벤트 복사본, 토픽 목록)]
        """
        out: list[tuple[CollectorEvent, list[str]]] = []
        for event in events:
            if event.auto_l1:
                out.append((dataclasses.replace(event, matched_keywords=list(event.matched_keywords)),
                            []))
                continue
            result = self.match(f"{event.title} {event.summary}")
            if result.score >= KEYWORD_THRESHOLD_V2:
                out.append((dataclasses.replace(event, keyword_score=result.score,
                                                matched_keywords=list(result.rule_ids)),
                            result.topics))
        return out


def normalize_youtube_tokens(keywords: list[str]) -> set[str]:
    """YouTube matched_keywords → 공통 토픽 토큰 (casefold 조회, None/미등록 제외)."""
    folded = {k.casefold(): v for k, v in YT_TOKEN_MAP.items()}
    tokens: set[str] = set()
    for keyword in keywords:
        token = folded.get(str(keyword).casefold())
        if token:
            tokens.add(token)
    return tokens


# ────────────────────────────────────────────────────────
# 정책 구현
# ────────────────────────────────────────────────────────
class ScoringPolicyV1:
    """
    제목: v1 정책 (현행 그대로)
    내용: MacroNewsLayer 의 기존 메서드를 기존 Step3~5 순서·인자로 호출만 한다 (결과 불변).
    """

    name = "v1"

    def __init__(self, layer: MacroNewsLayer) -> None:
        self._layer = layer

    def evaluate(self, inp: DetectionInput) -> PolicyOutcome:
        news, yt = inp.news_v1, inp.youtube_events
        news_score = self._layer._compute_news_score(news)
        youtube_bonus = self._layer._compute_youtube_bonus(news, yt)
        final_score = min(news_score + youtube_bonus, 10.0)
        health = self._layer._compute_health_score(news, yt)
        level, reason, factors = self._layer._judge_level(
            final_score, news, health, yt, inp.thresholds, dq_state=inp.dq_state,
        )
        return PolicyOutcome(
            policy=self.name, level=level, reason=reason, factors=factors, score=final_score,
            news_score=news_score, youtube_bonus=youtube_bonus, health_score=health,
            health_components={"health_score": health}, news_events=news,
        )


class ScoringPolicyV2:
    """
    제목: v2 정책 (규칙 기반)
    내용: 필터 전 후보 → KeywordMatcherV2 → 교차검증(복사본) → Tier 점수 + YT 토큰 보너스
          × 신뢰계수, health = 수집 품질(다양성·최신성). 뉴스 후보 0건 → health None(판정불가).
          레벨 판정은 MacroNewsLayer._judge_level 그대로 (L1 게이트·시장 프로파일 임계 동일).
    """

    name = "v2"

    def __init__(self, layer: MacroNewsLayer, matcher: KeywordMatcherV2 | None = None) -> None:
        self._layer = layer
        self._matcher = matcher or KeywordMatcherV2()
        logger.info(f"[ScoringPolicyV2] v{VERSION} 초기화 (rules={len(self._matcher._rules)}, "
                    f"threshold={KEYWORD_THRESHOLD_V2})")

    # ── 구성 요소 ──────────────────────────────────────
    def select_news(self, candidates: list[CollectorEvent]) -> tuple[list[CollectorEvent],
                                                                     dict[str, list[str]]]:
        """v2 필터 + 교차검증(복사본). 반환: (이벤트, event_id→토픽)."""
        pairs = self._matcher.filter_events(candidates)
        events = [e for e, _ in pairs]
        topics = {e.event_id: t for e, t in pairs}
        if events:
            # NewsCollector 와 같은 무상태 교차검증 함수 (복사본에만 기록, 리뷰 m4)
            events = apply_cross_validation(events)
        events.sort(key=lambda e: e.effective_score, reverse=True)
        return events, topics

    @staticmethod
    def compute_youtube_bonus(topics_by_news: list[list[str]],
                              youtube_events: list[CollectorEvent]) -> float:
        """YT 토큰 ∩ 뉴스 토픽 ≥ 1 인 YT 이벤트마다 1.0 × channel_weight (현행 산식)."""
        if not youtube_events or not topics_by_news:
            return 0.0
        news_sets = [set(t) for t in topics_by_news if t]
        bonus = 0.0
        for yt in youtube_events:
            tokens = normalize_youtube_tokens(yt.matched_keywords)
            if tokens and any(tokens & s for s in news_sets):
                bonus += 1.0 * yt.channel_weight
        return bonus

    @staticmethod
    def compute_health(candidates: list[CollectorEvent], youtube_raw: list[CollectorEvent],
                       now: datetime | None = None) -> tuple[float | None, dict]:
        """health v2 = w_d·diversity + w_r·recency. 뉴스 후보 0건 → None."""
        if not candidates:
            return None, {"diversity": None, "recency": None}
        tiers = frozenset(e.tier for e in candidates if e.tier)
        diversity = _DIVERSITY_MAP.get(tiers, 0.5)
        if youtube_raw:
            diversity = min(diversity + 0.1, 1.0)
        now = now or datetime.now(UTC)
        cutoff = now - timedelta(seconds=get_fresh_window_seconds())
        events = [*candidates, *youtube_raw]
        recent = sum(1 for e in events
                     if e.published_at.tzinfo is not None and e.published_at >= cutoff)
        recency = recent / len(events)
        health = HEALTH_V2_W_DIVERSITY * diversity + HEALTH_V2_W_RECENCY * recency
        return round(min(health, 1.0), 4), {"diversity": diversity, "recency": recency}

    @staticmethod
    def compute_trust(news: list[CollectorEvent]) -> tuple[float, dict]:
        """신뢰계수 [0.6, 1.0]. 교차검증·중복도 정의는 v1 health 요소 3·4 와 동일."""
        if not news:
            return 1.0, {"cross_val": None, "dedup": None}
        cross_val = sum(1 for e in news if e.source_count > 1) / len(news)
        dedup = min(sum(e.source_count for e in news) / len(news) / 3.0, 1.0)
        trust = TRUST_BASE + TRUST_SPAN * (0.5 * cross_val + 0.5 * dedup)
        return round(trust, 4), {"cross_val": cross_val, "dedup": dedup}

    # ── 평가 ──────────────────────────────────────────
    def evaluate(self, inp: DetectionInput) -> PolicyOutcome:
        news, topics = self.select_news(inp.news_candidates)
        news_score = self._layer._compute_news_score(news)
        youtube_bonus = self.compute_youtube_bonus(
            [topics.get(e.event_id, []) for e in news], inp.youtube_events,
        )
        trust, trust_parts = self.compute_trust(news)
        score = min((news_score + youtube_bonus) * trust, 10.0)
        health, health_parts = self.compute_health(inp.news_candidates, inp.youtube_raw)
        components = {"health_score": health, **health_parts, "trust": trust, **trust_parts}

        if health is None:
            if inp.dq_state is not None and inp.dq_state.degraded_flag:
                factors = [{"factor": "data_quality_degraded", "weight": None,
                            "reasons": inp.dq_state.degraded_reasons[:5]}]
                reason = (
                    f"SYSTEM_DEGRADED: 수집 시스템 이상 감지 "
                    f"(success={inp.dq_state.source_success_rate:.2f}, "
                    f"reasons={','.join(inp.dq_state.degraded_reasons[:2])})"
                )
                level: AlertLevel = "SYSTEM_DEGRADED"
            else:
                factors = [{"factor": "undeterminable_no_news", "weight": None}]
                reason = "NONE(판정불가): 뉴스 후보 0건"
                level = "NONE"
        else:
            level, reason, factors = self._layer._judge_level(
                score, news, health, inp.youtube_events, inp.thresholds, dq_state=inp.dq_state,
            )
        return PolicyOutcome(
            policy=self.name, level=level, reason=reason, factors=factors, score=score,
            news_score=news_score, youtube_bonus=youtube_bonus, health_score=health,
            health_components=components, news_events=news, trust=trust,
        )


# ────────────────────────────────────────────────────────
# shadow 기록
# ────────────────────────────────────────────────────────
def _r(value: float | None, nd: int = 3) -> float | None:
    return None if value is None else round(float(value), nd)


def outcome_to_shadow(outcome: PolicyOutcome) -> dict:
    """PolicyOutcome → ia_alert_run.detail.shadow (JSON 직렬화 가능)."""
    return {
        "policy": outcome.policy,
        "level": outcome.level,
        "score": _r(outcome.score),
        "health": _r(outcome.health_score),
        "news_score": _r(outcome.news_score),
        "yt_bonus": _r(outcome.youtube_bonus),
        "trust": _r(outcome.trust),
        "top_news": [
            {"source": e.source_name, "title": e.title[:SHADOW_TITLE_MAX],
             "kw": list(e.matched_keywords[:5])}
            for e in outcome.news_events[:SHADOW_TOP_NEWS]
        ],
        "factors": _jsonable(outcome.factors[:SHADOW_MAX_FACTORS]),
        "reason": outcome.reason[:SHADOW_REASON_MAX],
    }


def _jsonable(value):
    """factors 내부 값을 JSON 기본 타입으로 정규화 (float/int/str/bool/None/list/dict)."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return str(value)
