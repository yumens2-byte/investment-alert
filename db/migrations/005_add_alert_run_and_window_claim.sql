-- ============================================================================
-- Migration: 005_add_alert_run_and_window_claim
-- Date:      2026-10-12
-- Author:    GTT팀 (run_alert v1.4.0 — 실행 가시화 / 창당 발행 claim)
-- Purpose:   P3 실행결과 가시화(ia_alert_run) + D3 창당 발행 claim(ia_alert_window_claim)
--            ia_alert_run          : run_alert.py 1실행 1행 (core/run_recorder.py)
--            ia_alert_window_claim : 창(window)당 발행 1회 보장 (AlertStore.claim_window)
--
-- 영향:      신규 테이블 2개 + 인덱스 3개
-- 순서:      ★ 앱(run_alert v1.4.0) 배포 전 적용 필수 ★
--            미적용 상태로 배포 시 claim_window → "error"(fail-open, 창당 1회 보장 무력화),
--            ia_alert_run 기록 실패(로그만) → 가시화 미동작
-- 보안:      RLS / REVOKE 구문은 본 파일에 없음 → hold/006_enable_rls_alert_tables.sql 로 이관 (적용 보류)
-- 멱등:      IF NOT EXISTS
-- 운영:      ia_alert_run 은 현행 cron */45 기준 일 ~48행(정정 메모 참조), claim 은 일 ~3행
-- 보관:      미정 (002 와 동일하게 별도 결정)
-- 롤백:      파일 하단 ROLLBACK 섹션 참조
--
-- [정정 메모] 002_add_data_quality_state.sql 헤더 "cron */45 = 1일 32회" 는 부정확.
--            */45 는 매시 :00, :45 에 실행되어 1일 48회 (24h x 2). 002 파일은 수정하지 않음.
-- ============================================================================

CREATE TABLE IF NOT EXISTS ia_alert_run (
    id                  BIGSERIAL    PRIMARY KEY,
    gh_run_id           TEXT,
    gh_run_attempt      TEXT,
    gh_event            TEXT,                       -- schedule | workflow_dispatch
    started_at          TIMESTAMPTZ  NOT NULL,
    finished_at         TIMESTAMPTZ,
    duration_ms         INTEGER,
    dry_run             BOOLEAN      NOT NULL DEFAULT FALSE,
    outcome             TEXT         NOT NULL,
    alert_id            TEXT,                       -- ia_alert_history.alert_id (FK 미설정: 감지 전 종료 다수)
    level               TEXT,
    score               DOUBLE PRECISION,
    news_events         INTEGER,
    youtube_events      INTEGER,
    targets             JSONB,                      -- ["tg_free","tg_paid","tg_internal","x"]
    published_channels  JSONB,
    errors              JSONB,
    detail              JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_ia_alert_run_outcome CHECK (outcome IN (
        'outside_window','holiday','claimed_already','level_none',
        'cooldown_level','cooldown_topic','duplicate_blocked',
        'published','partial_fail','all_fail','error'
    ))
);

CREATE INDEX IF NOT EXISTS idx_ia_alert_run_started
    ON ia_alert_run (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_ia_alert_run_outcome_started
    ON ia_alert_run (outcome, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_ia_alert_run_alert_id
    ON ia_alert_run (alert_id) WHERE alert_id IS NOT NULL;

COMMENT ON TABLE ia_alert_run IS 'run_alert.py 1실행 1행. RunRecorder(core/run_recorder.py) fail-open 기록';

CREATE TABLE IF NOT EXISTS ia_alert_window_claim (
    claim_key   TEXT         PRIMARY KEY,           -- '{et_date}:{target}' 예: '2026-10-13:09:30'
    alert_id    TEXT,
    gh_run_id   TEXT,
    claimed_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE ia_alert_window_claim IS 'D3 창당 발행 1회 claim. PK 충돌 = claimed_already';

NOTIFY pgrst, 'reload schema';

-- ============================================================================
-- ROLLBACK (수동 실행 — 앱의 claim/RunRecorder 사용 코드 비활성화 후)
-- ============================================================================
-- DROP TABLE IF EXISTS ia_alert_window_claim;
-- DROP TABLE IF EXISTS ia_alert_run;   -- 인덱스 동반 삭제
-- NOTIFY pgrst, 'reload schema';
