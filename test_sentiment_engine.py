"""
제목: SentimentEngine 단위 테스트
내용: 밴드 선형보간 경계값, 결측 재정규화/DEGRADED, E/D/T 산출,
      상태 라벨 15종 매트릭스, Golden Master(고정 입력 스냅샷) 검증.
"""

from __future__ import annotations

import pytest

from config.sentiment_settings import (
    BAND_HY_OAS,
    BAND_PCR,
    BAND_VIX_RATIO,
    LEVEL_BOUNDS,
    LEVEL_LABEL_KR,
    WEIGHTS,
)
from detection.sentiment_engine import (
    LEVEL_DEGRADED,
    STATE_LABELS,
    TREND_DOWN,
    TREND_FLAT,
    TREND_UP,
    SentimentEngine,
    interpolate_band,
)


def _collected(
    vix_ratio: float | None = 0.90,
    pcr: float | None = 0.70,
    hy_oas: float | None = 350.0,
    breadth: float | None = 0.0,
    crypto_fg: float | None = 50.0,
) -> dict:
    """제목: 테스트용 수집 결과 생성기 (None = 결측)"""

    def _item(value: float | None) -> dict | None:
        if value is None:
            return None
        return {"value": value, "date": "2026-08-26"}

    return {
        "vix_ratio": _item(vix_ratio),
        "pcr": _item(pcr),
        "hy_oas": _item(hy_oas),
        "breadth": _item(breadth),
        "crypto_fg": _item(crypto_fg),
    }


class TestInterpolateBand:
    """제목: 밴드 선형보간 — 경계값 전수"""

    @pytest.mark.unit
    def test_breakpoints_exact(self):
        """제목: 브레이크포인트 정확 일치 시 정의 점수 반환"""
        for band in (BAND_VIX_RATIO, BAND_PCR, BAND_HY_OAS):
            for x, y in band:
                assert interpolate_band(x, band) == pytest.approx(y)

    @pytest.mark.unit
    def test_clamp_below_and_above(self):
        """제목: 범위 밖 clamp"""
        assert interpolate_band(0.50, BAND_VIX_RATIO) == pytest.approx(100.0)
        assert interpolate_band(2.00, BAND_VIX_RATIO) == pytest.approx(0.0)
        assert interpolate_band(100.0, BAND_HY_OAS) == pytest.approx(100.0)
        assert interpolate_band(9999.0, BAND_HY_OAS) == pytest.approx(0.0)

    @pytest.mark.unit
    def test_midpoint_linear(self):
        """제목: 구간 중앙값 선형보간 정확성"""
        # BAND_VIX_RATIO: (0.93,45)~(1.00,25) 중앙 0.965 → 35
        assert interpolate_band(0.965, BAND_VIX_RATIO) == pytest.approx(35.0, abs=0.01)
        # BAND_HY_OAS: (330,55)~(400,45) 중앙 365 → 50
        assert interpolate_band(365.0, BAND_HY_OAS) == pytest.approx(50.0, abs=0.01)

    @pytest.mark.unit
    def test_monotonic_direction(self):
        """제목: 공포 방향 단조성 — 원값 증가 시 점수 비증가 (VIX/PCR/HY)"""
        for band in (BAND_VIX_RATIO, BAND_PCR, BAND_HY_OAS):
            prev = None
            for x in [b[0] for b in band]:
                score = interpolate_band(x, band)
                if prev is not None:
                    assert score <= prev
                prev = score


class TestComputeE:
    """제목: E 점수 및 레벨"""

    @pytest.mark.unit
    def test_full_indicators_weighted_sum(self):
        """제목: 전 지표 유효 시 가중합 = 수기 계산 일치"""
        engine = SentimentEngine()
        result = engine.compute(_collected())
        expected = sum(
            result.scores[k] * WEIGHTS[k] for k in WEIGHTS
        )
        assert result.e_score == pytest.approx(expected, abs=0.01)
        assert result.missing == []
        assert not result.is_degraded

    @pytest.mark.unit
    def test_level_bounds(self):
        """제목: 레벨 경계 판정 (하한 포함)"""
        engine = SentimentEngine()
        assert engine._resolve_level(75.0) == "EXTREME_GREED"
        assert engine._resolve_level(74.99) == "GREED"
        assert engine._resolve_level(55.0) == "GREED"
        assert engine._resolve_level(45.0) == "NEUTRAL"
        assert engine._resolve_level(25.0) == "FEAR"
        assert engine._resolve_level(24.99) == "EXTREME_FEAR"
        assert engine._resolve_level(0.0) == "EXTREME_FEAR"

    @pytest.mark.unit
    def test_level_labels_defined(self):
        """제목: 레벨-한글 라벨 매핑 완결성"""
        for _, level in LEVEL_BOUNDS:
            assert level in LEVEL_LABEL_KR


class TestMissingPolicy:
    """제목: 결측 정책 — 재정규화 / DEGRADED"""

    @pytest.mark.unit
    def test_one_missing_renormalizes(self):
        """제목: 1개 결측 시 잔여 가중치 재정규화"""
        engine = SentimentEngine()
        result = engine.compute(_collected(pcr=None))
        assert result.missing == ["pcr"]
        assert not result.is_degraded
        valid = {k: v for k, v in result.scores.items() if v is not None}
        expected = sum(valid[k] * WEIGHTS[k] for k in valid) / sum(
            WEIGHTS[k] for k in valid
        )
        assert result.e_score == pytest.approx(expected, abs=0.01)

    @pytest.mark.unit
    def test_two_missing_degraded(self):
        """제목: 2개 결측 → DEGRADED, E/D/T 미산출"""
        engine = SentimentEngine()
        result = engine.compute(_collected(pcr=None, hy_oas=None))
        assert result.level == LEVEL_DEGRADED
        assert result.is_degraded
        assert result.e_score is None
        assert result.d_score is None

    @pytest.mark.unit
    def test_all_missing_degraded(self):
        """제목: 전체 결측 → DEGRADED"""
        engine = SentimentEngine()
        result = engine.compute(
            _collected(None, None, None, None, None)
        )
        assert result.is_degraded


class TestDivergence:
    """제목: D축 산출"""

    @pytest.mark.unit
    def test_d_score_fast_minus_slow(self):
        """제목: D = fast − slow 수기 계산 일치"""
        engine = SentimentEngine()
        result = engine.compute(_collected())
        s = result.scores
        fast = s["vix_ratio"] * 0.6 + s["pcr"] * 0.4
        slow = s["hy_oas"] * 0.7 + s["breadth"] * 0.3
        assert result.fast_fear == pytest.approx(fast, abs=0.01)
        assert result.slow_fear == pytest.approx(slow, abs=0.01)
        assert result.d_score == pytest.approx(fast - slow, abs=0.02)

    @pytest.mark.unit
    def test_d_none_when_component_missing(self):
        """제목: D축 구성 지표 결측 시 D=None (부분 산출 금지)"""
        engine = SentimentEngine()
        result = engine.compute(_collected(breadth=None))
        assert not result.is_degraded  # 1개 결측 — E는 산출
        assert result.slow_fear is None
        assert result.d_score is None

    @pytest.mark.unit
    def test_divergence_note(self):
        """제목: 괴리 판정 키 — FAST/SLOW/ALIGNED 경계"""
        assert SentimentEngine.divergence_note(-20.0) == "FAST"
        assert SentimentEngine.divergence_note(20.0) == "SLOW"
        assert SentimentEngine.divergence_note(19.99) == "ALIGNED"
        assert SentimentEngine.divergence_note(-19.99) == "ALIGNED"
        assert SentimentEngine.divergence_note(None) is None


class TestTrend:
    """제목: T축 산출"""

    @pytest.mark.unit
    def test_t_score_and_directions(self):
        """제목: T = E − prev, 방향 3분기 경계"""
        engine = SentimentEngine()
        base = engine.compute(_collected())
        e = base.e_score

        up = engine.compute(_collected(), prev_e_score=e - 8.0)
        assert up.t_score == pytest.approx(8.0, abs=0.01)
        assert up.trend == TREND_UP

        down = engine.compute(_collected(), prev_e_score=e + 8.0)
        assert down.trend == TREND_DOWN

        flat = engine.compute(_collected(), prev_e_score=e - 7.99)
        assert flat.trend == TREND_FLAT

    @pytest.mark.unit
    def test_cold_start_none(self):
        """제목: 콜드스타트 (prev 없음) → T=None, 상태라벨 None"""
        engine = SentimentEngine()
        result = engine.compute(_collected(), prev_e_score=None)
        assert result.t_score is None
        assert result.trend is None
        assert result.state_label is None


class TestStateLabels:
    """제목: 상태 라벨 15종 매트릭스"""

    @pytest.mark.unit
    def test_matrix_complete(self):
        """제목: 5레벨 × 3방향 = 15종 전부 정의"""
        levels = [level for _, level in LEVEL_BOUNDS]
        trends = [TREND_UP, TREND_FLAT, TREND_DOWN]
        assert len(STATE_LABELS) == 15
        for level in levels:
            for trend in trends:
                assert (level, trend) in STATE_LABELS
                assert STATE_LABELS[(level, trend)]  # 빈 문자열 금지

    @pytest.mark.unit
    def test_state_label_resolution(self):
        """제목: compute 결과에 상태 라벨 반영"""
        engine = SentimentEngine()
        result = engine.compute(_collected(), prev_e_score=0.0)  # 큰 양의 T → UP
        assert result.trend == TREND_UP
        assert result.state_label == STATE_LABELS[(result.level, TREND_UP)]


class TestGoldenMaster:
    """제목: Golden Master — 고정 입력 스냅샷 (금융 계산 회귀 방지)"""

    @pytest.mark.unit
    def test_snapshot_fixed_input(self):
        """
        제목: 고정 입력 → 고정 출력
        내용: 밴드/가중치 상수 변경 시 이 테스트가 깨져 의도적 변경임을 강제 확인.
        입력: vix_ratio=0.94, pcr=0.81, hy_oas=318, breadth=-1.4, fg=38
        """
        engine = SentimentEngine()
        result = engine.compute(
            _collected(
                vix_ratio=0.94, pcr=0.81, hy_oas=318.0, breadth=-1.4, crypto_fg=38.0
            )
        )
        # 수기 검산 값 (밴드 정의 기준)
        assert result.scores["vix_ratio"] == pytest.approx(42.14, abs=0.01)
        assert result.scores["pcr"] == pytest.approx(41.47, abs=0.01)
        assert result.scores["hy_oas"] == pytest.approx(59.8, abs=0.01)
        assert result.scores["breadth"] == pytest.approx(41.0, abs=0.01)
        assert result.scores["crypto_fg"] == pytest.approx(38.0, abs=0.01)
        assert result.e_score == pytest.approx(44.9, abs=0.02)
        assert result.level == "FEAR"
