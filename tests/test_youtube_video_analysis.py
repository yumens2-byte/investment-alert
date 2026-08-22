from datetime import UTC, datetime, timedelta

from collectors.base import CollectorEvent
from detection.youtube_video_analysis import YouTubeVideoAnalysisPipeline


def _event(video_id: str, channel: str, summary: str, *, hours: int = 1) -> CollectorEvent:
    title = "CPI 발표 이후 금리 전망"
    return CollectorEvent(
        source_type="youtube", source_name=channel, event_id=video_id,
        title=title, summary=summary,
        url=f"https://www.youtube.com/watch?v={video_id}",
        published_at=datetime.now(UTC) - timedelta(hours=hours),
        keyword_score=3.0, channel_weight=1.0, matched_keywords=["CPI", "발표"],
    )


def test_metadata_only_does_not_invent_claims() -> None:
    result = YouTubeVideoAnalysisPipeline().analyze([_event("a", "A", "")])
    summary = result.summaries[0]
    assert summary.evidence_level == "metadata"
    assert summary.factual_claims == []
    assert summary.opinions_or_forecasts == []
    assert summary.uncertainty_notes


def test_description_separates_fact_and_forecast() -> None:
    description = "미국 CPI가 오늘 발표됐습니다. 금리 인하 가능성은 낮아질 전망입니다."
    summary = YouTubeVideoAnalysisPipeline().analyze([_event("a", "A", description)]).summaries[0]
    assert summary.evidence_level == "description"
    assert summary.factual_claims == ["미국 CPI가 오늘 발표됐습니다."]
    assert summary.opinions_or_forecasts == ["금리 인하 가능성은 낮아질 전망입니다."]


def test_deduplicates_video_id_and_skips_old_event() -> None:
    recent = _event("same", "A", "미국 CPI가 발표됐습니다.")
    duplicate = _event("same", "A", "미국 CPI가 발표됐습니다.", hours=2)
    old = _event("old", "B", "미국 CPI가 발표됐습니다.", hours=30)
    result = YouTubeVideoAnalysisPipeline().analyze([recent, duplicate, old])
    assert [item.video_id for item in result.summaries] == ["same"]
    assert result.skipped_count == 2


def test_compares_independent_channels_as_internal_only() -> None:
    claim = "미국 CPI가 시장 예상보다 높게 발표됐습니다."
    result = YouTubeVideoAnalysisPipeline().analyze([
        _event("a", "채널A", claim),
        _event("b", "채널B", claim),
    ])
    assert len(result.comparisons) == 1
    comparison = result.comparisons[0]
    assert comparison.common_claims == [claim]
    assert comparison.channels == ["채널A", "채널B"]
    assert comparison.publish_scope in {"internal", "none"}
    assert comparison.confidence < 0.70
    assert comparison.news_confirmed_claims == []


def test_same_channel_is_not_independent_confirmation() -> None:
    claim = "미국 CPI가 시장 예상보다 높게 발표됐습니다."
    result = YouTubeVideoAnalysisPipeline().analyze([
        _event("a", "채널A", claim),
        _event("b", "채널A", claim),
    ])
    assert result.comparisons == []
