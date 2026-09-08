# -*- coding: utf-8 -*-
"""A-134: o P/VP historico do FII dividia VPA por preco retroajustado.

``market.historical_prices.close`` nao e preco bruto -- e preco retroajustado
por split, e a docstring de ``load_fii_metrics_mensal`` afirmava o contrario.
Onde o fundo grupou ou desdobrou cotas, o numerador vinha na escala do ajuste e
o denominador na escala da epoca:

    FLRP11  P/VP exibido 0,0090  |  preco da B3 / VPA = 0,9038
    SJAU11  P/VP exibido   206   |  preco da B3 / VPA = 20,6

Medido no armazem local em 02/09/2026: 351 pares (fundo, mes) em 47 dos 284
fundos. E nao era so o grafico de ``views/fiis.py``:
``core/portfolio/adapters/fii.py`` entrega esta serie a
``core/global_portfolio/signals.py``, que compara o P/VP de hoje contra o
proprio historico. Historico cem vezes menor crava percentil 1,0, e
``1.0 - 2.0 * 1.0`` e o sinal maximo de "reduzir" -- o defeito chegava na
decisao invertido, nao so na tela.
"""
from __future__ import annotations

import pandas as pd
import pytest

import core.market_read as mr

FUNDO = "TESTE11"


def _metricas() -> pd.DataFrame:
    return pd.DataFrame([
        {"Data": "2025-01-01", "VPA": 100.0, "Patrimonio": 1e6, "Cotistas": 10,
         "DY_Patrimonial": 0.008, "Pct_Imoveis": 1.0, "Pct_Papel": 0.0,
         "Pct_Caixa": 0.0, "Pct_Fundos": 0.0},
    ])


def _instalar(monkeypatch, preco: pd.DataFrame) -> list[str]:
    """Troca ``_q`` por um dublê e devolve a lista de SQLs que ele viu."""
    vistos: list[str] = []

    def _fake(sql, params=None, engine=None):
        vistos.append(sql)
        if "fii_metrics_monthly" in sql:
            return _metricas()
        return preco.copy()

    monkeypatch.setattr(mr, "_q", _fake)
    mr.load_fii_metrics_mensal.clear()
    return vistos


def _fita(close: float) -> pd.DataFrame:
    return pd.DataFrame([{"date": "2025-01-31", "close": close}])


def test_preco_sai_da_fita_da_b3_e_nao_da_serie_retroajustada(monkeypatch):
    vistos = _instalar(monkeypatch, _fita(90.0))
    mr.load_fii_metrics_mensal(FUNDO)
    sql_preco = " ".join(s for s in vistos if "fii_metrics_monthly" not in s)
    assert "market.fii_b3_security_history" in sql_preco
    assert "market.historical_prices" not in sql_preco, (
        "serie retroajustada por split de volta no numerador do P/VP")


def test_pvp_e_preco_negociado_dividido_pelo_vpa(monkeypatch):
    _instalar(monkeypatch, _fita(90.0))
    met = mr.load_fii_metrics_mensal(FUNDO)
    assert float(met.loc[0, "P/VP"]) == pytest.approx(0.90)


def test_mes_sem_pregao_fica_nulo_em_vez_de_herdar_o_ajustado(monkeypatch):
    """Fundo fora da fita perde o P/VP daquele mes, e isso e deliberado.

    A fita cobre 7.446 dos 8.483 meses com VPA. Herdar ``historical_prices``
    nos 1.037 restantes seria preencher lacuna com o numero que pode estar do
    lado errado de um grupamento -- preenchimento que nunca contradiz.
    """
    _instalar(monkeypatch, pd.DataFrame(columns=["date", "close"]))
    met = mr.load_fii_metrics_mensal(FUNDO)
    assert pd.isna(met.loc[0, "P/VP"])
    assert met.attrs.get("pvp_load_error") is None


def test_falha_de_leitura_nao_se_disfarca_de_fundo_sem_preco(monkeypatch):
    """Erro tem que parecer erro. Sem a marca, a tela mostra a mesma serie
    vazia para 'nao consegui ler' e para 'nao ha pregao'."""
    vazio = pd.DataFrame(columns=["date", "close"])
    vazio.attrs["load_error"] = "query_failed"
    _instalar(monkeypatch, vazio)
    met = mr.load_fii_metrics_mensal(FUNDO)
    assert pd.isna(met.loc[0, "P/VP"])
    assert met.attrs.get("pvp_load_error") == "query_failed"
