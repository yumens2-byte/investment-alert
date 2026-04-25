"""
제목: investment-alert 파이프라인 엔트리포인트
내용: MacroNewsLayer(감지) → AlertEngine(Signal 생성) → Publisher(발행) →
      AlertStore(쿨다운 설정 + 발행결과 기록) 전체 파이프라인을 실행합니다.

주요 함수:
  - main(): 전체 파이프라인 실행 (GitHub Actions에서 직접 호출)
"""

from __future__ import annotations

import sys

from collectors.news_collector import NewsCollector
from collectors.youtube_collector import YouTubeCollector
from core.data_logger import DataLogger
from core.logger import configure_root_logger, get_logger
from db.alert_store import AlertStore
from detection.alert_engine import AlertEngine
from detection.macro_news_layer import MacroNewsLayer
from publishers.alert_formatter import AlertFormatter
from publishers.telegram_publisher import TelegramPublisher
from publishers.x_publisher import XPublisher

VERSION = "1.0.0"


def main() -> None:
    """
    제목: investment-alert 파이프라인 메인
    내용: 전체 Alert 파이프라인을 순서대로 실행합니다.
          각 단계의 실패는 격리하여 후속 단계에 영향을 주지 않습니다.

    처리 플로우:
      1. 의존성 초기화 (Collector, Store, Publisher)
      2. MacroNewsLayer.detect() — 뉴스/YouTube 감지
      3. AlertEngine.process() — AlertSignal 생성 + 감사로그 저장
      4. NONE 또는 쿨다운 → 발행 스킵
      5. L1/L2/L3 → 채널별 발행 (X / TG Free / TG Paid)
      6. AlertStore.update_publish_result() — 발행 결과 기록
      7. AlertStore.set_cooldown() — 쿨다운 설정
    """
    from datetime import datetime, UTC
    _log_ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    _log_file = f"logs/run_alert_{_log_ts}.log"
      
     from datetime import datetime, UTC
    _log_ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    _log_file = f"logs/run_alert_{_log_ts}.log"
    configure_root_logger(log_file=_log_file)
    logger = get_logger(__name__)
    logger.info(f"[run_alert] v{VERSION} 시작 — 로그파일: {_log_file}")
    # configure_root_logger(log_file=_log_file)
    # logger = get_logger(__name__)
    # logger.info(f"[run_alert] v{VERSION} 시작 — 로그파일: {_log_file}")


    # ── Step 1: 의존성 초기화 ────────────────────────────────
    alert_store = AlertStore()
    news_collector = NewsCollector()
    yt_collector = YouTubeCollector()
    macro_layer = MacroNewsLayer(
        news_collector=news_collector,
        youtube_collector=yt_collector,
    )
    alert_engine = AlertEngine(alert_store=alert_store)
    formatter = AlertFormatter()
    x_pub = XPublisher()
    tg_pub = TelegramPublisher()

    # ── Step 2: 감지 ────────────────────────────────────────
    logger.info("[run_alert] Step 2: Macro-News 감지 시작")
    result = macro_layer.detect()
    logger.info(
        f"[run_alert] 감지 결과: level={result.level}, "
        f"score={result.score:.2f}, health={result.health_score:.2f}"
    )

    # ── Step 3: AlertSignal 생성 ─────────────────────────────
    signal = alert_engine.process(result)

    # ── Step 3-LOG: 전체 수집 데이터 로그 출력 ─────────────────
    logger.info("[run_alert] Step 3-LOG: 전체 수집 데이터 로그 출력")
    data_logger = DataLogger()
    data_logger.log_all(result=result, signal=signal)

    # ── Step 4: 발행 판단 ────────────────────────────────────
    if result.level == "NONE":
        logger.info("[run_alert] NONE 레벨 — 발행 스킵")
        sys.exit(0)

    if signal.is_cooldown_active:
        logger.info(f"[run_alert] {signal.level} 쿨다운 활성 — 발행 스킵")
        sys.exit(0)

    # ── Step 5: 채널별 발행 ──────────────────────────────────
    logger.info(f"[run_alert] Step 5: {signal.level} 발행 시작")

    x_ok = tg_free_ok = tg_paid_ok = False
    x_err = tg_free_err = tg_paid_err = None

    x_msg = formatter.format_x(
        level=signal.level,
        score=signal.score,
        reasoning=signal.reasoning,
        top_news_titles=signal.top_news_titles,
    )
    tg_msg = formatter.format_tg(
        level=signal.level,
        score=signal.score,
        reasoning=signal.reasoning,
        top_news_titles=signal.top_news_titles,
        top_youtube_titles=signal.top_youtube_titles,
        health_score=signal.health_score,
        alert_id=signal.alert_id,
    )

    # X 발행 (L1만)
    if signal.publish_x:
        try:
            x_pub.publish(x_msg)
            x_ok = True
        except Exception as e:
            x_err = str(e)
            logger.error(f"[run_alert] X 발행 실패: {e}")

    # TG Free 발행 (L1/L2)
    if signal.publish_tg_free:
        try:
            tg_pub.publish_free(tg_msg)
            tg_free_ok = True
        except Exception as e:
            tg_free_err = str(e)
            logger.error(f"[run_alert] TG Free 발행 실패: {e}")

    # TG Paid 발행 (L1/L2)
    if signal.publish_tg_paid:
        try:
            tg_pub.publish_paid(tg_msg)
            tg_paid_ok = True
        except Exception as e:
            tg_paid_err = str(e)
            logger.error(f"[run_alert] TG Paid 발행 실패: {e}")

    # ── Step 6: 발행 결과 기록 ───────────────────────────────
    alert_store.update_publish_result(
        alert_id=signal.alert_id,
        x_published=x_ok,
        tg_free_published=tg_free_ok,
        tg_paid_published=tg_paid_ok,
        x_error=x_err,
        tg_free_error=tg_free_err,
        tg_paid_error=tg_paid_err,
    )

    # ── Step 7: 쿨다운 설정 ──────────────────────────────────
    if tg_free_ok or tg_paid_ok or x_ok:
        alert_store.set_cooldown(level=signal.level, alert_id=signal.alert_id)
        logger.info(f"[run_alert] {signal.level} 쿨다운 설정 완료")

    logger.info(
        f"[run_alert] 완료: level={signal.level}, "
        f"x={x_ok}, tg_free={tg_free_ok}, tg_paid={tg_paid_ok}"
    )


if __name__ == "__main__":
    main()
