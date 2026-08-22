"""Regressões de bootstrap para migrations SQL que precisam tolerar schema vazio."""

from pathlib import Path

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "supabase_unificado" / "schema"


def _migration(name: str) -> str:
    return (SCHEMA_DIR / name).read_text(encoding="utf-8")


def test_014_legacy_comments_are_conditional_for_absent_legacy_tables():
    sql = _migration("014_legacy_isolation.sql")

    assert "to_regclass" in sql
    assert "COMMENT ON TABLE" in sql


def test_028_hardening_does_not_require_optional_legacy_function():
    sql = _migration("028_private_policy_and_function_hardening.sql")

    assert "to_regprocedure" in sql
    assert (
        "IF to_regprocedure('public.match_corporate_chunks(vector,integer,text)')"
        in sql
    )
    assert "EXECUTE 'ALTER FUNCTION public.match_corporate_chunks" in sql


def test_037_parser_checkpoint_index_is_idempotent():
    sql = _migration("037_fii_b3_parser_checkpoints.sql")

    assert "CREATE INDEX IF NOT EXISTS idx_fii_b3_archive_parser_status" in sql
