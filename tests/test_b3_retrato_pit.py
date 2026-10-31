"""Retrato PIT da safra: o quadro que o piso e o Score de Entrada teriam lido."""
from __future__ import annotations

import pandas as pd

from core.b3_retrato_pit import (
    COLUNAS_RETRATO,
    MAX_ANOS_PL,
    anos_hist_ate,
    historico_ate,
    quadro_decisao_pit,
    retrato_ate,
    series_pl_ate,
)


def _hist(anos, **extra):
    linhas = []
    for i, ano in enumerate(anos):
        linhas.append({
            "Data": pd.Timestamp(ano, 12, 31), "ROE": 0.10 + 0.01 * i,
            "DY": 0.05, "Payout": 0.40, "P/L": 8.0 + i,
            "AvailableAt": pd.Timestamp(ano + 1, 3, 20), **extra,
        })
    return pd.DataFrame(linhas)


def test_colunas_iguais_as_do_retrato_de_hoje():
    import core.market_read as mr
    assert COLUNAS_RETRATO == tuple(mr._MULT_COLS)


def test_max_anos_igual_ao_do_loader():
    import inspect

    from core.dossie_b3 import load_pl_lucro_anual_batch
    padrao = inspect.signature(load_pl_lucro_anual_batch).parameters["max_anos"].default
    assert MAX_ANOS_PL == padrao


def test_historico_corta_no_ano_base_e_some_quem_nao_tinha_ano():
    hb = {"AAAA3": _hist(range(2015, 2024)), "NOVA3": _hist([2022, 2023])}
    h = historico_ate(hb, 2019)
    assert set(h) == {"AAAA3"}
    assert pd.to_datetime(h["AAAA3"]["Data"]).dt.year.max() == 2019


def test_vintage_barra_balanco_publicado_depois_da_decisao():
    import views.empresas_b3 as emp

    df = _hist(range(2016, 2021))
    # o exercício 2019 só saiu em junho de 2020 -- depois da decisão de abril
    df.loc[df["Data"].dt.year == 2019, "AvailableAt"] = pd.Timestamp(2020, 6, 1)
    decisao_em = pd.Timestamp(2020, 4, 1)

    def aceita(recorte):
        return emp._classificar_disponibilidade_pit(recorte, decisao_em)[1]

    h = historico_ate({"AAAA3": df}, 2019, aceita)["AAAA3"]
    assert "_ano" not in h.columns
    assert pd.to_datetime(h["Data"]).dt.year.max() == 2018
    r = retrato_ate({"AAAA3": df}, 2019, aceita)
    assert pd.Timestamp(r.loc[0, "data"]).year == 2018


def test_retrato_e_a_ultima_linha_nas_colunas_de_hoje():
    r = retrato_ate({"aaaa3.SA": _hist(range(2015, 2020))}, 2018)
    assert list(r.columns) == ["Ticker", "data", *COLUNAS_RETRATO]
    assert r.loc[0, "Ticker"] == "AAAA3"
    assert r.loc[0, "P/L"] == 8.0 + 3  # exercício 2018
    assert pd.isna(r.loc[0, "FCO_Negativo"])


def test_retrato_vazio_mantem_colunas():
    r = retrato_ate({"AAAA3": _hist([2022])}, 2019)
    assert r.empty and list(r.columns) == ["Ticker", "data", *COLUNAS_RETRATO]


def test_serie_pl_corta_antes_do_teto():
    serie = [{"ano": a, "pl_mi": 100.0 + a, "lucro_mi": 10.0} for a in range(2000, 2024)]
    s = series_pl_ate({"AAAA3": serie}, 2015, max_anos=5)["AAAA3"]
    assert [x["ano"] for x in s] == [2011, 2012, 2013, 2014, 2015]


def test_anos_hist_desconta_os_exercicios_posteriores():
    hb = {"AAAA3": _hist(range(2015, 2024))}
    assert anos_hist_ate({"AAAA3": 9, "SEMH3": 4}, hb, 2019) == {"AAAA3": 5, "SEMH3": 4}
    assert anos_hist_ate({"AAAA3": 2}, hb, 2015) == {"AAAA3": 0}


def test_quadro_enriquecido_so_com_o_passado():
    hb = {"AAAA3": _hist(range(2012, 2024))}
    serie = {"AAAA3": [{"ano": a, "pl_mi": 100.0 * (a - 2005), "lucro_mi": 10.0}
                       for a in range(2008, 2024)]}
    q = quadro_decisao_pit(hb, serie, 2018)
    hoje = quadro_decisao_pit(hb, serie, 2023)
    assert len(q) == 1
    # a série de PL vai só até 2018: 2008..2018 são 11 anos, 10 pares;
    # hoje o teto de 12 anos dá 11 pares
    assert q.loc[0, "n_pares_pl"] == 10 and hoje.loc[0, "n_pares_pl"] == 11
    assert q.loc[0, "n_anos_payout"] < hoje.loc[0, "n_anos_payout"]
    assert pd.Timestamp(q.loc[0, "data"]).year == 2018
    assert q.loc[0, "P/L"] != hoje.loc[0, "P/L"]
