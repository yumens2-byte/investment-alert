"""
제목: EDT 심리지표 발행 포맷터 (v1.0.0)
내용: 마스터 승인 2종 포맷을 생성한다.
      - Variant B: 표준 심리 스코어 카드 (구성 지표 노출)
      - EDT: E/D/T 3축 카드 (브랜드 지표)
      매 발행 시 2종 중 랜덤 선택 + 오프닝/코멘트/해시태그/이모지 랜덤 조합.
      anti-bot 최상위 원칙: 동일 스케줄·동일 문형 반복 금지, X duplicate 403 방지.

주요 클래스:
  - SentimentFormatter: format_x_random() / format_tg_internal()

원칙:
  - 추측값 금지 — 결측 지표는 "—" 표기, 기준일 명시 (HY OAS T+1 분리 표기)
  - 예측/성과 주장 문구 사용 금지 (팩트 원칙)
"""

from __future__ import annotations

import random

from config.sentiment_settings import (
    LEVEL_EMOJI,
    LEVEL_LABEL_KR,
    T_LOOKBACK_DAYS,
)
from core.logger import get_logger
from detection.sentiment_engine import (
    TREND_DOWN,
    TREND_UP,
    SentimentEngine,
    SentimentResult,
)

VERSION = "1.0.0"

logger = get_logger(__name__)

# ────────────────────────────────────────────────────────
# 랜덤 풀 (anti-bot — 조합 다양성 확보)
# ────────────────────────────────────────────────────────
_OPENINGS_B = [
    "🧭 미장 심리 스코어",
    "🌡️ 오늘의 공포·탐욕 체크",
    "📊 미장 심리 온도",
    "🧭 오늘의 시장 심리",
]

_OPENINGS_EDT = [
    "🧭 EDT 지표",
    "📡 EDT 시그널",
    "🧭 오늘의 EDT",
]

_HASHTAG_POOL = [
    "#미국주식",
    "#미장",
    "#시장심리",
    "#투자심리",
    "#VIX",
    "#SP500",
    "#주식",
]

_HASHTAG_EDT = "#EDT지표"  # 브랜드 태그 — EDT 포맷에 고정 포함

_DISCLAIMERS = [
    "⚠️ 투자 참고 정보",
    "⚠️ 투자 판단의 참고 자료입니다",
    "⚠️ 투자 권유가 아닌 참고 정보입니다",
]

# D축 해석 코멘트 (팩트 서술 — 예측 주장 없음)
_D_COMMENTS: dict[str, list[str]] = {
    "FAST": [
        "빠른 돈이 먼저 숨었다.\n느린 돈이 따라가는지가 관전 포인트.",
        "옵션시장이 먼저 움츠렸고\n크레딧은 태연한 하루.",
        "파생시장만 경계 태세.\n신용시장과의 괴리를 주목.",
    ],
    "SLOW": [
        "크레딧이 먼저 움직였다.\n조용한 균열인지 지켜볼 구간.",
        "신용시장이 더 무겁다.\n파생과의 온도차가 벌어졌다.",
    ],
    "ALIGNED": [
        "빠른 돈과 느린 돈이 같은 방향.\n시장 전체가 한 목소리인 하루.",
        "파생과 신용의 온도가 나란하다.",
    ],
}

_TREND_ARROWS = {TREND_UP: "↗", TREND_DOWN: "↘", None: "→"}


class SentimentFormatter:
    """
    제목: EDT 발행 텍스트 생성기
    내용: X용 2종 랜덤 포맷 + TG Internal 검수용 상세 포맷.
    """

    def __init__(self, rng: random.Random | None = None) -> None:
        """
        제목: SentimentFormatter 초기화

        Args:
            rng: 테스트 주입용 난수 생성기 (None이면 시스템 random)
        """
        self._rng = rng or random.Random()
        logger.info(f"[SentimentFormatter] v{VERSION} 초기화")

    # ────────────────────────────────────────────────
    # 공개 API
    # ────────────────────────────────────────────────
    def format_x_random(self, result: SentimentResult, score_date: str) -> str:
        """
        제목: X 발행 텍스트 생성 (2종 포맷 랜덤 선택)
        내용: 마스터 승인 포맷 — Variant B / EDT 중 랜덤.
              T 콜드스타트(trend=None)면 Variant B 강제 (EDT는 3축 완결 시만).

        Args:
            result: SentimentEngine 산출 결과 (DEGRADED 아님 전제)
            score_date: 기준일 YYYY-MM-DD

        Returns:
            str: 발행 텍스트
        """
        use_edt = result.d_score is not None and self._rng.random() < 0.5
        if use_edt:
            return self.format_edt(result, score_date)
        return self.format_variant_b(result, score_date)

    def format_variant_b(self, result: SentimentResult, score_date: str) -> str:
        """제목: Variant B 포맷 — 표준 심리 스코어 카드"""
        opening = self._rng.choice(_OPENINGS_B)
        level_kr = LEVEL_LABEL_KR.get(result.level, result.level)
        emoji = LEVEL_EMOJI.get(result.level, "")
        mmdd = self._mmdd(score_date)

        lines = [
            f"{opening} {result.e_score:.1f} / 100",
            f"{emoji} {level_kr} 구간",
            "",
            f"VIX구조 {self._fmt_raw(result, 'vix_ratio', '{:.2f}')} | "
            f"PCR {self._fmt_raw(result, 'pcr', '{:.2f}')}",
            f"HY {self._fmt_raw(result, 'hy_oas', '{:.0f}bp')} | "
            f"시장폭 {self._fmt_raw(result, 'breadth', '{:+.1f}%')} | "
            f"F&G {self._fmt_raw(result, 'crypto_fg', '{:.0f}')}",
        ]

        trend_line = self._trend_line(result)
        if trend_line:
            lines += ["", trend_line]

        comment = self._d_comment(result)
        if comment:
            lines += ["", comment]

        if result.missing:
            lines += ["", f"* {len(result.missing)}개 지표 수집 실패 — 잔여 지표 재산출"]

        lines += ["", f"{self._rng.choice(_DISCLAIMERS)} · {self._date_note(result, mmdd)}"]
        lines.append(self._hashtags(include_edt=False))
        return "\n".join(lines)

    def format_edt(self, result: SentimentResult, score_date: str) -> str:
        """제목: EDT 포맷 — E/D/T 3축 카드 (마스터 승인 샘플 기준)"""
        opening = self._rng.choice(_OPENINGS_EDT)
        level_kr = LEVEL_LABEL_KR.get(result.level, result.level)
        emoji = LEVEL_EMOJI.get(result.level, "")
        mmdd = self._mmdd(score_date)

        lines = [f"{opening} — {mmdd}", ""]
        lines.append(f"E {result.e_score:.1f} {emoji} {level_kr}")

        d_key = SentimentEngine.divergence_note(result.d_score)
        if result.d_score is not None:
            d_desc = {
                "FAST": "⚡ 파생 단독 공포 (신용은 평온)",
                "SLOW": "🧊 신용이 더 공포 (파생은 평온)",
                "ALIGNED": "🔗 파생·신용 정합",
            }.get(d_key or "", "")
            lines.append(f"D {result.d_score:+.0f}  {d_desc}")
        else:
            lines.append("D —  (구성 지표 결측)")

        if result.t_score is not None:
            arrow = _TREND_ARROWS.get(result.trend, "→")
            lines.append(f"T {result.t_score:+.1f} {arrow} {T_LOOKBACK_DAYS}일 흐름")
        else:
            lines.append("T —  (이력 축적 중)")

        if result.state_label:
            lines += ["", f"상태: {result.state_label}"]

        comment = self._d_comment(result)
        if comment:
            lines += ["", comment]

        lines += [
            "",
            f"{self._rng.choice(_DISCLAIMERS)} · 산출: 5개 시장데이터 합성",
        ]
        lines.append(self._hashtags(include_edt=True))
        return "\n".join(lines)

    def format_tg_internal(self, result: SentimentResult, score_date: str) -> str:
        """
        제목: TG Internal 검수용 상세 포맷
        내용: 운영자 디버깅 친화 — 원값/점수/기준일 전체 표기 (랜덤화 불필요).
        """
        lines = [
            f"📊 [EDT internal] {score_date}",
            f"E={result.e_score} level={result.level}",
            f"D={result.d_score} (fast={result.fast_fear} / slow={result.slow_fear})",
            f"T={result.t_score} trend={result.trend} state={result.state_label}",
            "",
        ]
        for key in ("vix_ratio", "pcr", "hy_oas", "breadth", "crypto_fg"):
            raw = result.raw.get(key)
            score = result.scores.get(key)
            if raw is None:
                lines.append(f"- {key}: MISSING")
            else:
                lines.append(
                    f"- {key}: {raw.get('value')} (score={score}, date={raw.get('date')})"
                )
        if result.missing:
            lines.append(f"missing={result.missing}")
        return "\n".join(lines)

    # ────────────────────────────────────────────────
    # 내부 헬퍼
    # ────────────────────────────────────────────────
    def _hashtags(self, include_edt: bool) -> str:
        """제목: 해시태그 랜덤 조합 (풀에서 2~3개 샘플)"""
        count = self._rng.choice([2, 3])
        tags = self._rng.sample(_HASHTAG_POOL, count)
        if include_edt:
            tags = [_HASHTAG_EDT, *tags[: count - 1]] if count > 1 else [_HASHTAG_EDT]
        return " ".join(tags)

    def _d_comment(self, result: SentimentResult) -> str | None:
        """제목: D축 해석 코멘트 랜덤 선택"""
        d_key = SentimentEngine.divergence_note(result.d_score)
        pool = _D_COMMENTS.get(d_key or "")
        if not pool:
            return None
        return self._rng.choice(pool)

    @staticmethod
    def _trend_line(result: SentimentResult) -> str | None:
        """제목: 전일 대비 흐름 라인 (T 결측 시 생략)"""
        if result.t_score is None:
            return None
        arrow = "▲" if result.t_score > 0 else ("▼" if result.t_score < 0 else "―")
        return f"{T_LOOKBACK_DAYS}일 전 대비 {arrow} {abs(result.t_score):.1f}p"

    @staticmethod
    def _fmt_raw(result: SentimentResult, key: str, fmt: str) -> str:
        """제목: 원값 포맷 (결측 시 '—' — 추측값 금지)"""
        raw = result.raw.get(key)
        if raw is None or raw.get("value") is None:
            return "—"
        return fmt.format(float(raw["value"]))

    @staticmethod
    def _date_note(result: SentimentResult, mmdd: str) -> str:
        """제목: 기준일 표기 — HY OAS(T+1)만 기준일 분리 표기"""
        hy = result.raw.get("hy_oas")
        base = f"기준 {mmdd}"
        if hy and hy.get("date"):
            hy_mmdd = SentimentFormatter._mmdd(str(hy["date"]))
            if hy_mmdd != mmdd:
                return f"{base} (HY: {hy_mmdd})"
        return base

    @staticmethod
    def _mmdd(iso_date: str) -> str:
        """제목: YYYY-MM-DD → MM/DD"""
        parts = iso_date.split("-")
        if len(parts) == 3:
            return f"{parts[1]}/{parts[2]}"
        return iso_date
