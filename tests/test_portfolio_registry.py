"""Registro de classes de ativo."""
import importlib
import re

import pytest

from core.portfolio.registry import SPECS, asset_classes, get_spec, load_adapter


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


@pytest.mark.parametrize("key,adapter_module", [
    ("b3", "core.portfolio.adapters.b3"),
    ("us", "core.portfolio.adapters.us"),
    ("fii", "core.portfolio.adapters.fii"),
])
def test_adapter_module_por_classe(key, adapter_module):
    """Verifica que cada classe tem o modulo adaptador correto."""
    spec = get_spec(key)
    assert spec.adapter_module == adapter_module


def test_check_do_schema_cobre_exatamente_as_classes_registradas():
    """Verifica bidirecionalmente que CHECK em 049 e SPECS.keys sao o mesmo conjunto."""
    from pathlib import Path
    sql = (Path(__file__).resolve().parents[1] / "supabase_unificado" / "schema"
           / "049_portfolio_asset_snapshots.sql").read_text(encoding="utf-8")

    # Extrai o CHECK (asset_class IN (...)) do SQL
    match = re.search(r"CHECK\s*\(\s*asset_class\s+IN\s*\((.*?)\)\s*\)", sql, re.IGNORECASE)
    assert match, "Nao encontrou CHECK (asset_class IN (...)) no schema"

    # Extrai as classes entre aspas simples do CHECK
    check_values = match.group(1)
    quoted_values = re.findall(r"'([^']+)'", check_values)
    schema_classes = set(quoted_values)

    # Verifica que SPECS e schema tem exatamente o mesmo conjunto (bidirecionalmente)
    assert schema_classes == set(SPECS), (
        f"Divergencia entre CHECK em 049 e SPECS: "
        f"schema tem {schema_classes}, SPECS tem {set(SPECS)}"
    )


def test_load_adapter_classe_desconhecida_levanta_erro():
    """load_adapter("cripto") levanta KeyError antes de tentar importar."""
    with pytest.raises(KeyError, match="cripto"):
        load_adapter("cripto")


@pytest.mark.parametrize("key,expected_module", [
    ("b3", "core.portfolio.adapters.b3"),
    ("us", "core.portfolio.adapters.us"),
    ("fii", "core.portfolio.adapters.fii"),
])
def test_load_adapter_passa_modulo_correto_para_importlib(key, expected_module, monkeypatch):
    """Verifica que load_adapter passa o nome de modulo correto para importlib.import_module."""
    # Monkeypatch para evitar tentar importar modulos que nao existem ainda (Tasks 6-8).
    # Testa que o contrato de load_adapter esta correto.
    calls = []

    def mock_import_module(name):
        calls.append(name)
        # Retorna um mock para nao quebrar o teste
        import types
        return types.ModuleType(name)

    monkeypatch.setattr(importlib, "import_module", mock_import_module)
    load_adapter(key)
    assert calls == [expected_module]
