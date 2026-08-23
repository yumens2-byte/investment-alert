"""RSS evidence based recent YouTube video summary and comparison pipeline."""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from collectors.base import CollectorEvent
from core.logger import get_logger

logger = get_logger(__name__)

_SPACE_RE = re.compile(r"\s+")
_SENTENCE_RE = re.compile(r"(?<=[.!?。！？])\s+|\n+")
_TICKER_RE = re.compile(r"\b[A-Z]{2,5}\b")
_FORECAST_MARKERS = ("전망", "예상", "가능성", "것이다", "보인다", "forecast", "expect", "likely")
_EVENT_KEYWORDS = {
    "monetary_policy": ("fomc", "파월", "금리", "긴축", "완화"),
    "inflation": ("cpi", "인플레", "물가"),
    "employment": ("고용", "실업", "nfp"),
    "earnings": ("실적", "어닝", "매출"),
    "market_shock": ("폭락", "급락", "급등", "서킷브레이커", "거래정지"),
    "geopolitics": ("전쟁", "공격", "관세", "북한"),
}


@dataclass(slots=True)
class VideoSummary:
    video_id: str
    channel_name: str
    published_at: datetime
    title: str
    url: str
    evidence_level: str
    evidence_chars: int
    topic: str
    entities: list[str] = field(default_factory=list)
    event_type: str = "unknown"
    factual_claims: list[str] = field(default_factory=list)
    opinions_or_forecasts: list[str] = field(default_factory=list)
    concise_summary: str = ""
    uncertainty_notes: list[str] = field(default_factory=list)
    source_fingerprint: str = ""
    model_version: str = "rules-v1"


@dataclass(slots=True)
class VideoComparison:
    comparison_id: str
    window_start: datetime
    window_end: datetime
    topic_key: str
    topic_label: str
    video_ids: list[str]
    channels: list[str]
    common_claims: list[str] = field(default_factory=list)
    disagreements: list[dict] = field(default_factory=list)
    unique_claims: list[dict] = field(default_factory=list)
    stance_changes: list[dict] = field(default_factory=list)
    news_confirmed_claims: list[str] = field(default_factory=list)
    unverified_claims: list[str] = field(default_factory=list)
    comparison_summary: str = ""
    confidence: float = 0.0
    evidence_coverage: float = 0.0
    publish_scope: str = "none"


@dataclass(slots=True)
class YouTubeAnalysisResult:
    summaries: list[VideoSummary] = field(default_factory=list)
    comparisons: list[VideoComparison] = field(default_factory=list)
    skipped_count: int = 0


class YouTubeVideoAnalysisPipeline:
    """Create conservative RSS evidence summaries and cross-channel comparisons."""

    def __init__(self, window_hours: int = 24, max_candidates: int = 12) -> None:
        self.window_hours = window_hours
        self.max_candidates = max_candidates

    def analyze(self, events: list[CollectorEvent]) -> YouTubeAnalysisResult:
        candidates = self._select_candidates(events)
        summaries = [self._summarize(event) for event in candidates]
        comparisons = self._compare(summaries)
        logger.info(
            "[YouTubeVideoAnalysis] summaries=%d comparisons=%d skipped=%d",
            len(summaries), len(comparisons), max(0, len(events) - len(candidates)),
        )
        return YouTubeAnalysisResult(
            summaries=summaries,
            comparisons=comparisons,
            skipped_count=max(0, len(events) - len(candidates)),
        )

    def _select_candidates(self, events: list[CollectorEvent]) -> list[CollectorEvent]:
        cutoff = datetime.now(UTC) - timedelta(hours=self.window_hours)
        unique: dict[str, CollectorEvent] = {}
        for event in events:
            published = event.published_at
            if published.tzinfo is None:
                published = published.replace(tzinfo=UTC)
            if published < cutoff:
                continue
            video_id = self._video_id(event)
            current = unique.get(video_id)
            if current is None or event.published_at > current.published_at:
                unique[video_id] = event
        ranked = sorted(
            unique.values(),
            key=lambda item: (item.keyword_score * item.channel_weight, item.published_at),
            reverse=True,
        )
        return ranked[: self.max_candidates]

    def _summarize(self, event: CollectorEvent) -> VideoSummary:
        description = self._clean(event.summary)
        evidence_level = "description" if description else "metadata"
        event_type = self._event_type(f"{event.title} {description}")
        entities = self._entities(f"{event.title} {description}", event.matched_keywords)
        factual: list[str] = []
        forecasts: list[str] = []
        if description:
            for sentence in self._sentences(description)[:8]:
                target = forecasts if any(marker in sentence.lower() for marker in _FORECAST_MARKERS) else factual
                target.append(sentence)

        uncertainty = []
        if evidence_level == "metadata":
            uncertainty.append("RSS description이 없어 제목 기반 주제 분류만 수행함")
            concise = f"제목상 {event.title} 주제를 다루는 영상"
        else:
            concise = (factual + forecasts)[0] if (factual or forecasts) else description[:240]

        fingerprint = hashlib.sha256(
            f"{event.title}\n{description}".encode()
        ).hexdigest()[:16]
        return VideoSummary(
            video_id=self._video_id(event), channel_name=event.source_name,
            published_at=event.published_at, title=event.title, url=event.url,
            evidence_level=evidence_level, evidence_chars=len(description),
            topic=event_type if event_type != "unknown" else (entities[0] if entities else "unknown"),
            entities=entities, event_type=event_type, factual_claims=factual,
            opinions_or_forecasts=forecasts, concise_summary=concise,
            uncertainty_notes=uncertainty, source_fingerprint=fingerprint,
        )

    def _compare(self, summaries: list[VideoSummary]) -> list[VideoComparison]:
        groups: dict[str, list[VideoSummary]] = {}
        for summary in summaries:
            topic_key = summary.event_type
            if topic_key == "unknown":
                topic_key = summary.entities[0].lower() if summary.entities else f"video:{summary.video_id}"
            groups.setdefault(topic_key, []).append(summary)

        results: list[VideoComparison] = []
        for topic_key, group in groups.items():
            channels = sorted({item.channel_name for item in group})
            if len(group) < 2 or len(channels) < 2:
                continue
            claims: dict[str, list[tuple[str, str]]] = {}
            for item in group:
                for claim in item.factual_claims + item.opinions_or_forecasts:
                    claims.setdefault(self._normalize_claim(claim), []).append((item.channel_name, claim))
            common = [values[0][1] for values in claims.values() if len({v[0] for v in values}) >= 2]
            unique = [
                {"channel": values[0][0], "claim": values[0][1]}
                for values in claims.values() if len({v[0] for v in values}) == 1
            ]
            evidence_coverage = sum(item.evidence_level == "description" for item in group) / len(group)
            confidence = min(0.69, 0.30 * evidence_coverage + 0.25 * min(len(channels) / 3, 1.0))
            if not common:
                confidence = max(0.0, confidence - 0.10)
            comparison_id = hashlib.sha256(
                f"{topic_key}|{'|'.join(sorted(item.video_id for item in group))}".encode()
            ).hexdigest()[:16]
            results.append(VideoComparison(
                comparison_id=comparison_id,
                window_start=min(item.published_at for item in group),
                window_end=max(item.published_at for item in group),
                topic_key=topic_key, topic_label=topic_key,
                video_ids=[item.video_id for item in group], channels=channels,
                common_claims=common, unique_claims=unique,
                unverified_claims=common + [item["claim"] for item in unique],
                comparison_summary=(
                    f"{len(channels)}개 채널의 {len(group)}개 영상 비교: "
                    f"공통 주장 {len(common)}건, 채널별 주장 {len(unique)}건"
                ),
                confidence=round(confidence, 3), evidence_coverage=round(evidence_coverage, 3),
                publish_scope="internal" if confidence >= 0.45 else "none",
            ))
        return sorted(results, key=lambda item: item.confidence, reverse=True)

    @staticmethod
    def _video_id(event: CollectorEvent) -> str:
        match = re.search(r"[?&]v=([^&]+)", event.url)
        return match.group(1) if match else event.event_id

    @staticmethod
    def _clean(value: str) -> str:
        value = re.sub(r"<[^>]+>", " ", html.unescape(value or ""))
        return _SPACE_RE.sub(" ", value).strip()[:4000]

    @staticmethod
    def _sentences(value: str) -> list[str]:
        return [part.strip()[:500] for part in _SENTENCE_RE.split(value) if len(part.strip()) >= 12]

    @staticmethod
    def _event_type(value: str) -> str:
        lowered = value.lower()
        for event_type, keywords in _EVENT_KEYWORDS.items():
            if any(keyword in lowered for keyword in keywords):
                return event_type
        return "unknown"

    @staticmethod
    def _entities(value: str, matched_keywords: list[str]) -> list[str]:
        entities = set(_TICKER_RE.findall(value))
        entities.update(keyword for keyword in matched_keywords if len(keyword) >= 2)
        return sorted(entities)[:12]

    @staticmethod
    def _normalize_claim(value: str) -> str:
        return re.sub(r"[^0-9a-z가-힣]+", " ", value.lower()).strip()
