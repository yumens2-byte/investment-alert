"""
제목: investment-alert 파이프라인 엔트리포인트
내용: MacroNewsLayer(감지) -> AlertEngine(Signal 생성) -> Publisher(발행) ->
      AlertStore(쿨다운 설정 + 발행결과 기록) 전체 파이프라인을 실행합니다.

주요 함수:
  - main(): 전체 파이프라인 실행 (GitHub Actions에서 직접 호출)
"""

from __future__ import annotations

import random as _random
import re as _re
import sys
import time as _time

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

VERSION = "1.2.0"


# B7 패치 (v1.2.0): top_news 정규화 hash 헬퍼
# 동일 뉴스를 다른 source가 보도하거나 약간 다른 표현으로 재게시할 때
# 안티봇 hash 충돌이 회피되지 않는 문제를 방지하기 위해 정규화 후 비교.
_NORMALIZE_PATTERN = _re.compile(r"[^a-z0-9가-힣]+")


def _normalize_for_hash(text: str) -> str:
    """
    제목: top_news 제목 정규화
    내용: 소문자화, 비문자/숫자 제거, 앞 60자 추출. 안티봇 hash 비교용.
    """
    if not text:
        return ""
    lowered = text.lower()
    cleaned = _NORMALIZE_PATTERN.sub("", lowered)
    return cleaned[:60]


def _log_preflight_warnings() -> None:
    """
    제목: 운영 전 사전 점검 경고 로그
    내용: alert 미발행의 흔한 원인(환경변수 누락/DRY_RUN)을 시작 시점에
          명시적으로 기록하여 운영자가 빠르게 원인을 파악하도록 돕습니다.
    """
    import os

    from config.settings import get_env_bool

    logger = get_logger(__name__)

    dry_run = get_env_bool("DRY_RUN", True)
    if dry_run:
        logger.warning("[run_alert] DRY_RUN=true — 실제 외부 채널 발행은 수행되지 않습니다.")

    if not os.getenv("YOUTUBE_CHANNELS", "").strip():
        logger.warning("[run_alert] YOUTUBE_CHANNELS 미설정 — YouTube 감지 레이어가 비활성화됩니다.")

    if not os.getenv("SUPABASE_URL", "").strip() or not os.getenv("SUPABASE_KEY", "").strip():
        logger.warning("[run_alert] SUPABASE 설정 미완료 — 쿨다운/감사로그 저장이 동작하지 않을 수 있습니다.")


def main() -> None:
    """
    제목: investment-alert 파이프라인 메인
    내용: 전체 Alert 파이프라인을 순서대로 실행합니다.
          각 단계의 실패는 격리하여 후속 단계에 영향을 주지 않습니다.

    처리 플로우:
      1. 로거 초기화 (콘솔 + 파일 동시 출력)
      2. 의존성 초기화 (Collector, Store, Publisher)
      3. MacroNewsLayer.detect() - 뉴스/YouTube 감지
      4. AlertEngine.process() - AlertSignal 생성 + 감사로그 저장
      5. 전체 수집 데이터 로그 출력 (DataLogger)
      6. NONE 또는 쿨다운 -> 발행 스킵
      7. L1/L2/L3 -> 채널별 발행 (X / TG Free / TG Paid)
      8. AlertStore.update_publish_result() - 발행 결과 기록
      9. AlertStore.set_cooldown() - 쿨다운 설정
    """
    from datetime import UTC, datetime

    _log_ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    _log_file = f"logs/run_alert_{_log_ts}.log"
    configure_root_logger(log_file=_log_file)
    logger = get_logger(__name__)
    logger.info(f"[run_alert] v{VERSION} 시작 - 로그파일: {_log_file}")
    _log_preflight_warnings()

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
    # 운영 경고는 DataLogger 출력과 별개로 즉시 요약 로그를 남겨
    # 로그 탐색/알림 룰(예: CloudWatch, Loki)에서 누락 없이 감지되도록 한다.
    if getattr(result, "ops_warnings", None):
        logger.info("[run_alert] 운영 경고 감지: %s", " | ".join(result.ops_warnings[:2]))

    # ── Step 3: AlertSignal 생성 ─────────────────────────────
    signal = alert_engine.process(result)

    # ── Step 4: 전체 수집 데이터 로그 출력 ───────────────────
    logger.info("[run_alert] Step 4-LOG: 전체 수집 데이터 로그 출력")
    data_logger = DataLogger()
    data_logger.log_all(result=result, signal=signal)

    # ── Step 5: 발행 판단 ────────────────────────────────────
    if result.level == "NONE":
        logger.info("[run_alert] NONE 레벨 - 발행 스킵")
        sys.exit(0)

    if signal.is_cooldown_active:
        logger.info(f"[run_alert] {signal.level} 쿨다운 활성 - 발행 스킵")
        sys.exit(0)

    # ── Step 6: 채널별 발행 ──────────────────────────────────
    logger.info(f"[run_alert] Step 6: {signal.level} 발행 시작")

    x_ok = tg_free_ok = tg_paid_ok = tg_internal_ok = False
    x_err = tg_free_err = tg_paid_err = tg_internal_err = None

    # B3 패치 (v1.1.0): 채널 간 jitter — 안티봇 정책
    def _publish_jitter() -> None:
        """채널 간 발행 시각 분산. X 안티봇 + 중복발행 위험 완화."""
        delay = _random.uniform(2.0, 5.0)
        logger.debug(f"[run_alert] 채널 간 jitter sleep {delay:.2f}s")
        _time.sleep(delay)

    # B7 패치 (v1.2.0): 최근 발행과 top_news[0] 정규화 hash 충돌 검사
    # 충돌 시 AI 호출 회피 → 템플릿(다른 해시태그)로 강제 fallback.
    # 안티봇: 동일 input 뉴스가 연속 발화하면 Gemini 출력도 유사해질 위험을 차단.
    def _detect_topnews_hash_collision() -> bool:
        """현재 top_news[0]이 최근 5건 alert와 정규화 비교 시 충돌 여부."""
        if not signal.top_news_titles:
            return False
        try:
            recent = alert_store.get_recent_top_news_titles(n=5)
        except Exception as e:
            logger.warning(f"[run_alert] B7 hash 조회 실패 → 비활성 fallback: {e}")
            return False
        current = _normalize_for_hash(signal.top_news_titles[0])
        for title in recent:
            if _normalize_for_hash(title) == current:
                logger.info(
                    f"[run_alert] B7 hash 충돌 감지 → 템플릿 강제 (current='{current[:40]}')"
                )
                return True
        return False

    force_x_template = _detect_topnews_hash_collision() if signal.publish_x else False

    # B3 패치 (v1.1.0): 채널별 본문 차별화 — 안티봇 정책
    # AS-IS: tg_msg 한 번 생성 → Free/Paid 동일 본문 발행 = 안티봇 위험
    # TO-BE: 각 분기 안에서 format_tg 별도 호출 → random 셔플로 본문 차별화

    if signal.publish_x:
        x_msg = formatter.format_x(     # publish_x=True일 때만 생성 → Gemini 호출 조건부
            level=signal.level,
            score=signal.score,
            reasoning=signal.reasoning,
            top_news_titles=signal.top_news_titles,
            force_template=force_x_template,  # B7 패치 (v1.2.0)
        )
        try:
            x_pub.publish(x_msg)
            x_ok = True
        except Exception as e:
            x_err = str(e)
            logger.error(f"[run_alert] X 발행 실패: {e}")
        _publish_jitter()

    if signal.publish_tg_free:
        try:
            tg_msg_free = formatter.format_tg(
                level=signal.level,
                score=signal.score,
                reasoning=signal.reasoning,
                top_news_titles=signal.top_news_titles,
                top_youtube_titles=signal.top_youtube_titles,
                health_score=signal.health_score,
                alert_id=signal.alert_id,
            )
            tg_pub.publish_free(tg_msg_free)
            tg_free_ok = True
        except Exception as e:
            tg_free_err = str(e)
            logger.error(f"[run_alert] TG Free 발행 실패: {e}")
        _publish_jitter()

    if signal.publish_tg_paid:
        try:
            tg_msg_paid = formatter.format_tg(
                level=signal.level,
                score=signal.score,
                reasoning=signal.reasoning,
                top_news_titles=signal.top_news_titles,
                top_youtube_titles=signal.top_youtube_titles,
                health_score=signal.health_score,
                alert_id=signal.alert_id,
            )
            tg_pub.publish_paid(tg_msg_paid)
            tg_paid_ok = True
        except Exception as e:
            tg_paid_err = str(e)
            logger.error(f"[run_alert] TG Paid 발행 실패: {e}")
        _publish_jitter()

    if signal.publish_tg_internal:
        try:
            if signal.level == "SYSTEM_DEGRADED":
                internal_msg = formatter.format_degraded(
                    dq_state=signal.dq_state_dict,
                    alert_id=signal.alert_id,
                )
            else:
                # Internal은 운영자 채널 — 셔플 불필요. 기존 포맷 유지.
                internal_msg = formatter.format_internal(
                    level=signal.level,
                    score=signal.score,
                    reasoning=signal.reasoning,
                    top_news_titles=signal.top_news_titles,
                    top_youtube_titles=signal.top_youtube_titles,
                    health_score=signal.health_score,
                    alert_id=signal.alert_id,
                    playbook=None,  # Phase 2에서 playbook 주입
                )
            tg_pub.publish_internal(internal_msg)
            tg_internal_ok = True
        except Exception as e:
            tg_internal_err = str(e)
            logger.error(f"[run_alert] TG Internal 발행 실패: {e}")
        # 마지막 채널 — jitter 불필요

    # ── Step 7: 발행 결과 기록 (B5 패치 — audit fallback 분기) ───
    if signal.audit_persisted:
        alert_store.update_publish_result(
            alert_id=signal.alert_id,
            x_published=x_ok,
            tg_free_published=tg_free_ok,
            tg_paid_published=tg_paid_ok,
            x_error=x_err,
            tg_free_error=tg_free_err,
            tg_paid_error=tg_paid_err,
            tg_internal_published=tg_internal_ok,
            tg_internal_error=tg_internal_err,
        )
    else:
        # B5: save_alert 실패 → update 불가능. 발행 결과를 fallback에 추가 기록
        from core.audit_fallback import append_audit_fallback
        append_audit_fallback({
            "stage": "publish_result",
            "alert_id": signal.alert_id,
            "level": signal.level,
            "x_published": x_ok,
            "tg_free_published": tg_free_ok,
            "tg_paid_published": tg_paid_ok,
            "tg_internal_published": tg_internal_ok,
            "x_error": x_err,
            "tg_free_error": tg_free_err,
            "tg_paid_error": tg_paid_err,
            "tg_internal_error": tg_internal_err,
            "reason": "audit_fallback_due_to_save_alert_failure",
        })
        logger.warning(
            f"[run_alert] audit_persisted=False — fallback JSONL 기록 "
            f"(alert_id={signal.alert_id[:8]}, x={x_ok}, tg_free={tg_free_ok}, "
            f"tg_paid={tg_paid_ok}, tg_internal={tg_internal_ok})"
        )

    # ── Step 8: 쿨다운 설정 ──────────────────────────────────
    if tg_free_ok or tg_paid_ok or x_ok or tg_internal_ok:
        alert_store.set_cooldown(level=signal.level, alert_id=signal.alert_id)
        logger.info(f"[run_alert] {signal.level} 쿨다운 설정 완료")

    logger.info(
        f"[run_alert] 완료: level={signal.level}, "
        f"x={x_ok}, tg_free={tg_free_ok}, tg_paid={tg_paid_ok}, "
        f"tg_internal={tg_internal_ok}, audit_persisted={signal.audit_persisted}"
    )


if __name__ == "__main__":
    main()
