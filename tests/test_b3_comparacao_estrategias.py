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
    t = ce.trajetoria_de_renda([
        {"safra": 2017, "retorno": 0.10, "rendimento": 0.05},
        {"safra": 2018, "retorno": 0.10, "rendimento": 0.05},
    ], capital=100.0)
    assert t["anos"][0]["renda"] == pytest.approx(5.0)
    assert t["anos"][1]["capital_inicio"] == pytest.approx(105.0)
    assert t["anos"][1]["renda"] == pytest.approx(5.25)
    assert t["capital_final"] == pytest.approx(105.0 * 1.05)
    assert t["crescimento_renda_aa"] == pytest.approx(0.05)
    assert t["quedas_de_renda"] == 0
    assert t["renda_total"] == pytest.approx(10.25)


def test_ano_sem_renda_deixa_tendencia_indefinida_e_conta_queda():
    t = ce.trajetoria_de_renda([
        {"safra": 2017, "retorno": 0.0, "rendimento": 0.05},
        {"safra": 2018, "retorno": 0.0, "rendimento": None},
    ], capital=100.0)
    assert t["crescimento_renda_aa"] is None
    assert t["quedas_de_renda"] == 1


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
