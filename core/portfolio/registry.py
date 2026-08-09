"""Registro das classes de ativo suportadas pela camada canonica.

Adicionar uma classe nova significa acrescentar uma entrada em SPECS e criar o
adaptador correspondente. Nenhuma migracao de schema e necessaria.

Coberto por tests/test_portfolio_registry.py.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from types import ModuleType


@dataclass(frozen=True)
class AssetClassSpec:
    """Descreve onde vive a carteira-modelo de uma classe e como le-la."""

    key: str
    label: str
    models_table: str
    items_table: str
    symbol_column: str
    currency: str
    country: str
    adapter_module: str


SPECS: dict[str, AssetClassSpec] = {
    "b3": AssetClassSpec(
        key="b3",
        label="Empresas B3",
        models_table="b3_portfolio_models",
        items_table="b3_portfolio_model_items",
        symbol_column="ticker",
        currency="BRL",
        country="BR",
        adapter_module="core.portfolio.adapters.b3",
    ),
    "us": AssetClassSpec(
        key="us",
        label="Empresas Americanas",
        models_table="us_portfolio_models",
        items_table="us_portfolio_model_items",
        symbol_column="symbol",
        currency="USD",
        country="US",
        adapter_module="core.portfolio.adapters.us",
    ),
    "fii": AssetClassSpec(
        key="fii",
        label="FIIs",
        models_table="fii_portfolio_models",
        items_table="fii_portfolio_model_items",
        symbol_column="ticker",
        currency="BRL",
        country="BR",
        adapter_module="core.portfolio.adapters.fii",
    ),
}


def asset_classes() -> tuple[str, ...]:
    """Chaves registradas em ordem alfabetica (determinismo)."""
    return tuple(sorted(SPECS))


def get_spec(key: str) -> AssetClassSpec:
    """Obtem a especificacao de uma classe de ativo por chave (case-insensitive, tolerante a espacos)."""
    normal = str(key or "").strip().lower()
    if normal not in SPECS:
        raise KeyError(f"classe de ativo desconhecida: {key!r}")
    return SPECS[normal]


def load_adapter(key: str) -> ModuleType:
    """Importa o adaptador da classe sob demanda."""
    return importlib.import_module(get_spec(key).adapter_module)
