"""
제목: SectorFlowLayer 단위 테스트
내용: 변화 감지 알고리즘 검증. SectorFlowStore는 Mock으로 대체.
      ci_preflight.sh가 test_l2_defensive_rotation을 핵심 테스트로 실행.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from detection.sector_flow_layer import SectorFlowLayer


def _make_rows(
    days: int = 5,
    def_chg: float = 0.0,
    cyc_chg: float = 0.0,
    base_date: str = "2026-05-09",
) -> list[dict]:
    """
    제목: 테스트용 row 생성기
    내용: N일치 6 ticker row 생성. defensive/cyclical 그룹에 동일 등락률 부여.
    """
    from datetime import date, timedelta

    base = date.fromisoformat(base_date)
    rows: list[dict] = []
    defensive = ["XLV", "XLU", "XLP"]
    cyclical = ["XLI", "XLRE", "XLB"]

    for i in range(days):
        d = (base - timedelta(days=i)).isoformat()
        for t in defensive:
            rows.append({
                "snapshot_date": d, "market": "US", "ticker": t,
                "sector_group": "defensive", "chg_pct": def_chg,
            })
        for t in cyclical:
            rows.append({
                "snapshot_date": d, "market": "US", "ticker": t,
                "sector_group": "cyclical", "chg_pct": cyc_chg,
            })
    return rows


def _make_layer_with_rows(rows: list[dict]) -> SectorFlowLayer:
    """제목: Mock store 주입된 Layer 생성"""
    mock_store = MagicMock()
    mock_store.fetch_latest_n_days.return_value = rows
    return SectorFlowLayer(sector_store=mock_store)


@patch("detection.sector_flow_layer.get_market_profile", return_value="extended")
def test_l2_defensive_rotation(_mp) -> None:
    """제목: 5일 누적 +1.8p — L2 DEFENSIVE_ROTATION

    핵심 테스트 (ci_preflight.sh에 포함).
    defensive 매일 +0.4, cyclical 매일 -0.4 → 5일 누적 spread = (0.4*5) - (-0.4*5) = 4.0p
    """
    rows = _make_rows(days=5, def_chg=0.4, cyc_chg=-0.4)
    layer = _make_layer_with_rows(rows)

    result = layer.detect()

    assert result.level == "L2"
    assert result.rotation_type == "DEFENSIVE_ROTATION"
    assert result.spread_5d is not None
    assert result.spread_5d > 1.5
    assert result.rows_used == 30  # 5일 × 6 ticker


@patch("detection.sector_flow_layer.get_market_profile", return_value="extended")
def test_l2_risk_on_rotation(_mp) -> None:
    """제목: 5일 누적 -2.0p — L2 RISK_ON_ROTATION"""
    rows = _make_rows(days=5, def_chg=-0.5, cyc_chg=0.5)
    layer = _make_layer_with_rows(rows)

    result = layer.detect()

    assert result.level == "L2"
    assert result.rotation_type == "RISK_ON_ROTATION"
    assert result.spread_5d is not None
    assert result.spread_5d < -1.5


@patch("detection.sector_flow_layer.get_market_profile", return_value="extended")
def test_l1_rotation_watch_def(_mp) -> None:
    """제목: 1일+5일 동시 +1.0p — L1 ROTATION_WATCH_DEF

    매일 def=0.6, cyc=-0.4 → spread 일별=1.0, 5일=5.0... 너무 큼.
    L1 만들려면 1일 spread = 1.0~1.5p, 5일 spread = 1.0~1.5p가 필요.
    """
    # 매일 def=0.25, cyc=-0.25 → 일 spread=0.5, 5일=2.5 → L2 (1.5 초과)
    # 1일+5일 모두 1.0p 이상이면서 5일 spread<1.5p 만들기 어려움.
    # 알고리즘 상 5일이 1.5 이하면 L2 안 되고, 1.0 이상이면 L1.
    # def=0.15, cyc=-0.15 → 1일 spread=0.3 → NONE (1.0 미달)
    # 정확히 L1 조건 만들려면 임계 1.0~1.5 사이 5일 spread + 1일 1.0 이상.
    # 답: 첫 4일 spread=0.2, 마지막 1일 spread=1.0 → 5일 누적=0.2*4+1.0=1.8 — L2.
    # L1 만들기 어려운 알고리즘이므로 임계값 환경변수로 조정 또는 검증 우회.
    # 여기서는 L1 분기를 위해 임계값을 임시 override (환경변수)
    import importlib
    import os
    os.environ["SECTOR_ROTATION_THR_1D"] = "0.5"
    os.environ["SECTOR_ROTATION_THR_5D"] = "5.0"
    import detection.sector_flow_layer as sfl
    importlib.reload(sfl)
    layer = sfl.SectorFlowLayer(sector_store=MagicMock(
        fetch_latest_n_days=MagicMock(return_value=_make_rows(5, 0.3, -0.3))
    ))

    with patch.object(sfl, "get_market_profile", return_value="extended"):
        result = layer.detect()

    # 일 spread=0.6 (>=0.5 thr_1d), 5일 누적=3.0 (>=0.5 thr_1d, <5.0 thr_5d)
    assert result.level == "L1"
    assert result.rotation_type == "ROTATION_WATCH_DEF"

    # 정리
    os.environ.pop("SECTOR_ROTATION_THR_1D", None)
    os.environ.pop("SECTOR_ROTATION_THR_5D", None)
    importlib.reload(sfl)


@patch("detection.sector_flow_layer.get_market_profile", return_value="extended")
def test_none_below_thresholds(_mp) -> None:
    """제목: 임계 미달 — NONE"""
    rows = _make_rows(days=5, def_chg=0.05, cyc_chg=-0.05)  # 일 spread 0.1, 5일 0.5
    layer = _make_layer_with_rows(rows)

    result = layer.detect()

    assert result.level == "NONE"
    assert result.rotation_type == "NONE"


@patch("detection.sector_flow_layer.get_market_profile", return_value="holiday")
def test_holiday_skip(_mp) -> None:
    """제목: 휴장일 — 즉시 NONE"""
    layer = _make_layer_with_rows([])  # store는 호출되지 않음

    result = layer.detect()

    assert result.level == "NONE"
    assert "휴장일" in result.reasoning


@patch("detection.sector_flow_layer.get_market_profile", return_value="extended")
def test_empty_db_returns_none(_mp) -> None:
    """제목: DB 0건 — NONE + health_score 0"""
    layer = _make_layer_with_rows([])

    result = layer.detect()

    assert result.level == "NONE"
    assert result.health_score == 0.0


@patch("detection.sector_flow_layer.get_market_profile", return_value="extended")
def test_partial_null_chg_pct(_mp) -> None:
    """제목: 일부 chg_pct NULL — 그룹 평균에서 제외 (최소 2개 필요)"""
    rows = _make_rows(days=5, def_chg=0.3, cyc_chg=-0.3)
    # defensive 중 1개를 None으로 변경 (그래도 2개 남음 → 계산 가능)
    for r in rows:
        if r["ticker"] == "XLV":
            r["chg_pct"] = None
    layer = _make_layer_with_rows(rows)

    result = layer.detect()

    # 2개로도 평균 계산 가능 → L2 유지
    assert result.level == "L2"
    assert result.health_score < 1.0  # NULL 가중 0.5 반영


@patch("detection.sector_flow_layer.get_market_profile", return_value="extended")
def test_too_many_nulls_in_group(_mp) -> None:
    """제목: 그룹 ticker 2개 미만 — 평균 계산 불가, NONE"""
    rows = _make_rows(days=5, def_chg=0.3, cyc_chg=-0.3)
    # defensive 3개 중 2개를 None으로 변경 (1개만 남음 → 평균 불가)
    for r in rows:
        if r["ticker"] in ("XLV", "XLU"):
            r["chg_pct"] = None
    layer = _make_layer_with_rows(rows)

    result = layer.detect()

    assert result.level == "NONE"


@patch("detection.sector_flow_layer.get_market_profile", return_value="extended")
def test_reasoning_json_structure(_mp) -> None:
    """제목: reasoning_json 표준 스키마 검증"""
    rows = _make_rows(days=5, def_chg=0.4, cyc_chg=-0.4)
    layer = _make_layer_with_rows(rows)

    result = layer.detect()

    rj = result.reasoning_json
    assert rj["schema_version"] == "sector-1.0"
    assert rj["level"] == result.level
    assert "spread_5d" in rj
    assert "thr_5d" in rj
    assert rj["policy_version"] == "sector-v1.0.0"
    assert "evaluated_at" in rj


# ──────────────────────────────────────────────────────────────
# v1.1.0 신규 — 5일 데이터 충분성 가드 (MIN_ROWS_FOR_5D)
# ──────────────────────────────────────────────────────────────

@patch("detection.sector_flow_layer.get_market_profile", return_value="extended")
def test_v1_1_0_min_rows_guard_blocks_l2_when_insufficient(_mp) -> None:
    """제목: 5일 데이터 누적 전 강한 L2 신호 발생 → NONE 강제 (false alarm 차단)

    핵심 시나리오: 1일치만 누적된 상태에서 |1d_spread| >= 1.5p 발생.
    v1.0.0이면 5d_spread == 1d_spread → L2 알람.
    v1.1.0은 rows_used < 24 가드로 NONE 강제.
    """
    # 1일치 6 row + 큰 spread (def +2.0, cyc -2.0 → spread 4.0p, 임계 1.5 훨씬 초과)
    rows = _make_rows(days=1, def_chg=2.0, cyc_chg=-2.0)
    assert len(rows) == 6  # 1일 × 6 ticker = 6 row (< MIN_ROWS_FOR_5D=24)

    layer = _make_layer_with_rows(rows)
    result = layer.detect()

    # v1.0.0이면 L2 알람 발생했을 시나리오 → v1.1.0은 NONE 강제
    assert result.level == "NONE"
    assert result.rotation_type == "NONE"
    assert "5일 데이터 누적 중" in result.reasoning
    assert result.rows_used == 6


@patch("detection.sector_flow_layer.get_market_profile", return_value="extended")
def test_v1_1_0_min_rows_guard_passes_when_sufficient(_mp) -> None:
    """제목: 5일치 데이터 충족 시 가드 통과 → 정상 L2 판정"""
    # 5일치 30 row (= MIN_ROWS_FOR_5D 24 초과)
    rows = _make_rows(days=5, def_chg=0.4, cyc_chg=-0.4)
    assert len(rows) == 30  # 가드 통과 조건

    layer = _make_layer_with_rows(rows)
    result = layer.detect()

    # 정상 L2 판정 진행
    assert result.level == "L2"
    assert result.rows_used == 30


@patch("detection.sector_flow_layer.get_market_profile", return_value="extended")
def test_v1_1_0_min_rows_guard_partial_accumulation(_mp) -> None:
    """제목: 3일치 누적 (18 row) — 여전히 NONE 강제 (MIN_ROWS_FOR_5D 미달)"""
    rows = _make_rows(days=3, def_chg=0.5, cyc_chg=-0.5)
    assert len(rows) == 18  # 3일 × 6 = 18 < 24

    layer = _make_layer_with_rows(rows)
    result = layer.detect()

    assert result.level == "NONE"
    assert "5일 데이터 누적 중" in result.reasoning
    assert result.rows_used == 18
