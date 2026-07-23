from types import SimpleNamespace

import run_market_ingest as cli
from scripts import apply_b3_schema


def test_market_cli_prefere_senha_efetiva_do_container(monkeypatch):
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout="senha sintetica com espaco\n"),
    )
    for key in ("SUPABASE_UNIFICADO_URL", "DATABASE_URL", "SUPABASE_DB_URL"):
        monkeypatch.delenv(key, raising=False)

    assert cli._point_to_warehouse() is True
    assert "senha%20sintetica%20com%20espaco" in cli.os.environ["DATABASE_URL"]
    assert cli.os.environ["DATABASE_URL"].startswith("postgresql://postgres:")
    assert cli.os.environ["DATABASE_URL"].endswith("@127.0.0.1:5433/postgres")


def test_migration_b3_usa_resolvedor_correto_para_funcao_e_tabela():
    assert apply_b3_schema.MIGRATIONS == (
        ("043_b3_validation_and_pit_audit.sql", "regclass",
         "market.b3_validation_runs"),
        ("044_b3_audit_immutability.sql", "regprocedure",
         "market.prevent_b3_audit_mutation()"),
    )


def test_fii_snapshot_schema_readiness_is_callable():
    from scripts.publish_fii_selection_snapshot import _schema_ready

    assert callable(_schema_ready)
