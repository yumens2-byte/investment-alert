-- DuplicateGuard 중복 발행 억제 테이블
-- 적용일: 2026-07-16

CREATE TABLE IF NOT EXISTS ia_topic_state (
    topic_key TEXT PRIMARY KEY,
    canonical_title TEXT,
    keywords JSONB DEFAULT '[]'::jsonb,
    source_urls JSONB DEFAULT '[]'::jsonb,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_alert_id TEXT,
    last_x_published_at TIMESTAMPTZ,
    last_level TEXT,
    last_score NUMERIC,
    seen_count INTEGER NOT NULL DEFAULT 1,
    update_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ia_topic_state_last_seen_at
    ON ia_topic_state (last_seen_at DESC);

CREATE INDEX IF NOT EXISTS idx_ia_topic_state_last_x_published_at
    ON ia_topic_state (last_x_published_at DESC);

CREATE TABLE IF NOT EXISTS ia_x_publish_fingerprint (
    id BIGSERIAL PRIMARY KEY,
    alert_id TEXT NOT NULL,
    topic_key TEXT,
    content_fingerprint TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    tweet_id TEXT,
    published_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ia_x_publish_fingerprint_published_at
    ON ia_x_publish_fingerprint (published_at DESC);

CREATE INDEX IF NOT EXISTS idx_ia_x_publish_fingerprint_content
    ON ia_x_publish_fingerprint (content_fingerprint);

CREATE INDEX IF NOT EXISTS idx_ia_x_publish_fingerprint_topic
    ON ia_x_publish_fingerprint (topic_key, published_at DESC);

CREATE TABLE IF NOT EXISTS ia_duplicate_decision_log (
    id BIGSERIAL PRIMARY KEY,
    alert_id TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'x',
    topic_key TEXT,
    action TEXT NOT NULL,
    reason TEXT NOT NULL,
    similarity_score NUMERIC,
    previous_alert_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ia_duplicate_decision_log_created_at
    ON ia_duplicate_decision_log (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_ia_duplicate_decision_log_topic
    ON ia_duplicate_decision_log (topic_key, created_at DESC);

-- Rollback:
-- DROP TABLE IF EXISTS ia_duplicate_decision_log;
-- DROP TABLE IF EXISTS ia_x_publish_fingerprint;
-- DROP TABLE IF EXISTS ia_topic_state;
