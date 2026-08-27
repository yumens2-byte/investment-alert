"""
제목: DataQualityStore 단위 테스트 (Phase 1 / N-03 보충)
내용: Supabase 클라이언트를 mocker로 stub하여 5개 시나리오 검증.

테스트 매트릭스:
  1. 정상 INSERT — id 반환
  2. response.data 빈 경우 — None 반환
  3. id 필드 누락 — None 반환
  4. Supabase 호출 예외 — None 반환 (raise 안 함)
  5. cycle 시간 누락 — 클라이언트 호출 없이 None 반환
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from db.dq_store import DataQualityStore
from detection.dq_monitor import DataQualityState

# ── helpers ──────────────────────────────────────────────────────


def _make_state(degraded: bool = False) -> DataQualityState:
    """기본 DataQualityState 생성"""
    return DataQualityState(
        fresh_event_ratio=0.85 if not degraded else 0.05,
        source_success_rate=1.0 if not degraded else 0.25,
        lag_seconds_p95=12.5,
        volume_zscore=0.3,
        degraded_flag=degraded,
        degraded_reasons=[] if not degraded else ["fresh_event_ratio=0.05<0.10"],
        cycle_started_at=datetime.now(UTC) - timedelta(seconds=12),
        cycle_finished_at=datetime.now(UTC),
        source_results={"fed_rss": True, "reuters": True},
    )


def _make_store_with_mock(mock_client: MagicMock) -> DataQualityStore:
    """_get_client을 stubbing한 DataQualityStore 반환"""
    store = DataQualityStore(supabase_url="https://x.supabase.co", supabase_key="k")
    store._client = mock_client  # lazy-init 우회
    return store


# ── tests ────────────────────────────────────────────────────────


def test_save_dq_state_success_returns_id() -> None:
    """1. 정상 INSERT 시 row id 반환"""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.data = [{"id": 42, "degraded_flag": False}]
    mock_client.table.return_value.insert.return_value.execute.return_value = mock_response

    store = _make_store_with_mock(mock_client)
    result = store.save_dq_state(_make_state())

    assert result == 42
    mock_client.table.assert_called_once_with("ia_data_quality_state")


def test_save_dq_state_empty_response_returns_none() -> None:
    """2. response.data가 빈 경우 None 반환"""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.data = []
    mock_client.table.return_value.insert.return_value.execute.return_value = mock_response

    store = _make_store_with_mock(mock_client)
    result = store.save_dq_state(_make_state())

    assert result is None


def test_save_dq_state_missing_id_returns_none() -> None:
    """3. INSERT 성공했으나 id 필드 없으면 None 반환"""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.data = [{"degraded_flag": False}]  # id 누락
    mock_client.table.return_value.insert.return_value.execute.return_value = mock_response

    store = _make_store_with_mock(mock_client)
    result = store.save_dq_state(_make_state())

    assert result is None


def test_save_dq_state_supabase_exception_returns_none() -> None:
    """4. Supabase 호출 중 예외 발생 시 raise 아닌 None 반환 (파이프라인 보호)"""
    mock_client = MagicMock()
    mock_client.table.return_value.insert.return_value.execute.side_effect = (
        RuntimeError("Supabase 503")
    )

    store = _make_store_with_mock(mock_client)
    # 예외가 raise되지 않아야 함
    result = store.save_dq_state(_make_state())

    assert result is None


def test_save_dq_state_cycle_missing_returns_none_without_client_call() -> None:
    """5. cycle_started_at 또는 cycle_finished_at 누락 시 클라이언트 호출 없이 None"""
    mock_client = MagicMock()
    store = _make_store_with_mock(mock_client)

    # cycle_started_at = None
    bad_state = DataQualityState(
        fresh_event_ratio=1.0, source_success_rate=1.0, lag_seconds_p95=10.0,
        volume_zscore=None, degraded_flag=False, degraded_reasons=[],
        cycle_started_at=None,  # 누락
        cycle_finished_at=datetime.now(UTC),
    )
    result = store.save_dq_state(bad_state)

    assert result is None
    # client.table()이 호출되지 않았어야 함
    mock_client.table.assert_not_called()
