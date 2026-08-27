"""
제목: SentimentCollector 단위 테스트 (mock 기반)
내용: Yahoo/CBOE/FRED/alternative.me 파싱 로직 및 실패 격리 검증.
      네트워크 호출은 전부 mock — 통합 테스트는 dry_run 실행으로 대체.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from collectors.sentiment_collector import SentimentCollector

# 실측 검증된 CBOE CSV 포맷 (2026-08 — 헤더/공백 혼재 재현)
_CBOE_CSV = (
    "Cboe Volume and Put/Call Ratio data disclaimer,,,,\n"
    ", PRODUCT: EQUITY,,EXCHANGE: Cboe,\n"
    "DATE,CALL,PUT,TOTAL,P/C Ratio\n"
    "11/1/2006,976510,623929,1600439,0.64\n"
    "08/25/2026, 941270, 713020, 1654290, 0.76\n"
    "08/26/2026, 800800, 648648, 1449448, 0.81\n"
)


def _mock_resp(json_data=None, text: str = "") -> MagicMock:
    """제목: requests 응답 mock 생성기"""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = json_data
    resp.text = text
    return resp


def _yahoo_chart_json(ts_close_pairs: list[tuple[int, float | None]]) -> dict:
    """제목: Yahoo v8 chart 응답 mock 생성기"""
    return {
        "chart": {
            "result": [
                {
                    "timestamp": [ts for ts, _ in ts_close_pairs],
                    "indicators": {
                        "quote": [{"close": [c for _, c in ts_close_pairs]}]
                    },
                }
            ]
        }
    }


class TestCboePcr:
    """제목: CBOE PCR CSV 파싱"""

    @pytest.mark.unit
    def test_parses_last_valid_row(self):
        """제목: 마지막 유효 행 채택 + 공백 strip + 날짜 ISO 변환"""
        collector = SentimentCollector(fred_api_key="dummy")
        with patch(
            "collectors.sentiment_collector.requests.get",
            return_value=_mock_resp(text=_CBOE_CSV),
        ):
            result = collector.collect_pcr()
        assert result is not None
        assert result["value"] == pytest.approx(0.81)
        assert result["date"] == "2026-08-26"

    @pytest.mark.unit
    def test_fallback_to_second_url(self):
        """제목: archive 실패 시 current CSV fallback"""
        collector = SentimentCollector(fred_api_key="dummy")
        fail = MagicMock()
        fail.raise_for_status.side_effect = RuntimeError("404")
        ok = _mock_resp(text=_CBOE_CSV)
        with patch(
            "collectors.sentiment_collector.requests.get", side_effect=[fail, ok]
        ):
            result = collector.collect_pcr()
        assert result is not None
        assert result["value"] == pytest.approx(0.81)

    @pytest.mark.unit
    def test_all_sources_fail_returns_none(self):
        """제목: 전 소스 실패 시 None (예외 미전파)"""
        collector = SentimentCollector(fred_api_key="dummy")
        with patch(
            "collectors.sentiment_collector.requests.get",
            side_effect=RuntimeError("network down"),
        ):
            assert collector.collect_pcr() is None

    @pytest.mark.unit
    def test_no_valid_row_returns_none(self):
        """제목: 유효 데이터 행 부재 시 None"""
        collector = SentimentCollector(fred_api_key="dummy")
        with patch(
            "collectors.sentiment_collector.requests.get",
            return_value=_mock_resp(text="DATE,CALL,PUT,TOTAL,P/C Ratio\n"),
        ):
            assert collector.collect_pcr() is None

    @pytest.mark.unit
    def test_us_date_parser(self):
        """제목: MM/DD/YYYY 파서 경계"""
        assert SentimentCollector._parse_us_date("08/26/2026") == "2026-08-26"
        assert SentimentCollector._parse_us_date(" 1/3/2007 ") == "2007-01-03"
        assert SentimentCollector._parse_us_date("not-a-date") is None
        assert SentimentCollector._parse_us_date("2026-08-26") is None


class TestFredHyOas:
    """제목: FRED HY OAS 수집"""

    @pytest.mark.unit
    def test_percent_to_bp_and_skips_dot(self):
        """제목: % → bp 환산 + FRED 결측('.') 스킵"""
        collector = SentimentCollector(fred_api_key="dummy")
        payload = {
            "observations": [
                {"date": "2026-08-26", "value": "."},
                {"date": "2026-08-25", "value": "3.18"},
            ]
        }
        with patch(
            "collectors.sentiment_collector.requests.get",
            return_value=_mock_resp(json_data=payload),
        ):
            result = collector.collect_hy_oas()
        assert result is not None
        assert result["value"] == pytest.approx(318.0)
        assert result["date"] == "2026-08-25"

    @pytest.mark.unit
    def test_missing_api_key_returns_none(self):
        """제목: FRED_API_KEY 미설정 시 None (경고 후 격리)"""
        with patch.dict("os.environ", {"FRED_API_KEY": ""}, clear=False):
            collector = SentimentCollector(fred_api_key="")
            assert collector.collect_hy_oas() is None

    @pytest.mark.unit
    def test_http_error_returns_none(self):
        """제목: HTTP 오류 격리"""
        collector = SentimentCollector(fred_api_key="dummy")
        with patch(
            "collectors.sentiment_collector.requests.get",
            side_effect=RuntimeError("5xx"),
        ):
            assert collector.collect_hy_oas() is None


class TestCryptoFg:
    """제목: alternative.me F&G 수집"""

    @pytest.mark.unit
    def test_parses_value_and_date(self):
        """제목: value/timestamp 파싱"""
        collector = SentimentCollector(fred_api_key="dummy")
        payload = {"data": [{"value": "38", "timestamp": "1756166400"}]}
        with patch(
            "collectors.sentiment_collector.requests.get",
            return_value=_mock_resp(json_data=payload),
        ):
            result = collector.collect_crypto_fg()
        assert result is not None
        assert result["value"] == pytest.approx(38.0)
        assert result["date"] == "2025-08-26"

    @pytest.mark.unit
    def test_empty_data_returns_none(self):
        """제목: data 빈 배열 → None"""
        collector = SentimentCollector(fred_api_key="dummy")
        with patch(
            "collectors.sentiment_collector.requests.get",
            return_value=_mock_resp(json_data={"data": []}),
        ):
            assert collector.collect_crypto_fg() is None


class TestYahooSeries:
    """제목: Yahoo 종가 시계열 / VIX 비율 / Breadth"""

    @pytest.mark.unit
    def test_series_skips_none_close(self):
        """제목: close None 항목 스킵"""
        collector = SentimentCollector(fred_api_key="dummy")
        payload = _yahoo_chart_json(
            [(1756166400, 100.0), (1756252800, None), (1756339200, 102.0)]
        )
        with patch(
            "collectors.sentiment_collector.requests.get",
            return_value=_mock_resp(json_data=payload),
        ):
            rows = collector._fetch_series_via_requests("SPY", "10d")
        assert [r["close"] for r in rows] == [100.0, 102.0]

    @pytest.mark.unit
    def test_vix_ratio_computation(self):
        """제목: VIX/VIX3M 비율 계산"""
        collector = SentimentCollector(fred_api_key="dummy")

        def _series(ticker: str, chart_range: str) -> list[dict]:
            if ticker == "^VIX":
                return [{"date": "2026-08-26", "close": 18.8}]
            return [{"date": "2026-08-26", "close": 20.0}]

        with patch.object(collector, "_fetch_close_series", side_effect=_series):
            result = collector.collect_vix_ratio()
        assert result is not None
        assert result["value"] == pytest.approx(0.94)
        assert result["vix"] == pytest.approx(18.8)
        assert result["vix3m"] == pytest.approx(20.0)

    @pytest.mark.unit
    def test_vix_ratio_none_on_partial_failure(self):
        """제목: VIX3M 실패 시 비율 None"""
        collector = SentimentCollector(fred_api_key="dummy")

        def _series(ticker: str, chart_range: str) -> list[dict]:
            if ticker == "^VIX":
                return [{"date": "2026-08-26", "close": 18.8}]
            return []

        with patch.object(collector, "_fetch_close_series", side_effect=_series):
            assert collector.collect_vix_ratio() is None

    @pytest.mark.unit
    def test_breadth_relative_return(self):
        """제목: RSP−SPY 20영업일 상대수익률 계산"""
        collector = SentimentCollector(fred_api_key="dummy")

        def _make(base: float, last: float) -> list[dict]:
            # 21개 시계열: [0]=base ... [20]=last
            rows = [{"date": f"d{i}", "close": base} for i in range(20)]
            rows.append({"date": "2026-08-26", "close": last})
            return rows

        def _series(ticker: str, chart_range: str) -> list[dict]:
            if ticker == "RSP":
                return _make(100.0, 102.0)  # +2.0%
            return _make(100.0, 103.0)  # SPY +3.0%

        with patch.object(collector, "_fetch_close_series", side_effect=_series):
            result = collector.collect_breadth()
        assert result is not None
        assert result["value"] == pytest.approx(-1.0, abs=0.001)

    @pytest.mark.unit
    def test_breadth_insufficient_series(self):
        """제목: 시계열 부족 시 None"""
        collector = SentimentCollector(fred_api_key="dummy")
        with patch.object(collector, "_fetch_close_series", return_value=[]):
            assert collector.collect_breadth() is None


class TestYfinanceFallback:
    """제목: yfinance 2순위 fallback 경로"""

    @pytest.mark.unit
    def test_fallback_invoked_when_requests_empty(self):
        """제목: requests 빈 결과 → yfinance fallback 호출 및 파싱"""
        collector = SentimentCollector(fred_api_key="dummy")

        class _FakeIdx:
            def strftime(self, fmt: str) -> str:
                return "2026-08-26"

        class _FakeHist:
            def iterrows(self):
                yield _FakeIdx(), {"Close": 100.5}
                yield _FakeIdx(), {"Close": None}  # None 스킵 검증

        fake_yf = MagicMock()
        fake_yf.Ticker.return_value.history.return_value = _FakeHist()

        with (
            patch.object(collector, "_fetch_series_via_requests", return_value=[]),
            patch.dict("sys.modules", {"yfinance": fake_yf}),
        ):
            rows = collector._fetch_close_series("SPY", "10d")
        assert rows == [{"date": "2026-08-26", "close": 100.5}]
        fake_yf.Ticker.assert_called_once_with("SPY")

    @pytest.mark.unit
    def test_fallback_failure_returns_empty(self):
        """제목: yfinance 예외 시 빈 리스트 (격리)"""
        collector = SentimentCollector(fred_api_key="dummy")
        fake_yf = MagicMock()
        fake_yf.Ticker.side_effect = RuntimeError("yf down")
        with (
            patch.object(collector, "_fetch_series_via_requests", return_value=[]),
            patch.dict("sys.modules", {"yfinance": fake_yf}),
        ):
            assert collector._fetch_close_series("SPY", "10d") == []

    @pytest.mark.unit
    def test_fetch_latest_close_returns_last(self):
        """제목: 최신 종가 = 시계열 마지막 요소"""
        collector = SentimentCollector(fred_api_key="dummy")
        series = [
            {"date": "2026-08-25", "close": 99.0},
            {"date": "2026-08-26", "close": 101.0},
        ]
        with patch.object(collector, "_fetch_close_series", return_value=series):
            latest = collector._fetch_latest_close("^VIX")
        assert latest == {"date": "2026-08-26", "close": 101.0}

    @pytest.mark.unit
    def test_run_with_timeout_raises_on_timeout(self):
        """제목: signal timeout — 초과 시 예외 전파 (무한 대기 차단)"""
        import time as _t

        from collectors.sentiment_collector import _run_with_timeout

        with pytest.raises(Exception, match="timeout"):
            _run_with_timeout(lambda: _t.sleep(3), timeout_sec=1)


class TestCollectAll:
    """제목: collect_all 오케스트레이션"""

    @pytest.mark.unit
    def test_all_keys_present_with_isolation(self):
        """제목: 일부 실패해도 5개 키 전부 반환 (실패 키 None)"""
        collector = SentimentCollector(fred_api_key="dummy")
        with (
            patch.object(
                collector,
                "collect_vix_ratio",
                return_value={"value": 0.9, "date": "2026-08-26"},
            ),
            patch.object(collector, "collect_pcr", side_effect=RuntimeError("boom")),
            patch.object(collector, "collect_hy_oas", return_value=None),
            patch.object(
                collector,
                "collect_breadth",
                return_value={"value": 0.5, "date": "2026-08-26"},
            ),
            patch.object(
                collector,
                "collect_crypto_fg",
                return_value={"value": 50.0, "date": "2026-08-26"},
            ),
        ):
            result = collector.collect_all()
        assert set(result.keys()) == {"vix_ratio", "pcr", "hy_oas", "breadth", "crypto_fg"}
        assert result["pcr"] is None  # 예외 → 격리
        assert result["hy_oas"] is None
        assert result["vix_ratio"]["value"] == 0.9
