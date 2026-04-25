"""
제목: Alert 메시지 포맷터
내용: AlertSignal을 받아 X(Twitter)와 Telegram 발행용 메시지를 생성합니다.
      L1/L2/L3 레벨별로 톤과 긴급도가 다르게 구성됩니다.

주요 클래스:
  - AlertFormatter: 레벨별 X/TG 메시지 생성

주요 함수:
  - AlertFormatter.format_x(signal): X용 단문 메시지 (280자 이내)
  - AlertFormatter.format_tg(signal): TG용 HTML 메시지 (상세)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.logger import get_logger

if TYPE_CHECKING:
    pass

VERSION = "1.0.0"

logger = get_logger(__name__)

# ────────────────────────────────────────────────────────
# 레벨별 이모지 및 헤더
# ────────────────────────────────────────────────────────
LEVEL_META: dict[str, dict[str, str]] = {
    "L1": {
        "emoji": "🚨",
        "label": "CRITICAL",
        "x_prefix": "🚨 [긴급 Alert]",
        "tg_header": "🚨 <b>[L1 CRITICAL Alert]</b>",
    },
    "L2": {
        "emoji": "⚠️",
        "label": "HIGH",
        "x_prefix": "⚠️ [Alert]",
        "tg_header": "⚠️ <b>[L2 HIGH Alert]</b>",
    },
    "L3": {
        "emoji": "📊",
        "label": "MEDIUM",
        "x_prefix": "📊 [모니터링]",
        "tg_header": "📊 <b>[L3 MEDIUM 모니터링]</b>",
    },
}

# X 메시지 최대 길이
X_MAX_LENGTH = 275  # 5자 여유


class AlertFormatter:
    """
    제목: Alert 메시지 포맷터
    내용: AlertSignal 데이터를 X와 Telegram에 맞는 메시지 형식으로 변환합니다.

    책임:
      - 레벨별 이모지/헤더/톤 적용
      - X: 280자 이내 단문 (해시태그 포함)
      - TG: HTML 포맷 상세 메시지 (reasoning, top_news 포함)
    """

    def format_x(
        self,
        level: str,
        score: float,
        reasoning: str,
        top_news_titles: list[str],
    ) -> str:
        """
        제목: X(Twitter) 발행용 메시지 생성
        내용: 280자 이내 단문. 레벨 이모지, 점수, 상위 뉴스 제목 1건, 해시태그 포함.

        처리 플로우:
          1. 레벨 메타 조회
          2. 본문 구성 (점수, 판정 근거 요약, 상위 뉴스)
          3. 280자 초과 시 뉴스 제목 절단
          4. 해시태그 부착

        Args:
            level: 'L1' | 'L2' | 'L3'
            score: Macro-News Score
            reasoning: 판정 근거 텍스트
            top_news_titles: 상위 뉴스 제목 리스트

        Returns:
            str: X 발행용 메시지 (280자 이내)
        """
        meta = LEVEL_META.get(level, LEVEL_META["L3"])
        prefix = meta["x_prefix"]

        # 제목: reasoning 요약 (100자 제한)
        reason_short = reasoning[:100].split("\n")[0]

        # 제목: 상위 뉴스 제목 1건
        top_news = top_news_titles[0][:60] + "..." if top_news_titles else ""

        hashtags = "#미국증시 #Alert #InvestmentOS"

        body_parts = [
            f"{prefix}",
            f"Score {score:.1f} | {reason_short}",
        ]
        if top_news:
            body_parts.append(f"📰 {top_news}")
        body_parts.append(hashtags)

        message = "\n".join(body_parts)

        # 제목: 280자 초과 시 절단
        if len(message) > X_MAX_LENGTH:
            message = message[:X_MAX_LENGTH]

        return message

    def format_tg(
        self,
        level: str,
        score: float,
        reasoning: str,
        top_news_titles: list[str],
        top_youtube_titles: list[str],
        health_score: float,
        alert_id: str,
    ) -> str:
        """
        제목: Telegram 발행용 HTML 메시지 생성
        내용: 상세 정보를 HTML 포맷으로 구성.
              reasoning, top_news, top_youtube, health_score 포함.

        Args:
            level: 'L1' | 'L2' | 'L3'
            score: Macro-News Score
            reasoning: 판정 근거 텍스트
            top_news_titles: 상위 뉴스 제목 리스트
            top_youtube_titles: 상위 YouTube 제목 리스트
            health_score: 데이터 건강도
            alert_id: 감사 추적 ID (단축 표시)

        Returns:
            str: Telegram HTML 메시지
        """
        meta = LEVEL_META.get(level, LEVEL_META["L3"])
        header = meta["tg_header"]

        lines: list[str] = [
            header,
            "",
            f"<b>Score</b>: {score:.2f} | <b>Health</b>: {health_score:.0%}",
            f"<b>판정근거</b>: {reasoning[:200]}",
            "",
        ]

        # 제목: 상위 뉴스
        if top_news_titles:
            lines.append("📰 <b>주요 뉴스</b>")
            for i, title in enumerate(top_news_titles[:3], 1):
                lines.append(f"  {i}. {title[:80]}")
            lines.append("")

        # 제목: YouTube 확인
        if top_youtube_titles:
            lines.append("📺 <b>유튜브 확인</b>")
            for i, title in enumerate(top_youtube_titles[:2], 1):
                lines.append(f"  {i}. {title[:60]}")
            lines.append("")

        lines.append(f"<code>ID: {alert_id[:8]}</code> | ⚠️ 투자 참고 정보")

        return "\n".join(lines)
