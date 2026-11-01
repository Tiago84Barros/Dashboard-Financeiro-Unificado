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


def test_selecao_e_pela_legenda_e_nao_por_widget():
    """Pedido de 31/10/2026: sem pílulas verdes; a legenda liga e desliga."""
    import views.investimentos as inv

    secao = inspect.getsource(inv._secao_desempenho_categorias)
    assert "st.pills(" not in secao and "st.multiselect(" not in secao
    corpo = inspect.getsource(inv._tab_analise)
    trecho = corpo[corpo.index("with ta:"):corpo.index("with tb:")]
    assert "_secao_desempenho_categorias(posicoes)" in trecho


def _serie(fim: float) -> pd.Series:
    return pd.Series([0.0, fim], index=pd.to_datetime(["2026-01-01", "2026-02-01"]))


def test_grafico_abre_com_acoes_etf_e_tesouro_e_o_resto_na_legenda():
    import views.investimentos as inv

    series = {c.nome: _serie(i + 1.0) for i, c in enumerate(dc.CATEGORIAS)}
    fig = inv._fig_desempenho_categorias(series)
    linhas = {t.name: t.visible for t in fig.data if t.showlegend is not False}
    assert set(linhas) == {c.nome for c in dc.CATEGORIAS}
    assert {n for n, v in linhas.items() if v is True} == set(dc.INICIAIS)
    assert all(v == "legendonly" for n, v in linhas.items() if n not in dc.INICIAIS)
    # Ponto final e rótulo seguem a linha: mesmo grupo, mesma visibilidade.
    for t in fig.data:
        dono = next(x for x in fig.data if x.name == t.legendgroup)
        assert t.visible == dono.visible
    assert fig.layout.dragmode is False


def test_sem_nenhuma_inicial_o_grafico_mostra_tudo():
    import views.investimentos as inv

    fig = inv._fig_desempenho_categorias({"Empresas Americanas": _serie(-3.0),
                                          "Renda Fixa": _serie(1.0)})
    assert all(t.visible is True for t in fig.data)
    textos = [t.text[0] for t in fig.data if t.mode == "text"]
    assert textos == ["<b>-3,0%</b>", "<b>+1,0%</b>"]


def test_rotulos_de_fim_de_linha_nao_se_sobrepoem():
    import views.investimentos as inv

    pos = inv._rotulos_sem_colisao([("A", 5.0), ("B", 5.1), ("C", 20.0)], folga=1.0)
    assert pos["B"] - pos["A"] >= 1.0 - 1e-9
    assert pos["A"] < pos["B"] < pos["C"]


def test_placar_ordena_do_melhor_para_o_pior():
    import views.investimentos as inv

    s = pd.Series([0.0, 1.0], index=pd.to_datetime(["2026-01-01", "2026-02-01"]))
    base = {"inicio": s.index[0], "cobertos": 1, "total": 1, "moeda": "BRL", "fonte": "x"}
    info = {"Empresas Americanas": {**base, "retorno": -3.0},
            "ETFs Americanos": {**base, "retorno": 7.5}}
    html = inv._cards_desempenho_categorias(
        {"Empresas Americanas": s, "ETFs Americanos": s}, info)
    assert html.index("ETFs Americanos") < html.index("Empresas Americanas")
    assert "+7,5%" in html and "-3,0%" in html


def test_hover_mostra_duas_casas_em_pt_br():
    """O texto do hover vai pronto: o d3 do hover unificado não formatava."""
    import views.investimentos as inv

    s = pd.Series([0.0, 19.525809911220506, -16.8],
                  index=pd.to_datetime(["2026-01-01", "2026-02-01", "2026-03-01"]))
    fig = inv._fig_desempenho_categorias({"ETFs Americanos": s})
    linha = next(t for t in fig.data if t.name == "ETFs Americanos")
    assert list(linha.customdata) == ["0,00%", "+19,53%", "-16,80%"]
    assert "%{customdata}" in linha.hovertemplate and "%{y" not in linha.hovertemplate


def test_amostra_semanal_so_guarda_valores_que_a_serie_teve():
    idx = pd.bdate_range("2026-01-05", "2026-02-27")   # 8 semanas úteis
    s = pd.Series(range(len(idx)), index=idx, dtype=float)
    a = dc.amostrar_para_grafico(s, "W")
    assert a.index[0] == idx[0] and a.iloc[0] == 0      # o 0% de partida fica
    assert a.index[-1] == idx[-1] and a.iloc[-1] == s.iloc[-1]
    assert len(a) == 9                                  # partida + 8 sextas
    assert all(s.loc[d] == v for d, v in a.items())     # nada interpolado


def test_amostra_nao_mexe_em_serie_mais_rala_que_a_frequencia():
    s = pd.Series([0.0, 1.0, 2.5, 3.0],
                  index=pd.date_range("2026-01-31", periods=4, freq="ME"))
    assert dc.amostrar_para_grafico(s, "W").equals(s)


def test_grafico_desenha_a_serie_amostrada_mas_o_rotulo_e_o_retorno_final():
    import views.investimentos as inv

    idx = pd.bdate_range("2026-01-05", "2026-06-30")
    s = pd.Series([i * 0.1 for i in range(len(idx))], index=idx)
    fig = inv._fig_desempenho_categorias({"ETFs Americanos": s}, "W")
    linha = next(t for t in fig.data if t.name == "ETFs Americanos")
    assert len(linha.x) < len(idx) / 4
    assert [t.text[0] for t in fig.data if t.mode == "text"] == [
        f"<b>+{s.iloc[-1]:.1f}%</b>".replace(".", ",")]


def test_pontos_ficam_mais_espacados_quanto_maior_o_periodo():
    import views.investimentos as inv

    assert inv._AMOSTRA_GRAFICO == {"6M": "W", "YTD": "W", "12M": "M", "24M": "M",
                                    "5Y": "Q"}
    cinco_anos = pd.Series(range(1300), index=pd.bdate_range("2021-11-01", periods=1300),
                           dtype=float)
    assert len(dc.amostrar_para_grafico(cinco_anos, "Q")) <= 22   # e não 1.300 dias
    idx = pd.bdate_range("2025-11-03", "2026-10-30")
    s = pd.Series([i * 0.05 for i in range(len(idx))], index=idx)
    a = dc.amostrar_para_grafico(s, "M")
    assert len(a) == 13                         # partida + 12 fins de mês
    assert a.iloc[-1] == s.iloc[-1] and a.iloc[0] == s.iloc[0]
    assert len(dc.amostrar_para_grafico(s, "Q")) <= 6
