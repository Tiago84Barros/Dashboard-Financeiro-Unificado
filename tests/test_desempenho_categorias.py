"""Desempenho por Categoria (Análise › Visão Geral): cálculo e montagem da tela."""
from __future__ import annotations

import inspect
from datetime import date

import pandas as pd
import pytest

from core import desempenho_categorias as dc


def _df(dados: dict, datas: list[str]) -> pd.DataFrame:
    return pd.DataFrame(dados, index=pd.to_datetime(datas))


def test_cada_categoria_casa_com_o_rotulo_que_a_carteira_grava():
    from core.investimentos import _CLASS_LABEL as rot

    assert len(dc.CATEGORIAS) == 6
    posicoes = [
        {"ticker": "PETR4", "classe": rot["stock"], "quantidade": 10},
        {"ticker": "HGLG11", "classe": rot["fii"], "quantidade": 5},
        {"ticker": "AAPL", "classe": rot["stock_us"], "quantidade": 1},
        {"ticker": "VOO", "classe": rot["etf_intl"], "quantidade": 2},
        {"ticker": "TIPCA2035", "classe": rot["tesouro"], "quantidade": 1.5},
        {"ticker": "CDB-X", "classe": rot["renda_fixa"], "quantidade": 1},
        {"ticker": "ZERO3", "classe": rot["stock"], "quantidade": 0},
    ]
    grupos = dc.posicoes_por_categoria(posicoes)
    assert list(grupos) == [c.nome for c in dc.CATEGORIAS]
    tickers = [[p["ticker"] for p in v] for v in grupos.values()]
    assert tickers == [["PETR4"], ["HGLG11"], ["AAPL"], ["VOO"], ["TIPCA2035"], ["CDB-X"]]
    assert "Empresas Americanas" in grupos and "ETFs Americanos" in grupos


def test_retorno_encadeado_de_um_ativo_e_o_retorno_do_preco():
    precos = _df({"A": [10.0, 11.0, 12.1]}, ["2026-01-01", "2026-01-02", "2026-01-05"])
    s = dc.retorno_encadeado(precos, {"A": 3})
    assert s.iloc[0] == 0
    assert s.iloc[-1] == pytest.approx(21.0)


def test_ativo_que_entra_depois_nao_cria_degrau():
    """B só tem preço a partir do 3º dia; no dia em que entra, a cesta não pula."""
    precos = _df({"A": [10.0, 10.0, 10.0, 10.0], "B": [None, None, 50.0, 55.0]},
                 ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"])
    s = dc.retorno_encadeado(precos, {"A": 1, "B": 1})
    assert list(s.round(6)[:3]) == [0, 0, 0]
    # último passo: valor 10+50=60 → 10+55=65
    assert s.iloc[-1] == pytest.approx((65 / 60 - 1) * 100)


def test_buraco_no_meio_herda_preco_mas_nao_estende_depois_do_fim():
    precos = _df({"A": [10.0, None, 12.0, None]},
                 ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"])
    s = dc.retorno_encadeado(precos, {"A": 1})
    assert len(s) == 3            # o dia 4, sem preço depois do último, sai
    assert s.iloc[1] == pytest.approx(0.0)
    assert s.iloc[-1] == pytest.approx(20.0)


def test_sem_quantidade_ou_sem_preco_devolve_vazio():
    precos = _df({"A": [10.0, 11.0]}, ["2026-01-01", "2026-01-02"])
    assert dc.retorno_encadeado(precos, {"B": 1}).empty
    assert dc.retorno_encadeado(_df({"A": [None, None]},
                                    ["2026-01-01", "2026-01-02"]), {"A": 1}).empty


def test_conversao_usa_o_ultimo_cambio_conhecido_no_dia():
    usd = _df({"VOO": [100.0, 110.0]}, ["2026-01-02", "2026-01-05"])
    fx = pd.Series([5.0, 5.5], index=pd.to_datetime(["2026-01-01", "2026-01-05"]))
    brl = dc.converter_para_brl(usd, fx)
    assert list(brl["VOO"]) == [500.0, 605.0]
    assert dc.converter_para_brl(usd, pd.Series(dtype=float)).empty


def test_renda_fixa_fica_com_uma_fonte_por_ativo():
    """A fonte com mais fotos vence; a outra não entra nem na data em comum."""
    linhas = pd.DataFrame([
        {"ticker": "CDB", "data": "2026-01-31", "fonte": "b3", "preco": 1000.0},
        {"ticker": "CDB", "data": "2026-02-28", "fonte": "b3", "preco": 1010.0},
        {"ticker": "CDB", "data": "2026-02-28", "fonte": "xp", "preco": 1.01},
        {"ticker": "LCI", "data": "2026-02-28", "fonte": "xp", "preco": 2000.0},
    ])
    u = dc.unitario_de_fonte_unica(linhas)
    assert list(u["CDB"]) == [1000.0, 1010.0]
    assert u["LCI"].dropna().tolist() == [2000.0]


def test_inicio_do_periodo():
    assert dc.inicio_do_periodo(None, date(2026, 10, 31)) == date(2026, 1, 1)
    assert dc.inicio_do_periodo(10, date(2026, 10, 31)) == date(2026, 10, 21)


def test_tela_tem_selecao_do_que_comparar_na_visao_geral():
    import views.investimentos as inv

    secao = inspect.getsource(inv._secao_desempenho_categorias)
    assert "st.multiselect(" in secao
    assert "analise_vg_categorias_sel" in secao
    corpo = inspect.getsource(inv._tab_analise)
    trecho = corpo[corpo.index("with ta:"):corpo.index("with tb:")]
    assert "_secao_desempenho_categorias(posicoes)" in trecho


def test_figura_desenha_so_as_selecionadas():
    import views.investimentos as inv

    s = pd.Series([0.0, 5.0], index=pd.to_datetime(["2026-01-01", "2026-02-01"]))
    fig = inv._fig_desempenho_categorias(
        {"Empresas Americanas": s, "ETFs Americanos": s * 2}, ["ETFs Americanos"])
    assert [t.name for t in fig.data] == ["ETFs Americanos (+10.0%)"]
