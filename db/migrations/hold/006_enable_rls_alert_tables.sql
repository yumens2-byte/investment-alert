-- ============================================================================
-- Migration: 006_enable_rls_alert_tables
-- Date:      2026-10-12
-- Author:    GTT팀 (run_alert v1.4.0 — D6 보안)
-- Purpose:   alert 계열 8개 테이블 RLS ON + anon/authenticated 권한 회수
--            정책(POLICY) 없이 RLS ON — service_role 은 rolbypassrls=true 이므로 접근 유지
--            (ia_data_quality_state · ia_engagement_* 와 동일 패턴)
--
-- ***************************************************************************
-- * 보류: SUPABASE_KEY role 확인 전 적용 금지                                *
-- *   - 마스터 진술 "service_role 아님" vs DB 통계(pg_stat_statements) 상    *
-- *     해당 테이블 접근 role = service_role → 충돌 미해결                   *
-- *   - SUPABASE_KEY 가 anon 키인 상태로 적용 시 AlertStore 전 조회가 실패   *
-- *     → 쿨다운 무력화(False 반환)로 X 중복 발행 위험                       *
-- ***************************************************************************
--
-- 대상:      ia_alert_history, ia_cooldown_state, ia_topic_cooldown, ia_topic_state,
--            ia_x_publish_fingerprint, ia_duplicate_decision_log,
--            ia_alert_run, ia_alert_window_claim
-- 전제:      004, 005 적용 완료 (미적용 시 해당 테이블 부재로 실패)
-- 영향:      RLS ON 8개, 테이블 REVOKE 8개, 시퀀스 REVOKE 6개
--            시퀀스 제외: ia_topic_state(PK topic_key TEXT), ia_alert_window_claim(PK claim_key TEXT)
--            — BIGSERIAL 없음 (004/005 DDL 기준)
-- 주의:      RLS 는 TRUNCATE/REFERENCES/TRIGGER 를 막지 않으므로 REVOKE 병행 필수
--            FORCE RLS 미사용 (owner 대시보드 조회 차단, service_role 은 영향 없음)
-- 멱등:      ENABLE RLS / REVOKE / GRANT 는 반복 실행해도 오류 없음
-- 롤백:      파일 하단 ROLLBACK 섹션 참조
-- ============================================================================

BEGIN;

ALTER TABLE public.ia_alert_history          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ia_cooldown_state         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ia_topic_cooldown         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ia_topic_state            ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ia_x_publish_fingerprint  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ia_duplicate_decision_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ia_alert_run              ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ia_alert_window_claim     ENABLE ROW LEVEL SECURITY;

-- RLS 가 막지 못하는 TRUNCATE/REFERENCES/TRIGGER 포함 전부 회수
REVOKE ALL ON TABLE
    public.ia_alert_history, public.ia_cooldown_state, public.ia_topic_cooldown,
    public.ia_topic_state, public.ia_x_publish_fingerprint, public.ia_duplicate_decision_log,
    public.ia_alert_run, public.ia_alert_window_claim
FROM anon, authenticated;

REVOKE ALL ON SEQUENCE
    public.ia_alert_history_id_seq, public.ia_cooldown_state_id_seq,
    public.ia_topic_cooldown_id_seq, public.ia_x_publish_fingerprint_id_seq,
    public.ia_duplicate_decision_log_id_seq, public.ia_alert_run_id_seq
FROM anon, authenticated;

-- service_role 권한은 현행 유지 (명시적 재부여로 멱등 보장)
GRANT ALL ON TABLE
    public.ia_alert_history, public.ia_cooldown_state, public.ia_topic_cooldown,
    public.ia_topic_state, public.ia_x_publish_fingerprint, public.ia_duplicate_decision_log,
    public.ia_alert_run, public.ia_alert_window_claim
TO service_role;

GRANT USAGE, SELECT ON SEQUENCE
    public.ia_alert_history_id_seq, public.ia_cooldown_state_id_seq,
    public.ia_topic_cooldown_id_seq, public.ia_x_publish_fingerprint_id_seq,
    public.ia_duplicate_decision_log_id_seq, public.ia_alert_run_id_seq
TO service_role;

COMMIT;

NOTIFY pgrst, 'reload schema';

-- 검증 (적용 후: relrowsecurity=t, anon_select=f)
-- SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
--   FROM pg_class c
--  WHERE c.relnamespace = 'public'::regnamespace
--    AND c.relname IN ('ia_alert_history','ia_cooldown_state','ia_topic_cooldown',
--                      'ia_topic_state','ia_x_publish_fingerprint','ia_duplicate_decision_log',
--                      'ia_alert_run','ia_alert_window_claim');
-- SELECT t, has_table_privilege('anon', 'public.'||t, 'SELECT') AS anon_select
--   FROM unnest(ARRAY['ia_alert_history','ia_cooldown_state','ia_topic_cooldown',
--                     'ia_topic_state','ia_x_publish_fingerprint','ia_duplicate_decision_log',
--                     'ia_alert_run','ia_alert_window_claim']) t;

-- ============================================================================
-- ROLLBACK (수동 실행)
-- 기존 3테이블(ia_alert_history/ia_cooldown_state/ia_topic_cooldown)은 2026-10-12 실측 grant
-- (anon/authenticated 테이블 ALL, 시퀀스 USAGE·SELECT·UPDATE) 로 원복.
-- 004/005 신규 5테이블의 생성 직후 grant 는 Supabase 기본 권한(default privileges)에 따름
-- — 적용 직전 role_table_grants 결과를 저장해 두고 그 결과를 롤백 기준으로 사용할 것.
-- ============================================================================
-- BEGIN;
-- ALTER TABLE public.ia_alert_history          DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE public.ia_cooldown_state         DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE public.ia_topic_cooldown         DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE public.ia_topic_state            DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE public.ia_x_publish_fingerprint  DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE public.ia_duplicate_decision_log DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE public.ia_alert_run              DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE public.ia_alert_window_claim     DISABLE ROW LEVEL SECURITY;
-- GRANT ALL ON TABLE public.ia_alert_history, public.ia_cooldown_state,
--                    public.ia_topic_cooldown TO anon, authenticated;
-- GRANT USAGE, SELECT, UPDATE ON SEQUENCE public.ia_alert_history_id_seq,
--       public.ia_cooldown_state_id_seq, public.ia_topic_cooldown_id_seq TO anon, authenticated;
-- COMMIT;
-- NOTIFY pgrst, 'reload schema';
