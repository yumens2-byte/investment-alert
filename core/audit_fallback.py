"""
제목: 감사 로그 fallback 저장소 (B5 대응 / Day 5)
내용: AlertStore.save_alert() 또는 update_publish_result() 실패 시
      로컬 JSONL에 임시 기록한다. GitHub Actions artifacts로 14일 보관되므로
      사후 추적 + 감사 대응 가능.

설계 근거:
  - B5_PATCH_GUIDE.md (Day 1 Role B 결정안 a + b 결합)
  - 06 보고서 §5 (감사 추적 누락 위험 해소)

원칙:
  - fallback의 fallback은 없음 — 디스크 오류 시에도 raise 안 함
  - 파일 잠금 미사용 (cron 직렬 실행 가정)
  - JSON serialization은 default=str로 datetime 등 비표준 객체 안전 처리
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.logger import get_logger

VERSION = "1.0.0"

# 기본 fallback 파일 경로 — alert.yml의 logs/ 디렉토리와 일치
# (artifacts upload-artifact가 logs/ 전체를 14일 보관)
DEFAULT_FALLBACK_DIR: Path = Path("logs")
DEFAULT_FALLBACK_FILE: Path = DEFAULT_FALLBACK_DIR / "alert_audit_fallback.jsonl"

logger = get_logger(__name__)


def append_audit_fallback(
    record: dict[str, Any],
    fallback_file: Path | None = None,
) -> bool:
    """
    제목: audit fallback JSONL 기록
    내용: save_alert / update_publish_result 실패 시 로컬에 1줄 추가.

    처리 플로우:
      1. fallback_file 미지정 시 DEFAULT_FALLBACK_FILE 사용
      2. 부모 디렉토리 mkdir
      3. record에 fallback_recorded_at(UTC ISO) 자동 부여
      4. JSON 직렬화 (ensure_ascii=False, default=str로 datetime 안전)
      5. 한 줄 append
      6. 모든 예외는 catch + 에러 로그 (raise 안 함)

    Args:
        record: 저장할 dict (alert_id, level, score, channels, error 등 자유 형식)
        fallback_file: 기록할 파일 경로. None이면 DEFAULT_FALLBACK_FILE.

    Returns:
        bool: 기록 성공 여부 (디스크 오류 등 실패 시 False, 절대 raise 안 함)
    """
    target_file = fallback_file if fallback_file is not None else DEFAULT_FALLBACK_FILE

    try:
        # 부모 디렉토리 보장
        target_file.parent.mkdir(parents=True, exist_ok=True)

        # 메타 필드 자동 부여 (호출자 record에 같은 키 있으면 덮어씀 방지 위해 setdefault)
        if "fallback_recorded_at" not in record:
            record["fallback_recorded_at"] = datetime.now(UTC).isoformat()

        # JSON 직렬화 — default=str로 datetime/UUID 등 비표준 객체 안전 처리
        line = json.dumps(record, ensure_ascii=False, default=str)

        with target_file.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

        # WARNING 레벨로 기록 (정상 경로 아님을 명시)
        alert_id = record.get("alert_id", "?")
        alert_id_short = str(alert_id)[:8] if alert_id else "?"
        reason = record.get("reason", "unknown")
        logger.warning(
            f"[AuditFallback] v{VERSION} 로컬 fallback 기록: "
            f"alert_id={alert_id_short} reason={reason} file={target_file}"
        )
        return True

    except Exception as e:
        # fallback의 fallback은 없음 — stderr 로그만 남기고 False 반환
        logger.error(
            f"[AuditFallback] fallback 기록 자체 실패: {type(e).__name__}: {e}"
        )
        return False
