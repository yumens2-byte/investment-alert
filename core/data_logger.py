"""
제목: 전체 수집 데이터 구조화 로그 출력 모듈
내용: MacroNewsResult 전체 내용(뉴스 이벤트 전체, YouTube 이벤트 전체,
      Score 분해, 레벨 판정, 건강도)을 INFO 레벨로 구조화하여 출력합니다.
      디버깅 및 운영 모니터링용.

주요 클래스:
  - DataLogger: MacroNewsResult 전체 내용 로그 출력

주요 함수:
  - DataLogger.log_all(result, signal): 전체 수집 데이터 로그 출력
  - DataLogger.log_news_events(events): 뉴스 이벤트 상세 로그
  - DataLogger.log_youtube_events(events): YouTube 이벤트 상세 로그
  - DataLogger.log_score_breakdown(result): 점수 분해 로그
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.logger import get_logger

if TYPE_CHECKING:
    from detection.alert_engine import AlertSignal
    from detection.macro_news_layer import MacroNewsResult

VERSION = "1.0.0"

logger = get_logger(__name__)

# 제목: 출력 구분선
_DIV = "─" * 60
_DIV_BOLD = "═" * 60


class DataLogger:
    """
    제목: 전체 수집 데이터 구조화 로그 출력기
    내용: run_alert.py 파이프라인에서 감지 완료 후 호출되어
          수집된 모든 뉴스/YouTube 이벤트와 점수 분해를 INFO 로그로 출력합니다.

    책임:
      - 뉴스 이벤트 전체 목록 (제목, 소스, tier, 점수, 키워드)
      - YouTube 이벤트 전체 목록 (제목, 채널, 점수, 가중치)
      - Score 분해 (news_score + youtube_bonus = final_score)
      - 레벨 판정 + health_score + reasoning
      - AlertSignal 발행 계획 (publish_x/tg_free/tg_paid)
    """

    def log_all(self, result: MacroNewsResult, signal: AlertSignal | None = None) -> None:
        """
        제목: 전체 수집 데이터 로그 출력 메인
        내용: MacroNewsResult와 AlertSignal 전체를 구조화된 형태로 INFO 로그 출력.

        처리 플로우:
          1. 헤더 출력
          2. 뉴스 이벤트 전체
          3. YouTube 이벤트 전체
          4. Score 분해
          5. 레벨 판정 및 reasoning
          6. AlertSignal 발행 계획 (signal이 있는 경우)

        Args:
            result: MacroNewsLayer.detect() 반환값
            signal: AlertEngine.process() 반환값 (선택)
        """
        logger.info(_DIV_BOLD)
        logger.info("[DataLogger] v%s ── 전체 수집 데이터 로그 출력 시작", VERSION)
        logger.info(_DIV_BOLD)

        self.log_news_events(result.news_events)
        self.log_youtube_events(result.youtube_events)
        self.log_score_breakdown(result)

        if signal is not None:
            self.log_alert_signal(signal)

        logger.info(_DIV_BOLD)
        logger.info("[DataLogger] 전체 로그 출력 완료")
        logger.info(_DIV_BOLD)

    def log_news_events(self, events: list) -> None:
        """
        제목: 뉴스 이벤트 전체 로그 출력
        내용: 수집된 모든 뉴스 이벤트를 번호순으로 출력.

        Args:
            events: List[CollectorEvent] (뉴스)
        """
        logger.info(_DIV)
        logger.info("[DataLogger] 📰 뉴스 이벤트 전체 (%d건)", len(events))
        logger.info(_DIV)

        if not events:
            logger.info("  [없음]")
            return

        for i, e in enumerate(events, 1):
            keywords_str = ", ".join(e.matched_keywords[:5]) if e.matched_keywords else "없음"
            score_str = f"{e.effective_score:.2f}"
            source_count_str = f"(복수소스={e.source_count})" if e.source_count > 1 else ""

            logger.info(
                "  [%2d] tier=%s | source=%-20s | score=%s %s",
                i, e.tier or "?", e.source_name[:20], score_str, source_count_str,
            )
            logger.info(
                "       제목: %s",
                e.title[:80] + ("..." if len(e.title) > 80 else ""),
            )
            logger.info("       키워드: %s", keywords_str)
            if e.ai_reasoning:
                logger.info(
                    "       AI근거: %s",
                    e.ai_reasoning[:60] + ("..." if len(e.ai_reasoning) > 60 else ""),
                )
            logger.info("       URL: %s", e.url[:70])

    def log_youtube_events(self, events: list) -> None:
        """
        제목: YouTube 이벤트 전체 로그 출력

        Args:
            events: List[CollectorEvent] (YouTube)
        """
        logger.info(_DIV)
        logger.info("[DataLogger] 📺 YouTube 이벤트 전체 (%d건)", len(events))
        logger.info(_DIV)

        if not events:
            logger.info("  [없음]")
            return

        for i, e in enumerate(events, 1):
            keywords_str = ", ".join(e.matched_keywords[:5]) if e.matched_keywords else "없음"
            weighted = e.keyword_score * e.channel_weight

            logger.info(
                "  [%2d] 채널=%-20s | weight=%.1f | kw_score=%.2f | weighted=%.2f",
                i, e.source_name[:20], e.channel_weight, e.keyword_score, weighted,
            )
            logger.info(
                "       제목: %s",
                e.title[:80] + ("..." if len(e.title) > 80 else ""),
            )
            logger.info("       키워드: %s", keywords_str)
            logger.info("       URL: %s", e.url[:70])

    def log_score_breakdown(self, result: MacroNewsResult) -> None:
        """
        제목: Macro-News Score 분해 로그 출력
        내용: 최종 점수가 어떻게 구성됐는지 분해하여 출력.

        Args:
            result: MacroNewsResult
        """
        logger.info(_DIV)
        logger.info("[DataLogger] 📊 Score 분해")
        logger.info(_DIV)
        logger.info("  뉴스 점수    : %.4f", result.news_score)
        logger.info("  뉴스 이벤트 수: %d건", len(result.news_events))
        logger.info("  YouTube 보너스: %.4f", result.youtube_bonus)
        logger.info("  YouTube 이벤트 수: %d건", len(result.youtube_events))
        logger.info("  ─────────────────────")
        logger.info("  최종 Score   : %.4f", result.score)
        logger.info("  데이터 건강도: %.2f (%.0f%%)", result.health_score, result.health_score * 100)
        logger.info("")
        logger.info("  ▶ 판정 레벨  : %s", result.level)
        logger.info("  ▶ 판정 근거  : %s", result.reasoning[:120])
        # 운영 경고는 본문 가독성을 위해 최대 2건까지만 노출한다.
        # (전체 원본은 result.ops_warnings에 보존되어 다른 sink에서 전량 사용 가능)
        if getattr(result, "ops_warnings", None):
            logger.info("  ▶ 운영 경고  : %s", " | ".join(result.ops_warnings[:2]))

    def log_alert_signal(self, signal: AlertSignal) -> None:
        """
        제목: AlertSignal 발행 계획 로그 출력

        Args:
            signal: AlertSignal
        """
        logger.info(_DIV)
        logger.info("[DataLogger] 🚨 AlertSignal 발행 계획")
        logger.info(_DIV)
        logger.info("  alert_id     : %s", signal.alert_id)
        logger.info("  level        : %s", signal.level)
        logger.info("  쿨다운 활성   : %s", signal.is_cooldown_active)
        logger.info("  발행 대상     :")
        logger.info("    X (Twitter) : %s", "✅ 발행" if signal.publish_x else "❌ 스킵")
        logger.info("    TG Free     : %s", "✅ 발행" if signal.publish_tg_free else "❌ 스킵")
        logger.info("    TG Paid     : %s", "✅ 발행" if signal.publish_tg_paid else "❌ 스킵")
        logger.info("  상위 뉴스 제목:")
        for j, title in enumerate(signal.top_news_titles[:3], 1):
            logger.info("    %d. %s", j, title[:70])
        if signal.top_youtube_titles:
            logger.info("  상위 YouTube 제목:")
            for j, title in enumerate(signal.top_youtube_titles[:2], 1):
                logger.info("    %d. %s", j, title[:60])
