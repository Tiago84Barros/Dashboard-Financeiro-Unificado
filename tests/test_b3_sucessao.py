"""Sucessão de ticker na fita da B3: a troca de código liga pelos dados.

Datas e códigos reais do armazém em 08/10/2026.
"""
from datetime import date

import pandas as pd

from data_pipeline.market import b3_sucessao as bs
from scripts.publish_valuation_historico import (
    fita_pelo_universo,
    mercado_pela_sucessao,
)

D = date

INTERVALOS = [
    # ticker, primeiro pregão, último, primeiro fechamento, último
    ("EMBR3", D(2010, 1, 4), D(2025, 10, 31), 10.0, 80.0),
    ("EMBJ3", D(2025, 11, 3), D(2026, 10, 7), 81.0, 70.0),
    ("ELET3", D(2010, 1, 4), D(2025, 11, 7), 5.0, 60.0),
    ("AXIA3", D(2025, 11, 10), D(2026, 10, 7), 59.0, 55.0),
    ("ELET6", D(2010, 1, 4), D(2025, 11, 7), 7.0, 64.0),
    ("AXIA6", D(2025, 11, 10), D(2026, 6, 5), 63.0, 60.0),
    ("AXIA7", D(2025, 12, 22), D(2026, 10, 7), 50.0, 52.0),
    ("ELET5", D(2010, 2, 9), D(2025, 9, 26), 7.0, 60.0),
    ("AXIA5", D(2025, 12, 1), D(2026, 6, 1), 61.0, 60.0),
    ("CCRO3", D(2010, 1, 4), D(2025, 4, 30), 8.0, 12.0),
    ("MOTV3", D(2025, 5, 2), D(2026, 10, 7), 12.2, 14.0),
    ("NTCO3", D(2019, 12, 18), D(2025, 6, 27), 30.0, 10.0),
    ("NATU3", D(2025, 7, 1), D(2026, 10, 7), 10.1, 9.0),
]
CVM = {"EMBR3": 20087, "EMBJ3": 20087, "ELET3": 2437, "AXIA3": 2437,
       "ELET6": 2437, "AXIA6": 2437, "AXIA7": 2437, "ELET5": 2437,
       "AXIA5": 2437, "CCRO3": 18821, "MOTV3": 18821,
       "NTCO3": 24783, "NATU3": 19550}


def test_troca_de_nome_liga_pelo_codigo_cvm_e_pelas_datas():
    s = bs.derivar_sucessoes(INTERVALOS, CVM)

    assert s == {"EMBR3": "EMBJ3", "ELET3": "AXIA3", "ELET6": "AXIA6",
                 "CCRO3": "MOTV3"}


def test_classe_nova_e_lacuna_longa_nao_herdam():
    s = bs.derivar_sucessoes(INTERVALOS, CVM)

    # AXIA7 é classe nova; ELET5 parou em set e AXIA5 só começou em dez
    assert "AXIA7" not in s.values() and "ELET5" not in s


def test_reestruturacao_com_codigos_cvm_diferentes_nao_liga():
    assert "NTCO3" not in bs.derivar_sucessoes(INTERVALOS, CVM)


def test_troca_com_salto_de_preco_nao_liga():
    # grupamento no mesmo dia mudaria a escala do fechamento sem ajuste
    iv = [("XPTO3", D(2020, 1, 2), D(2025, 3, 3), 1.0, 1.0),
          ("NOVO3", D(2025, 3, 5), D(2026, 1, 2), 10.0, 10.0)]
    assert bs.derivar_sucessoes(iv, {"XPTO3": 1, "NOVO3": 1}) == {}


def test_dois_candidatos_e_reestruturacao_nao_troca_de_nome():
    iv = [("VELH3", D(2020, 1, 2), D(2025, 3, 3), 1.0, 1.0),
          ("NOVA3", D(2025, 3, 5), D(2026, 1, 2), 1.0, 1.0),
          ("OUTR3", D(2025, 3, 6), D(2026, 1, 2), 1.0, 1.0)]
    assert bs.derivar_sucessoes(iv, {"VELH3": 1, "NOVA3": 1, "OUTR3": 1}) == {}


def test_ticker_sem_codigo_cvm_herda_dos_irmaos_de_raiz():
    cvm = {"AXIA3": 2437, "AXIA7": 2437, "ELET3": 2437, "ELET6": 2437,
           "NOVA3": 1, "NOVA4": 2}
    out = bs.completar_cvm(["AXIA6", "NOVA11", "ECOM3"], cvm)

    assert out["AXIA6"] == 2437
    # raiz com códigos divergentes e raiz desconhecida ficam sem código
    assert "NOVA11" not in out and "ECOM3" not in out


def test_cadeia_de_duas_trocas():
    c = bs.cadeias({"A3": "B3", "B3": "C3"})
    assert c["A3"] == c["B3"] == c["C3"] == ["A3", "B3", "C3"]


def test_fita_do_codigo_novo_chega_ao_ticker_do_universo():
    fita = pd.DataFrame([
        ("ELET3", 2025, D(2025, 11, 7), 60.0),
        ("AXIA3", 2025, D(2025, 12, 30), 58.0),
        ("AXIA3", 2026, D(2026, 10, 7), 55.0),
        ("PETR4", 2026, D(2026, 10, 7), 30.0),
    ], columns=["ticker", "ano", "trade_date", "close"])

    out = fita_pelo_universo(fita, {"ELET3": "AXIA3"}, {"ELET3", "PETR4"},
                             ["ticker", "ano"])
    got = {(r.ticker, r.ano): r.close for r in out.itertuples()}

    # 2025 de ELET3 é o último pregão do ano -- já sob AXIA3
    assert got[("ELET3", 2025)] == 58.0
    assert got[("ELET3", 2026)] == 55.0
    assert got[("PETR4", 2026)] == 30.0
    # AXIA3 fora do universo segue com o que já tinha
    assert got[("AXIA3", 2026)] == 55.0 and ("AXIA3", 2025) in got


def test_codigo_novo_no_universo_recebe_o_historico_do_antigo():
    fita = pd.DataFrame([
        ("EMBR3", 2024, D(2024, 12, 30), 50.0),
        ("EMBJ3", 2026, D(2026, 10, 7), 70.0),
    ], columns=["ticker", "ano", "trade_date", "close"])

    out = fita_pelo_universo(fita, {"EMBR3": "EMBJ3"}, {"EMBR3", "EMBJ3"},
                             ["ticker", "ano"])
    got = {(r.ticker, r.ano): r.close for r in out.itertuples()}

    assert got[("EMBJ3", 2024)] == 50.0 and got[("EMBR3", 2026)] == 70.0


def test_sem_sucessao_a_fita_passa_igual():
    fita = pd.DataFrame([("PETR4", D(2026, 10, 7), 30.0)],
                        columns=["ticker", "trade_date", "close"])
    out = fita_pelo_universo(fita, {}, {"PETR4"}, ["ticker"])
    assert out.to_dict("records") == fita.to_dict("records")


def test_mercado_conta_a_empresa_renomeada_uma_vez_so():
    fech = pd.DataFrame([
        ("ELET3", D(2025, 10, 31), 60.0),
        ("ELET3", D(2025, 11, 7), 61.0),
        ("AXIA3", D(2025, 11, 28), 58.0),
        ("AXIA3", D(2026, 9, 30), 55.0),
    ], columns=["ticker", "data", "close"])
    vol = pd.DataFrame([("ELET3", 2025, 100.0), ("AXIA3", 2025, 30.0),
                        ("AXIA3", 2026, 50.0)], columns=["ticker", "ano", "vol"])

    f, v = mercado_pela_sucessao(fech, vol, {"ELET3": "AXIA3"},
                                 {"ELET3", "AXIA3"})

    assert set(f["ticker"]) == set(v["ticker"]) == {"ELET3"}
    # novembro: fica o último pregão do mês, já sob o código novo
    nov = f[pd.to_datetime(f["data"]).dt.month == 11]
    assert nov["close"].tolist() == [58.0]
    assert dict(zip(v["ano"], v["vol"])) == {2025: 130.0, 2026: 50.0}


def test_representante_e_o_primeiro_da_cadeia_com_dado():
    assert bs.representantes({"A3": "B3"}, {"A3", "B3"}) == {"A3": "A3", "B3": "A3"}
    assert bs.representantes({"A3": "B3"}, {"B3"}) == {"A3": "B3", "B3": "B3"}
