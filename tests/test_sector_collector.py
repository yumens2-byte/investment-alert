"""
제목: SectorCollector 단위 테스트
내용: Yahoo Finance API 응답 파싱, 등락률 계산, 개별 ticker 실패 격리.
      모든 HTTP 호출은 Mock으로 대체 (외부 의존 없음).
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


def test_parse_chart_normal() -> None:
    """제목: 정상 응답 파싱 — 5일 등락률 계산"""
    # 6개 종가 → 5건 등락률 (인접 비교)
    timestamps = [1746000000 + i * 86400 for i in range(6)]
    closes = [100.0, 101.0, 99.0, 102.0, 100.0, 103.0]

    rows = SectorCollector._parse_chart(_make_yahoo_response(timestamps, closes))

    assert len(rows) == 5
    # 최신순 정렬 확인
    assert rows[0]["date"] > rows[-1]["date"]
    # 첫 번째 등락률: (103-100)/100 * 100 = 3.0
    assert rows[0]["chg_pct"] == 3.0


def test_parse_chart_with_none_closes() -> None:
    """제목: 응답에 None 종가 포함 — 제외 후 계산"""
    timestamps = [1746000000 + i * 86400 for i in range(5)]
    closes = [100.0, None, 102.0, None, 104.0]

    rows = SectorCollector._parse_chart(_make_yahoo_response(timestamps, closes))

    # None 제외 후 3개 종가 → 2건 등락률
    assert len(rows) == 2


def test_parse_chart_insufficient_data() -> None:
    """제목: 종가 1개만 — 등락률 계산 불가, 빈 리스트"""
    rows = SectorCollector._parse_chart(
        _make_yahoo_response([1746000000], [100.0])
    )
    assert rows == []


def test_parse_chart_malformed_response() -> None:
    """제목: 응답 구조 깨짐 — KeyError 회피, 빈 리스트"""
    bad_data = {"chart": {"error": "blah"}}
    rows = SectorCollector._parse_chart(bad_data)
    assert rows == []


def test_parse_chart_zero_prev_close() -> None:
    """제목: 이전 종가 0 — DivisionByZero 회피, 해당 row 스킵"""
    timestamps = [1746000000 + i * 86400 for i in range(3)]
    closes = [0.0, 100.0, 105.0]

    rows = SectorCollector._parse_chart(_make_yahoo_response(timestamps, closes))

    # 0.0 → 100.0은 스킵, 100.0 → 105.0만 계산
    assert len(rows) == 1
    assert rows[0]["chg_pct"] == 5.0


def test_collect_sector_changes_all_success() -> None:
    """제목: 6 ticker 모두 정상 — 6개 키 반환"""
    collector = SectorCollector()

    timestamps = [1746000000 + i * 86400 for i in range(3)]
    closes = [100.0, 101.0, 102.0]
    mock_response = _make_yahoo_response(timestamps, closes)

    with patch("collectors.sector_collector.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_response
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = collector.collect_sector_changes()

    assert len(result) == 6
    for _ticker, rows in result.items():
        assert isinstance(rows, list)
        assert len(rows) >= 1


def test_collect_sector_changes_partial_failure() -> None:
    """제목: 일부 ticker 실패 — 나머지는 계속 진행 (격리)"""
    collector = SectorCollector(max_retries=0)  # 빠른 실패용

    timestamps = [1746000000 + i * 86400 for i in range(3)]
    closes = [100.0, 101.0, 102.0]
    ok_response = _make_yahoo_response(timestamps, closes)

    call_count = {"n": 0}

    def _side_effect(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] <= 2:
            # 첫 2회는 실패
            raise Exception("simulated 500")
        mock_resp = MagicMock()
        mock_resp.json.return_value = ok_response
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    with patch("collectors.sector_collector.requests.get", side_effect=_side_effect):
        result = collector.collect_sector_changes()

    # 6 ticker 모두 키 존재. 처음 2개는 빈 리스트, 나머지는 데이터.
    assert len(result) == 6
    empty_count = sum(1 for v in result.values() if not v)
    assert empty_count == 2
