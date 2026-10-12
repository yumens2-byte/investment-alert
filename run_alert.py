"""
run_alert.py
=============
역할: Macro-News Alert 감지 + 발송 (canonical 단일 경로)
실행: python run_alert.py

파이프라인 (run()):
  [Pre] DRY_RUN 아니면 ET 창 판정(core.alert_window.decide_from_env) — 창 밖/휴장일이면 즉시 종료
  → MacroNewsLayer(NewsCollector, YouTubeCollector).detect()
  → AlertEngine(AlertStore).process()  (쿨다운/레벨 판정)
  → 창당 발행 claim (AlertStore.claim_window)
  → AlertFormatter → TG free/paid/internal, X 발행 (채널별 실패 격리, 채널 간 jitter)
  → AlertStore.update_publish_result / set_cooldown
  → RunRecorder 가 ia_alert_run 1행 기록, main() 이 Step Summary / GITHUB_OUTPUT / 종료코드 처리

v1.4.0 (P5 부채 정리):
  - _run_legacy 및 전용 헬퍼(_X_ALERT_*, _load/_save_x_alert_history,
    _is_x_cooldown_active, _format_x_alert_tweet, _load_prev_snapshot) 삭제
  - 레거시 구현은 git history 참조 (v1.3.0, 91ca4c6)

v1.4.0 (P1/P2/P3/P7):
  - P1: 창 판정을 core.alert_window.decide_from_env 로 위임 (NYSE 휴장일 → outcome holiday)
  - P2: should_publish 이후 발행 직전 창 claim (claim_key = "{et_date}:{target}")
        외부 공개 채널(X/tg_free/tg_paid) 대상일 때만 claim (내부 전용 L3/SYSTEM_DEGRADED 는 생략)
        already_claimed → 발행 없이 종료, error → fail-open 발행(::warning::),
        성공 채널 0(전 채널 실패/발행 중 예외) → release_claim
  - 채널 오류 문자열 TG 봇 토큰 마스킹(_redact_secrets) 후 저장/알림
  - P3: RunRecorder(ia_alert_run) outcome 11종 기록, main() 종료코드
        (all_fail/error = 1, 그 외 0), partial_fail ::warning:: + 내부 TG 실패 알림,
        update_publish_result(published_channels=...) + 실패 시 audit fallback
  - P7: DuplicateGuard 연결 (X 만 차단, DUP_OBSERVE_ONLY 기본 true = 판정·기록만)
  - 채널 간 jitter 2~5초 (DRY_RUN 생략, 두 번째 채널부터)
"""
import html
import logging
import os
import random as _random
import re
import sys
import time as _time
from datetime import UTC, datetime

from config.settings import DRY_RUN, LOG_LEVEL
from core.alert_window import WindowDecision, decide_from_env
from core.run_recorder import RunRecorder

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("run_alert")

VERSION = "1.5.0"  # Q1 소스 장애 내부 경고 / v1.4.0: P1 gate 위임 / P2 창 claim / P3 RunRecorder·종료코드 / P5 legacy 삭제 / P7 DupGuard

CHANNELS: tuple[str, ...] = ("tg_free", "tg_paid", "tg_internal", "x")
NOTIFY_OUTCOMES: frozenset[str] = frozenset({"partial_fail", "all_fail", "error"})
_TRUE_VALUES = {"1", "true", "yes", "on"}
PUBLIC_CHANNELS: tuple[str, ...] = ("tg_free", "tg_paid", "x")  # 창 claim 대상 (외부 공개)
# Q1: 소스 장애 경고 억제 (cron 21,33 × 5h ≈ 일 10회 → 소스당 1일 1회)
SOURCE_FAIL_COOLDOWN_PREFIX: str = "ops:source_fail:"
SOURCE_FAIL_COOLDOWN_MINUTES: int = 1440
_TG_BOT_TOKEN_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]+")


# ─────────────────────────────────────────────────────────────
# 내부 유틸
# ─────────────────────────────────────────────────────────────

def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in _TRUE_VALUES


def _redact_secrets(text: str) -> str:
    """오류 문자열의 TG 봇 토큰 마스킹 (requests 예외 메시지에 API URL 이 포함됨)."""
    return _TG_BOT_TOKEN_RE.sub("bot<redacted>", text)


def _publish_jitter() -> None:
    """채널 간 발행 시각 분산 (안티봇). DRY_RUN 시 생략. run_sector_alert.py 패턴 동일."""
    if DRY_RUN:
        return
    delay = _random.uniform(2.0, 5.0)
    logger.debug(f"[run_alert] 채널 간 jitter sleep {delay:.2f}s")
    _time.sleep(delay)


def _window_skip_outcome(window: WindowDecision) -> str:
    """창 밖 사유 분류: holiday_et: → holiday, 그 외(주말/창 밖/마감 임박) → outside_window."""
    return "holiday" if window.reason.startswith("holiday_et:") else "outside_window"


def _target_channels(signal, x_blocked: bool = False) -> list[str]:
    """발행 대상 채널 목록 (DuplicateGuard 로 X 가 차단되면 X 제외)."""
    flags = {
        "tg_free": signal.publish_tg_free,
        "tg_paid": signal.publish_tg_paid,
        "tg_internal": signal.publish_tg_internal,
        "x": signal.publish_x and not x_blocked,
    }
    return [ch for ch in CHANNELS if flags[ch]]


def _decide_outcome(targets: list[str], ok: list[str], x_blocked: bool) -> str:
    if len(ok) == len(targets):
        return "duplicate_blocked" if x_blocked else "published"
    return "partial_fail" if ok else "all_fail"


# ─────────────────────────────────────────────────────────────
# P2: 창 claim
# ─────────────────────────────────────────────────────────────

def _claim_window(alert_store, window: WindowDecision | None, signal, rec, summary: dict):
    """
    창당 발행 1회 claim.

    Returns:
        (proceed, claim_key): proceed=False 면 already_claimed → 발행 없이 종료.
        claim_key 는 이번 run 이 소유한 claim (release 대상), 없으면 None.
    """
    if window is None or alert_store is None or window.target is None:
        return True, None
    if not any(getattr(signal, f"publish_{ch}") for ch in PUBLIC_CHANNELS):
        # 내부 전용(L3 / SYSTEM_DEGRADED: tg_internal 만 대상) → claim 없이 발행 (PUBLISH_POLICY)
        rec.add_detail(claim_skipped="internal_only")
        return True, None
    claim_key = f"{window.et_date}:{window.target}"
    run_id = os.getenv("GITHUB_RUN_ID", "local")
    claim = alert_store.claim_window(claim_key, signal.alert_id, run_id)
    summary["claim_key"] = claim_key
    if claim == "already_claimed":
        logger.info(f"[run_alert] 창 이미 발행됨 — 스킵: claim_key={claim_key}")
        rec.finish("claimed_already", claim_key=claim_key)
        return False, None
    if claim == "error":
        logger.warning(f"[run_alert] claim 저장소 오류 — fail-open 발행 진행: {claim_key}")
        print(f"::warning::window claim 저장소 오류 — fail-open 발행 진행: {claim_key}")
        summary["claim_error"] = True
        rec.add_detail(claim_error=True, claim_key=claim_key)
        return True, None
    rec.add_detail(claim_key=claim_key)
    return True, claim_key


# ─────────────────────────────────────────────────────────────
# P7: DuplicateGuard
# ─────────────────────────────────────────────────────────────

def _evaluate_dup_pre(dup_guard, alert_store, signal, result, rec):
    """X 대상일 때 테이블 프로브 후 pre-format 판정. 불가/비대상이면 None (fail-open)."""
    if dup_guard is None or not signal.publish_x or alert_store is None:
        return None
    probe_err = alert_store.probe_duplicate_guard_tables()
    if probe_err:
        logger.warning(f"[run_alert] DuplicateGuard 테이블 접근 불가 — fail-open 통과: {probe_err}")
        print(f"::warning::DuplicateGuard unavailable (fail-open): {probe_err[:120]}")
        rec.add_detail(dup_guard="unavailable", dup_guard_error=probe_err)
        return None
    decision = dup_guard.evaluate_pre_format(signal, result)
    rec.add_detail(dup_guard="pre:" + decision.action)
    if decision.action == "guard_unavailable":
        logger.warning(f"[run_alert] DuplicateGuard pre 판정 불가(fail-open): {decision.reason}")
        print(f"::warning::DuplicateGuard {decision.reason}")
    return decision


def _notify_dup_suppressed(telegram, signal, decision) -> None:
    """X 중복 억제 운영 알림 (내부 TG). 실패는 삼키고 published 집계에 포함하지 않는다."""
    try:
        telegram.publish_internal(
            f"<b>[X 중복 억제]</b> {html.escape(signal.alert_id[:8])} "
            f"level={html.escape(str(signal.level))}\n"
            f"reason={html.escape(decision.reason)} "
            f"prev={html.escape(str(decision.previous_alert_id or '-'))}"
        )
    except Exception as exc:
        logger.warning(f"[run_alert] X 중복 억제 내부 알림 실패(무시): {type(exc).__name__}: {exc}")


# ─────────────────────────────────────────────────────────────
# 발행
# ─────────────────────────────────────────────────────────────

def _publish_channels(signal, result, formatter, telegram, x_publisher_cls,
                      dup_guard, dup_decision, rec, published: dict, errors: dict) -> bool:
    """
    채널별 발행 (실패 격리 + 두 번째 채널부터 jitter).
    published / errors 는 호출자 소유 dict 를 직접 갱신한다 (중도 예외 시에도 성공 채널 판별용).

    Returns:
        x_blocked: DuplicateGuard 로 X 발행이 차단되었으면 True
    """
    sent_any = [False]
    top_news = signal.top_news_titles
    top_youtube = signal.top_youtube_titles

    tg_text = formatter.format_tg(
        signal.level, signal.score, signal.reasoning, top_news, top_youtube,
        signal.health_score, signal.alert_id,
    )
    internal_text = (
        formatter.format_degraded(signal.dq_state_dict, signal.alert_id)
        if signal.level == "SYSTEM_DEGRADED"
        else formatter.format_internal(
            signal.level, signal.score, signal.reasoning, top_news, top_youtube,
            signal.health_score, signal.alert_id,
        )
    )

    def _before_send() -> None:
        if sent_any[0]:
            _publish_jitter()
        sent_any[0] = True

    def _publish(channel: str, operation):
        _before_send()
        try:
            value = operation()
            published[channel] = True
            return value
        except Exception as exc:
            errors[channel] = _redact_secrets(f"{type(exc).__name__}: {exc}")[:500]
            logger.error(f"[run_alert] {channel} 발행 실패 (다른 채널 계속): {errors[channel]}")
            return None

    if signal.publish_tg_free:
        _publish("tg_free", lambda: telegram.publish_free(tg_text))
    if signal.publish_tg_paid:
        _publish("tg_paid", lambda: telegram.publish_paid(tg_text))
    if signal.publish_tg_internal:
        _publish("tg_internal", lambda: telegram.publish_internal(internal_text))

    x_blocked = False
    if signal.publish_x:
        x_text = formatter.format_x(
            signal.level, signal.score, signal.reasoning, top_news,
            force_template=DRY_RUN,
        )
        decision = dup_decision
        if decision is not None and not decision.suppress_x:
            post = dup_guard.evaluate_post_format(signal, x_text, decision.topic_key)
            if post.suppress_x:
                decision = post
        blocked = decision is not None and decision.suppress_x
        observe_only = _env_bool("DUP_OBSERVE_ONLY", True)

        if blocked and not observe_only:
            x_blocked = True
            logger.info(
                f"[run_alert] X 중복 억제: {decision.reason} prev={decision.previous_alert_id}"
            )
            _before_send()
            _notify_dup_suppressed(telegram, signal, decision)
        else:
            if blocked:
                logger.info(f"[run_alert] (observe) X 중복 판정이나 발행 진행: {decision.reason}")
            tweet_id = _publish("x", lambda: x_publisher_cls(dry_run=DRY_RUN).publish(x_text))
            if published["x"] and decision is not None:
                dup_guard.record_x_fingerprint(
                    signal.alert_id, decision.topic_key, x_text, tweet_id,
                )

        if decision is not None:
            dup_guard.record_topic_observation(
                signal, result, decision, x_published=published["x"],
            )
            rec.add_detail(
                dup_action=decision.action,
                dup_reason=decision.reason,
                dup_observe_only=observe_only,
                dup_would_block=blocked,
                dup_similarity=decision.similarity_score,
                dup_previous_alert_id=decision.previous_alert_id,
            )

    return x_blocked


def _record_publish_result(alert_store, signal, published: dict, errors: dict,
                           ok: list[str]) -> None:
    """ia_alert_history 발행 결과 반영. 실패(False) 시 로컬 audit fallback (raise 없음)."""
    updated = alert_store.update_publish_result(
        signal.alert_id,
        x_published=published["x"],
        tg_free_published=published["tg_free"],
        tg_paid_published=published["tg_paid"],
        tg_internal_published=published["tg_internal"],
        x_error=errors.get("x"),
        tg_free_error=errors.get("tg_free"),
        tg_paid_error=errors.get("tg_paid"),
        tg_internal_error=errors.get("tg_internal"),
        published_channels=ok,
    )
    if updated is False:
        from core.audit_fallback import append_audit_fallback
        append_audit_fallback({
            "stage": "update_publish_result",
            "alert_id": signal.alert_id,
            "level": signal.level,
            "score": signal.score,
            "published": dict(published),
            "published_channels": ok,
            "errors": dict(errors),
            "reason": "update_publish_result_returned_false",
        })


# ─────────────────────────────────────────────────────────────
# 메인 실행
# ─────────────────────────────────────────────────────────────

def _notify_source_failures(alert_store, result) -> None:
    """
    제목: 일부 뉴스 소스 장애 내부 TG 경고 (Q1, Q-D5)
    내용: SYSTEM_DEGRADED 와 별개의 운영 경고. 소스별 ia_cooldown_state 키
          'ops:source_fail:<소스>' 로 SOURCE_FAIL_COOLDOWN_MINUTES 동안 재경고 억제.
          DRY_RUN / alert_store 없음 → 생략. 실패는 삼킨다 (파이프라인·종료코드 불변).
    """
    # 리뷰 m2: collect() 전체 예외(news_collector)는 SYSTEM_DEGRADED 경보 대상 — 중복 경고 제외
    failed = [n for n in (getattr(result, "failed_sources", None) or []) if n != "news_collector"]
    if not failed:
        return
    if DRY_RUN or alert_store is None:
        logger.info(f"[run_alert] 소스 장애 경고 생략(DRY_RUN/저장소 없음): {failed}")
        return
    try:
        fresh = [
            n for n in failed
            if not alert_store.is_cooldown_active(f"{SOURCE_FAIL_COOLDOWN_PREFIX}{n}")
        ]
        if not fresh:
            logger.info(f"[run_alert] 소스 장애 경고 억제(쿨다운): {failed}")
            return
        by_name = {
            r.get("name"): r for r in (getattr(result, "news_source_results", None) or [])
            if isinstance(r, dict)
        }
        # 소스 단위 성공률 우선 (v1 DQ 는 수집기 2키 — 리뷰 M3), 없으면 DQ 값
        rate = getattr(result, "source_level_success_rate", None)
        if rate is None:
            rate = getattr(getattr(result, "dq_state", None), "source_success_rate", None)
        rate_text = f"{rate:.2f}" if isinstance(rate, int | float) else "-"
        lines = []
        for n in fresh:
            info = by_name.get(n, {})
            tier = html.escape(str(info.get("tier", "-")))
            reason = html.escape(str(info.get("error", "collect_failed")))
            lines.append(f"- {html.escape(str(n))} (Tier {tier}): {reason}\n")
        text = (
            "<b>[run_alert] 수집 소스 장애</b>\n"
            + "".join(lines)
            + f"success_rate={rate_text} level={html.escape(str(result.level))}"
        )
        from publishers.telegram_publisher import TelegramPublisher
        TelegramPublisher(dry_run=DRY_RUN).publish_internal(text[:3500])
        for n in fresh:
            alert_store.set_cooldown(
                f"{SOURCE_FAIL_COOLDOWN_PREFIX}{n}", "source_fail",
                minutes=SOURCE_FAIL_COOLDOWN_MINUTES,
            )
        logger.warning(f"[run_alert] 소스 장애 내부 경고 발송: {fresh}")
    except Exception as e:
        logger.warning(
            f"[run_alert] 소스 장애 경고 발송 실패(무시): "
            f"{_redact_secrets(f'{type(e).__name__}: {e}')[:300]}"
        )


def _run_pipeline(alert_store, rec: RunRecorder) -> dict:
    """창 판정 → 감지 → claim → 발행 → 기록. outcome 은 rec.finish 로 확정한다."""
    window: WindowDecision | None = None
    if DRY_RUN:
        logger.info("[run_alert] DRY_RUN — 시간 윈도우 체크 생략, 강제 실행")
    else:
        window = decide_from_env()  # margin 0: gate 이후 재판정
        if not window.inside:
            outcome = _window_skip_outcome(window)
            logger.info(f"[run_alert] 시간 윈도우 아님({outcome}) — 스킵: {window.reason}")
            rec.finish(outcome, window_reason=window.reason)
            if os.getenv("GITHUB_EVENT_NAME") == "schedule" and outcome == "outside_window":
                print(f"::warning::gate 통과 후 창 이탈 — {window.reason}")
            return {"alerts_detected": 0, "alerts_sent": 0, "reason": outcome}
        logger.info(f"[run_alert] 창 진입: {window.reason}")
        rec.add_detail(window_reason=window.reason)

    from collectors.news_collector import NewsCollector
    from collectors.youtube_collector import YouTubeCollector
    from detection.alert_engine import AlertEngine
    from detection.duplicate_guard import DuplicateGuard
    from detection.macro_news_layer import MacroNewsLayer
    from publishers.alert_formatter import AlertFormatter
    from publishers.telegram_publisher import TelegramPublisher
    from publishers.x_publisher import XPublisher

    class _DryRunDQStore:
        """Avoid external persistence and false error logs during a dry run."""

        @staticmethod
        def save_dq_state(_state):
            return None

    layer = MacroNewsLayer(
        news_collector=NewsCollector(),
        youtube_collector=YouTubeCollector(),
        dq_store=_DryRunDQStore() if DRY_RUN else None,  # type: ignore[arg-type]
    )
    result = layer.detect()
    signal = AlertEngine(alert_store=alert_store).process(result)

    summary = {
        "alerts_detected": int(result.level != "NONE"),
        "alerts_sent": 0,
        "level": result.level,
        "score": result.score,
        "news_events": len(result.news_events),
        "youtube_events": len(result.youtube_events),
        "youtube_summaries": len(result.youtube_summaries),
        "youtube_comparisons": len(result.youtube_comparisons),
        "timestamp": datetime.now(UTC).isoformat(),
    }
    rec.set(
        level=result.level,
        score=result.score,
        alert_id=signal.alert_id,
        news_events=len(result.news_events),
        youtube_events=len(result.youtube_events),
    )
    # Q1: 소스별 수집 결과 기록 + 일부 소스 장애 내부 경고 (모든 outcome 공통)
    rec.add_detail(
        source_results=list(result.news_source_results or []),
        failed_sources=list(result.failed_sources or []),
    )
    _notify_source_failures(alert_store, result)
    # Q2: 판정 정책 + 비활성 정책 shadow 기록 (컬럼 추가 없이 detail 만, 모든 outcome 공통)
    rec.add_detail(
        detection_policy=getattr(result, "active_policy", "v1"),
        shadow=getattr(result, "shadow", None),
    )

    if result.level == "NONE" or not signal.should_publish:
        logger.info(
            "[run_alert] Alert 발행 없음 — level=%s news=%d youtube=%d summaries=%d comparisons=%d",
            result.level,
            len(result.news_events),
            len(result.youtube_events),
            len(result.youtube_summaries),
            len(result.youtube_comparisons),
        )
        if result.level == "NONE":
            rec.finish("level_none")
        elif signal.suppressed_reason:
            rec.finish(signal.suppressed_reason)
        else:
            rec.finish("level_none", note="no_target_channel")
        return summary

    proceed, claim_key = _claim_window(alert_store, window, signal, rec, summary)
    if not proceed:
        return summary

    published = {"x": False, "tg_free": False, "tg_paid": False, "tg_internal": False}
    errors: dict[str, str] = {}
    try:
        dup_guard = DuplicateGuard(alert_store=alert_store) if alert_store is not None else None
        dup_decision = _evaluate_dup_pre(dup_guard, alert_store, signal, result, rec)
        x_blocked = _publish_channels(
            signal, result, AlertFormatter(), TelegramPublisher(dry_run=DRY_RUN), XPublisher,
            dup_guard, dup_decision, rec, published, errors,
        )
    finally:
        # 성공 채널 0 (전 채널 실패 또는 발행 전/중 예외) → claim 해제, 2차 슬롯 재시도 허용
        if claim_key is not None and alert_store is not None and not any(published.values()):
            alert_store.release_claim(claim_key, os.getenv("GITHUB_RUN_ID", "local"))

    sent_count = sum(published.values())
    targets = _target_channels(signal, x_blocked=x_blocked)
    ok = [ch for ch in targets if published[ch]]
    outcome = _decide_outcome(targets, ok, x_blocked)
    summary["alerts_sent"] = sent_count
    summary["published"] = published
    summary["errors"] = errors
    summary["targets"] = targets
    rec.set(targets=targets, published_channels=ok, errors=errors)
    rec.finish(outcome)

    if alert_store is not None:
        _record_publish_result(alert_store, signal, published, errors, ok)
        if sent_count:
            alert_store.set_cooldown(signal.level, signal.alert_id)

    logger.info(
        "[run_alert] 완료 — outcome=%s level=%s detected=%d sent=%d summaries=%d comparisons=%d",
        outcome,
        result.level,
        summary["alerts_detected"],
        sent_count,
        summary["youtube_summaries"],
        summary["youtube_comparisons"],
    )
    return summary


def run() -> dict:
    """Canonical Macro-News pipeline (collectors/detection stack).

    RunRecorder 로 ia_alert_run 1행을 기록하고 summary 에 outcome / exit_code 를 담아 반환한다.
    파이프라인 예외는 RunRecorder 가 error 로 기록한 뒤 재전파한다 (main 이 exit 1).
    """
    logger.info("=" * 50)
    logger.info(f"[run_alert] canonical Macro-News 시작 | DRY_RUN={DRY_RUN}")
    logger.info("=" * 50)

    alert_store = None
    if not DRY_RUN:
        from db.alert_store import AlertStore
        alert_store = AlertStore()

    with RunRecorder(alert_store=alert_store, dry_run=DRY_RUN) as rec:
        summary = _run_pipeline(alert_store, rec)
    summary["outcome"] = rec.row["outcome"]
    summary["exit_code"] = rec.exit_code
    return summary


# ─────────────────────────────────────────────────────────────
# GitHub Actions 출력 / 실패 알림
# ─────────────────────────────────────────────────────────────

def _write_step_summary(summary: dict) -> None:
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if not path:
        return
    pub = summary.get("published") or {}
    rows = [
        ("outcome", summary.get("outcome")),
        ("level", summary.get("level", "-")),
        ("score", summary.get("score", "-")),
        ("alerts_sent", summary.get("alerts_sent", 0)),
        ("channels", ", ".join(k for k, v in pub.items() if v) or "-"),
        ("errors", ", ".join((summary.get("errors") or {}).keys()) or "-"),
        ("dry_run", DRY_RUN),
    ]
    md = "### run_alert 결과\n\n| 항목 | 값 |\n|---|---|\n"
    md += "".join(f"| {k} | {v} |\n" for k, v in rows)
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(md)
    except OSError as e:
        logger.warning(f"[run_alert] STEP_SUMMARY 쓰기 실패: {e}")


def _write_github_output(summary: dict) -> None:
    path = os.getenv("GITHUB_OUTPUT")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"result={summary.get('outcome', 'error')}\n")
            f.write(f"alerts_sent={summary.get('alerts_sent', 0)}\n")
    except OSError as e:
        logger.warning(f"[run_alert] GITHUB_OUTPUT 쓰기 실패: {e}")


def _notify_internal_failure(summary: dict) -> None:
    """partial_fail / all_fail / error 내부 TG 알림. 알림 실패는 삼킨다 (종료코드 불변)."""
    try:
        from publishers.telegram_publisher import TelegramPublisher
        run_url = (
            f"{os.getenv('GITHUB_SERVER_URL', '')}/{os.getenv('GITHUB_REPOSITORY', '')}"
            f"/actions/runs/{os.getenv('GITHUB_RUN_ID', '')}"
        )
        errs = summary.get("errors") or {}
        text = (
            f"<b>[run_alert] {html.escape(str(summary.get('outcome')))}</b>\n"
            f"level={html.escape(str(summary.get('level', '-')))} "
            f"sent={summary.get('alerts_sent', 0)}\n"
            + "".join(
                f"- {html.escape(str(k))}: {html.escape(_redact_secrets(str(v))[:200])}\n"
                for k, v in errs.items()
            )
            + html.escape(run_url)
        )
        TelegramPublisher(dry_run=DRY_RUN).publish_internal(text[:3500])
    except Exception as e:
        logger.warning(f"[run_alert] 내부 실패 알림 발송 실패(무시): {type(e).__name__}: {e}")


def main() -> None:
    logger.info(f"[run_alert] v{VERSION} 시작")
    try:
        summary = run()
    except Exception as e:
        logger.critical(f"[run_alert] 예외: {e}", exc_info=True)
        summary = {"outcome": "error", "exit_code": 1,
                   "errors": {"run": _redact_secrets(str(e))[:300]}}
    _write_step_summary(summary)
    _write_github_output(summary)
    outcome = summary.get("outcome")
    if outcome == "partial_fail":
        print(f"::warning title=alert partial_fail::errors={list(summary.get('errors') or {})}")
    if outcome in NOTIFY_OUTCOMES:
        _notify_internal_failure(summary)
    sys.exit(summary.get("exit_code", 1))


if __name__ == "__main__":
    main()
