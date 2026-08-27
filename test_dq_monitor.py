"""
제목: DataQualityMonitor 단위 테스트 (Phase 1 / N-06)
내용: detection/dq_monitor.py의 10개 케이스를 검증한다.

테스트 매트릭스:
  1. 정상 케이스 (모든 소스 OK, fresh 충분)
  2. 절반 소스 실패 → degraded
  3. 다수 소스 실패 → degraded
  4. fresh 이벤트 0건 → degraded
  5. lag 초과 → degraded
  6. volume_zscore 임계 미달 → degraded
  7. 환경변수 override → 임계 변경 동작
  8. 빈 events 안전 처리
  9. to_dict() JSON serializable
  10. 다중 사유 동시 발생 시 모두 기록
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from collectors.base import CollectorEvent
from detection.dq_monitor import DataQualityMonitor

# ── helpers ──────────────────────────────────────────────────────


def _make_event(
    source_name: str = "test_source",
    age_seconds: int = 600,
    title_suffix: str = "",
) -> CollectorEvent:
    """CollectorEvent 인스턴스 생성. age_seconds 만큼 과거 published_at 부여."""
    now = datetime.now(UTC)
    published_at = now - timedelta(seconds=age_seconds)
    title = f"테스트 뉴스 {title_suffix}".strip()
    url = f"https://example.com/{source_name}/{title_suffix or 'a'}"
    raw = f"{source_name}|{url}|{title}".encode()
    event_id = hashlib.sha256(raw).hexdigest()
    return CollectorEvent(
        source_type="news",
        source_name=source_name,
        event_id=event_id,
        title=title,
        summary="요약",
        url=url,
        published_at=published_at,
        tier="A",
        channel_weight=1.0,
        matched_keywords=["테스트"],
    )


def _cycle_window(seconds: float = 10.0) -> tuple[datetime, datetime]:
    """cycle_started_at, cycle_finished_at 페어 생성 (정상 cycle용)."""
    start = datetime.now(UTC) - timedelta(seconds=seconds)
    end = datetime.now(UTC)
    return start, end


# ── tests ────────────────────────────────────────────────────────


def test_all_sources_ok_returns_not_degraded() -> None:
    """1. 모든 소스 OK + fresh 100% + lag 짧음 → degraded=False"""
    monitor = DataQualityMonitor()
    start, end = _cycle_window(seconds=10.0)
    state = monitor.evaluate(
        cycle_started_at=start,
        cycle_finished_at=end,
        source_results={"fed_rss": True, "reuters": True, "wsj": True, "yahoo": True},
        news_events=[_make_event(age_seconds=600, title_suffix="a")],
        youtube_events=[_make_event(age_seconds=300, title_suffix="b")],
    )
    assert state.degraded_flag is False
    assert state.degraded_reasons == []
    assert state.source_success_rate == 1.0
    assert state.fresh_event_ratio == 1.0


def test_threshold_boundary_source_success_rate() -> None:
    """2. 소스 success_rate 임계(0.75) 경계값 동작.
       조건은 < 이므로 0.75 정확값은 통과. 0.50 (절반 실패)부터 degraded.
       (B-fix2: 임계 0.50 → 0.75로 강화. fresh가 정보성 지표화되어 source가 주 건강 지표)"""
    monitor = DataQualityMonitor()
    start, end = _cycle_window()

    # 0.75 정확히 → 임계 미만 아니므로 source_success_rate 사유 없음
    state_at_threshold = monitor.evaluate(
        cycle_started_at=start,
        cycle_finished_at=end,
        source_results={"a": True, "b": True, "c": True, "d": False},  # 0.75
        news_events=[_make_event(age_seconds=600, title_suffix="x")],
        youtube_events=[],
    )
    assert state_at_threshold.source_success_rate == 0.75
    assert not any(
        "source_success_rate" in r for r in state_at_threshold.degraded_reasons
    )

    # 0.50 (절반 실패) → 임계 0.75 미만 → degraded
    state_below = monitor.evaluate(
        cycle_started_at=start,
        cycle_finished_at=end,
        source_results={"a": True, "b": True, "c": False, "d": False},  # 0.50
        news_events=[_make_event(age_seconds=600, title_suffix="x")],
        youtube_events=[],
    )
    assert state_below.source_success_rate == 0.50
    assert state_below.degraded_flag is True
    assert any("source_success_rate" in r for r in state_below.degraded_reasons)


def test_majority_sources_fail_returns_degraded() -> None:
    """3. 소스 다수 실패 (success=0.25) → degraded=True"""
    monitor = DataQualityMonitor()
    start, end = _cycle_window()
    state = monitor.evaluate(
        cycle_started_at=start,
        cycle_finished_at=end,
        source_results={"a": True, "b": False, "c": False, "d": False},
        news_events=[_make_event(age_seconds=600, title_suffix="x")],
        youtube_events=[],
    )
    assert state.degraded_flag is True
    assert state.source_success_rate == 0.25
    assert any("source_success_rate" in r for r in state.degraded_reasons)


def test_old_events_alone_do_not_trigger_degraded() -> None:
    """4. fresh window 밖 이벤트만 있어도 source가 정상이면 degraded 아님.
       (B-fix2: fresh는 정보성 지표화. 평화/휴장 시간대 거짓 경보 방지)
       fresh window는 기본 24h이므로 24h 초과 발행만 'old'로 처리."""
    monitor = DataQualityMonitor()
    start, end = _cycle_window()
    # 25시간 전 (24h fresh window 밖)
    state = monitor.evaluate(
        cycle_started_at=start,
        cycle_finished_at=end,
        source_results={"a": True, "b": True, "c": True, "d": True},
        news_events=[
            _make_event(age_seconds=25 * 3600, title_suffix="old1"),
            _make_event(age_seconds=25 * 3600, title_suffix="old2"),
        ],
        youtube_events=[],
    )
    # source 100% 정상 + fresh=0 → degraded=False (B-fix2)
    assert state.degraded_flag is False
    assert state.fresh_event_ratio == 0.0
    assert state.source_success_rate == 1.0
    # fresh_event_ratio 사유는 발생하지 않아야 함 (임계 0.0)
    assert not any("fresh_event_ratio" in r for r in state.degraded_reasons)


def test_lag_exceeds_threshold_returns_degraded() -> None:
    """5. cycle 소요시간이 임계 초과 → degraded=True"""
    monitor = DataQualityMonitor()
    # 95초 소요 → 임계 90초 초과
    start = datetime.now(UTC) - timedelta(seconds=95)
    end = datetime.now(UTC)
    state = monitor.evaluate(
        cycle_started_at=start,
        cycle_finished_at=end,
        source_results={"a": True, "b": True},
        news_events=[_make_event(age_seconds=300, title_suffix="x")],
        youtube_events=[],
    )
    assert state.degraded_flag is True
    assert state.lag_seconds_p95 > 90.0
    assert any("lag_seconds_p95" in r for r in state.degraded_reasons)


def test_volume_zscore_below_threshold_returns_degraded() -> None:
    """6. baseline 대비 수집량 급감 (zscore<-2) → degraded=True"""
    monitor = DataQualityMonitor()
    start, end = _cycle_window()
    # baseline=20, current=5
    # std = max(20*0.30, 1.0) = 6.0
    # zscore = (5 - 20) / 6.0 = -2.5  → 임계 -2.0 미만
    state = monitor.evaluate(
        cycle_started_at=start,
        cycle_finished_at=end,
        source_results={"a": True, "b": True},
        news_events=[_make_event(age_seconds=600, title_suffix=str(i)) for i in range(5)],
        youtube_events=[],
        baseline_volume_avg=20.0,
    )
    assert state.degraded_flag is True
    assert state.volume_zscore is not None
    assert state.volume_zscore < -2.0
    assert any("volume_zscore" in r for r in state.degraded_reasons)


def test_threshold_override_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """7. 환경변수로 임계값을 엄격하게 변경 시 정상 케이스도 degraded로 판정"""
    monkeypatch.setenv("DQ_SOURCE_SUCCESS_MIN", "0.99")  # 99% 이상만 OK
    monitor = DataQualityMonitor()
    start, end = _cycle_window()
    state = monitor.evaluate(
        cycle_started_at=start,
        cycle_finished_at=end,
        source_results={"a": True, "b": True, "c": True, "d": False},  # 0.75
        news_events=[_make_event(age_seconds=600, title_suffix="x")],
        youtube_events=[],
    )
    assert state.degraded_flag is True
    assert any("source_success_rate" in r for r in state.degraded_reasons)


def test_empty_events_safely_handled() -> None:
    """8. events=[] + 모든 소스 실패 시 예외 없이 degraded=True 반환.
       (B-fix2: fresh는 정보성 지표화. source 실패만 degraded 사유)"""
    monitor = DataQualityMonitor()
    start, end = _cycle_window()
    state = monitor.evaluate(
        cycle_started_at=start,
        cycle_finished_at=end,
        source_results={"a": False, "b": False},
        news_events=[],
        youtube_events=[],
    )
    assert state.degraded_flag is True
    assert state.fresh_event_ratio == 0.0
    assert state.source_success_rate == 0.0
    # source_success_rate가 핵심 사유 (fresh는 정보성이라 사유 미기록)
    assert any("source_success_rate" in r for r in state.degraded_reasons)
    # fresh_event_ratio는 임계 0.0이라 사유로 안 잡힘
    assert not any("fresh_event_ratio" in r for r in state.degraded_reasons)


def test_to_dict_json_serializable() -> None:
    """9. DataQualityState.to_dict() 결과가 json.dumps 가능 (Supabase 적재 호환)"""
    monitor = DataQualityMonitor()
    start, end = _cycle_window()
    state = monitor.evaluate(
        cycle_started_at=start,
        cycle_finished_at=end,
        source_results={"a": True, "b": True},
        news_events=[_make_event(age_seconds=600, title_suffix="x")],
        youtube_events=[],
        baseline_volume_avg=10.0,
    )
    serialized = json.dumps(state.to_dict(), ensure_ascii=False)
    assert isinstance(serialized, str)
    parsed = json.loads(serialized)
    # 핵심 키 존재 확인
    assert "degraded_flag" in parsed
    assert "source_success_rate" in parsed
    assert "cycle_started_at" in parsed
    # ISO 8601 datetime 문자열인지 확인
    assert "T" in parsed["cycle_started_at"]


def test_multiple_reasons_all_recorded() -> None:
    """10. 여러 임계 동시 위반 시 모든 사유가 degraded_reasons에 포함된다.
       (B-fix2: fresh는 정보성. success_rate + lag 두 사유 동시 위반 검증)"""
    monitor = DataQualityMonitor()
    # 95초 lag + 모든 소스 실패
    start = datetime.now(UTC) - timedelta(seconds=95)
    end = datetime.now(UTC)
    state = monitor.evaluate(
        cycle_started_at=start,
        cycle_finished_at=end,
        source_results={"a": False, "b": False, "c": False},
        news_events=[_make_event(age_seconds=7200, title_suffix="old")],
        youtube_events=[],
    )
    assert state.degraded_flag is True
    # 2개 사유 동시 발생 (success_rate, lag) — fresh는 정보성이라 사유에 없음
    assert len(state.degraded_reasons) >= 2
    joined = " | ".join(state.degraded_reasons)
    assert "source_success_rate" in joined
    assert "lag_seconds_p95" in joined
    # fresh는 정보성이라 사유로 잡히지 않음
    assert "fresh_event_ratio" not in joined
