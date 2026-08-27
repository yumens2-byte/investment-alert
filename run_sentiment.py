"""
제목: EDT 심리지표 파이프라인 엔트리포인트 (v1.0.0)
내용: SentimentCollector(수집) → SentimentEngine(E/D/T 산출) →
      SentimentStore(적재) → SentimentFormatter(2종 랜덤 포맷) →
      XPublisher / TelegramPublisher(발행) → 발행 플래그 기록.

처리 플로우:
  1. 로거 + 사전 점검 경고
  2. 휴장일/주말 체크 → 조기 종료 (자원 절약)
  3. 수집 (4소스, 개별 실패 격리)
  4. T축 룩백 조회 (콜드스타트 시 None)
  5. E/D/T 산출 — 결측 2개 이상이면 DEGRADED
  6. Supabase upsert (발행 전 저장 — 발행-기록 짝 규약)
  7. DEGRADED → 발행 스킵 (default-deny) / 정상 → X 발행(jitter) + TG Internal
  8. 발행 성공 시 published 플래그 기록

안전 규약:
  - X 쓰기 무재시도 (이중 발행 방지)
  - DRY_RUN 판정: settings.DRY_RUN (get_env_bool "true" 기본)
  - SENTIMENT_ENABLED=false 시 즉시 종료 (긴급 정지 스위치)
"""

from __future__ import annotations

import random as _random
import sys
import time as _time
from datetime import UTC, datetime

from collectors.sentiment_collector import SentimentCollector
from config.market_calendar import PROFILE_HOLIDAY, get_market_profile
from config.sentiment_settings import T_LOOKBACK_DAYS
from config.settings import DRY_RUN, get_env_bool
from core.logger import configure_root_logger, get_logger
from db.sentiment_store import SentimentStore
from detection.sentiment_engine import SentimentEngine, SentimentResult
from publishers.sentiment_formatter import SentimentFormatter
from publishers.telegram_publisher import TelegramPublisher
from publishers.x_publisher import XPublisher

VERSION = "1.0.0"


def _log_preflight_warnings(logger) -> None:
    """제목: 사전 점검 경고 로그"""
    if DRY_RUN:
        logger.info("[run_sentiment] DRY_RUN=true — 모의 실행 (발행 없음)")
    else:
        logger.info("[run_sentiment] DRY_RUN=false — 실발행 모드")


def _publish_jitter(logger) -> None:
    """제목: 발행 직전 랜덤 지연 (anti-bot — run_sector_alert 동일 패턴)"""
    delay = _random.uniform(2.0, 8.0)
    logger.debug(f"[run_sentiment] 발행 jitter sleep {delay:.2f}s")
    _time.sleep(delay)


def _resolve_score_date(collected: dict[str, dict | None]) -> str:
    """
    제목: 기준일 결정
    내용: 시장 지표(vix_ratio → breadth → pcr) 기준일 우선 사용.
          전부 결측이면 UTC 오늘 (DEGRADED 경로 — 기록용).
    """
    for key in ("vix_ratio", "breadth", "pcr"):
        item = collected.get(key)
        if item and item.get("date"):
            return str(item["date"])
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _build_row(result: SentimentResult, score_date: str) -> dict:
    """제목: ia_sentiment_daily upsert row 구성 (원값+점수 전체 적재 — 백테스트용)"""

    def _raw_value(key: str) -> float | None:
        item = result.raw.get(key)
        return item.get("value") if item else None

    def _raw_date(key: str) -> str | None:
        item = result.raw.get(key)
        return item.get("date") if item else None

    vix_item = result.raw.get("vix_ratio") or {}
    return {
        "score_date": score_date,
        "vix_ratio": _raw_value("vix_ratio"),
        "vix_close": vix_item.get("vix"),
        "vix3m_close": vix_item.get("vix3m"),
        "vix_ratio_score": result.scores.get("vix_ratio"),
        "pcr_equity": _raw_value("pcr"),
        "pcr_score": result.scores.get("pcr"),
        "pcr_date": _raw_date("pcr"),
        "hy_oas_bp": _raw_value("hy_oas"),
        "hy_oas_score": result.scores.get("hy_oas"),
        "hy_oas_date": _raw_date("hy_oas"),
        "breadth_rel_pct": _raw_value("breadth"),
        "breadth_score": result.scores.get("breadth"),
        "crypto_fg": _raw_value("crypto_fg"),
        "crypto_fg_score": result.scores.get("crypto_fg"),
        "e_score": result.e_score,
        "fast_fear": result.fast_fear,
        "slow_fear": result.slow_fear,
        "d_score": result.d_score,
        "t_score": result.t_score,
        "trend": result.trend,
        "state_label": result.state_label,
        "level": result.level,
        "missing_count": len(result.missing),
    }


def main() -> None:
    """
    제목: EDT 심리지표 파이프라인 메인
    내용: 휴장 스킵 → 수집 → 산출 → 적재 → 발행 → 기록. 단계 실패 격리.
    """
    _log_ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    _log_file = f"logs/run_sentiment_{_log_ts}.log"
    configure_root_logger(log_file=_log_file)
    logger = get_logger(__name__)
    logger.info(f"[run_sentiment] v{VERSION} 시작 - 로그파일: {_log_file}")
    _log_preflight_warnings(logger)

    # Step 0: 긴급 정지 스위치
    if not get_env_bool("SENTIMENT_ENABLED", default=True):
        logger.info("[run_sentiment] SENTIMENT_ENABLED=false — 파이프라인 스킵")
        sys.exit(0)

    # Step 1: 휴장일 체크 (자원 절약 — run_sector_alert 동일)
    profile = get_market_profile()
    if profile == PROFILE_HOLIDAY:
        logger.info(f"[run_sentiment] 휴장일/주말 (profile={profile}) — 파이프라인 스킵")
        sys.exit(0)

    # Step 2: 의존성 초기화
    collector = SentimentCollector()
    engine = SentimentEngine()
    store = SentimentStore()
    formatter = SentimentFormatter()

    # Step 3: 수집
    logger.info("[run_sentiment] Step 3: 지표 수집")
    collected = collector.collect_all()
    score_date = _resolve_score_date(collected)
    logger.info(f"[run_sentiment] 기준일: {score_date}")

    # Step 4: T축 룩백 (실패/콜드스타트 → None, 파이프라인 계속)
    prev_e = store.fetch_e_score_days_ago(T_LOOKBACK_DAYS, today=score_date)

    # Step 5: 산출
    result = engine.compute(collected, prev_e_score=prev_e)

    # Step 6: 적재 (발행 전 저장 — 발행-기록 짝 규약)
    row = _build_row(result, score_date)
    if not store.upsert_daily(row):
        logger.warning("[run_sentiment] Supabase 적재 실패 — 발행은 계속 진행")

    # Step 7: DEGRADED → 발행 스킵 (default-deny)
    if result.is_degraded:
        logger.warning(
            f"[run_sentiment] DEGRADED (결측 {len(result.missing)}건: {result.missing}) "
            f"— 발행 스킵 (default-deny)"
        )
        sys.exit(0)

    # Step 8: X 발행 (2종 랜덤 포맷, 무재시도)
    x_text = formatter.format_x_random(result, score_date)
    logger.info(f"[run_sentiment] X 발행 텍스트 ({len(x_text)}자):\n{x_text}")
    _publish_jitter(logger)
    try:
        x_pub = XPublisher()
        x_result = x_pub.publish(x_text)
        logger.info(f"[run_sentiment] X 발행 결과: {x_result}")
        if not DRY_RUN:
            store.mark_published(score_date, "x")
    except Exception as e:
        # X 쓰기 무재시도 규약 — 실패 로그만 남기고 계속
        logger.warning(f"[run_sentiment] X 발행 실패 (무재시도): {type(e).__name__}: {e}")

    # Step 9: TG Internal 검수 사본
    tg_text = formatter.format_tg_internal(result, score_date)
    _publish_jitter(logger)
    try:
        tg_pub = TelegramPublisher()
        tg_result = tg_pub.publish_internal(tg_text)
        logger.info(f"[run_sentiment] TG Internal 발행 결과: {tg_result}")
        if not DRY_RUN:
            store.mark_published(score_date, "tg_internal")
    except Exception as e:
        logger.warning(
            f"[run_sentiment] TG Internal 발행 실패: {type(e).__name__}: {e}"
        )

    logger.info(f"[run_sentiment] v{VERSION} 완료")


if __name__ == "__main__":
    main()
