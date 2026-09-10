"""Sustentabilidade histórica da distribuição — spec 2026-09-09."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.b3_renda_sustentavel import (
    MIN_ANOS,
    enrich_com_historico_patrimonial,
    enrich_com_renda_sustentavel,
    fracao_pl_em_queda_com_lucro,
    leitura_da_serie,
    sustentabilidade_do_ano,
)


def _serie(payouts, dys=None):
    """Histórico anual no formato de load_multiplos_historico_batch."""
    n = len(payouts)
    dys = dys if dys is not None else [0.05] * n
    return pd.DataFrame({
        "Ticker": ["XPTO3"] * n,
        "Data": pd.to_datetime([f"{2018 + i}-12-31" for i in range(n)]),
        "DY": dys,
        "Payout": payouts,
    })


def test_fronteiras_da_faixa():
    # Teste 1 do spec: os quatro pontos exatos da faixa de dois lados.
    assert sustentabilidade_do_ano(0.05) == 0.0
    assert sustentabilidade_do_ano(1.30) == 0.0
    assert sustentabilidade_do_ano(0.25) == 1.0
    assert sustentabilidade_do_ano(0.80) == 1.0


def test_episodio_isolado_nao_condena():
    # Teste 2: um ano de 300% em oito, os outros sete dentro da faixa.
    leitura = leitura_da_serie(_serie([0.50] * 7 + [3.00]))
    assert leitura["payout_sustentabilidade"] >= 0.85


def test_padrao_persistente_zera():
    # Teste 3: acima do TETO em todos os anos.
    leitura = leitura_da_serie(_serie([1.40, 1.55, 2.10, 1.80, 1.60]))
    assert leitura["payout_sustentabilidade"] == 0.0


def test_ano_incoerente_sai_como_ausencia():
    # Teste 4: DY > 0,5% com Payout <= 1% é contradição da fonte.
    leitura = leitura_da_serie(
        _serie([0.40, 0.005, 0.50, 0.60], dys=[0.05, 0.06, 0.05, 0.05])
    )
    assert leitura["n_anos_payout"] == 3
    assert leitura["payout_sustentabilidade"] == 1.0


def test_historico_curto_produz_nan():
    # Teste 5: menos de MIN_ANOS observados não vira nota.
    leitura = leitura_da_serie(_serie([0.40] * (MIN_ANOS - 1)))
    assert leitura["payout_sustentabilidade"] is None
    assert leitura["n_anos_payout"] == MIN_ANOS - 1


def test_payouts_nao_finitos_saem_da_serie_como_ausencia():
    """Infinitos/NaN não entram nem na contagem nem na mediana histórica."""
    leitura = leitura_da_serie(_serie([0.40, np.inf, 0.50, -np.inf, np.nan, 0.60]))
    assert leitura["n_anos_payout"] == 3
    assert leitura["payout_sustentabilidade"] == 1.0
    assert leitura["payout_mediano_hist"] == pytest.approx(0.50)


def test_dy_sustentavel_nunca_cai_no_dy_bruto():
    # Teste 6: sem sustentabilidade, dy_sustentavel é NaN — nunca o DY cru.
    df_mult = pd.DataFrame({"Ticker": ["XPTO3", "CURTA3"], "DY": [0.08, 0.09]})
    hist = {
        "XPTO3": _serie([0.40, 0.50, 0.60, 0.55]),
        "CURTA3": _serie([0.40, 0.50]),
    }
    out = enrich_com_renda_sustentavel(df_mult, hist)
    linha = out[out["Ticker"] == "CURTA3"].iloc[0]
    assert np.isnan(linha["payout_sustentabilidade"])
    assert np.isnan(linha["dy_sustentavel"])
    boa = out[out["Ticker"] == "XPTO3"].iloc[0]
    assert boa["dy_sustentavel"] == pytest.approx(0.08 * boa["payout_sustentabilidade"])


def test_enrich_preserva_quadro_sem_historico():
    df_mult = pd.DataFrame({"Ticker": ["XPTO3"], "DY": [0.08]})
    out = enrich_com_renda_sustentavel(df_mult, {})
    assert list(out["Ticker"]) == ["XPTO3"]


def _anual(pares):
    """Pares: lista de (ano, pl_mi, lucro_mi)."""
    return [{"ano": ano, "pl_mi": pl, "lucro_mi": lucro}
            for ano, pl, lucro in pares]


def test_fracao_conta_apenas_pares_com_queda_e_lucro():
    serie = _anual([
        (2019, 100.0, 10.0),
        (2020, 90.0, 8.0),
        (2021, 95.0, 9.0),
        (2022, 80.0, 5.0),
        (2023, 70.0, -2.0),
    ])
    frac, n_pares = fracao_pl_em_queda_com_lucro(serie)
    assert n_pares == 4
    assert frac == pytest.approx(0.5)


def test_ausencia_de_dado_nao_e_zero():
    """Faltante não pode fabricar queda por coerção para zero."""
    serie = _anual([
        (2019, 100.0, 10.0),
        (2020, None, 8.0),
        (2021, 95.0, None),
        (2022, 90.0, 7.0),
    ])
    frac, n_pares = fracao_pl_em_queda_com_lucro(serie)
    assert n_pares == 1
    assert frac == pytest.approx(1.0)


def test_pares_patrimoniais_nao_finitos_sao_ausencia():
    serie = _anual([
        (2019, 100.0, 10.0),
        (2020, np.inf, 8.0),
        (2021, 90.0, -np.inf),
        (2022, 80.0, 7.0),
    ])
    frac, n_pares = fracao_pl_em_queda_com_lucro(serie)
    assert n_pares == 1
    assert frac == pytest.approx(1.0)


def test_serie_sem_par_avaliavel():
    frac, n_pares = fracao_pl_em_queda_com_lucro(_anual([(2020, 100.0, 5.0)]))
    assert frac is None
    assert n_pares == 0


def test_enrich_patrimonial_acrescenta_colunas():
    df_mult = pd.DataFrame({"Ticker": ["XPTO3", "SEMDADO3"], "DY": [0.08, 0.02]})
    lote = {"XPTO3": _anual([(2019, 100.0, 10.0), (2020, 90.0, 8.0)])}
    out = enrich_com_historico_patrimonial(df_mult, lote)
    linha = out[out["Ticker"] == "XPTO3"].iloc[0]
    assert linha["pl_queda_com_lucro_frac"] == pytest.approx(1.0)
    assert linha["n_pares_pl"] == 1
    vazia = out[out["Ticker"] == "SEMDADO3"].iloc[0]
    assert np.isnan(vazia["pl_queda_com_lucro_frac"])
