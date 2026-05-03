"""
제목: Alert 메시지 포맷터
내용: AlertSignal을 받아 X(Twitter)와 Telegram 발행용 메시지를 생성합니다.
      L1/L2/L3 레벨별로 톤과 긴급도가 다르게 구성됩니다.
      v1.1.0: DRY_RUN=false 시 Gemini AI 자연어 트윗 생성 지원.

주요 클래스:
  - AlertFormatter: 레벨별 X/TG 메시지 생성

주요 함수:
  - AlertFormatter.format_x(): X용 단문 메시지 (280자 이내)
  - AlertFormatter.format_tg(): TG용 HTML 메시지 (상세)
  - AlertFormatter._generate_ai_tweet(): Gemini AI 자연어 트윗 (DRY_RUN=false 전용)
"""

from __future__ import annotations

import os
import random as _random
import re as _re
from typing import TYPE_CHECKING

from core.logger import get_logger

if TYPE_CHECKING:
    pass

VERSION = "1.1.0"

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
    "SYSTEM_DEGRADED": {
        "emoji": "🛠️",
        "label": "DEGRADED",
        "x_prefix": "🛠️ [시스템 경보]",
        "tg_header": "🛠️ <b>[SYSTEM_DEGRADED 운영 경보]</b>",
    },
}

# X 메시지 최대 길이
X_MAX_LENGTH = 275  # 5자 여유

# ────────────────────────────────────────────────────────
# X 해시태그 풀 (anti-bot 랜덤화 — v1.1.0)
# ────────────────────────────────────────────────────────
_X_HASHTAG_POOL: list[str] = [
    "#미국증시 #투자경보",
    "#글로벌경제 #시장경보",
    "#미국시장 #InvestmentAlert",
    "#경제위기 #투자주의",
    "#InvestmentOS #미국증시",
    "#시장경보 #글로벌증시",
    "#미국경제 #Alert",
]

# ────────────────────────────────────────────────────────
# 비한국어 차단 패턴 (v1.1.0)
# 힌디/아랍/키릴/일본 가나/태국/히브리/그리스 등 감지
# 한자(CJK)는 한국 콘텐츠에서 허용 — false positive 방지
# ────────────────────────────────────────────────────────
_NON_KR_PATTERN = _re.compile(
    r"[\u0900-\u097F"  # Devanagari (힌디)
    r"\u0600-\u06FF"   # Arabic
    r"\u0400-\u04FF"   # Cyrillic (러시아어)
    r"\u3040-\u30FF"   # Hiragana/Katakana (일본어 가나)
    r"\u0E00-\u0E7F"   # Thai
    r"\u0590-\u05FF"   # Hebrew
    r"\u0370-\u03FF]"  # Greek
)


def _is_dry_run() -> bool:
    """환경변수 DRY_RUN 파싱. 미설정 시 True (안전 기본값)."""
    val = os.environ.get("DRY_RUN", "true").lower()
    return val not in ("false", "0", "no")


class AlertFormatter:
    """
    제목: Alert 메시지 포맷터
    내용: AlertSignal 데이터를 X와 Telegram에 맞는 메시지 형식으로 변환합니다.

    책임:
      - 레벨별 이모지/헤더/톤 적용
      - X: 280자 이내 단문 (해시태그 포함)
      - TG: HTML 포맷 상세 메시지 (reasoning, top_news 포함)
      - v1.1.0: DRY_RUN=false 시 Gemini AI 자연어 트윗 생성
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
        내용: DRY_RUN=false이면 Gemini AI 자연어 트윗 시도.
              실패 또는 DRY_RUN=true이면 기존 템플릿 fallback.

        처리 플로우:
          1. DRY_RUN 환경변수 확인
          2. DRY_RUN=false → _generate_ai_tweet() 시도
          3. AI 성공 → AI 트윗 반환
          4. AI 실패 또는 DRY_RUN=true → _format_x_template() 반환

        Args:
            level: 'L1' | 'L2' | 'L3'
            score: Macro-News Score
            reasoning: 판정 근거 텍스트
            top_news_titles: 상위 뉴스 제목 리스트

        Returns:
            str: X 발행용 메시지 (280자 이내)
        """
        if not _is_dry_run():
            ai_msg = self._generate_ai_tweet(level, score, reasoning, top_news_titles)
            if ai_msg:
                return ai_msg

        return self._format_x_template(level, score, reasoning, top_news_titles)

    def _format_x_template(
        self,
        level: str,
        score: float,
        reasoning: str,
        top_news_titles: list[str],
    ) -> str:
        """
        제목: 기존 템플릿 방식 메시지 생성
        내용: DRY_RUN=true 또는 AI 실패 시 fallback.
              v1.0.0 format_x() 로직 이관, 동작 변경 없음.
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

    def _generate_ai_tweet(
        self,
        level: str,
        score: float,
        reasoning: str,
        top_news_titles: list[str],
    ) -> str | None:
        """
        제목: Gemini AI 자연어 트윗 생성 (DRY_RUN=false 전용)
        내용: gemini-2.5-flash-lite 호출 → 한국어 자연어 Alert 트윗 생성.
              실패/검증 탈락 시 None 반환 → 호출자가 템플릿 fallback.

        처리 플로우:
          1. GEMINI_API_KEY 존재 확인
          2. google-genai SDK로 Gemini 호출
          3. 응답 텍스트 추출
          4. 검증 1: 200자 이내
          5. 검증 2: 비한국어 문자 가드
          6. 성공 시 트윗 반환, 실패 시 None

        Args:
            level: 'L1' | 'L2' | 'L3'
            score: Macro-News Score
            reasoning: 판정 근거 텍스트
            top_news_titles: 상위 뉴스 제목 리스트

        Returns:
            str | None: AI 생성 트윗 또는 None (fallback 신호)
        """
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            logger.warning("[AlertFormatter] GEMINI_API_KEY 미설정 → 템플릿 fallback")
            return None

        try:
            from google import genai as _genai  # noqa: PLC0415

            client = _genai.Client(api_key=api_key)

            level_tone = {
                "L1": "매우 긴박하고 심각한 경보. 즉각적인 위험 수준.",
                "L2": "경고 수준. 시장 주의 필요.",
                "L3": "모니터링 수준. 관찰 권장.",
            }.get(level, "주의 수준.")

            top_news_str = top_news_titles[0][:80] if top_news_titles else "없음"
            hashtags = _random.choice(_X_HASHTAG_POOL)

            prompt = (
                f"미국 금융시장 긴급 Alert 트윗을 한국어로 작성하세요.\n\n"
                f"[Alert 정보]\n"
                f"- 등급: {level} / Score: {score:.1f}/10\n"
                f"- 상황: {level_tone}\n"
                f"- 판정 근거: {reasoning[:150]}\n"
                f"- 주요 뉴스: {top_news_str}\n\n"
                f"[작성 조건]\n"
                f"1. 200자 이내 (필수)\n"
                f"2. 첫 줄: 이모지 + 한 줄 핵심 요약\n"
                f"3. 중간: 구체적 상황 1~2줄\n"
                f"4. '⚠️ 투자 참고 정보, 투자 권유 아님' 문구 포함\n"
                f"5. 마지막 줄 해시태그: {hashtags}\n"
                f"6. 자연스러운 한국어 (사람이 쓴 것처럼, 매번 다른 표현)\n"
                f"7. 트윗 본문만 출력 (설명·마크다운 불필요)\n"
            )

            response = client.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=prompt,
            )
            tweet = response.text.strip() if response.text else ""

            # 검증 1: 길이
            if not tweet or len(tweet) > X_MAX_LENGTH:
                logger.warning(
                    f"[AlertFormatter] AI 트윗 길이 오류 ({len(tweet)}자) → fallback"
                )
                return None

            # 검증 2: 비한국어 가드
            if _NON_KR_PATTERN.search(tweet):
                logger.warning(
                    "[AlertFormatter] AI 트윗 비한국어 문자 감지 → fallback"
                )
                return None

            logger.info(
                f"[AlertFormatter] AI 트윗 생성 완료 ({len(tweet)}자, level={level})"
            )
            return tweet

        except Exception as e:
            logger.warning(
                f"[AlertFormatter] AI 트윗 생성 실패 → 템플릿 fallback: "
                f"{type(e).__name__}: {e}"
            )
            return None

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

    def format_internal(
        self,
        level: str,
        score: float,
        reasoning: str,
        top_news_titles: list[str],
        top_youtube_titles: list[str],
        health_score: float,
        alert_id: str,
        playbook: list[str] | None = None,
    ) -> str:
        """
        제목: TG 내부 운영 채널 발행 메시지 포맷
        내용: L1/L2/L3 모두 + SYSTEM_DEGRADED 모두 내부 채널로 발송될 때 사용.
              format_tg와 유사하나 운영자 친화적 정보 포함:
              - alert_id 전체 노출 (8자만이 아닌)
              - reasoning 풀텍스트
              - playbook 체크리스트 (FR-06 Phase 2 연동)

        Args:
            level: 'L1'|'L2'|'L3'|'SYSTEM_DEGRADED'
            score: Macro-News Score (SYSTEM_DEGRADED는 0.0)
            reasoning: 판정 근거 (ReasoningBuilder 텍스트)
            top_news_titles: 상위 뉴스 제목 리스트
            top_youtube_titles: 상위 YouTube 제목 리스트
            health_score: 데이터 건강도
            alert_id: 감사 추적 키 (전체 노출)
            playbook: 운영 체크리스트 (FR-06, Phase 2 채움)

        Returns:
            str: HTML 포맷 내부 메시지
        """
        meta = LEVEL_META.get(level, LEVEL_META["L3"])

        lines = [
            f"{meta['tg_header']}  <i>(내부)</i>",
            "",
            f"📊 Score: <b>{score:.2f}</b> | Health: <b>{health_score:.2f}</b>",
            "",
            "📝 <b>판정 근거</b>",
            f"  {reasoning}",
            "",
        ]

        if top_news_titles:
            lines.append("📰 <b>상위 뉴스</b>")
            for i, title in enumerate(top_news_titles[:3], 1):
                lines.append(f"  {i}. {title[:80]}")
            lines.append("")

        if top_youtube_titles:
            lines.append("📺 <b>YouTube 확인</b>")
            for i, title in enumerate(top_youtube_titles[:2], 1):
                lines.append(f"  {i}. {title[:60]}")
            lines.append("")

        if playbook:
            lines.append("📋 <b>운영 체크리스트</b>")
            for item in playbook[:5]:
                lines.append(f"  • {item}")
            lines.append("")

        lines.append(f"<code>alert_id: {alert_id}</code>")

        return "\n".join(lines)

    def format_degraded(
        self,
        dq_state: dict | None,
        alert_id: str,
    ) -> str:
        """
        제목: SYSTEM_DEGRADED 전용 내부 운영 메시지 포맷
        내용: DataQualityState 정보를 운영자 친화적으로 정리한다.
              dq_state는 DataQualityState.to_dict() 결과(dict) 또는 dataclass 직접 전달 모두 지원.

        Args:
            dq_state: DataQualityState (dict 또는 dataclass). None 가능.
            alert_id: 감사 추적 키

        Returns:
            str: HTML 포맷 SYSTEM_DEGRADED 메시지
        """
        meta = LEVEL_META.get("SYSTEM_DEGRADED", LEVEL_META["L3"])

        # dq_state가 dataclass면 to_dict() 호출, dict면 그대로 사용
        if dq_state is None:
            state_dict: dict = {}
        elif isinstance(dq_state, dict):
            state_dict = dq_state
        else:
            # DataQualityState dataclass 가정 — to_dict() 메서드 호출
            try:
                state_dict = dq_state.to_dict()
            except AttributeError:
                state_dict = {}

        success = state_dict.get("source_success_rate", 0.0)
        fresh = state_dict.get("fresh_event_ratio", 0.0)
        lag = state_dict.get("lag_seconds_p95", 0.0)
        reasons = state_dict.get("degraded_reasons", []) or []
        source_results = state_dict.get("source_results", {}) or {}

        # 실패 소스 추출
        failed_sources = [name for name, ok in source_results.items() if not ok]

        lines = [
            meta["tg_header"],
            "",
            "⚠️ <b>수집 시스템 이상이 감지되었습니다</b>",
            "",
            "📊 <b>지표</b>",
            f"  • source_success_rate: <b>{success:.2f}</b>",
            f"  • fresh_event_ratio: <b>{fresh:.2f}</b>",
            f"  • lag_seconds_p95: <b>{lag:.1f}s</b>",
            "",
        ]

        if failed_sources:
            lines.append("❌ <b>실패한 소스</b>")
            for name in failed_sources[:5]:
                lines.append(f"  • {name}")
            lines.append("")

        if reasons:
            lines.append("🔍 <b>위반 사유</b>")
            for r in reasons[:5]:
                lines.append(f"  • <code>{r}</code>")
            lines.append("")

        lines.extend([
            "🛠️ <b>조치 권고</b>",
            "  • GitHub Actions 로그 확인",
            "  • RSS 피드 URL 상태 점검",
            "  • Notion 에러 회고 페이지에 기록",
            "",
            f"<code>alert_id: {alert_id}</code>",
        ])

        return "\n".join(lines)
