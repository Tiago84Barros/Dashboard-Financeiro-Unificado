"""Régua da §29: renda e valorização pelo pregão diário, trajetória e veredito."""
from datetime import date

import pytest

import core.b3_comparacao_estrategias as ce


def test_carteira_renormaliza_sobre_quem_tem_dado():
    y, cob = ce.rendimento_da_carteira({"A": 0.5, "B": 0.25, "C": 0.25},
                                       {"A": 0.06, "B": 0.03, "C": None})
    assert y == pytest.approx((0.5 * 0.06 + 0.25 * 0.03) / 0.75)
    assert cob == pytest.approx(0.75)
    assert ce.rendimento_da_carteira({"A": 1.0}, {}) == (None, 0.0)


def test_trajetoria_gasta_a_renda_e_cresce_so_pelo_preco():
    # Exemplo da revisão: preço 10 -> 11 e 5% de provento reinvestido.
    fator = 0.05
    t = ce.trajetoria_de_renda([
        {"safra": 2017, "retorno": 0.155, "rendimento": fator, "renda_caixa": 0.048,
         "valorizacao": 0.10},
        {"safra": 2018, "retorno": 0.155, "rendimento": fator, "renda_caixa": 0.048,
         "valorizacao": 0.10},
    ], capital=100.0)
    # O capital cresce só pelo preço: 100 x 1,10 = 110 (não 110,50).
    assert t["anos"][1]["capital_inicio"] == pytest.approx(110.0)
    assert t["capital_final"] == pytest.approx(121.0)
    # A renda é o caixa recebido, não o fator.
    assert t["anos"][0]["renda"] == pytest.approx(4.8)
    assert t["anos"][1]["renda"] == pytest.approx(110.0 * 0.048)
    assert t["crescimento_renda_aa"] == pytest.approx(0.10)
    assert t["quedas_de_renda"] == 0 and t["insuficiente"] is False
    assert t["renda_total"] == pytest.approx(4.8 + 5.28)


def test_ano_sem_rendimento_deixa_a_trajetoria_insuficiente_e_nao_vira_zero():
    t = ce.trajetoria_de_renda([
        {"safra": 2017, "rendimento": 0.05, "renda_caixa": 0.05, "valorizacao": 0.0},
        {"safra": 2018, "rendimento": None, "renda_caixa": None, "valorizacao": None},
        {"safra": 2019, "rendimento": 0.05, "renda_caixa": 0.05, "valorizacao": 0.0},
    ], capital=100.0)
    assert t["insuficiente"] is True and t["safras_sem_rendimento"] == [2018]
    for k in ("capital_final", "renda_total", "crescimento_renda_aa",
              "quedas_de_renda", "renda_ultimo_sobre_primeiro"):
        assert t[k] is None, k
    assert t["anos"][1]["renda"] is None and t["anos"][2]["capital_inicio"] is None
    # Renda zero MEDIDA continua sendo dado: tendência indefinida, queda contada.
    z = ce.trajetoria_de_renda([
        {"safra": 2017, "rendimento": 0.05, "renda_caixa": 0.05, "valorizacao": 0.0},
        {"safra": 2018, "rendimento": 0.0, "renda_caixa": 0.0, "valorizacao": 0.0},
    ], capital=100.0)
    assert z["insuficiente"] is False
    assert z["crescimento_renda_aa"] is None and z["quedas_de_renda"] == 1


def test_pregao_separa_fator_caixa_e_valorizacao():
    # Preço 10 -> 10,5 -> 11; provento de 0,50 por ação no último dia.
    # O motor dá 1 + ret = (k x preco + caixa) / P_{t-1}.
    linhas = [(10.0, None, 1.0, 0.0),
              (10.5, 0.05, 1.0, 0.0),
              (11.0, (11.0 + 0.5) / 10.5 - 1.0, 1.0, 0.5)]
    y = ce.rendimentos_do_pregao(linhas)
    # Caixa = 0,50 / 10 (capital do início).
    assert y["caixa"] == pytest.approx(0.05)
    assert y["preco"] == pytest.approx(0.10)
    # 1 + retorno = (1 + valorização) x (1 + fator).
    assert (1 + y["preco"]) * (1 + y["fator"]) == pytest.approx(11.5 / 10.0)
    # Sem provento: fator e caixa zero.
    z = ce.rendimentos_do_pregao([(10.0, None, 1.0, 0.0), (11.0, 0.10, 1.0, 0.0)])
    assert z == {"fator": 0.0, "caixa": 0.0, "preco": pytest.approx(0.10)}


def test_pregao_desdobramento_multiplica_acoes_e_caixa():
    # Desdobramento 2:1 no 2º dia (k = 2, preço 20 -> 10); provento de 0,50 por
    # ação nova no 3º: quem tinha uma ação recebe 2 x 0,50 = 1,00.
    linhas = [(20.0, None, 1.0, 0.0),
              (10.0, 0.0, 2.0, 0.0),
              (10.0, 0.5 / 10.0, 1.0, 0.5)]
    y = ce.rendimentos_do_pregao(linhas)
    assert y["preco"] == pytest.approx(0.0)      # 2 x 10 / 20 - 1
    assert y["caixa"] == pytest.approx(1.0 / 20.0)
    assert y["fator"] == pytest.approx(0.05)


def test_pregao_sem_retorno_conta_zero_e_dado_ruim_sai():
    # ret vazio (salto sem evento): o motor põe k = P_ant / P e caixa 0.
    y = ce.rendimentos_do_pregao([(10.0, None, 1.0, 0.0),
                                  (20.0, float("nan"), 0.5, 0.0)])
    assert y["preco"] == pytest.approx(0.0) and y["fator"] == 0.0
    assert ce.rendimentos_do_pregao([(10.0, None, 1.0, 0.0)]) is None
    assert ce.rendimentos_do_pregao([(0.0, None, 1.0, 0.0), (10.0, 0.0, 1.0, 0.0)]) is None
    assert ce.rendimentos_do_pregao([(10.0, None, 1.0, 0.0), (10.0, 0.0, 0.0, 0.0)]) is None
    # Caixa de 150% do capital é dado ruim, não renda.
    assert ce.rendimentos_do_pregao([(10.0, None, 1.0, 0.0),
                                     (10.0, 1.5, 1.0, 15.0)]) is None
    # Ruído negativo pequeno vira zero.
    r = ce.rendimentos_do_pregao([(10.0, None, 1.0, 0.0), (10.0, -0.01, 1.0, 0.0)])
    assert r["fator"] == 0.0 and r["preco"] == pytest.approx(0.0)


def test_eventos_de_acoes_filtra_rotulo_papel_e_fator():
    dd = {
        "cashDividends": [{"isinCode": "BRXXXXACNOR0"}, {"isinCode": "BRXXXXACNOR0"},
                          {"isinCode": "BRXXXXR01OR0"}],
        "stockDividends": [
            {"label": "DESDOBRAMENTO", "factor": 5, "lastDatePrior": "2021-05-03T00:00:00Z",
             "assetIssued": "BRXXXXACNOR0"},
            {"label": "BONIFICACAO", "factor": 1.1, "lastDatePrior": "2019-04-30",
             "assetIssued": None},
            {"label": "GRUPAMENTO", "factor": 0.008, "lastDatePrior": "2023-08-01"},
            # resgate e subscrição não ajustam provento
            {"label": "RESG TOTAL RV", "factor": 1, "lastDatePrior": "2022-01-01"},
            {"label": "SUBSCRICAO", "factor": 0.2, "lastDatePrior": "2022-02-01"},
            # outro papel (recibo) e fator 1 ou inválido ficam de fora
            {"label": "BONIFICACAO", "factor": 1.2, "lastDatePrior": "2020-01-02",
             "assetIssued": "BRXXXXR01OR0"},
            {"label": "DESDOBRAMENTO", "factor": 1, "lastDatePrior": "2020-06-01"},
            {"label": "DESDOBRAMENTO", "factor": None, "lastDatePrior": "2020-07-01"},
        ],
    }
    assert ce.eventos_de_acoes(dd) == [(date(2019, 4, 30), 1.1), (date(2021, 5, 3), 5.0),
                                      (date(2023, 8, 1), 0.008)]
    assert ce.eventos_de_acoes(None) == [] and ce.eventos_de_acoes({}) == []


def test_fator_de_desajuste_conta_so_eventos_depois_da_data_com():
    ev = [(date(2019, 4, 30), 1.1), (date(2021, 5, 3), 5.0), (date(2023, 8, 1), 0.008)]
    # Provento de 2020: desdobramento de 2021 e grupamento de 2023 depois dele.
    assert ce.fator_de_desajuste(ev, date(2020, 6, 1)) == pytest.approx(5.0 * 0.008)
    # Na mesma data com do evento, o provento é da ação antiga: não conta.
    assert ce.fator_de_desajuste(ev, date(2021, 5, 3)) == pytest.approx(0.008)
    assert ce.fator_de_desajuste(ev, "2018-01-02") == pytest.approx(1.1 * 5.0 * 0.008)
    assert ce.fator_de_desajuste(ev, date(2024, 1, 2)) == 1.0
    assert ce.fator_de_desajuste([], date(2020, 1, 2)) == 1.0
    assert ce.fator_de_desajuste(ev, None) == 1.0
    assert ce.fator_de_desajuste(ev, float("nan")) == 1.0


def test_decisao_reprova_r2_e_r3_com_trajetoria_insuficiente():
    cheia = {"crescimento_renda_aa": 0.2, "quedas_de_renda": 0, "capital_final": 1e9,
             "insuficiente": False}
    falta = {"crescimento_renda_aa": None, "quedas_de_renda": None, "capital_final": None,
             "insuficiente": True}
    d = ce.decidir_padrao(_vs(), falta, cheia)
    assert d["falhou"] == ["r2_dividendo_crescente", "r3_valorizacao"]
    d = ce.decidir_padrao(_vs(), cheia, falta)
    assert d["candidata"] is False


def _vs(media=0.02, lo=0.005, sem=0.01):
    return {"media": media, "ic95": [lo, 0.04], "media_sem_melhor_safra": sem}


def _renda(g, quedas, capital):
    return {"crescimento_renda_aa": g, "quedas_de_renda": quedas, "capital_final": capital}


def test_decidir_exige_as_tres_regras():
    ew = _renda(0.05, 2, 150.0)
    ok = ce.decidir_padrao(_vs(), _renda(0.06, 2, 151.0), ew)
    assert ok["candidata"] and ok["falhou"] == []
    # IC que cruza o zero reprova R1.
    assert "r1_retorno" in ce.decidir_padrao(_vs(lo=-0.01), _renda(0.06, 2, 151.0), ew)["falhou"]
    # Renda crescendo mais, mas caindo mais vezes, reprova R2.
    assert "r2_dividendo_crescente" in ce.decidir_padrao(
        _vs(), _renda(0.08, 3, 151.0), ew)["falhou"]
    # Tendência indefinida reprova R2.
    assert "r2_dividendo_crescente" in ce.decidir_padrao(
        _vs(), _renda(None, 0, 151.0), ew)["falhou"]
    # Renda alta comendo o capital reprova R3.
    assert ce.decidir_padrao(_vs(), _renda(0.06, 1, 140.0), ew)["falhou"] == ["r3_valorizacao"]


def test_escolher_padrao_fica_com_o_atual_sem_candidata():
    bracos = {
        "a": {"vs_equal_weight": {"media": 0.03}, "decisao": {"candidata": False}},
        "b": {"vs_equal_weight": {"media": 0.01}, "decisao": {"candidata": False}},
    }
    r = ce.escolher_padrao(bracos, "nota_fatores")
    assert r["padrao"] == "nota_fatores" and not r["trocou"]


def test_escolher_padrao_pega_o_maior_excesso_entre_candidatas():
    bracos = {
        "a": {"vs_equal_weight": {"media": 0.05}, "decisao": {"candidata": False}},
        "b": {"vs_equal_weight": {"media": 0.02}, "decisao": {"candidata": True}},
        "c": {"vs_equal_weight": {"media": 0.03}, "decisao": {"candidata": True}},
    }
    r = ce.escolher_padrao(bracos, "b")
    assert r["padrao"] == "c" and r["trocou"] and r["candidatas"] == ["c", "b"]


def test_constantes_da_regra_registradas():
    assert ce.CAPITAL_INICIAL == 100_000.0
    assert ce.TOLERANCIA_PONTA_DIAS == 45
    assert (ce.RENDIMENTO_MIN, ce.RENDIMENTO_MAX) == (-0.05, 1.0)
    assert ce.VERSAO == "1.3.0"
    assert ce.ROTULOS_DE_ACOES == {"DESDOBRAMENTO", "BONIFICACAO", "GRUPAMENTO"}


def test_valorizacao_da_carteira_e_ponderada_papel_a_papel():
    # Exemplo da revisão do #810 (régua 1.1.0): pesos iguais, A +100% sem
    # provento, B 0% de preço com fator de 20%. Retorno total 60%, fator médio
    # 10%: 1,60/1,10 = 1,4545 não é a valorização. A certa é 50%.
    rend = {"A": {"fator": 0.0, "caixa": 0.0, "preco": 1.0},
            "B": {"fator": 0.2, "caixa": 0.2, "preco": 0.0}}
    lin = ce.linha_de_renda(2017, {"A": 0.5, "B": 0.5}, rend, liquido=0.60, bruto=0.60)
    assert lin["valorizacao"] == pytest.approx(0.5)
    assert lin["renda_caixa"] == pytest.approx(0.1)
    assert lin["rendimento"] == pytest.approx(0.1)
    assert lin["fator_custo"] == pytest.approx(1.0) and lin["cobertura"] == 1.0
    t = ce.trajetoria_de_renda([lin], capital=100.0)
    assert t["capital_final"] == pytest.approx(150.0)
    assert t["renda_total"] == pytest.approx(10.0)


def test_linha_de_renda_aplica_o_custo_e_renormaliza():
    rend = {"A": {"fator": 0.0, "caixa": 0.0, "preco": 0.10}, "B": None}
    lin = ce.linha_de_renda(2017, {"a": 0.5, "b": 0.5}, rend, liquido=0.089, bruto=0.10)
    assert lin["valorizacao"] == pytest.approx(0.10)
    assert lin["cobertura"] == pytest.approx(0.5)
    assert lin["fator_custo"] == pytest.approx(1.089 / 1.10)
    t = ce.trajetoria_de_renda([lin], capital=100.0)
    assert t["capital_final"] == pytest.approx(100.0 * 1.10 * 1.089 / 1.10)
    # Sem nenhum papel com dado: a linha fica sem valorização e a trajetória,
    # insuficiente.
    vazia = ce.linha_de_renda(2018, {"B": 1.0}, rend, liquido=0.0)
    assert vazia["valorizacao"] is None and vazia["renda_caixa"] is None
    assert ce.trajetoria_de_renda([vazia])["insuficiente"] is True


def test_custo_de_abril_reduz_o_capital_que_rende():
    """O custo sai no rebalanceamento, antes do ano: a renda é sobre o investido.

    Capital 100, fator de custo 0,99 e provento de 10%: renda 9,90, não 10.
    """
    t = ce.trajetoria_de_renda([
        {"safra": 2017, "renda_caixa": 0.10, "valorizacao": 0.0, "fator_custo": 0.99},
        {"safra": 2018, "renda_caixa": 0.10, "valorizacao": 0.0, "fator_custo": 1.0},
    ], capital=100.0)
    assert t["anos"][0]["capital_investido"] == pytest.approx(99.0)
    assert t["anos"][0]["renda"] == pytest.approx(9.90)
    assert t["anos"][1]["capital_inicio"] == pytest.approx(99.0)
    assert t["anos"][1]["renda"] == pytest.approx(9.90)
    assert t["capital_final"] == pytest.approx(99.0)

