"""Régua da §29: rendimento pela razão ajustado/bruto, renda e veredito."""
import math

import pytest

import core.b3_comparacao_estrategias as ce


def test_rendimento_pela_razao_mede_so_o_provento():
    # Preço bruto 10 -> 11 (+10%), ajustado 10 -> 11,55 (+15,5%): 5% de provento.
    assert ce.rendimento_por_razao(10, 10, 11, 11.55) == pytest.approx(0.05)


def test_desdobramento_nas_duas_series_nao_vira_provento():
    # As duas séries descontam o desdobramento: a razão não muda.
    assert ce.rendimento_por_razao(20, 18, 10, 9) == pytest.approx(0.0)


def test_ruido_negativo_pequeno_vira_zero_e_salto_vira_ausencia():
    assert ce.rendimento_por_razao(10, 10, 10, 9.8) == 0.0
    assert ce.rendimento_por_razao(10, 10, 10, 9.0) is None      # -10%
    assert ce.rendimento_por_razao(10, 10, 10, 25.0) is None     # +150%
    assert ce.rendimento_por_razao(None, 10, 10, 10) is None
    assert ce.rendimento_por_razao(0, 10, 10, 10) is None


def test_carteira_renormaliza_sobre_quem_tem_dado():
    y, cob = ce.rendimento_da_carteira({"A": 0.5, "B": 0.25, "C": 0.25},
                                       {"A": 0.06, "B": 0.03, "C": None})
    assert y == pytest.approx((0.5 * 0.06 + 0.25 * 0.03) / 0.75)
    assert cob == pytest.approx(0.75)
    assert ce.rendimento_da_carteira({"A": 1.0}, {}) == (None, 0.0)


def test_trajetoria_gasta_a_renda_e_cresce_so_pelo_preco():
    # Exemplo da revisão: fechamento 10 -> 11, ajustado 10 -> 11,55.
    fator = ce.rendimento_por_razao(10.0, 10.0, 11.0, 11.55)
    assert fator == pytest.approx(0.05)
    t = ce.trajetoria_de_renda([
        {"safra": 2017, "retorno": 0.155, "rendimento": fator, "renda_caixa": 0.048},
        {"safra": 2018, "retorno": 0.155, "rendimento": fator, "renda_caixa": 0.048},
    ], capital=100.0)
    # O capital cresce só pelo preço: 100 x 1,155 / 1,05 = 110 (não 110,50).
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
        {"safra": 2017, "retorno": 0.0, "rendimento": 0.05, "renda_caixa": 0.05},
        {"safra": 2018, "retorno": 0.0, "rendimento": None, "renda_caixa": None},
        {"safra": 2019, "retorno": 0.0, "rendimento": 0.05, "renda_caixa": 0.05},
    ], capital=100.0)
    assert t["insuficiente"] is True and t["safras_sem_rendimento"] == [2018]
    for k in ("capital_final", "renda_total", "crescimento_renda_aa",
              "quedas_de_renda", "renda_ultimo_sobre_primeiro"):
        assert t[k] is None, k
    assert t["anos"][1]["renda"] is None and t["anos"][2]["capital_inicio"] is None
    # Renda zero MEDIDA continua sendo dado: tendência indefinida, queda contada.
    z = ce.trajetoria_de_renda([
        {"safra": 2017, "retorno": 0.0, "rendimento": 0.05, "renda_caixa": 0.05},
        {"safra": 2018, "retorno": 0.0, "rendimento": 0.0, "renda_caixa": 0.0},
    ], capital=100.0)
    assert z["insuficiente"] is False
    assert z["crescimento_renda_aa"] is None and z["quedas_de_renda"] == 1


def test_rendimentos_do_ano_separa_fator_e_caixa_da_serie_diaria():
    # Preço sobe 10 -> 11 em dois passos; provento de 0,50 por ação na data ex
    # (ajuste retroativo: r = P_{i-1} / (P_{i-1} - D) no dia).
    c = [10.0, 10.5, 11.0]
    r_ex = 10.5 / (10.5 - 0.5)
    a = [10.0, 10.5, 11.0 * r_ex]
    y = ce.rendimentos_do_ano(zip(c, a))
    assert y["fator"] == pytest.approx(r_ex - 1.0)
    # Caixa = 0,50 / 10 (capital do início), não o fator.
    assert y["caixa"] == pytest.approx(0.05)
    # Sem provento: fator e caixa zero.
    assert ce.rendimentos_do_ano([(10.0, 9.0), (11.0, 9.9)]) == {"fator": 0.0, "caixa": 0.0}


def test_rendimentos_do_ano_ignora_desdobramento_e_tira_quebra():
    # Desdobramento 2:1 com as duas séries saltando juntas (razão ~1): o índice
    # de preço que pesa o caixa não cai pela metade.
    serie = [(20.0, 20.0), (10.0, 10.0), (10.0, 10.0 * 10.0 / 9.5)]
    y = ce.rendimentos_do_ano(serie)
    assert y["caixa"] == pytest.approx(0.05)
    # Razão diária fora da faixa é quebra: o ticker-ano sai.
    assert ce.rendimentos_do_ano([(10.0, 10.0), (10.0, 9.0)]) is None
    assert ce.rendimentos_do_ano([(10.0, 10.0)]) is None
    assert ce.rendimentos_do_ano([(10.0, 10.0), (0.0, 1.0)]) is None


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
    assert math.isclose(ce.rendimento_por_razao(1, 1, 1, 1), 0.0)
