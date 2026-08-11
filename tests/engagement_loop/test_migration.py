"""Static safety checks for the engagement-loop Supabase migration."""

from pathlib import Path

MIGRATION = Path("db/migrations/005_add_engagement_loop_tables.sql")


def test_migration_is_isolated_and_enables_rls_for_every_table() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    tables = (
        "ia_engagement_loops",
        "ia_engagement_facts",
        "ia_engagement_contents",
        "ia_engagement_events",
        "ia_engagement_metrics",
        "ia_engagement_responses",
    )
    for table in tables:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in sql
        assert f"REVOKE ALL ON TABLE {table} FROM anon, authenticated" in sql
        assert f"GRANT ALL ON TABLE {table} TO service_role" in sql

    assert "REFERENCES ia_alert" not in sql
    assert "REFERENCES ia_sector" not in sql


def test_migration_guards_criteria_and_publication_idempotency() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "NEW.criteria IS DISTINCT FROM OLD.criteria" in sql
    assert "NEW.status <> 'planned'" in sql
    assert "invalid engagement loop status transition" in sql
    assert "UNIQUE (loop_id, slot, revision)" in sql
    assert "UNIQUE (content_sha256)" in sql
    assert "approved_sha256 = content_sha256" in sql


def test_migration_preserves_audit_events_and_limits_sequence_grants() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "engagement events are append-only" in sql
    assert "ON ALL SEQUENCES IN SCHEMA public" not in sql
    assert "ON SEQUENCE ia_engagement_facts_id_seq" in sql
