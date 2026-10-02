from datetime import date

from core.proventos import serie_por_ativo


def _ev(ticker, total, pagamento, tipo="dividend"):
    return {"ticker": ticker, "tipo": tipo, "total_amount": total,
            "payment_date": pagamento}


EVENTOS = [
    _ev("BBAS3", 100.0, date(2021, 3, 10)),
    _ev("BBAS3", 50.0, date(2023, 6, 1)),
    _ev("BBAS3", 25.0, date(2023, 6, 20), tipo="jcp"),
    _ev("MXRF11", 10.0, date(2023, 1, 15), tipo="reit_income"),
    _ev("MXRF11", 999.0, date(2023, 2, 15), tipo="amortization"),
]


def test_anual_tem_anos_contiguos_com_zero_no_ano_sem_pagamento():
    d = serie_por_ativo(EVENTOS, "anual")
    assert d["periodos"] == ["2021", "2022", "2023"]
    assert d["series"]["BBAS3"] == [100.0, 0.0, 75.0]
    assert d["series"]["MXRF11"] == [0.0, 0.0, 10.0]


def test_amortizacao_nao_conta_como_provento():
    d = serie_por_ativo(EVENTOS, "anual")
    assert d["totais"]["MXRF11"] == 10.0


def test_ordem_por_total_decrescente():
    assert serie_por_ativo(EVENTOS)["ordem"] == ["BBAS3", "MXRF11"]


def test_mensal_dentro_de_um_ano_passado_tem_doze_meses():
    d = serie_por_ativo(EVENTOS, "mensal", ano=2023)
    assert len(d["periodos"]) == 12
    assert d["periodos"][0] == "Jan/23"
    assert d["series"]["BBAS3"][5] == 75.0
    assert d["series"]["MXRF11"][0] == 10.0


def test_mensal_historico_completo_e_contiguo():
    d = serie_por_ativo(EVENTOS, "mensal")
    assert d["periodos"][0] == "Mar/21"
    assert d["periodos"][-1] == "Jun/23"
    assert len(d["periodos"]) == 28


def test_filtro_de_tickers_e_payment_date_em_texto():
    eventos = [*EVENTOS, _ev("ITSA4", 5.0, "2022-08-01")]
    d = serie_por_ativo(eventos, "anual", tickers=["ITSA4"])
    assert d["ordem"] == ["ITSA4"]
    assert d["periodos"] == ["2022"]


def test_ano_corrente_nao_mostra_meses_futuros():
    hoje = date.today()
    d = serie_por_ativo([_ev("X", 1.0, date(hoje.year, 1, 5))], "mensal", ano=hoje.year)
    assert len(d["periodos"]) == hoje.month


def test_sem_eventos_devolve_estrutura_vazia():
    assert serie_por_ativo([], "anual")["ordem"] == []
