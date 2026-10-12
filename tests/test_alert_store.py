"""
제목: AlertStore 단위 테스트
내용: Supabase 연동 없이 Mock 기반으로 save_alert, update_publish_result,
      is_cooldown_active, set_cooldown을 테스트합니다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from db.alert_store import (
    COOLDOWN_MINUTES,
    TABLE_ALERT_RUN,
    TABLE_ALERT_WINDOW_CLAIM,
    TABLE_DUPLICATE_DECISION_LOG,
    TABLE_TOPIC_STATE,
    TABLE_X_PUBLISH_FINGERPRINT,
    AlertStore,
)


@pytest.fixture
def store() -> AlertStore:
    return AlertStore(supabase_url="https://test.supabase.co", supabase_key="testkey")


def _make_mock_client() -> MagicMock:
    """Supabase 클라이언트 mock"""
    client = MagicMock()
    # 체이닝 메서드 지원
    client.table.return_value = client
    client.upsert.return_value = client
    client.update.return_value = client
    client.select.return_value = client
    client.eq.return_value = client
    client.gte.return_value = client
    client.order.return_value = client
    client.limit.return_value = client
    client.insert.return_value = client
    client.delete.return_value = client
    client.execute.return_value = MagicMock(data=[])
    return client


# ── save_alert ────────────────────────────────────────
@pytest.mark.unit
def test_save_alert_success(store: AlertStore) -> None:
    """save_alert 성공 시 True 반환"""
    store._client = _make_mock_client()
    result = store.save_alert(
        alert_id="uuid-1234",
        level="L2",
        score=5.5,
        health_score=0.85,
        reasoning="L2 판정",
        top_news=[{"title": "News", "source": "reuters"}],
        top_youtube=[],
    )
    assert result is True


@pytest.mark.unit
def test_save_alert_failure_returns_false(store: AlertStore) -> None:
    """save_alert 실패 시 False 반환 (예외 발생 시)"""
    mock_client = _make_mock_client()
    mock_client.execute.side_effect = RuntimeError("DB Error")
    store._client = mock_client
    result = store.save_alert("uuid", "L2", 5.0, 0.8, "근거", [], [])
    assert result is False


# ── update_publish_result ─────────────────────────────
@pytest.mark.unit
def test_update_publish_result_success(store: AlertStore) -> None:
    """update_publish_result 성공 시 True 반환"""
    store._client = _make_mock_client()
    result = store.update_publish_result(
        alert_id="uuid-1234",
        x_published=True,
        tg_free_published=True,
        tg_paid_published=False,
        x_error=None,
    )
    assert result is True


# ── is_cooldown_active ────────────────────────────────
@pytest.mark.unit
def test_cooldown_active_when_future(store: AlertStore) -> None:
    """cooldown_until이 미래이면 True"""
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    mock_client = _make_mock_client()
    mock_client.execute.return_value = MagicMock(data=[{"cooldown_until": future}])
    store._client = mock_client
    assert store.is_cooldown_active("L2") is True


@pytest.mark.unit
def test_cooldown_inactive_when_past(store: AlertStore) -> None:
    """cooldown_until이 과거이면 False"""
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    mock_client = _make_mock_client()
    mock_client.execute.return_value = MagicMock(data=[{"cooldown_until": past}])
    store._client = mock_client
    assert store.is_cooldown_active("L2") is False


@pytest.mark.unit
def test_cooldown_inactive_when_no_rows(store: AlertStore) -> None:
    """레코드 없으면 False"""
    store._client = _make_mock_client()
    assert store.is_cooldown_active("L1") is False


@pytest.mark.unit
def test_cooldown_query_failure_returns_false(store: AlertStore) -> None:
    """Supabase 오류 시 False 반환 (안전 처리)"""
    mock_client = _make_mock_client()
    mock_client.execute.side_effect = RuntimeError("DB Error")
    store._client = mock_client
    assert store.is_cooldown_active("L1") is False


# ── set_cooldown ──────────────────────────────────────
@pytest.mark.unit
def test_set_cooldown_success(store: AlertStore) -> None:
    """set_cooldown 성공 시 True 반환"""
    store._client = _make_mock_client()
    result = store.set_cooldown(level="L2", alert_id="uuid-1234")
    assert result is True


@pytest.mark.unit
def test_cooldown_minutes_defined() -> None:
    """L1/L2/L3 모두 쿨다운 분 정의됨"""
    for level in ("L1", "L2", "L3"):
        assert level in COOLDOWN_MINUTES
        assert COOLDOWN_MINUTES[level] > 0


# ── get_client lazy init ──────────────────────────────
@pytest.mark.unit
def test_get_client_raises_when_no_credentials() -> None:
    """URL/KEY 미설정 시 RuntimeError"""
    store = AlertStore(supabase_url="", supabase_key="")
    with pytest.raises(RuntimeError, match="SUPABASE_URL"):
        store._get_client()


# ── v1.2.0: claim_window / release_claim ──────────────
@pytest.mark.unit
def test_claim_window_twice_claimed_then_already(store: AlertStore) -> None:
    """동일 claim_key 2회: 1회차 행 반환 → claimed, 2회차 빈 배열 → already_claimed"""
    client = _make_mock_client()
    client.execute.side_effect = [
        MagicMock(data=[{"claim_key": "2026-10-13:09:30"}]),
        MagicMock(data=[]),
    ]
    store._client = client
    assert store.claim_window("2026-10-13:09:30", "a-1", "123") == "claimed"
    assert store.claim_window("2026-10-13:09:30", "a-2", "456") == "already_claimed"
    client.table.assert_called_with(TABLE_ALERT_WINDOW_CLAIM)
    args, kwargs = client.upsert.call_args
    assert args[0]["claim_key"] == "2026-10-13:09:30"
    assert args[0]["gh_run_id"] == "456"
    assert kwargs == {"on_conflict": "claim_key", "ignore_duplicates": True}


@pytest.mark.unit
def test_claim_window_client_exception_returns_error(store: AlertStore) -> None:
    """client 예외 시 error 반환, 예외 전파 없음"""
    client = _make_mock_client()
    client.execute.side_effect = RuntimeError("DB down")
    store._client = client
    assert store.claim_window("k", "a", "r") == "error"


@pytest.mark.unit
def test_release_claim_filters_by_key_and_run_id(store: AlertStore) -> None:
    """release_claim: delete 후 eq(claim_key), eq(gh_run_id) 2개 호출"""
    client = _make_mock_client()
    store._client = client
    assert store.release_claim("2026-10-13:09:30", "123") is True
    client.table.assert_called_with(TABLE_ALERT_WINDOW_CLAIM)
    client.delete.assert_called_once()
    eq_calls = [c.args for c in client.eq.call_args_list]
    assert eq_calls == [("claim_key", "2026-10-13:09:30"), ("gh_run_id", "123")]


@pytest.mark.unit
def test_release_claim_exception_returns_false(store: AlertStore) -> None:
    client = _make_mock_client()
    client.execute.side_effect = RuntimeError("DB down")
    store._client = client
    assert store.release_claim("k", "r") is False


# ── v1.2.0: save_run ──────────────────────────────────
@pytest.mark.unit
def test_save_run_success(store: AlertStore) -> None:
    client = _make_mock_client()
    store._client = client
    row = {"outcome": "published", "started_at": "2026-10-12T00:00:00+00:00", "detail": {}}
    assert store.save_run(row) is True
    client.table.assert_called_with(TABLE_ALERT_RUN)
    client.insert.assert_called_once_with(row)


@pytest.mark.unit
def test_save_run_exception_returns_false(store: AlertStore) -> None:
    client = _make_mock_client()
    client.execute.side_effect = RuntimeError("PGRST205")
    store._client = client
    assert store.save_run({"outcome": "error"}) is False


# ── v1.2.0: update_publish_result / save_alert 신규 인자 ─
@pytest.mark.unit
def test_update_publish_result_includes_published_channels(store: AlertStore) -> None:
    client = _make_mock_client()
    store._client = client
    ok = store.update_publish_result(
        "uuid-1", x_published=True, published_channels=["x", "tg_free"]
    )
    assert ok is True
    updates = client.update.call_args.args[0]
    assert updates["published_channels"] == ["x", "tg_free"]


@pytest.mark.unit
def test_update_publish_result_omits_published_channels_when_none(store: AlertStore) -> None:
    client = _make_mock_client()
    store._client = client
    store.update_publish_result("uuid-1", x_published=True)
    assert "published_channels" not in client.update.call_args.args[0]


@pytest.mark.unit
def test_save_alert_includes_source(store: AlertStore) -> None:
    client = _make_mock_client()
    store._client = client
    store.save_alert("uuid", "L2", 0.0, 0.9, "r", [], [], source="sector")
    assert client.upsert.call_args.args[0]["source"] == "sector"


@pytest.mark.unit
def test_save_alert_omits_source_when_none(store: AlertStore) -> None:
    """source=None 이면 payload 미포함 → DB DEFAULT('macro')"""
    client = _make_mock_client()
    store._client = client
    store.save_alert("uuid", "L2", 5.0, 0.9, "r", [], [])
    assert "source" not in client.upsert.call_args.args[0]


# ── v1.2.0: probe_duplicate_guard_tables ──────────────
@pytest.mark.unit
def test_probe_duplicate_guard_tables_ok_returns_none(store: AlertStore) -> None:
    client = _make_mock_client()
    store._client = client
    assert store.probe_duplicate_guard_tables() is None
    tables = [c.args[0] for c in client.table.call_args_list]
    assert tables == [
        TABLE_TOPIC_STATE,
        TABLE_X_PUBLISH_FINGERPRINT,
        TABLE_DUPLICATE_DECISION_LOG,
    ]


@pytest.mark.unit
def test_probe_duplicate_guard_tables_exception_returns_reason(store: AlertStore) -> None:
    client = _make_mock_client()
    client.execute.side_effect = RuntimeError("relation does not exist")
    store._client = client
    reason = store.probe_duplicate_guard_tables()
    assert isinstance(reason, str)
    assert reason.startswith("RuntimeError: relation does not exist")
    assert len(reason) <= 300
