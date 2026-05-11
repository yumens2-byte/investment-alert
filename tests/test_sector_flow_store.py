"""
제목: SectorFlowStore 단위 테스트
내용: Supabase 클라이언트는 Mock. upsert/fetch 호출 시그니처 + 파라미터 검증.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from db.sector_flow_store import TABLE_SECTOR_FLOW, SectorFlowStore


def _make_store_with_mock() -> tuple[SectorFlowStore, MagicMock]:
    """제목: Mock client 주입된 Store 생성"""
    store = SectorFlowStore(supabase_url="https://test.supabase.co", supabase_key="key")
    mock_client = MagicMock()
    store._client = mock_client  # type: ignore[assignment]
    return store, mock_client


def test_upsert_daily_rows_normal() -> None:
    """제목: 6 ticker 모두 등락률 있음 — 6 row upsert"""
    store, mock_client = _make_store_with_mock()
    mock_client.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])

    ok = store.upsert_daily_rows(
        snapshot_date="2026-05-09",
        market="US",
        ticker_chg_map={
            "XLV": -0.45, "XLU": 0.30, "XLP": -0.10,
            "XLI": 0.55, "XLRE": 0.20, "XLB": 0.70,
        },
    )

    assert ok is True
    mock_client.table.assert_called_with(TABLE_SECTOR_FLOW)
    call_kwargs = mock_client.table.return_value.upsert.call_args
    rows = call_kwargs.args[0]
    assert len(rows) == 6
    # sector_group 매핑 확인
    xlv_row = next(r for r in rows if r["ticker"] == "XLV")
    assert xlv_row["sector_group"] == "defensive"
    xli_row = next(r for r in rows if r["ticker"] == "XLI")
    assert xli_row["sector_group"] == "cyclical"


def test_upsert_daily_rows_with_none() -> None:
    """제목: 일부 None 등락률 — 그대로 upsert (DB에서 NULL 처리)"""
    store, mock_client = _make_store_with_mock()
    mock_client.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])

    ok = store.upsert_daily_rows(
        snapshot_date="2026-05-09",
        market="US",
        ticker_chg_map={
            "XLV": None, "XLU": 0.30, "XLP": None,
            "XLI": 0.55, "XLRE": 0.20, "XLB": 0.70,
        },
    )

    assert ok is True
    rows = mock_client.table.return_value.upsert.call_args.args[0]
    assert len(rows) == 6  # None도 row로 적재
    xlv_row = next(r for r in rows if r["ticker"] == "XLV")
    assert xlv_row["chg_pct"] is None


def test_upsert_unknown_ticker_skipped() -> None:
    """제목: 그룹 미정의 ticker — 스킵, 나머지만 upsert"""
    store, mock_client = _make_store_with_mock()
    mock_client.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])

    ok = store.upsert_daily_rows(
        snapshot_date="2026-05-09",
        market="US",
        ticker_chg_map={
            "XLV": 0.1, "XLU": 0.2, "UNKNOWN_TICKER": 0.5,
        },
    )

    assert ok is True
    rows = mock_client.table.return_value.upsert.call_args.args[0]
    assert len(rows) == 2  # UNKNOWN_TICKER 스킵
    assert all(r["ticker"] in ("XLV", "XLU") for r in rows)


def test_upsert_empty_dict_returns_false() -> None:
    """제목: 빈 dict — False 반환 (적재 0건 명시)"""
    store, mock_client = _make_store_with_mock()
    ok = store.upsert_daily_rows("2026-05-09", "US", {})
    assert ok is False


def test_upsert_exception_returns_false() -> None:
    """제목: Supabase 예외 발생 — False 반환 (raise 안 함)"""
    store = SectorFlowStore(supabase_url="https://test.supabase.co", supabase_key="key")
    mock_client = MagicMock()
    mock_client.table.side_effect = Exception("connection refused")
    store._client = mock_client  # type: ignore[assignment]

    ok = store.upsert_daily_rows(
        snapshot_date="2026-05-09",
        market="US",
        ticker_chg_map={"XLV": 0.1},
    )
    assert ok is False


def test_fetch_latest_n_days_normal() -> None:
    """제목: 정상 조회 — list[dict] 반환"""
    store, mock_client = _make_store_with_mock()
    expected_data = [
        {"snapshot_date": "2026-05-09", "market": "US", "ticker": "XLV",
         "sector_group": "defensive", "chg_pct": -0.45},
    ]
    (
        mock_client.table.return_value.select.return_value.eq.return_value
        .order.return_value.limit.return_value.execute
    ).return_value = MagicMock(data=expected_data)

    data = store.fetch_latest_n_days(n=5)
    assert data == expected_data


def test_fetch_latest_exception_returns_empty() -> None:
    """제목: 조회 실패 — 빈 리스트 (raise 안 함)"""
    store = SectorFlowStore(supabase_url="https://test.supabase.co", supabase_key="key")
    mock_client = MagicMock()
    mock_client.table.side_effect = Exception("timeout")
    store._client = mock_client  # type: ignore[assignment]

    data = store.fetch_latest_n_days(n=5)
    assert data == []


def test_get_client_raises_without_env() -> None:
    """제목: SUPABASE_URL/KEY 미설정 시 RuntimeError"""
    store = SectorFlowStore(supabase_url="", supabase_key="")
    try:
        store._get_client()
        raise AssertionError("RuntimeError 미발생")
    except RuntimeError:
        pass
