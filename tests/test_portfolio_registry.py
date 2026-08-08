"""Registro de classes de ativo."""
import pytest

from core.portfolio.registry import SPECS, asset_classes, get_spec


def test_tres_classes_registradas():
    assert set(SPECS) == {"b3", "us", "fii"}


def test_asset_classes_e_deterministico():
    assert asset_classes() == ("b3", "fii", "us")


def test_get_spec_aceita_maiusculas_e_espacos():
    assert get_spec("  B3 ").key == "b3"


def test_classe_desconhecida_levanta_erro_claro():
    with pytest.raises(KeyError, match="cripto"):
        get_spec("cripto")


@pytest.mark.parametrize("key,moeda,pais", [
    ("b3", "BRL", "BR"),
    ("fii", "BRL", "BR"),
    ("us", "USD", "US"),
])
def test_moeda_e_pais_por_classe(key, moeda, pais):
    spec = get_spec(key)
    assert spec.currency == moeda
    assert spec.country == pais


@pytest.mark.parametrize("key,models,items,coluna", [
    ("b3", "b3_portfolio_models", "b3_portfolio_model_items", "ticker"),
    ("us", "us_portfolio_models", "us_portfolio_model_items", "symbol"),
    ("fii", "fii_portfolio_models", "fii_portfolio_model_items", "ticker"),
])
def test_tabelas_e_coluna_chave_batem_com_o_schema_existente(key, models, items, coluna):
    spec = get_spec(key)
    assert spec.models_table == models
    assert spec.items_table == items
    assert spec.symbol_column == coluna


def test_check_do_schema_cobre_exatamente_as_classes_registradas():
    # O CHECK em 049 lista as classes aceitas; registro e schema nao podem divergir.
    from pathlib import Path
    sql = (Path(__file__).resolve().parents[1] / "supabase_unificado" / "schema"
           / "049_portfolio_asset_snapshots.sql").read_text(encoding="utf-8")
    for key in SPECS:
        assert f"'{key}'" in sql
