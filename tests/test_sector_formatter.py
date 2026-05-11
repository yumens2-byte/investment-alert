"""
제목: SectorFormatter 단위 테스트
내용: HTML 메시지 생성 — 헤더, spread 수치, 운영자 메타 등 검증.
"""

from __future__ import annotations

from dataclasses import dataclass

from publishers.sector_formatter import SectorFormatter


@dataclass
class _MockSignal:
    """제목: SectorSignal 인터페이스만 가진 가벼운 mock"""
    level: str = "L2"
    rotation_type: str = "DEFENSIVE_ROTATION"
    spread_1d: float | None = 0.68
    spread_5d: float | None = 1.82
    def_avg_1d: float | None = 0.42
    cyc_avg_1d: float | None = -0.26
    def_avg_5d: float | None = 0.95
    cyc_avg_5d: float | None = -0.87
    health_score: float = 0.95
    rows_used: int = 30
    shadow_mode: bool = True
    policy_version: str = "sector-v1.0.0"
    alert_id: str = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


def test_format_internal_includes_all_metadata() -> None:
    """제목: Internal 메시지는 health/rows/shadow/policy/id 모두 포함"""
    formatter = SectorFormatter()
    signal = _MockSignal()

    msg = formatter.format_internal(signal)

    assert "[Sector Flow Alert L2]" in msg
    assert "방어주 로테이션" in msg
    assert "+1.82" in msg  # spread_5d
    assert "+0.68" in msg  # spread_1d
    assert "health=0.95" in msg
    assert "rows=30" in msg
    assert "shadow=True" in msg
    assert "sector-v1.0.0" in msg
    assert "a1b2c3d4" in msg  # alert_id 앞 8자


def test_format_tg_free_excludes_debug_metadata() -> None:
    """제목: Free 메시지는 health/policy/id 미포함 (사용자 친화)"""
    formatter = SectorFormatter()
    signal = _MockSignal()

    msg = formatter.format_tg_free(signal)

    assert "[Sector Flow Alert L2]" in msg
    assert "+1.82" in msg
    assert "health=" not in msg
    assert "policy=" not in msg
    assert "alert_id" not in msg


def test_format_risk_on_rotation() -> None:
    """제목: RISK_ON_ROTATION — 다른 이모지/라벨/설명"""
    formatter = SectorFormatter()
    signal = _MockSignal(
        rotation_type="RISK_ON_ROTATION",
        spread_5d=-1.82,
        def_avg_5d=-0.87,
        cyc_avg_5d=0.95,
    )

    msg = formatter.format_internal(signal)

    assert "위험선호 로테이션" in msg
    assert "🚀" in msg
    assert "-1.82" in msg


def test_format_none_values() -> None:
    """제목: spread/avg가 None — '—' 표시"""
    formatter = SectorFormatter()
    signal = _MockSignal(
        spread_1d=None,
        spread_5d=None,
        def_avg_1d=None,
        cyc_avg_1d=None,
        def_avg_5d=None,
        cyc_avg_5d=None,
    )

    msg = formatter.format_internal(signal)

    assert "—" in msg  # spread None이면 dash


def test_format_l1_rotation_watch() -> None:
    """제목: L1 ROTATION_WATCH_DEF — 워치 라벨"""
    formatter = SectorFormatter()
    signal = _MockSignal(
        level="L1",
        rotation_type="ROTATION_WATCH_DEF",
        spread_5d=1.1,
    )

    msg = formatter.format_tg_free(signal)

    assert "[Sector Flow Alert L1]" in msg
    assert "방어 로테이션 워치" in msg
    assert "👀" in msg


def test_format_unknown_rotation_type_fallback() -> None:
    """제목: 미정의 rotation_type — 기본 이모지/라벨 사용"""
    formatter = SectorFormatter()
    signal = _MockSignal(rotation_type="UNKNOWN_TYPE")

    msg = formatter.format_tg_free(signal)

    assert "UNKNOWN_TYPE" in msg
    assert "🔄" in msg
