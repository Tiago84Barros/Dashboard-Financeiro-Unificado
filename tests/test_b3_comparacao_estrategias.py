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
    # Valorização só pelo preço: 10 -> 11.
    assert y["preco"] == pytest.approx(0.10)
    # Sem provento: fator e caixa zero.
    z = ce.rendimentos_do_ano([(10.0, 9.0), (11.0, 9.9)])
    assert z["fator"] == 0.0 and z["caixa"] == 0.0 and z["preco"] == pytest.approx(0.10)


def test_rendimentos_do_ano_ignora_desdobramento_e_tira_quebra():
    # Desdobramento 2:1 com as duas séries saltando juntas (razão ~1): o índice
    # de preço que pesa o caixa não cai pela metade.
    serie = [(20.0, 20.0), (10.0, 10.0), (10.0, 10.0 * 10.0 / 9.5)]
    y = ce.rendimentos_do_ano(serie)
    assert y["caixa"] == pytest.approx(0.05)
    assert y["preco"] == pytest.approx(0.0)  # 20 -> 10 é desdobramento
    # Razão diária fora da faixa é quebra: o ticker-ano sai.
    assert ce.rendimentos_do_ano([(10.0, 10.0), (10.0, 9.0)]) is None
    assert ce.rendimentos_do_ano([(10.0, 10.0)]) is None
    assert ce.rendimentos_do_ano([(10.0, 10.0), (0.0, 1.0)]) is None


def test_salto_so_e_desdobramento_em_passo_diario():
    # A série de `market.historical_prices` é mensal até 2024: num passo de um
    # mês, -31% (BOVA11 em março de 2020) é mercado e entra no preço.
    from datetime import date
    mensal = [(date(2020, 2, 1), 100.6, 100.6), (date(2020, 3, 1), 69.35, 69.35)]
    y = ce.rendimentos_do_ano(mensal)
    assert y["preco"] == pytest.approx(69.35 / 100.6 - 1.0)
    # O mesmo salto em pregões seguidos continua sendo desdobramento.
    diario = [(date(2025, 6, 2), 20.0, 20.0), (date(2025, 6, 3), 10.0, 10.0)]
    assert ce.rendimentos_do_ano(diario)["preco"] == pytest.approx(0.0)
    # No limite: até PASSO_DIARIO_DIAS corridos ainda é passo diário.
    d0 = date(2025, 6, 2)
    from datetime import timedelta
    borda = [(d0, 20.0, 20.0), (d0 + timedelta(days=ce.PASSO_DIARIO_DIAS), 10.0, 10.0)]
    assert ce.rendimentos_do_ano(borda)["preco"] == pytest.approx(0.0)
    fora = [(d0, 20.0, 20.0), (d0 + timedelta(days=ce.PASSO_DIARIO_DIAS + 1), 10.0, 10.0)]
    assert ce.rendimentos_do_ano(fora)["preco"] == pytest.approx(-0.5)


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

