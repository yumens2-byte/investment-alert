"""
제목: SectorCollector 단위 테스트 (v1.1.0 — requests + yfinance fallback)
내용: v1.1.0의 핵심 — requests 1순위 + yfinance 2순위 fallback 메커니즘 검증.
      모든 외부 호출은 Mock으로 대체.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from collectors.sector_collector import SectorCollector


def _make_yahoo_response(timestamps: list[int], closes: list) -> dict:
    """Yahoo v8/chart 응답 구조 모방."""
    return {
        "chart": {
            "result": [{
                "timestamp": timestamps,
                "indicators": {
                    "quote": [{"close": closes}],
                },
            }],
        },
    }


# ─────────────────────────────────────────────────────────
# _parse_chart 단위 테스트 (기존 v1.0.0과 동일 — 정상 동작 보존 확인)
# ─────────────────────────────────────────────────────────

def test_parse_chart_normal() -> None:
    """제목: 정상 응답 파싱 — 5일 등락률 계산"""
    timestamps = [1746000000 + i * 86400 for i in range(6)]
    closes = [100.0, 101.0, 99.0, 102.0, 100.0, 103.0]
    rows = SectorCollector._parse_chart(_make_yahoo_response(timestamps, closes))
    assert len(rows) == 5
    assert rows[0]["date"] > rows[-1]["date"]
    assert rows[0]["chg_pct"] == 3.0


def test_parse_chart_with_none_closes() -> None:
    """제목: 응답에 None 종가 포함 — 제외 후 계산"""
    timestamps = [1746000000 + i * 86400 for i in range(5)]
    closes = [100.0, None, 102.0, None, 104.0]
    rows = SectorCollector._parse_chart(_make_yahoo_response(timestamps, closes))
    assert len(rows) == 2


def test_parse_chart_insufficient_data() -> None:
    """제목: 종가 1개만 — 등락률 계산 불가, 빈 리스트"""
    rows = SectorCollector._parse_chart(_make_yahoo_response([1746000000], [100.0]))
    assert rows == []


def test_parse_chart_malformed_response() -> None:
    """제목: 응답 구조 깨짐 — KeyError 회피, 빈 리스트"""
    assert SectorCollector._parse_chart({"chart": {"error": "blah"}}) == []
    assert SectorCollector._parse_chart({}) == []


def test_parse_chart_zero_prev_close() -> None:
    """제목: 이전 종가 0 — DivisionByZero 회피, 해당 row 스킵"""
    timestamps = [1746000000 + i * 86400 for i in range(3)]
    closes = [0.0, 100.0, 105.0]
    rows = SectorCollector._parse_chart(_make_yahoo_response(timestamps, closes))
    assert len(rows) == 1
    assert rows[0]["chg_pct"] == 5.0


# ─────────────────────────────────────────────────────────
# v1.1.0 신규: requests 1순위 동작 확인
# ─────────────────────────────────────────────────────────

def test_fetch_chart_requests_success_skips_yfinance() -> None:
    """제목: requests 성공 시 yfinance fallback 호출 안 함"""
    collector = SectorCollector()
    timestamps = [1746000000 + i * 86400 for i in range(6)]
    closes = [100.0 + i for i in range(6)]
    ok_response = _make_yahoo_response(timestamps, closes)

    with patch("collectors.sector_collector.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = ok_response
        mock_get.return_value = mock_resp

        # yfinance import 안 되도록 강제 — 호출되면 ImportError 발생할 것
        with patch.object(collector, "_fetch_via_yfinance") as mock_yf:
            rows = collector._fetch_chart("XLV")

        # requests 결과로 5건 반환
        assert len(rows) == 5
        # yfinance fallback 호출 안 됨
        mock_yf.assert_not_called()


def test_fetch_chart_requests_429_falls_back_to_yfinance() -> None:
    """제목: requests 429 → yfinance fallback 호출됨 (핵심 시나리오)"""
    collector = SectorCollector()

    yfinance_result = [{"date": "2026-05-09", "chg_pct": 0.45}]

    with patch("collectors.sector_collector.requests.get") as mock_get:
        # 429 응답
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_get.return_value = mock_resp

        with patch.object(
            collector, "_fetch_via_yfinance", return_value=yfinance_result
        ) as mock_yf:
            rows = collector._fetch_chart("XLV")

        # yfinance fallback 결과 반환
        assert rows == yfinance_result
        mock_yf.assert_called_once_with("XLV")


def test_fetch_chart_both_fail_returns_empty() -> None:
    """제목: requests + yfinance 모두 실패 → 빈 리스트"""
    collector = SectorCollector()

    with patch("collectors.sector_collector.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_get.return_value = mock_resp

        with patch.object(collector, "_fetch_via_yfinance", return_value=[]):
            rows = collector._fetch_chart("XLV")

        assert rows == []


def test_fetch_via_requests_exception_returns_empty() -> None:
    """제목: requests 자체 예외 (network error) — 빈 리스트, raise 안 함"""
    collector = SectorCollector()

    with patch(
        "collectors.sector_collector.requests.get",
        side_effect=Exception("connection refused"),
    ):
        rows = collector._fetch_via_requests("XLV")

    assert rows == []


# ─────────────────────────────────────────────────────────
# collect_sector_changes — 통합 동작
# ─────────────────────────────────────────────────────────

def test_collect_sector_changes_all_success() -> None:
    """제목: 6 ticker 모두 정상 — 6개 키 반환"""
    collector = SectorCollector()

    timestamps = [1746000000 + i * 86400 for i in range(3)]
    closes = [100.0, 101.0, 102.0]
    ok_response = _make_yahoo_response(timestamps, closes)

    with patch("collectors.sector_collector.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = ok_response
        mock_get.return_value = mock_resp

        result = collector.collect_sector_changes()

    assert len(result) == 6
    for _ticker, rows in result.items():
        assert isinstance(rows, list)
        assert len(rows) >= 1


def test_collect_sector_changes_429_with_yfinance_recovery() -> None:
    """제목: requests 6/6 모두 429 → yfinance fallback이 모두 성공 → 6/6 정상"""
    collector = SectorCollector()

    yfinance_result = [{"date": "2026-05-09", "chg_pct": 0.25}]

    with patch("collectors.sector_collector.requests.get") as mock_get:
        # 모든 requests 호출은 429
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_get.return_value = mock_resp

        with patch.object(
            collector, "_fetch_via_yfinance", return_value=yfinance_result
        ):
            result = collector.collect_sector_changes()

    # 6/6 모두 yfinance fallback으로 복구
    assert len(result) == 6
    for _ticker, rows in result.items():
        assert rows == yfinance_result


def test_collect_sector_changes_total_failure_isolated() -> None:
    """제목: requests + yfinance 모두 실패해도 raise 없이 빈 리스트로 격리"""
    collector = SectorCollector()

    with patch("collectors.sector_collector.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_get.return_value = mock_resp

        with patch.object(collector, "_fetch_via_yfinance", return_value=[]):
            result = collector.collect_sector_changes()

    assert len(result) == 6
    for _ticker, rows in result.items():
        assert rows == []
