-- ============================================================================
-- Migration: 001_add_reasoning_json
-- Date:      2026-04-26
-- Author:    GTT팀 (위기감지 고도화 Phase-1)
-- Purpose:   FR-05 표준화 — reasoning_json + policy_version + published_channels
--            기존 reasoning(text) 컬럼은 dual-write로 유지 (롤백 안전)
--
-- 영향:      ia_alert_history 테이블 — 컬럼 3개 추가, 인덱스 2개 추가
-- 롤백:      파일 하단 ROLLBACK 섹션 참조
-- 데이터:    기존 row의 신규 컬럼은 NULL (NOT NULL 제약 없음)
-- ============================================================================

ALTER TABLE ia_alert_history
  ADD COLUMN IF NOT EXISTS reasoning_json     JSONB,
  ADD COLUMN IF NOT EXISTS policy_version     TEXT,
  ADD COLUMN IF NOT EXISTS published_channels JSONB;

-- policy_version별 조회 가속 (FR-07 회귀 분석용)
CREATE INDEX IF NOT EXISTS idx_ia_alert_policy_version
  ON ia_alert_history (policy_version, created_at DESC);

-- reasoning_json 내부 키 검색 가속 (감사 대응용)
CREATE INDEX IF NOT EXISTS idx_ia_alert_reasoning_gin
  ON ia_alert_history USING GIN (reasoning_json);

COMMENT ON COLUMN ia_alert_history.reasoning_json IS
  'FR-05 표준화 reasoning. version 1.0 스키마 상세는 docs/reasoning_v1.json 참조';

COMMENT ON COLUMN ia_alert_history.policy_version IS
  '판정에 사용된 정책 버전 (semver: vX.Y.Z)';

COMMENT ON COLUMN ia_alert_history.published_channels IS
  '실제 발행 성공한 채널 리스트 예: ["x","tg_free","tg_internal"]';

-- ============================================================================
-- ROLLBACK (수동 실행)
-- ============================================================================
-- DROP INDEX IF EXISTS idx_ia_alert_reasoning_gin;
-- DROP INDEX IF EXISTS idx_ia_alert_policy_version;
-- ALTER TABLE ia_alert_history
--   DROP COLUMN IF EXISTS published_channels,
--   DROP COLUMN IF EXISTS policy_version,
--   DROP COLUMN IF EXISTS reasoning_json;
