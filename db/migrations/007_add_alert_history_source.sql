-- ============================================================================
-- Migration: 007_add_alert_history_source
-- Date:      2026-10-12
-- Author:    GTT팀 (run_alert v1.4.0 — R4 매크로/섹터 구분)
-- Purpose:   R4 매크로/섹터 구분 컬럼. 앱 미배포 상태에서도 동작(DEFAULT 'macro')
--            기존 구분 수단(policy_version LIKE 'sector%')은 env 변경 시 깨질 수 있어 컬럼화
--
-- 영향:      ia_alert_history 컬럼 1개 + CHECK 1개 + 인덱스 1개, 기존 섹터 행 backfill
-- 멱등:      ADD COLUMN IF NOT EXISTS / DROP CONSTRAINT IF EXISTS 후 ADD / backfill 조건 재실행 안전
-- 순서:      007 적용 직후 앱 배포(save_alert source="sector"), 배포 후 backfill UPDATE 1회 재실행
-- 롤백:      파일 하단 ROLLBACK 섹션 참조
-- ============================================================================

ALTER TABLE ia_alert_history
    ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'macro';

-- backfill (멱등: 조건 재실행해도 결과 동일)
UPDATE ia_alert_history
   SET source = 'sector'
 WHERE policy_version LIKE 'sector%'
   AND source <> 'sector';

ALTER TABLE ia_alert_history DROP CONSTRAINT IF EXISTS chk_ia_alert_history_source;
ALTER TABLE ia_alert_history
    ADD CONSTRAINT chk_ia_alert_history_source CHECK (source IN ('macro','sector'));

CREATE INDEX IF NOT EXISTS idx_ia_alert_history_source_created
    ON ia_alert_history (source, created_at DESC);

COMMENT ON COLUMN ia_alert_history.source IS 'macro | sector. 007 이전 행은 policy_version 으로 backfill';

NOTIFY pgrst, 'reload schema';

-- 검증
-- SELECT source, policy_version, count(*) FROM ia_alert_history GROUP BY 1,2 ORDER BY 1,2;
-- 기대: sector/sector-v1.0.0 = 85, macro/(v1.0.0,v1.1.0,NULL) = 84  (2026-10-12 기준)

-- ============================================================================
-- ROLLBACK (수동 실행)
-- ============================================================================
-- DROP INDEX IF EXISTS idx_ia_alert_history_source_created;
-- ALTER TABLE ia_alert_history DROP CONSTRAINT IF EXISTS chk_ia_alert_history_source;
-- ALTER TABLE ia_alert_history DROP COLUMN IF EXISTS source;
-- NOTIFY pgrst, 'reload schema';
