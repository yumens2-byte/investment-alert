-- ============================================================================
-- Migration: 003_add_sector_flow_daily
-- Date:      2026-05-11
-- Author:    GTT팀 (Sector Flow Alert)
-- Purpose:   섹터 ETF 일간 등락률 시계열 저장 — investment-alert 자체 수집
--            investment-os의 daily_snapshots는 손대지 않음 (단독 완결 원칙)
--
-- 영향:      신규 테이블 1개 + 인덱스 2개
-- 운영:      평일 1회 cron → 1일 6 row (US 6 ticker) = 연 약 1,560 row
-- 보관:      Phase 2에서 180일 archive 정책 추가 예정
-- 롤백:      파일 하단 ROLLBACK 섹션 참조
-- ============================================================================

CREATE TABLE IF NOT EXISTS ia_sector_flow_daily (
    id              BIGSERIAL    PRIMARY KEY,
    snapshot_date   DATE         NOT NULL,
    market          TEXT         NOT NULL DEFAULT 'US',
    ticker          TEXT         NOT NULL,
    sector_group    TEXT         NOT NULL,   -- 'defensive' | 'cyclical'
    chg_pct         NUMERIC(8,4),            -- 등락률(%) — NULL 허용 (수집 실패 시)
    policy_version  TEXT         NOT NULL,   -- 'sector-v1.0.0' 등
    collected_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- 멱등성 보장: 동일 날짜·시장·티커는 1 row
    CONSTRAINT uq_sector_flow_date_market_ticker UNIQUE (snapshot_date, market, ticker),
    -- 그룹 값 가드
    CONSTRAINT chk_sector_group CHECK (sector_group IN ('defensive', 'cyclical'))
);

-- 시계열 조회 가속 (감지 레이어가 최근 N일 조회 시 사용)
CREATE INDEX IF NOT EXISTS idx_ia_sfd_date_desc
    ON ia_sector_flow_daily (snapshot_date DESC);

-- 그룹별 시계열 조회 가속 (Phase 2 분석 대시보드용)
CREATE INDEX IF NOT EXISTS idx_ia_sfd_group_date
    ON ia_sector_flow_daily (sector_group, snapshot_date DESC);

COMMENT ON TABLE ia_sector_flow_daily IS
    'Sector Flow Alert — 일간 섹터 ETF 등락률. investment-alert 자체 수집·적재.';
COMMENT ON COLUMN ia_sector_flow_daily.sector_group IS
    'config/sector_groups.py에서 정의. policy_version으로 시점 추적 가능';
COMMENT ON COLUMN ia_sector_flow_daily.policy_version IS
    '적용된 그룹 정의 버전 (sector-vX.Y.Z). 그룹 재분류 시 시점 분기 추적용';

-- ============================================================================
-- ROLLBACK (수동 실행)
-- ============================================================================
-- DROP INDEX IF EXISTS idx_ia_sfd_group_date;
-- DROP INDEX IF EXISTS idx_ia_sfd_date_desc;
-- DROP TABLE IF EXISTS ia_sector_flow_daily;
