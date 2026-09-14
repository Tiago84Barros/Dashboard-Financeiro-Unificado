"""Regressões da leitura anual de renda sustentável para empresas dos EUA."""
from __future__ import annotations

import math

import pytest

from core.us_renda_sustentavel import (
    MIN_ANOS,
    leitura_da_serie,
    sustentabilidade_do_ano,
)


def _serie(payouts, fcfs=None):
    """Série EDGAR sintética; dividendos pagos seguem sinal de saída de caixa."""
    fcfs = fcfs if fcfs is not None else [100.0] * len(payouts)
    return [
        {
            "fiscal_year": 2018 + indice,
            "net_income": 100.0,
            "dividends_paid": -100.0 * payout,
            "free_cash_flow": fcf,
        }
        for indice, (payout, fcf) in enumerate(zip(payouts, fcfs))
    ]


@pytest.mark.parametrize(
    ("payout", "esperada"),
    [(0.05, 0.0), (0.25, 1.0), (0.80, 1.0), (1.30, 0.0)],
)
def test_faixa_bilateral_tem_fronteiras_explicitas(payout, esperada):
    assert sustentabilidade_do_ano(payout) == esperada


def test_episodio_isolado_nao_condena_politica_historica():
    leitura = leitura_da_serie(_serie([0.50] * 7 + [3.00]))

    assert leitura["n_anos_payout"] == 8
    assert leitura["payout_mediano_hist"] == pytest.approx(0.50)
    assert leitura["payout_sustentabilidade"] >= 0.85


def test_historico_curto_nao_vira_nota_zero():
    leitura = leitura_da_serie(_serie([0.50] * (MIN_ANOS - 1)))

    assert leitura["n_anos_payout"] == MIN_ANOS - 1
    assert leitura["payout_mediano_hist"] is None
    assert leitura["payout_sustentabilidade"] is None


def test_nao_finitos_e_lucros_nao_positivos_sao_ausencia_da_razao():
    serie = _serie([0.50, 0.50, 0.50, 0.50, 0.50])
    serie[1]["net_income"] = 0.0
    serie[2]["net_income"] = -10.0
    serie[3]["dividends_paid"] = math.inf
    serie[4]["net_income"] = math.nan

    leitura = leitura_da_serie(serie)

    assert leitura["n_anos_payout"] == 1
    assert leitura["payout_mediano_hist"] is None
    assert leitura["payout_sustentabilidade"] is None


def test_dividendos_acima_do_fcl_positivo_usa_valor_absoluto_da_saida_de_caixa():
    leitura = leitura_da_serie(_serie([0.50, 0.50, 0.50, 0.50], [40, 45, 60, -10]))

    assert leitura["n_anos_fcl_positivo"] == 3
    assert leitura["n_anos_dividendos_acima_fcl"] == 2
    assert leitura["fracao_dividendos_acima_fcl"] == pytest.approx(2 / 3)


def test_fcl_ausente_ou_nao_finito_nao_fabrica_folga_de_caixa():
    serie = _serie([0.50] * 4, [math.nan, math.inf, -20, 100])
    leitura = leitura_da_serie(serie)

    assert leitura["n_anos_fcl_positivo"] == 1
    assert leitura["n_anos_dividendos_acima_fcl"] == 0
    assert leitura["fracao_dividendos_acima_fcl"] == 0.0


def test_ano_fiscal_duplicado_e_ausencia_e_nao_e_duplicado_na_mediana():
    serie = _serie([0.50, 0.50, 0.50, 0.50])
    serie.append({
        "fiscal_year": 2020,
        "net_income": 100.0,
        "dividends_paid": -300.0,
        "free_cash_flow": 100.0,
    })

    leitura = leitura_da_serie(serie)

    assert leitura["n_anos_payout"] == 3
    assert leitura["payout_mediano_hist"] == pytest.approx(0.50)


def test_reit_e_apenas_metadado_sem_mudar_a_leitura_financeira():
    leitura = leitura_da_serie(_serie([0.50] * 3), is_reit=True)

    assert leitura["is_reit"] is True
    assert leitura["payout_sustentabilidade"] == pytest.approx(1.0)
