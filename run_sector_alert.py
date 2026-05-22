"""
제목: Sector Flow Alert 파이프라인 엔트리포인트
내용: SectorCollector(수집) → SectorFlowStore(적재) → SectorFlowLayer(감지) →
      SectorAlertEngine(Signal 생성) → Publisher(발행) → AlertStore(쿨다운).

      v1.1.0: SECTOR_KIND_TONE_ENABLED=true 시 친근 톤(F감성) 1순위 시도.
              sector_formatter_kind 모듈로 Gemini Flash-Lite 생성.
              실패 시 기존 sector_formatter로 graceful fallback.
              X 발행 없음 (TG Free/Paid/Internal 3채널만).
              Internal은 운영자 디버깅 친화로 기존 톤 유지.

처리 플로우:
  1. 로거 + 사전 점검 경고
  2. 휴장일/주말 체크 → 조기 종료
  3. 의존성 초기화
  4. 데이터 수집 (Yahoo Finance API)
  5. 적재 (ia_sector_flow_daily upsert)
  6. 변화 감지 (5일 spread 계산)
  7. SectorSignal 생성 (SHADOW_MODE 분기)
  8. 채널별 발행 (jitter 포함) — Free/Paid는 KIND 톤 1순위 + fallback
  9. AlertStore 발행 결과 기록 + 쿨다운 설정

주요 함수:
  - main(): GitHub Actions에서 호출되는 메인 엔트리포인트
"""

from __future__ import annotations

import os
import random as _random
import sys
import time as _time
from datetime import UTC, datetime, timedelta

from collectors.sector_collector import SectorCollector
from config.market_calendar import PROFILE_HOLIDAY, get_market_profile
from config.settings import get_env_bool
from core.logger import configure_root_logger, get_logger
from db.alert_store import AlertStore
from db.sector_flow_store import SectorFlowStore
from detection.sector_alert_engine import COOLDOWN_LEVEL_PREFIX, SectorAlertEngine
from detection.sector_flow_layer import SectorFlowLayer
from publishers.sector_formatter import SectorFormatter
from publishers.telegram_publisher import TelegramPublisher

VERSION = "1.1.0"


def _log_preflight_warnings() -> None:
    """제목: 사전 점검 경고 로그"""
    logger = get_logger(__name__)

    if get_env_bool("DRY_RUN", True):
        logger.warning(
            "[run_sector_alert] DRY_RUN=true — 실제 외부 채널 발행은 수행되지 않습니다."
        )

    if get_env_bool("SECTOR_SHADOW_MODE", True):
        logger.warning(
            "[run_sector_alert] SECTOR_SHADOW_MODE=true — "
            "TG Free/Paid 발행 차단. TG Internal만 발행 대상."
        )

    if not os.getenv("SUPABASE_URL", "").strip() or not os.getenv(
        "SUPABASE_KEY", ""
    ).strip():
        logger.warning(
            "[run_sector_alert] SUPABASE 미설정 — "
            "수집/감지는 진행, 적재/쿨다운 저장 동작 안 함."
        )


def _publish_jitter(logger) -> None:
    """제목: 채널 간 발행 시각 분산 (안티봇 + 중복 발행 위험 완화)"""
    delay = _random.uniform(2.0, 5.0)
    logger.debug(f"[run_sector_alert] 채널 간 jitter sleep {delay:.2f}s")
    _time.sleep(delay)


def main() -> None:
    """
    제목: Sector Flow Alert 파이프라인 메인
    내용: 휴장일 스킵 → 수집 → 적재 → 감지 → Signal → 발행 → 쿨다운.
          각 단계 실패는 격리하여 후속 단계에 영향 최소화.
    """
    _log_ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    _log_file = f"logs/run_sector_alert_{_log_ts}.log"
    configure_root_logger(log_file=_log_file)
    logger = get_logger(__name__)
    logger.info(f"[run_sector_alert] v{VERSION} 시작 - 로그파일: {_log_file}")
    _log_preflight_warnings()

    # Step 1: 휴장일 체크 (가장 먼저, 자원 절약)
    profile = get_market_profile()
    if profile == PROFILE_HOLIDAY:
        logger.info(
            f"[run_sector_alert] 휴장일/주말 (profile={profile}) — 파이프라인 스킵"
        )
        sys.exit(0)

    # Step 2: 의존성 초기화
    alert_store = AlertStore()
    sector_store = SectorFlowStore()
    collector = SectorCollector()
    layer = SectorFlowLayer(sector_store=sector_store)
    engine = SectorAlertEngine(alert_store=alert_store)
    formatter = SectorFormatter()
    tg_pub = TelegramPublisher()

    # Step 3: 데이터 수집
    logger.info("[run_sector_alert] Step 3: Yahoo Finance 수집")
    ticker_data = collector.collect_sector_changes()

    # 최신 1일 등락률 추출 + snapshot_date 결정
    today_chg: dict[str, float | None] = {}
    snapshot_date = ""

    for ticker, rows in ticker_data.items():
        if rows:
            today_chg[ticker] = rows[0].get("chg_pct")
            # snapshot_date는 가장 최신 날짜로 통일
            row_date = str(rows[0].get("date", ""))
            if row_date and (not snapshot_date or row_date > snapshot_date):
                snapshot_date = row_date
        else:
            today_chg[ticker] = None

    if not snapshot_date:
        snapshot_date = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
        logger.warning(
            f"[run_sector_alert] 수집 0건 — snapshot_date={snapshot_date}로 fallback"
        )

    # Step 4: 적재
    logger.info(f"[run_sector_alert] Step 4: 적재 (date={snapshot_date})")
    upsert_ok = sector_store.upsert_daily_rows(
        snapshot_date=snapshot_date,
        market="US",
        ticker_chg_map=today_chg,
    )
    if not upsert_ok:
        logger.warning(
            "[run_sector_alert] 적재 실패 — 감지는 계속 진행 (DB 과거 데이터로)"
        )

    # Step 5: 변화 감지
    logger.info("[run_sector_alert] Step 5: 변화 감지")
    result = layer.detect()
    logger.info(
        f"[run_sector_alert] 감지 결과: level={result.level}, "
        f"rotation={result.rotation_type}, "
        f"5d_spread={result.spread_5d}, 1d_spread={result.spread_1d}, "
        f"rows={result.rows_used}, health={result.health_score:.2f}"
    )
    # v1.0.1: NONE 레벨일 때 사유 명시 (5일 가드 발동/임계 미달 구분)
    if result.level == "NONE":
        logger.info(f"[run_sector_alert] NONE 사유: {result.reasoning}")

    # Step 6: SectorSignal 생성
    signal = engine.process(result)

    # Step 7: 발행 판단
    if result.level == "NONE":
        logger.info("[run_sector_alert] NONE 레벨 - 발행 스킵")
        sys.exit(0)

    if signal.is_cooldown_active:
        logger.info(
            f"[run_sector_alert] {signal.level} 쿨다운 활성 - 발행 스킵"
        )
        sys.exit(0)

    if not signal.should_publish:
        logger.info(
            "[run_sector_alert] should_publish=False - 발행 스킵 "
            "(shadow_mode 또는 정책)"
        )
        sys.exit(0)

    # Step 8: 채널별 발행
    logger.info(f"[run_sector_alert] Step 8: {signal.level} 발행 시작")

    tg_free_ok = tg_paid_ok = tg_internal_ok = False
    tg_free_err = tg_paid_err = tg_internal_err = None

    if signal.publish_tg_free:
        try:
            # v1.1.0: SECTOR_KIND_TONE_ENABLED=true 시 친근 톤 1순위 시도
            msg = None
            if os.environ.get("SECTOR_KIND_TONE_ENABLED", "").lower() in (
                "true", "1", "yes",
            ):
                try:
                    from publishers.sector_formatter_kind import format_tg_free_kind
                    msg = format_tg_free_kind(signal)
                except ImportError:
                    logger.warning(
                        "[run_sector_alert] sector_formatter_kind 임포트 실패 "
                        "→ 기존 흐름"
                    )
                except Exception as e:
                    logger.warning(
                        f"[run_sector_alert] KIND_TONE Free 예외 → 기존 흐름: "
                        f"{type(e).__name__}: {e}"
                    )
            if not msg:
                msg = formatter.format_tg_free(signal)
            tg_pub.publish_free(msg)
            tg_free_ok = True
        except Exception as e:
            tg_free_err = str(e)
            logger.error(f"[run_sector_alert] TG Free 발행 실패: {e}")
        _publish_jitter(logger)

    if signal.publish_tg_paid:
        try:
            # v1.1.0: SECTOR_KIND_TONE_ENABLED=true 시 친근 톤 1순위 시도
            msg = None
            if os.environ.get("SECTOR_KIND_TONE_ENABLED", "").lower() in (
                "true", "1", "yes",
            ):
                try:
                    from publishers.sector_formatter_kind import format_tg_paid_kind
                    msg = format_tg_paid_kind(signal)
                except ImportError:
                    logger.warning(
                        "[run_sector_alert] sector_formatter_kind 임포트 실패 "
                        "→ 기존 흐름"
                    )
                except Exception as e:
                    logger.warning(
                        f"[run_sector_alert] KIND_TONE Paid 예외 → 기존 흐름: "
                        f"{type(e).__name__}: {e}"
                    )
            if not msg:
                msg = formatter.format_tg_paid(signal)
            tg_pub.publish_paid(msg)
            tg_paid_ok = True
        except Exception as e:
            tg_paid_err = str(e)
            logger.error(f"[run_sector_alert] TG Paid 발행 실패: {e}")
        _publish_jitter(logger)

    if signal.publish_tg_internal:
        try:
            msg = formatter.format_internal(signal)
            tg_pub.publish_internal(msg)
            tg_internal_ok = True
        except Exception as e:
            tg_internal_err = str(e)
            logger.error(f"[run_sector_alert] TG Internal 발행 실패: {e}")

    # Step 9: 발행 결과 기록 (B5 패치 — audit fallback 분기)
    if signal.audit_persisted:
        alert_store.update_publish_result(
            alert_id=signal.alert_id,
            x_published=False,
            tg_free_published=tg_free_ok,
            tg_paid_published=tg_paid_ok,
            x_error=None,
            tg_free_error=tg_free_err,
            tg_paid_error=tg_paid_err,
            tg_internal_published=tg_internal_ok,
            tg_internal_error=tg_internal_err,
        )
    else:
        from core.audit_fallback import append_audit_fallback
        append_audit_fallback({
            "stage": "publish_result",
            "alert_id": signal.alert_id,
            "level": signal.level,
            "rotation_type": signal.rotation_type,
            "tg_free_published": tg_free_ok,
            "tg_paid_published": tg_paid_ok,
            "tg_internal_published": tg_internal_ok,
            "tg_free_error": tg_free_err,
            "tg_paid_error": tg_paid_err,
            "tg_internal_error": tg_internal_err,
            "reason": "sector_audit_fallback_due_to_save_alert_failure",
        })
        logger.warning(
            f"[run_sector_alert] audit_persisted=False — fallback JSONL 기록 "
            f"(alert_id={signal.alert_id[:8]})"
        )

    # Step 10: 쿨다운 설정 (sector: prefix)
    if tg_free_ok or tg_paid_ok or tg_internal_ok:
        prefixed_level = f"{COOLDOWN_LEVEL_PREFIX}{signal.level}"
        alert_store.set_cooldown(
            level=prefixed_level,
            alert_id=signal.alert_id,
        )
        logger.info(f"[run_sector_alert] 쿨다운 설정 완료: {prefixed_level}")

    logger.info(
        f"[run_sector_alert] 완료: level={signal.level}, "
        f"tg_free={tg_free_ok}, tg_paid={tg_paid_ok}, "
        f"tg_internal={tg_internal_ok}, audit={signal.audit_persisted}"
    )


if __name__ == "__main__":
    main()
