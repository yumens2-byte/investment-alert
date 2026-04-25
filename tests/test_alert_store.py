"""
제목: AlertStore 단위 테스트
내용: Supabase 연동 없이 Mock 기반으로 save_alert, update_publish_result,
      is_cooldown_active, set_cooldown을 테스트합니다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from db.alert_store import COOLDOWN_MINUTES, AlertStore


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
