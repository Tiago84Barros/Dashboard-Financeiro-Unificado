"""Metricas agregadas do patrimonio, com cobertura explicita."""
import math

import pandas as pd
import pytest

from core.global_portfolio.metrics import (
    COBERTURA_MINIMA,
    cobertura,
    dy_consolidado,
    qualidade_por_classe,
    valuation_agregado,
)


def _linha(classe, symbol, peso, fundamentals, metrics=None):
    return {"asset_class": classe, "symbol": symbol, "weight_global": peso,
            "payload": {"fundamentals": fundamentals, "metrics": metrics or {}}}


def test_valuation_usa_earnings_yield_e_nao_media_aritmetica():
    # Pesos iguais, P/L 10 e P/L 100.
    # Media aritmetica daria 55. Correto: E/P medio = (0.1+0.01)/2 = 0.055 -> 18,18.
    df = pd.DataFrame([
        _linha("b3", "A3", 0.5, {"P/L": 10.0}),
        _linha("b3", "B3X", 0.5, {"P/L": 100.0}),
    ])
    resultado = valuation_agregado(df, "pe")
    assert resultado.valor == pytest.approx(18.1818, rel=1e-3)
    assert resultado.valor != pytest.approx(55.0)


def test_valuation_ignora_pl_nao_positivo_e_reduz_cobertura():
    df = pd.DataFrame([
        _linha("b3", "A3", 0.5, {"P/L": 10.0}),
        _linha("b3", "B3X", 0.5, {"P/L": -5.0}),   # prejuizo: fora do calculo
    ])
    resultado = valuation_agregado(df, "pe")
    assert resultado.valor == pytest.approx(10.0)
    assert resultado.cobertura == pytest.approx(0.5)
    assert resultado.n_ativos == 1


def test_valuation_sem_nenhum_dado_devolve_none():
    df = pd.DataFrame([_linha("fii", "HGLG11", 1.0, {"pvp": 0.9})])
    resultado = valuation_agregado(df, "pe")
    assert resultado.valor is None
    assert resultado.cobertura == 0.0


def test_dy_consolidado_e_media_ponderada_simples():
    # DY e razao sobre preco e os pesos sao sobre preco: aritmetica esta correta.
    df = pd.DataFrame([
        _linha("b3", "A3", 0.6, {"DY": 10.0}),
        _linha("fii", "H11", 0.4, {"dy_12m": 5.0}),
    ])
    resultado = dy_consolidado(df)
    assert resultado.valor == pytest.approx(0.6 * 10.0 + 0.4 * 5.0)
    assert resultado.cobertura == pytest.approx(1.0)


def test_cobertura_e_a_fracao_de_peso_com_o_dado():
    df = pd.DataFrame([
        _linha("b3", "A3", 0.7, {"P/L": 8.0}),
        _linha("b3", "B3X", 0.3, {}),
    ])
    assert cobertura(df, "pe") == pytest.approx(0.7)


def test_metrica_abaixo_do_minimo_e_marcada_como_nao_confiavel():
    df = pd.DataFrame([
        _linha("b3", "A3", 0.4, {"P/L": 8.0}),
        _linha("b3", "B3X", 0.6, {}),
    ])
    resultado = valuation_agregado(df, "pe")
    assert resultado.cobertura == pytest.approx(0.4)
    assert resultado.cobertura < COBERTURA_MINIMA
    assert resultado.confiavel is False


def test_metrica_acima_do_minimo_e_confiavel():
    df = pd.DataFrame([_linha("b3", "A3", 1.0, {"P/L": 8.0})])
    assert valuation_agregado(df, "pe").confiavel is True


def test_qualidade_e_reportada_por_classe_e_nunca_agregada():
    df = pd.DataFrame([
        _linha("b3", "A3", 0.5, {}, {"score": 80.0}),
        _linha("b3", "B3X", 0.2, {}, {"score": 60.0}),
        _linha("us", "AAPL", 0.3, {}, {"entry_score": 70.0}),
    ])
    saida = qualidade_por_classe(df)
    assert set(saida) == {"b3", "us"}
    # b3: media ponderada pelos pesos DENTRO da classe -> (0.5*80 + 0.2*60)/0.7
    assert saida["b3"].valor == pytest.approx((0.5 * 80.0 + 0.2 * 60.0) / 0.7)
    assert saida["us"].valor == pytest.approx(70.0)


def test_qualidade_de_classe_sem_score_tem_cobertura_zero():
    df = pd.DataFrame([_linha("fii", "H11", 1.0, {}, {})])
    saida = qualidade_por_classe(df)
    assert saida["fii"].valor is None
    assert saida["fii"].cobertura == 0.0


def test_dataframe_vazio_nao_quebra():
    vazio = pd.DataFrame(columns=["asset_class", "symbol", "weight_global", "payload"])
    assert valuation_agregado(vazio, "pe").valor is None
    assert dy_consolidado(vazio).valor is None
    assert qualidade_por_classe(vazio) == {}


def test_peso_nan_nao_contamina_o_calculo():
    # float('nan') or 0.0 avalia para NaN (NaN e "truthy"); um parser ingenuo
    # deixaria esse peso passar pelo filtro "peso > 0" (NaN <= 0 e False) e
    # NaN se propagaria por toda soma a jusante.
    df = pd.DataFrame([
        _linha("b3", "A3", 0.5, {"P/L": 10.0}),
        _linha("b3", "B3X", float("nan"), {"P/L": 100.0}),
    ])
    resultado = valuation_agregado(df, "pe")
    assert resultado.valor is not None
    assert math.isfinite(resultado.valor)
    assert resultado.cobertura is not None
    assert math.isfinite(resultado.cobertura)

    qualidade = qualidade_por_classe(pd.DataFrame([
        _linha("b3", "A3", 0.5, {}, {"score": 80.0}),
        _linha("b3", "B3X", float("nan"), {}, {"score": 60.0}),
    ]))
    assert math.isfinite(qualidade["b3"].cobertura)
    assert qualidade["b3"].valor is None or math.isfinite(qualidade["b3"].valor)


def test_todos_os_pesos_zero_devolve_metrica_vazia():
    df = pd.DataFrame([
        _linha("b3", "A3", 0.0, {"P/L": 10.0}),
        _linha("b3", "B3X", 0.0, {"P/L": 20.0}),
    ])
    resultado = valuation_agregado(df, "pe")
    assert resultado.valor is None
    assert resultado.cobertura == 0.0
    assert cobertura(df, "pe") == 0.0
    assert qualidade_por_classe(df)["b3"].valor is None


def test_weight_global_ausente_devolve_metrica_vazia():
    df = pd.DataFrame([
        {"asset_class": "b3", "symbol": "A3",
         "payload": {"fundamentals": {"P/L": 10.0}, "metrics": {}}},
    ])
    resultado = valuation_agregado(df, "pe")
    assert resultado.valor is None
    assert resultado.cobertura == 0.0
