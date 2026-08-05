"""Garante que o schema 049 e aditivo e seguro."""
from pathlib import Path

import pytest

SCHEMA = Path(__file__).resolve().parents[1] / "supabase_unificado" / "schema" / "049_portfolio_asset_snapshots.sql"


@pytest.fixture(scope="module")
def sql() -> str:
    return SCHEMA.read_text(encoding="utf-8")


def test_arquivo_de_schema_existe():
    assert SCHEMA.is_file(), f"schema ausente: {SCHEMA}"


def test_schema_nao_contem_comando_destrutivo(sql):
    upper = sql.upper()
    for proibido in ("DROP TABLE", "TRUNCATE", "DELETE FROM"):
        assert proibido not in upper, f"comando destrutivo encontrado: {proibido}"


def test_criacoes_sao_idempotentes(sql):
    upper = sql.upper()
    assert upper.count("CREATE TABLE") == upper.count("CREATE TABLE IF NOT EXISTS")
    assert upper.count("CREATE INDEX") == upper.count("CREATE INDEX IF NOT EXISTS")


def test_tabelas_esperadas_declaradas(sql):
    assert "portfolio_asset_snapshots" in sql
    assert "portfolio_allocation_targets" in sql


def test_chave_natural_do_snapshot_e_unica(sql):
    assert "UNIQUE (asset_class, model_id, symbol)" in sql


def test_rls_habilitada_nas_duas_tabelas(sql):
    upper = sql.upper()
    assert upper.count("ENABLE ROW LEVEL SECURITY") == 2


def test_cascata_por_usuario_preservada(sql):
    # user_id sempre cascateia; model_id e polimorfico e por isso nao tem FK.
    assert sql.count("REFERENCES profiles(id) ON DELETE CASCADE") == 2
    assert "REFERENCES b3_portfolio_models" not in sql
