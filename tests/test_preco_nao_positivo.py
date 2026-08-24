"""A-121 e A-122: preço <= 0 não é preço.

Medido em 24/08/2026 sobre o painel mensal real da B3 (1.089 tickers):

* **5 tickers, 463 observações** com preço NEGATIVO — NEMO3 (132), PPAR3 (119),
  RSUL3 (108), FIGE4 (90), MMAQ4 (14). O ajuste por proventos leva o
  `adjusted_close` abaixo de zero e ninguém barrava.
* MMAQ4 tem ainda 65 meses com preço ZERO, que geram retorno infinito.

O que chegava à tela: queda máxima de **-2.638%** (MMAQ4) e **-104,2%**
(RSUL3) — ambas impossíveis, uma perda não passa de 100% — e volatilidade de
**361%** em NEMO3. E a correlação RSUL3 x NEMO3 aparecia como 0,368: um número
calculado inteiramente sobre preços negativos.

A-121 é o lado que quebra em voz alta: `.replace([inf], pd.NA)` transforma o
quadro float em `object` e `DataFrame.corr()` levanta `TypeError`. Uma única
cotação zerada na carteira derrubava a seção "Correlação entre ativos" inteira.
"""
import numpy as np
import pandas as pd
import pytest

from core.correlation_analysis import calcular_correlacao_mensal
from views.portfolio_b3 import _price_metrics


def _serie(valores, inicio="2021-01-31"):
    idx = pd.date_range(inicio, periods=len(valores), freq="ME")
    return pd.DataFrame({"XPTO3": valores}, index=idx)


# --- A-121 -------------------------------------------------------------

def test_cotacao_zerada_nao_derruba_a_correlacao():
    idx = pd.date_range("2020-01-31", periods=30, freq="ME")
    b = np.linspace(20.0, 10.0, 30)
    b[5] = 0.0  # zero -> pct_change = inf -> pd.NA -> corr() levantava TypeError
    precos = pd.DataFrame({"AAA3": np.linspace(10.0, 20.0, 30), "BBB3": b},
                          index=idx)
    d = calcular_correlacao_mensal(precos, 24)
    assert not d["corr"].empty
    assert np.isfinite(d["corr"].loc["AAA3", "BBB3"])


# --- A-122 -------------------------------------------------------------

def test_correlacao_ignora_precos_negativos():
    # BBB3 é negativa na primeira metade: só a segunda metade pode contar.
    idx = pd.date_range("2020-01-31", periods=60, freq="ME")
    b = np.concatenate([np.full(30, -5.0), np.linspace(10.0, 20.0, 30)])
    d = calcular_correlacao_mensal(
        pd.DataFrame({"AAA3": np.linspace(10.0, 40.0, 60), "BBB3": b},
                     index=idx), 24)
    if not d["corr"].empty and "BBB3" in d["corr"].columns:
        n = int(d["overlap"].loc["AAA3", "BBB3"])
        assert n <= 30, "meses de preço negativo não podem entrar na amostra"


def test_queda_maxima_nao_passa_de_cem_por_cento():
    # A forma do MMAQ4: cai a um preço negativo e volta. Antes: -2.638%.
    valores = [100.0] * 12 + [-4.0] + [90.0] * 12
    m = _price_metrics(_serie(valores), "XPTO3")
    assert m["max_drop_5y"] >= -1.0, "uma perda não passa de 100%"
    assert m["max_drop_5y"] == pytest.approx(-0.10, abs=0.01)


def test_volatilidade_nao_e_calculada_sobre_preco_negativo():
    # A forma do NEMO3: série majoritariamente negativa exibia vol de 361%.
    valores = [-3.0, -5.0, -2.0, -8.0, -1.0, -6.0] * 4
    m = _price_metrics(_serie(valores), "XPTO3")
    assert np.isnan(m["vol_12m"]), "sem preço válido não há volatilidade"


def test_serie_saudavel_nao_muda():
    valores = [10.0 * (1.02 ** i) for i in range(24)]
    m = _price_metrics(_serie(valores), "XPTO3")
    assert m["ret_12m"] == pytest.approx(0.02 ** 0 * (1.02 ** 12) - 1, abs=1e-6)
    assert m["max_drop_5y"] == pytest.approx(0.0, abs=1e-9)


# --- A-123 -------------------------------------------------------------

def test_card_de_destaque_declara_incerteza_quando_o_ic_cruza_zero():
    """O card de destaque mostrava só a estimativa pontual.

    Medido em 24/08/2026 sobre 3.610 pares de 60 carteiras aleatórias da B3:
    82% têm IC 95% cruzando zero. "Inversa mais forte -0,34" lido sem o IC
    [-0,63; +0,07] afirma uma proteção que o dado não sustenta.
    """
    from views.investimentos import _legenda_par

    fraco = pd.Series({"Par": "AAA3 x BBB3", "Correlação": -0.34,
                       "Observações": 26})
    texto, incerto = _legenda_par(fraco)
    assert incerto
    assert "26 meses" in texto and "IC 95%" in texto
    assert "não distinguível de independência" in texto


def test_card_de_destaque_nao_marca_incerteza_quando_o_ic_nao_cruza_zero():
    from views.investimentos import _legenda_par

    forte = pd.Series({"Par": "PETR4 x ITUB4", "Correlação": 0.61,
                       "Observações": 139})
    texto, incerto = _legenda_par(forte)
    assert not incerto
    assert "139 meses" in texto
    assert "não distinguível" not in texto
