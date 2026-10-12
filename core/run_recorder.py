"""
제목: run_alert 실행 결과 기록기 — ia_alert_run 1실행 1행 (fail-open)
내용: with 블록 1회 = ia_alert_run 1행. outcome 은 최초 finish() 1회만 확정한다.
      DB 기록은 AlertStore.save_run(row) 로 위임하며, 실패는 로그만 남기고 삼킨다.

주요 클래스:
  - RunRecorder: 실행 결과 누적/확정/기록

주요 상수:
  - OUTCOMES: 허용 outcome 11종 (Migration 005 CHECK 제약과 동일)
  - FAIL_EXIT_OUTCOMES: 종료코드 1 대상 outcome
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime

from core.logger import get_logger

VERSION = "1.0.0"

logger = get_logger(__name__)

OUTCOMES: frozenset[str] = frozenset({
    "outside_window",
    "holiday",
    "claimed_already",
    "level_none",
    "cooldown_level",
    "cooldown_topic",
    "duplicate_blocked",
    "published",
    "partial_fail",
    "all_fail",
    "error",
})
FAIL_EXIT_OUTCOMES: frozenset[str] = frozenset({"all_fail", "error"})


class RunRecorder:
    """
    제목: ia_alert_run 1행 기록기
    내용: set(**cols) 은 ia_alert_run 실제 컬럼만, 그 외 진단정보는 add_detail(**kw) 로 detail(JSONB)에 누적.

    책임:
      - outcome 최초 1회 확정 (미정의 값은 error + invalid_outcome)
      - with 블록 예외 시 error 기록 후 재전파
      - 종료 시 finished_at/duration_ms 산출 후 AlertStore.save_run 위임 (fail-open)
    """

    def __init__(self, alert_store: object | None = None, dry_run: bool = False) -> None:
        """
        제목: RunRecorder 초기화

        Args:
            alert_store: AlertStore 인스턴스 (None 이면 DB 미기록)
            dry_run: True 이면 DB 미기록
        """
        self._store = alert_store
        self._dry_run = dry_run
        self._t0 = time.monotonic()
        self.row: dict = {
            "gh_run_id": os.getenv("GITHUB_RUN_ID"),
            "gh_run_attempt": os.getenv("GITHUB_RUN_ATTEMPT"),
            "gh_event": os.getenv("GITHUB_EVENT_NAME"),
            "started_at": datetime.now(UTC).isoformat(),
            "dry_run": dry_run,
            "outcome": None,
            "detail": {},
        }
        logger.info(f"[RunRecorder] v{VERSION} 시작")

    def set(self, **fields: object) -> None:
        """ia_alert_run 컬럼 값 설정 (detail 외 실제 컬럼만)."""
        self.row.update(fields)

    def add_detail(self, **detail: object) -> None:
        """outcome 확정과 무관하게 detail(JSONB)만 누적."""
        self.row["detail"].update(detail)

    def finish(self, outcome: str, **detail: object) -> None:
        """
        제목: outcome 확정 (최초 1회만)
        내용: 미정의 outcome 은 error 로 치환하고 detail.invalid_outcome 에 원값 기록.
        """
        if outcome not in OUTCOMES:
            detail["invalid_outcome"] = outcome
            outcome = "error"
        if self.row["outcome"] is None:
            self.row["outcome"] = outcome
        self.row["detail"].update(detail)

    @property
    def exit_code(self) -> int:
        """all_fail / error 이면 1, 그 외 0."""
        return 1 if self.row["outcome"] in FAIL_EXIT_OUTCOMES else 0

    def __enter__(self) -> RunRecorder:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is not None:
            self.finish("error", exception=f"{exc_type.__name__}: {exc}"[:500])
        elif self.row["outcome"] is None:
            self.finish("error", exception="outcome_not_set")
        self.row["finished_at"] = datetime.now(UTC).isoformat()
        self.row["duration_ms"] = int((time.monotonic() - self._t0) * 1000)
        self._persist()
        return False  # 예외 재전파 (main 이 exit 1)

    def _persist(self) -> None:
        """AlertStore.save_run 위임. 기록 실패는 삼킨다 (fail-open)."""
        if self._dry_run or self._store is None:
            logger.info(f"[RunRecorder] DB 미기록(dry_run/store 없음): {self.row['outcome']}")
            return
        try:
            ok = self._store.save_run(self.row)  # type: ignore[union-attr]
            if not ok:
                logger.warning(f"[RunRecorder] ia_alert_run 기록 실패(무시): {self.row['outcome']}")
        except Exception as e:
            logger.warning(f"[RunRecorder] ia_alert_run 기록 예외(무시): {type(e).__name__}: {e}")
