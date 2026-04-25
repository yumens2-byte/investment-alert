"""
제목: DataQualityStore — ia_data_quality_state 적재 모듈 (Phase 1)
내용: DataQualityMonitor.evaluate() 결과를 Supabase에 적재한다.
      INSERT only (덮어쓰기 아닌 시계열). 매 cron cycle마다 1로우.

설계 근거:
  - 02 프로세스 설계서 §3.2 (ia_data_quality_state DDL)
  - 04 수정 리스트 M-01 (macro_news_layer가 호출)
  - DDL: db/migrations/002_add_data_quality_state.sql

설계 결정:
  - AlertStore와 같은 lazy-init 패턴 (db/alert_store.py 일관성)
  - 실패 시 raise 아닌 None 반환 (파이프라인 중단 방지)
  - JSON serializable 보장은 DataQualityState.to_dict()가 책임
"""

from __future__ import annotations

import os

from core.logger import get_logger
from detection.dq_monitor import DataQualityState

VERSION = "1.0.0"

# 테이블 이름 (DDL과 동기)
TABLE_DATA_QUALITY_STATE: str = "ia_data_quality_state"

logger = get_logger(__name__)


class DataQualityStore:
    """
    제목: DataQualityState 적재 전용 저장소
    내용: ia_data_quality_state 테이블에 1로우 INSERT.

    책임:
      - DataQualityState → dict 변환 (DataQualityState.to_dict 위임)
      - Supabase INSERT 실행
      - 실패 시 로그 + None 반환 (raise 아님)

    호출 예 (M-01 적용 후 macro_news_layer.detect()):
        store = DataQualityStore()
        row_id = store.save_dq_state(state)
        if row_id is None:
            logger.warning("[MacroNewsLayer] DQ 적재 실패 (감지는 계속)")
    """

    def __init__(
        self,
        supabase_url: str | None = None,
        supabase_key: str | None = None,
    ) -> None:
        """
        제목: DataQualityStore 초기화
        내용: 환경변수에서 Supabase 자격증명을 로드. AlertStore와 동일 패턴.

        Args:
            supabase_url: Supabase 프로젝트 URL (None이면 SUPABASE_URL env)
            supabase_key: Supabase key (None이면 SUPABASE_KEY env)
        """
        raw_url = supabase_url or os.getenv("SUPABASE_URL", "")
        # trailing slash 방어 (AlertStore와 동일)
        self._url = raw_url.rstrip("/") if raw_url else ""
        self._key = supabase_key or os.getenv("SUPABASE_KEY", "")
        self._client: object | None = None
        logger.info(f"[DQStore] v{VERSION} 초기화")

    def _get_client(self) -> object:
        """
        제목: Supabase 클라이언트 lazy init
        내용: 첫 호출 시 생성, 이후 재사용.

        Returns:
            supabase.Client: 초기화된 클라이언트

        Raises:
            RuntimeError: URL/KEY 미설정 시
        """
        if self._client is not None:
            return self._client

        if not self._url or not self._key:
            raise RuntimeError("SUPABASE_URL/SUPABASE_KEY 환경변수 미설정")

        from supabase import create_client  # type: ignore[import]
        self._client = create_client(self._url, self._key)
        return self._client

    def save_dq_state(self, state: DataQualityState) -> int | None:
        """
        제목: DataQualityState INSERT
        내용: ia_data_quality_state에 1로우 추가하고 생성된 id 반환.

        처리 플로우:
          1. cycle_started_at / cycle_finished_at 누락 시 즉시 None 반환
          2. state.to_dict()로 dict 변환
          3. 테이블에 INSERT
          4. 반환된 id 추출 (실패 시 None)
          5. 모든 예외는 catch + 로그 (raise 안 함)

        Args:
            state: DataQualityMonitor.evaluate() 결과

        Returns:
            int | None: 생성된 row id, 실패 시 None
        """
        if state.cycle_started_at is None or state.cycle_finished_at is None:
            logger.error(
                "[DQStore] cycle 시간 필드 누락 — DataQualityState.cycle_started_at/finished_at 필수"
            )
            return None

        try:
            client = self._get_client()

            # to_dict()는 ISO 문자열로 직렬화하여 Supabase TIMESTAMPTZ 호환
            payload = state.to_dict()

            # source_results는 JSONB 컬럼이므로 dict 유지 (이미 to_dict()가 처리)
            # degraded_reasons는 TEXT[] 컬럼 — list 유지 (이미 to_dict()가 처리)

            response = (
                client.table(TABLE_DATA_QUALITY_STATE)  # type: ignore[union-attr]
                .insert(payload)
                .execute()
            )

            # Supabase response.data는 INSERT된 row 리스트
            if not response.data:
                logger.warning(
                    "[DQStore] INSERT 응답에 data 없음 — id 추출 불가"
                )
                return None

            inserted_row = response.data[0]
            row_id = inserted_row.get("id")

            if row_id is None:
                logger.warning(
                    "[DQStore] INSERT 성공했으나 id 필드 누락 (스키마 점검 필요)"
                )
                return None

            row_id_int = int(row_id)
            logger.info(
                f"[DQStore] save_dq_state 완료: id={row_id_int}, "
                f"degraded={state.degraded_flag}"
            )
            return row_id_int

        except Exception as e:
            logger.error(
                f"[DQStore] save_dq_state 실패: {type(e).__name__}: {e}"
            )
            return None
