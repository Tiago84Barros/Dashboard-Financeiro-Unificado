"""As definições de ``scripts/medir_proventos_b3.py``, com dados sintéticos.

Os números citados em ``core.proventos_b3`` e em
``core.dividend_types.renda_por_evento`` saem deste script. Cada teste fixa uma
das definições do docstring dele: junção externa, tolerância, corte dos anos
fechados, as doze competências da CVM, o pagamento no mês seguinte e a faixa
de sensibilidade a essas convenções.
"""
from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest

from scripts import medir_proventos_b3 as m


def _anual(*linhas):
    return pd.DataFrame(list(linhas), columns=["ticker", "ano", "total"])


def _obs(ticker, competencias, *, dy=0.01, nav=100.0, source=m.FONTE_CVM):
    linhas = [(ticker, ref, metrica, source, valor)
              for ref in competencias
              for metrica, valor in (("dy_patrimonial_mes", dy), ("nav_per_share", nav))]
    return pd.DataFrame(linhas, columns=["ticker", "reference_date", "metric_name",
                                         "source", "value_numeric"])


def _meses(inicio, n):
    return pd.date_range(inicio, periods=n, freq="MS")


# ── concordância ────────────────────────────────────────────────────────────

def test_ano_de_um_lado_so_discorda_e_ticker_fora_do_universo_nao_entra():
    ref = _anual(("AAAA3", 2023, 1.00), ("AAAA3", 2024, 2.00), ("BBBB3", 2024, 1.0))
    cand = _anual(("AAAA3", 2023, 1.009), ("AAAA3", 2025, 0.5), ("BBBB3", 2024, 9.0))
    r = m.concordancia(ref, cand, {"AAAA3"})
    # 2023 bate a 0,9%; 2024 só na referência e 2025 só no candidato discordam
    assert r == {"ticker_anos": 3, "batem": 1, "fracao": pytest.approx(1 / 3)}


def test_tolerancia_de_um_por_cento_e_corte_dos_anos_fechados():
    ref = _anual(("AAAA3", 2023, 1.00), ("AAAA3", 2026, 0.4))
    cand = _anual(("AAAA3", 2023, 1.02), ("AAAA3", 2026, 0.4))
    assert m.concordancia(ref, cand, {"AAAA3"})["batem"] == 1  # só 2026; 2023 erra 2%
    fechados = m.concordancia(ref, cand, {"AAAA3"}, ate_ano=2025)
    assert (fechados["ticker_anos"], fechados["batem"]) == (1, 0)


def test_sem_ticker_ano_a_fracao_e_nula():
    assert m.concordancia(_anual(), _anual(), {"AAAA3"})["fracao"] is None


# ── CVM ─────────────────────────────────────────────────────────────────────

def test_competencia_paga_no_mes_seguinte_e_ano_exige_doze_competencias():
    # dez/2022 a nov/2023: os doze pagamentos caem em 2023
    completo = _obs("AAAA11", _meses("2022-12-01", 12))
    # jan a dez/2023: 11 pagamentos em 2023 e 1 em 2024; nenhum ano fecha
    deslocado = _obs("BBBB11", _meses("2023-01-01", 12))
    r = m.rendimento_cvm(pd.concat([completo, deslocado]), ate_ano=2025)
    assert r.to_dict("records") == [{"ticker": "AAAA11", "ano": 2023, "total": 12.0}]


def test_mes_sem_uma_das_metricas_nao_fecha_o_ano():
    obs = _obs("AAAA11", _meses("2022-12-01", 12))
    obs = obs[~((obs["reference_date"] == "2023-05-01")
                & (obs["metric_name"] == "nav_per_share"))]
    assert m.rendimento_cvm(obs, ate_ano=2025).empty


def test_valor_patrimonial_de_outra_fonte_nao_entra_na_referencia():
    cvm = _obs("AAAA11", _meses("2022-12-01", 12))
    # a brapi grava nav_per_share na mesma tabela, no mesmo mês, com outro valor
    brapi = _obs("AAAA11", _meses("2022-12-01", 12), nav=500.0, source="brapi_fii_v2")
    brapi = brapi[brapi["metric_name"] == "nav_per_share"]
    r = m.rendimento_cvm(pd.concat([brapi, cvm]), ate_ano=2025)
    assert r.to_dict("records") == [{"ticker": "AAAA11", "ano": 2023, "total": 12.0}]


def test_metrica_ausente_inteira_nao_derruba_a_medida():
    obs = _obs("AAAA11", _meses("2022-12-01", 12))
    so_nav = obs[obs["metric_name"] == "nav_per_share"]
    assert m.rendimento_cvm(so_nav, ate_ano=2025).empty
    # só o valor patrimonial da brapi, sem nada da CVM
    brapi = _obs("AAAA11", _meses("2022-12-01", 12), source="brapi_fii_v2")
    assert m.rendimento_cvm(brapi[brapi["metric_name"] == "nav_per_share"],
                            ate_ano=2025).empty


def test_ano_aberto_fica_fora_da_cvm():
    obs = _obs("AAAA11", _meses("2022-12-01", 12))
    assert m.rendimento_cvm(obs, ate_ano=2022).empty


def test_defasagem_desloca_o_ano_do_pagamento():
    # jan a dez/2023: pago no mês fecha 2023; um mês depois, nenhum ano fecha
    obs = _obs("AAAA11", _meses("2023-01-01", 12))
    assert m.rendimento_cvm(obs, ate_ano=2025, defasagem=0)["ano"].tolist() == [2023]
    assert m.rendimento_cvm(obs, ate_ano=2025, defasagem=1).empty
    # nov/2022 a out/2023, pago dois meses depois: jan a dez/2023
    obs = _obs("AAAA11", _meses("2022-11-01", 12))
    assert m.rendimento_cvm(obs, ate_ano=2025, defasagem=2)["ano"].tolist() == [2023]


def test_competencia_no_fim_do_mes_cai_no_mesmo_ano_de_pagamento():
    # o informe pode datar a competência no último dia do mês
    obs = _obs("AAAA11", pd.date_range("2022-12-31", periods=12, freq="ME"))
    assert m.rendimento_cvm(obs, ate_ano=2025)["ano"].tolist() == [2023]


def test_contra_cvm_junta_pelo_arquivo_e_ano_ausente_erra_cem_por_cento():
    cvm = _anual(("AAAA11", 2023, 10.0), ("BBBB11", 2023, 10.0), ("CCCC11", 2023, 10.0))
    arquivo = _anual(("AAAA11", 2023, 10.2), ("BBBB11", 2023, 9.2))  # CCCC11 fora
    metodo = _anual(("AAAA11", 2023, 10.2))  # sem BBBB11/2023: conta como zero
    r = m.contra_cvm(cvm, {"arquivo": arquivo, "metodo": metodo}, arquivo)
    assert (r["ticker_anos"], r["tickers"]) == (2, 2)
    assert (r["arquivo"]["ate_5pct"], r["arquivo"]["ate_10pct"]) == (0.5, 1.0)
    assert (r["metodo"]["ate_5pct"], r["metodo"]["ate_10pct"]) == (0.5, 0.5)
    assert r["metodo"]["erro_mediano"] == pytest.approx((0.02 + 1.0) / 2)


def test_juncao_esquerda_conta_o_ano_que_o_arquivo_nao_tem():
    cvm = _anual(("AAAA11", 2022, 10.0), ("AAAA11", 2023, 10.0), ("CCCC11", 2023, 10.0))
    arquivo = _anual(("AAAA11", 2023, 10.2))  # sem 2022; CCCC11 fora do arquivo
    interna = m.contra_cvm(cvm, {"arquivo": arquivo}, arquivo)
    esquerda = m.contra_cvm(cvm, {"arquivo": arquivo}, arquivo, juncao="esquerda")
    assert (interna["ticker_anos"], interna["arquivo"]["ate_5pct"]) == (1, 1.0)
    # 2022 entra e o arquivo, sem o ano, erra 100%; CCCC11 continua fora
    assert (esquerda["ticker_anos"], esquerda["tickers"]) == (2, 1)
    assert esquerda["arquivo"]["ate_5pct"] == 0.5
    with pytest.raises(ValueError):
        m.contra_cvm(cvm, {"arquivo": arquivo}, arquivo, juncao="externa")


# ── regra do mínimo e medição de ponta a ponta ──────────────────────────────

def test_com_minimo_fica_com_o_menor_valor_de_mesma_data_ex_e_pagamento():
    ev = pd.DataFrame({
        "ticker": ["AAAA3"] * 3, "type": ["DIVIDENDO"] * 3,
        "ex_date": pd.to_datetime(["2024-03-01", "2024-03-01", "2024-06-01"]),
        "payment_date": pd.to_datetime(["2024-03-20", "2024-03-20", "2024-06-20"]),
        "amount": [0.30, 0.10, 0.20]})
    assert sorted(m.com_minimo(ev)["amount"]) == [0.10, 0.20]


def _bruto():
    linhas = [
        # a revisão da data de pagamento: o mesmo provento gravado duas vezes
        ("AAAA3", "DIVIDENDO", "2023-03-01", "2023-03-20", 1.0, "2023-03-02"),
        ("AAAA3", "DIVIDENDO", "2023-03-01", "2023-03-21", 1.0, "2023-04-01"),
        ("AAAA3", "DIVIDENDO", "2024-03-01", "2024-03-20", 1.0, "2024-03-02"),
    ] + [("AAAA11", "RENDIMENTO", f"2023-{mes:02d}-01", f"2023-{mes:02d}-15", 1.0,
          f"2023-{mes:02d}-02") for mes in range(1, 13)]
    d = pd.DataFrame(linhas, columns=["ticker", "type", "ex_date", "payment_date",
                                      "amount", "created_at"])
    return d.assign(event_date=d["payment_date"])


def _publicado():
    return {"gerado_em": "2025-05-31T00:00:00+00:00", "foto_mais_nova": "2025-05-30",
            "por_ticker": {"AAAA3": {"anos": {"2023": 1.0, "2024": 1.0}},
                           "AAAA11": {"anos": {"2023": 12.0}}}}


def _crua():
    return _anual(("AAAA3", 2023, 2.0), ("AAAA3", 2024, 1.0), ("AAAA11", 2023, 12.0))


def test_medir_de_ponta_a_ponta(capsys):
    obs = _obs("AAAA11", _meses("2022-12-01", 12))

    r = m.medir(_publicado(), _crua(), _bruto(), obs, hoje=date(2025, 6, 1))

    assert r["arquivo"]["tickers"] == 2
    circ = r["contra_a_brapi_circular"]
    # a soma crua conta a revisão duas vezes; a regra de leitura, uma
    assert circ["soma crua de market.dividends"]["todos os anos"]["batem"] == 2
    assert circ["renda_por_evento"]["anos fechados"]["fracao"] == 1.0
    cvm = r["contra_a_cvm_fii"]
    assert (cvm["ticker_anos"], cvm["tickers"]) == (1, 1)
    assert cvm["arquivo"]["ate_5pct"] == 1.0
    json.dumps(r, allow_nan=False)  # o --json grava este dicionário

    sens = r["contra_a_cvm_sensibilidade"]
    variantes = {(v["defasagem_meses"], v["juncao"]): v for v in sens["variantes"]}
    assert set(variantes) == {(d, j) for d in m.DEFASAGENS_CVM for j in m.JUNCOES_CVM}
    # a medida principal é a variante de um mês pela junção interna
    principal = {k: v for k, v in variantes[(1, "interna")].items()
                 if k not in ("defasagem_meses", "juncao")}
    assert principal == cvm
    # dez/2022 a nov/2023 só fecha um ano com o pagamento um mês depois: as
    # outras variantes ficam sem ticker-ano e não entram na faixa
    assert variantes[(0, "interna")]["ticker_anos"] == 0
    assert sens["faixa_do_arquivo"]["ate_5pct"] == {"min": 1.0, "max": 1.0}
    m._imprime(r)
    assert "faixa do arquivo até 5%: 100.0% a 100.0%" in capsys.readouterr().out


def test_sem_ticker_ano_da_cvm_o_relatorio_sai_e_diz_indisponivel(capsys):
    # só o valor patrimonial da brapi: nenhum ano da CVM fecha
    brapi = _obs("AAAA11", _meses("2022-12-01", 12), source="brapi_fii_v2")
    r = m.medir(_publicado(), _crua(), _bruto(), brapi[brapi["metric_name"] == "nav_per_share"],
                hoje=date(2025, 6, 1))
    cvm = r["contra_a_cvm_fii"]
    assert (cvm["ticker_anos"], cvm["tickers"]) == (0, 0)
    assert cvm["arquivo"] == {"ate_5pct": None, "ate_10pct": None, "erro_mediano": None}
    assert r["contra_a_cvm_sensibilidade"]["faixa_do_arquivo"] == {
        "ate_5pct": None, "ate_10pct": None}
    json.dumps(r, allow_nan=False)
    m._imprime(r)
    saida = capsys.readouterr().out
    assert "comparação indisponível" in saida
    assert "renda_por_evento" in saida  # a parte circular continua no relatório


def test_principal_vazia_nao_esconde_a_variante_que_fecha_o_ano(capsys):
    # jan a dez/2023 fecha 2023 com o pagamento no mesmo mês, não um mês depois
    r = m.medir(_publicado(), _crua(), _bruto(), _obs("AAAA11", _meses("2023-01-01", 12)),
                hoje=date(2025, 6, 1))
    assert r["contra_a_cvm_fii"]["ticker_anos"] == 0
    assert r["contra_a_cvm_sensibilidade"]["faixa_do_arquivo"]["ate_5pct"] == {
        "min": 1.0, "max": 1.0}
    m._imprime(r)
    saida = capsys.readouterr().out
    assert "comparação indisponível" in saida
    assert "pagamento +0 meses, junção interna" in saida
    assert "faixa do arquivo até 5%: 100.0% a 100.0%" in saida
