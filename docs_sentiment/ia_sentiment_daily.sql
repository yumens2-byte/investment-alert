-- ────────────────────────────────────────────────────────
-- ia_sentiment_daily — EDT 심리지표 일간 산출 테이블
-- 프로젝트: investment-alert (Supabase ccomoimhhttaklfadaos, public 스키마 ia_ prefix)
-- 원칙: 원값+점수 전체 적재 (임계값 조정 시 백테스트 재산출 가능)
--       score_date UNIQUE — upsert 멱등성 키
-- ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.ia_sentiment_daily (
    id                BIGSERIAL PRIMARY KEY,
    score_date        DATE NOT NULL UNIQUE,

    -- 원값
    vix_ratio         NUMERIC(8,4),
    vix_close         NUMERIC(8,4),
    vix3m_close       NUMERIC(8,4),
    pcr_equity        NUMERIC(8,4),
    pcr_date          DATE,
    hy_oas_bp         NUMERIC(8,2),
    hy_oas_date       DATE,
    breadth_rel_pct   NUMERIC(8,4),
    crypto_fg         NUMERIC(6,2),

    -- 점수 (0~100)
    vix_ratio_score   NUMERIC(6,2),
    pcr_score         NUMERIC(6,2),
    hy_oas_score      NUMERIC(6,2),
    breadth_score     NUMERIC(6,2),
    crypto_fg_score   NUMERIC(6,2),

    -- EDT 3축
    e_score           NUMERIC(6,2),
    fast_fear         NUMERIC(6,2),
    slow_fear         NUMERIC(6,2),
    d_score           NUMERIC(7,2),
    t_score           NUMERIC(7,2),
    trend             VARCHAR(12),      -- IMPROVING | FLAT | WORSENING | NULL(콜드스타트)
    state_label       VARCHAR(20),      -- 15종 상태 라벨
    level             VARCHAR(16) NOT NULL,  -- EXTREME_FEAR~EXTREME_GREED | DEGRADED

    -- 운영
    missing_count     SMALLINT DEFAULT 0,
    published_x       BOOLEAN DEFAULT FALSE,
    published_tg_internal BOOLEAN DEFAULT FALSE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ia_sentiment_daily_date
    ON public.ia_sentiment_daily (score_date DESC);

-- PostgREST 스키마 캐시 갱신 (DDL 후 필수)
NOTIFY pgrst, 'reload schema';
