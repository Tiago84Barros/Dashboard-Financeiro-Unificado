"""B3-06/07: papel PARADO sai da elegibilidade por época, sem olhar o futuro.

A mediana da janela só via os meses com negócio e o ticker sem linha na janela
caía na regra da ausência — quem parou havia anos seguia elegível.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from core import b3_universo_pit as upit


def _vol(linhas):
    return pd.DataFrame(linhas, columns=["ticker", "mes", "financeiro"])


def _meses(tk, ini: date, fim: date, valor=21 * 5_000_000.0):
    return [(tk, d.date(), valor) for d in pd.date_range(ini, fim, freq="MS")]


def test_parou_em_novembro_passa_pelo_piso_mas_sai_como_parado():
    # Outubro e novembro com giro enorme: a mediana da janela (out-mar) passa
    # folgada pelo piso. Era exatamente o furo.
    linhas = _meses("VIVO3", date(2014, 1, 1), date(2015, 3, 1))
    linhas += _meses("PARO3", date(2014, 1, 1), date(2014, 11, 1),
                     valor=21 * 90_000_000.0)
    el = upit.elegiveis_por_ano(_vol(linhas), [2015], 1_000_000.0)
    assert el[2015]["abaixo"] == set()
    assert el[2015]["parados"] == {"PARO3"}
    assert upit.filtrar(["VIVO3", "PARO3"], el, 2015) == ["VIVO3"]


def test_parado_ha_anos_sem_linha_na_janela_sai():
    # Antes caía na regra da ausência (sem linha na janela = elegível).
    linhas = _meses("VIVO3", date(2012, 1, 1), date(2015, 3, 1))
    linhas += _meses("AMRT3", date(2012, 1, 1), date(2013, 6, 1))
    el = upit.elegiveis_por_ano(_vol(linhas), [2015], 0.0)
    assert upit.filtrar(["VIVO3", "AMRT3"], el, 2015) == ["VIVO3"]


def test_quem_nunca_apareceu_no_quadro_continua_elegivel():
    linhas = _meses("VIVO3", date(2014, 1, 1), date(2015, 3, 1))
    el = upit.elegiveis_por_ano(_vol(linhas), [2015], 0.0)
    assert upit.filtrar(["SEMDADO3", "VIVO3"], el, 2015) == ["SEMDADO3", "VIVO3"]


def test_fronteira_de_90_dias():
    # Último mês dezembro: fim em 01/01, 90 dias até 01/04 — não é parado.
    # Último mês novembro: 121 dias — parado.
    linhas = _meses("VIVO3", date(2014, 1, 1), date(2015, 3, 1))
    linhas += _meses("DEZE3", date(2014, 1, 1), date(2014, 12, 1))
    linhas += _meses("NOVE3", date(2014, 1, 1), date(2014, 11, 1))
    assert upit.parados_em(_vol(linhas), date(2015, 4, 1)) == {"NOVE3"}


def test_nao_olha_o_futuro():
    # Voltou a negociar DEPOIS da decisão: na data da decisão estava parado.
    linhas = _meses("VIVO3", date(2014, 1, 1), date(2016, 3, 1))
    linhas += _meses("VOLT3", date(2014, 1, 1), date(2014, 6, 1))
    linhas += _meses("VOLT3", date(2015, 5, 1), date(2016, 3, 1))
    # Estreou depois da decisão: não existe no quadro, não é parado.
    linhas += _meses("NOVO3", date(2015, 6, 1), date(2016, 3, 1))
    el = upit.elegiveis_por_ano(_vol(linhas), [2015, 2016], 0.0)
    assert el[2015]["parados"] == {"VOLT3"}
    assert el[2016]["parados"] == set()
    assert upit.filtrar(["VOLT3", "NOVO3"], el, 2015) == ["NOVO3"]


def test_ingestao_inteira_parada_nao_vira_parado():
    # Ninguém tem dado depois de agosto: a referência é o mercado, não a data.
    linhas = _meses("AAAA3", date(2025, 1, 1), date(2025, 8, 1))
    linhas += _meses("BBBB3", date(2025, 1, 1), date(2025, 8, 1))
    linhas += _meses("PARO3", date(2025, 1, 1), date(2025, 3, 1))
    assert upit.parados_em(_vol(linhas), date(2026, 1, 15)) == {"PARO3"}


def test_volume_zero_nao_conta_como_negocio_e_regra_desligavel():
    linhas = _meses("VIVO3", date(2014, 1, 1), date(2015, 3, 1))
    linhas += _meses("ZERO3", date(2014, 1, 1), date(2014, 6, 1))
    linhas += _meses("ZERO3", date(2014, 7, 1), date(2015, 3, 1), valor=0.0)
    assert upit.parados_em(_vol(linhas), date(2015, 4, 1)) == {"ZERO3"}
    el = upit.elegiveis_por_ano(_vol(linhas), [2015], 0.0, dias_parado=None)
    assert el[2015]["parados"] == set()


# ── No segmento: parado sai das safras novas e da carteira atual ─────────────


def _hist_seg(tks: list[str]) -> dict[str, pd.DataFrame]:
    anos = list(range(2016, pd.Timestamp.now().year))
    return {
        tk: pd.DataFrame({
            "Ticker": tk,
            "Data": [pd.Timestamp(a, 12, 31) for a in anos],
            "ROE": [0.12 + i * 0.03] * len(anos),
            "ROIC": [0.10 + i * 0.02] * len(anos),
            "Margem_Liquida": [0.08] * len(anos),
            "P/L": [8.0] * len(anos),
            "P/VP": [1.2] * len(anos),
            "DY": [0.04] * len(anos),
            "AvailableAt": [pd.Timestamp(a + 1, 3, 20) for a in anos],
        })
        for i, tk in enumerate(tks)
    }


def test_segmento_parado_concorre_ate_parar_e_nao_entra_na_carteira_atual():
    from views import portfolio_b3 as portfolio

    hoje = pd.Timestamp.now()
    tks = ["AAAA3", "BBBB3", "CCCC3", "PARA3"]  # PARA3 tem o melhor ROE
    idx = pd.date_range("2016-01-31", periods=(hoje.year - 2016) * 12, freq="ME")
    precos = pd.DataFrame({
        tk: 100.0 * np.cumprod(np.full(len(idx), 1.004 + i * 0.001))
        for i, tk in enumerate(tks)
    }, index=idx)
    linhas = []
    for tk in tks[:3]:
        linhas += _meses(tk, date(2017, 1, 1), hoje.date().replace(day=1))
    linhas += _meses("PARA3", date(2017, 1, 1), date(2020, 6, 1))
    vol = _vol(linhas)
    el = upit.elegiveis_por_ano(vol, list(range(2018, hoje.year)), 0.0)
    parados_hoje = upit.parados_em(vol, hoje.date())
    assert parados_hoje == {"PARA3"}
    res = portfolio._processar_segmento(
        tks, _hist_seg(tks), precos, "S", "SS", "SEG",
        taxa_selic_aa=0.0, selic_macro={}, macro_history={}, aporte=1000.0,
        ano_inicio=2018, gamma=1.0, cap=1.0, soft=0.0,
        elegibilidade_pit=el, parados_hoje=parados_hoje,
    )
    assert res is not None
    anos_com_parado = {a for a, lids in res["lids_por_ano"].items() if "PARA3" in lids}
    # Concorre (e lidera) enquanto negociava; a safra 2020 é decidida em
    # 01/04/2020, com negócio até março. A partir de 2021 está parado.
    assert {2018, 2019, 2020} <= anos_com_parado
    assert not {a for a in anos_com_parado if a >= 2021}
    assert "PARA3" not in res["score_proximo"]
    assert "PARA3" not in res["lids_prox"]
    # Continua no segmento: os anos em que liderou entram no backtest.
    assert "PARA3" in res["tickers"]


# ── Universo: PN/Unit herdam a taxonomia do ON pela raiz ─────────────────────


def test_leitor_do_universo_herda_setor_pela_raiz_com_exato_primeiro(monkeypatch):
    # A auditoria contou 336 de 460 ativos em public.setores lendo a TABELA, que
    # guarda uma classe por empresa de propósito (jobs de ingestão a usam como
    # universo de uma linha por empresa). O universo da tela vem deste leitor,
    # que casa PETR4/ITUB4/BBDC4 pela raiz: 445 ativos no armazém em 04/10.
    # Se a herança sumir, essas classes saem da tela em silêncio.
    import re

    from core import market_read as mr

    capturado: list[str] = []

    def fake_q(sql, params=None, engine=None):
        capturado.append(sql)
        return pd.DataFrame()

    monkeypatch.setattr(mr, "_q", fake_q)
    mr.load_setores.__wrapped__()
    sql = re.sub(r"\s+", " ", capturado[0])
    assert ("LEFT(UPPER(REPLACE(s2.ticker, '.SA', '')), 4) = LEFT(a.ticker, 4)"
            in sql)
    assert "ORDER BY (UPPER(REPLACE(s2.ticker, '.SA', '')) = a.ticker) DESC" in sql
