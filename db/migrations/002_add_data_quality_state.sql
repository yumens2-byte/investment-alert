-- ============================================================================
-- Migration: 002_add_data_quality_state
-- Date:      2026-04-26
-- Author:    GTT팀 (위기감지 고도화 Phase-1)
-- Purpose:   FR-03 데이터 품질 상태 이력 테이블 신규 생성
--            덮어쓰기 아닌 시계열 INSERT only. 매 cron cycle마다 1로우.
--
-- 운영:      cron */45 = 1일 32회. 1년 = ~11,680 row. 매우 가벼움.
-- 보관:      NFR-04에 따라 180일 후 archive (cron job은 Phase 3에서 별도 추가)
-- 롤백:      파일 하단 ROLLBACK 섹션 참조
-- ============================================================================

CREATE TABLE IF NOT EXISTS ia_data_quality_state (
    id                  BIGSERIAL    PRIMARY KEY,
    cycle_started_at    TIMESTAMPTZ  NOT NULL,
    cycle_finished_at   TIMESTAMPTZ  NOT NULL,
    source_success_rate NUMERIC(4,3) NOT NULL,
    fresh_event_ratio   NUMERIC(4,3) NOT NULL,
    lag_seconds_p95     NUMERIC(8,2) NOT NULL,
    volume_zscore       NUMERIC(8,3),
    degraded_flag       BOOLEAN      NOT NULL DEFAULT FALSE,
    degraded_reasons    TEXT[]       NOT NULL DEFAULT ARRAY[]::TEXT[],
    source_results      JSONB        NOT NULL,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- 무결성 가드: success_rate, fresh_ratio는 0~1 범위
    CONSTRAINT chk_dq_success_rate CHECK (source_success_rate >= 0 AND source_success_rate <= 1),
    CONSTRAINT chk_dq_fresh_ratio  CHECK (fresh_event_ratio   >= 0 AND fresh_event_ratio   <= 1),
    -- cycle_finished_at는 cycle_started_at보다 미래
    CONSTRAINT chk_dq_cycle_order  CHECK (cycle_finished_at >= cycle_started_at)
);

-- 시계열 조회 가속 (운영 대시보드용)
CREATE INDEX IF NOT EXISTS idx_dq_cycle_time
  ON ia_data_quality_state (cycle_started_at DESC);

-- degraded 발생 이력만 빠르게 조회 (부분 인덱스 — 저장 비용 최소화)
CREATE INDEX IF NOT EXISTS idx_dq_degraded
  ON ia_data_quality_state (degraded_flag, created_at DESC)
  WHERE degraded_flag = TRUE;

COMMENT ON TABLE ia_data_quality_state IS
  'FR-03 데이터 품질 경보 이력. NFR-04에 따라 180일 보관 후 archive 대상';

COMMENT ON COLUMN ia_data_quality_state.lag_seconds_p95 IS
  '단일 cycle은 단일 측정값. 추후 누적 p95는 view로 별도 산출';

-- ============================================================================
-- ROLLBACK (수동 실행)
-- ============================================================================
-- DROP INDEX IF EXISTS idx_dq_degraded;
-- DROP INDEX IF EXISTS idx_dq_cycle_time;
-- DROP TABLE IF EXISTS ia_data_quality_state;
