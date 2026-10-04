"""A "última observação" de cada série é a do período mais recente, não a do vintage.

Com as revisões do ALFRED, observações de 1962 ganham vintage de ontem. Ordenar
por ``COALESCE(vintage_date, reference_period)`` escolhia o FEDFUNDS de 1962-03,
o CPI de 1952-09 e o UNRATE de 1959-03; o filtro de período recente de
``core.contexto_mercado`` descartava tudo e nenhuma LLM recebia macro dos EUA.

Prende também: nenhuma série cai por corte alfabético (o ``LIMIT 20`` deixava o
World Bank de fora) e os provedores de teste do armazém não entram no prompt.

O teste em Postgres usa tabelas TEMP com rollback, como
``test_macro_postgres_temporal`` -- opt-in por ``APP4_TEST_MACRO_TEMP_TABLES=1``.
"""
from __future__ import annotations

import os
from contextlib import nullcontext
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from core.macro_data.context import published_macro_context

_AGORA = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)


def _obs(provider, code, periodo, valor, *, vintage=None, coleta=None, pais="US"):
    return {
        "provider": provider, "provider_code": code, "country_code": pais,
        "reference_period": periodo, "value": valor, "vintage_date": vintage,
        "retrieved_at": coleta or datetime(2026, 10, 3, tzinfo=timezone.utc),
        "released_at": None, "is_forecast": False, "is_preliminary": False,
        "unit": "%",
    }


def _alfred():
    """FEDFUNDS como o ALFRED grava: o período antigo revisado tem vintage novo
    e foi coletado por último; o período recente tem duas versões."""
    return [
        _obs("fred", "FEDFUNDS", date(2026, 9, 1), 4.10, vintage=date(2026, 9, 2),
             coleta=datetime(2026, 9, 2, tzinfo=timezone.utc)),
        _obs("fred", "FEDFUNDS", date(2026, 9, 1), 4.08, vintage=date(2026, 10, 2),
             coleta=datetime(2026, 10, 2, tzinfo=timezone.utc)),
        _obs("fred", "FEDFUNDS", date(2026, 8, 1), 4.33, vintage=date(2026, 10, 2),
             coleta=datetime(2026, 10, 2, 1, tzinfo=timezone.utc)),
        _obs("fred", "FEDFUNDS", date(1962, 3, 1), 2.85, vintage=date(2026, 10, 2),
             coleta=datetime(2026, 10, 3, tzinfo=timezone.utc)),
    ]


def _por_codigo(fatos):
    return {(f["provider"], f["provider_code"], f["country_code"]): f for f in fatos}


def test_publicado_escolhe_o_periodo_mais_recente_e_o_vintage_mais_novo():
    fatos = _por_codigo(published_macro_context(
        SimpleNamespace(observacoes=_alfred()), now=_AGORA))
    fed = fatos[("fred", "FEDFUNDS", "US")]
    assert fed["reference_period"] == "2026-09-01"
    assert fed["value"] == 4.08


def test_publicado_nao_corta_series_por_ordem_alfabetica():
    obs = [_obs("app4_domestic", f"serie_{i:02d}", date(2026, 9, 1), i, pais="BRA")
           for i in range(22)]
    obs += [_obs("world_bank", "NY.GDP.MKTP.KD.ZG", date(2025, 1, 1), 2.2, pais=p)
            for p in ("BRA", "USA")]
    fatos = _por_codigo(published_macro_context(SimpleNamespace(observacoes=obs),
                                                now=_AGORA))
    assert len(fatos) == 24
    assert ("world_bank", "NY.GDP.MKTP.KD.ZG", "USA") in fatos


def test_publicado_ignora_provedores_de_teste():
    obs = _alfred() + [_obs("test", "SERIES", date(2026, 1, 1), 1.2, pais=None),
                       _obs("test_repo", "SERIES", date(2026, 3, 1), 2.0, pais=None)]
    fatos = published_macro_context(SimpleNamespace(observacoes=obs), now=_AGORA)
    assert {f["provider"] for f in fatos} == {"fred"}


@pytest.mark.skipif(os.getenv("APP4_TEST_MACRO_TEMP_TABLES") != "1",
                    reason="requer Docker local e opt-in para tabelas temporárias")
def test_postgres_latest_escolhe_periodo_recente_sem_corte_nem_teste():
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    from core.config import settings
    from core.macro_data.context import latest_macro_context

    assert settings.MACRO_LOCAL_DB_URL
    assert make_url(settings.MACRO_LOCAL_DB_URL).host in {"localhost", "127.0.0.1", "::1"}
    engine = create_engine(settings.MACRO_LOCAL_DB_URL)
    try:
        with engine.connect() as conn:
            transaction = conn.begin()
            try:
                conn.execute(text(
                    "CREATE TEMP TABLE macro_indicators (provider text, provider_code text, "
                    "country_code text, name text, unit text) ON COMMIT DROP"))
                conn.execute(text(
                    "CREATE TEMP TABLE macro_observations (provider text, provider_code text, "
                    "country_code text, reference_period date, value numeric, "
                    "released_at timestamptz, retrieved_at timestamptz, is_forecast boolean, "
                    "is_preliminary boolean, vintage_date date) ON COMMIT DROP"))
                inserir = text(
                    "INSERT INTO macro_observations VALUES (:provider, :provider_code, "
                    ":country_code, :reference_period, :value, NULL, :retrieved_at, false, "
                    "false, :vintage_date)")
                linhas = _alfred()
                linhas += [_obs("app4_domestic", f"serie_{i:02d}", date(2026, 9, 1), i,
                                pais="BRA") for i in range(22)]
                linhas += [_obs("world_bank", "NY.GDP.MKTP.KD.ZG", date(2025, 1, 1), 2.2,
                                pais="USA"),
                           _obs("test", "SERIES", date(2026, 1, 1), 1.2, pais=None)]
                for linha in linhas:
                    conn.execute(inserir, {k: linha[k] for k in (
                        "provider", "provider_code", "country_code", "reference_period",
                        "value", "retrieved_at", "vintage_date")})

                class BoundEngine:
                    def connect(self):
                        return nullcontext(conn)

                fatos = _por_codigo(latest_macro_context(BoundEngine(), now=_AGORA))
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
    fed = fatos[("fred", "FEDFUNDS", "US")]
    assert fed["reference_period"] == "2026-09-01"
    assert float(fed["value"]) == 4.08
    assert ("world_bank", "NY.GDP.MKTP.KD.ZG", "USA") in fatos
    assert len(fatos) == 24
    assert not any(p == "test" for p, _, _ in fatos)
