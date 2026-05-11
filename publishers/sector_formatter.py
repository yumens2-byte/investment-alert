"""
제목: Sector Rotation 메시지 포맷터
내용: SectorSignal을 TG Internal/Free/Paid HTML 메시지로 변환.

주요 클래스:
  - SectorFormatter: sector 전용 메시지 포맷

주요 함수:
  - SectorFormatter.format_internal(signal): 운영자 채널 (디버깅 친화)
  - SectorFormatter.format_tg_free(signal): 무료 채널 (정식 전환 후 사용)
  - SectorFormatter.format_tg_paid(signal): 유료 채널 (정식 전환 후 사용)
"""

from __future__ import annotations

from core.logger import get_logger

VERSION = "1.0.0"

logger = get_logger(__name__)


# rotation_type별 시각화 메타
_ROTATION_META: dict[str, dict[str, str]] = {
    "DEFENSIVE_ROTATION": {
        "emoji": "🛡️",
        "label": "방어주 로테이션",
        "desc": "시장이 방어주로 회피 중. 경기 우려 또는 변동성 확대 신호.",
    },
    "RISK_ON_ROTATION": {
        "emoji": "🚀",
        "label": "위험선호 로테이션",
        "desc": "경기민감주로 자금 이동. 시장 낙관/경기 회복 기대.",
    },
    "ROTATION_WATCH_DEF": {
        "emoji": "👀",
        "label": "방어 로테이션 워치",
        "desc": "방어주 강세 초기 신호. 5일 누적은 아직 임계 미달.",
    },
    "ROTATION_WATCH_RISK": {
        "emoji": "👀",
        "label": "위험선호 워치",
        "desc": "경기민감주 강세 초기 신호. 5일 누적은 아직 임계 미달.",
    },
}


class SectorFormatter:
    """
    제목: Sector Alert 메시지 포맷터
    내용: SectorSignal → TG HTML 메시지.
          기존 AlertFormatter 패턴(HTML, parse_mode=HTML)과 정합.

    책임:
      - format_internal: 운영자 채널 — 모든 수치 + alert_id + policy_version
      - format_tg_free: 무료 채널 — 사용자 친화 톤
      - format_tg_paid: 유료 채널 — 무료 + 약간의 부가 해석
    """

    def format_internal(self, signal: object) -> str:
        """제목: TG Internal 운영자 채널 메시지"""
        return self._format_html(signal, audience="internal")

    def format_tg_free(self, signal: object) -> str:
        """제목: TG Free 무료 채널 메시지"""
        return self._format_html(signal, audience="free")

    def format_tg_paid(self, signal: object) -> str:
        """제목: TG Paid 유료 채널 메시지"""
        return self._format_html(signal, audience="paid")

    @staticmethod
    def _fmt(v: float | None, suffix: str = "p") -> str:
        """제목: 수치 포맷팅 — None은 '—', 양수는 +기호"""
        if v is None:
            return "—"
        return f"{v:+.2f}{suffix}"

    def _format_html(self, signal: object, audience: str) -> str:
        """
        제목: HTML 메시지 빌더
        내용: signal 속성을 사용해 TG HTML 메시지 생성.

        Args:
            signal: SectorSignal (level, rotation_type, spread_*, def_avg_*, cyc_avg_* 필수)
            audience: 'internal' | 'free' | 'paid'
        """
        rotation_type = getattr(signal, "rotation_type", "NONE")
        meta = _ROTATION_META.get(
            rotation_type,
            {"emoji": "🔄", "label": rotation_type, "desc": ""},
        )

        emoji = meta["emoji"]
        label = meta["label"]
        desc = meta["desc"]
        level = getattr(signal, "level", "L?")

        header = (
            f"{emoji} <b>[Sector Flow Alert {level}]</b>\n"
            f"<b>{label}</b>"
        )

        body_lines = [
            "",
            f"📊 5일 누적 spread: <code>{self._fmt(getattr(signal, 'spread_5d', None))}</code>",
            f"   방어주(XLV/XLU/XLP): {self._fmt(getattr(signal, 'def_avg_5d', None))}",
            f"   경기민감(XLI/XLRE/XLB): {self._fmt(getattr(signal, 'cyc_avg_5d', None))}",
            "",
            f"📅 1일 spread: <code>{self._fmt(getattr(signal, 'spread_1d', None))}</code>",
        ]

        if desc:
            body_lines.append("")
            body_lines.append(f"💡 {desc}")

        if audience == "internal":
            health = getattr(signal, "health_score", 0.0)
            rows = getattr(signal, "rows_used", 0)
            shadow = getattr(signal, "shadow_mode", False)
            policy = getattr(signal, "policy_version", "—")
            alert_id = getattr(signal, "alert_id", "?")
            body_lines.append("")
            body_lines.append(
                f"<i>health={health:.2f} | rows={rows} | shadow={shadow}</i>"
            )
            body_lines.append(
                f"<i>policy={policy} | id={alert_id[:8]}</i>"
            )

        return header + "\n" + "\n".join(body_lines)
