"""A-118: fundo escolhido que sai de campo nao pode virar retorno dos outros.

`point_in_time_backtest` renormalizava os pesos sobre os fundos com retorno no
periodo (`weights.loc[valid] / weights.loc[valid].sum()`). Um fundo escolhido
que liquidou -- sem nenhuma linha de retorno -- tinha a fatia dele redistribuida
entre os sobreviventes: o dinheiro do fundo que sumiu rendia o que os OUTROS
renderam.

Medido em 24/08/2026, cesta de tres fundos com um caindo 2% ao dia:

    com o fundo presente : -6,46%   (cobertura 100%)
    o fundo liquida      : +10,49%  (cobertura 67%)

Inversao de sinal. `coverage` ja caia para 67% e era reportado, mas como media
agregada. A fatia ausente passa a render ZERO -- nao inventa a perda, que pode
ser buraco de dado, e nao deixa o ganho dos sobreviventes ocupar o lugar dela.
"""
import pandas as pd
import pytest

from core.fii_validation import point_in_time_backtest

TICKERS = ["AAA11", "BBB11", "CCC11"]
DIAS = pd.bdate_range("2023-02-01", "2023-02-28")


def _snapshots():
    return pd.DataFrame([
        {"reference_date": d, "available_at": f"{d} 00:00:00",
         "ticker": t, "score": 90.0 - i}
        for d in ("2023-01-31", "2023-02-28")
        for i, t in enumerate(TICKERS)
    ])


def _retornos(*, ccc_presente: bool, ret_ccc: float = -0.02):
    linhas = []
    for d in DIAS:
        for t in TICKERS:
            if t == "CCC11" and not ccc_presente:
                continue
            linhas.append({"date": d, "ticker": t,
                           "total_return": ret_ccc if t == "CCC11" else 0.005})
    return pd.DataFrame(linhas)


def _rodar(ccc_presente: bool):
    return point_in_time_backtest(
        _snapshots(), _retornos(ccc_presente=ccc_presente),
        pd.Series(0.0, index=DIAS), top_n=3,
        transaction_cost=0.0, slippage=0.0)


def test_fundo_que_liquidou_nao_rende_o_que_os_sobreviventes_renderam():
    presente = _rodar(True)
    sumiu = _rodar(False)

    # Um terco da carteira saiu de campo: no maximo dois tercos do ganho dos
    # sobreviventes pode aparecer. A versao antiga devolvia o ganho INTEIRO.
    assert sumiu["mean_return"] < presente["mean_return"] + 1.0
    ganho_sobreviventes = (1.005 ** len(DIAS)) - 1.0
    assert sumiu["mean_return"] <= ganho_sobreviventes * (2 / 3) + 1e-9, (
        "a fatia do fundo que sumiu nao pode render o que os outros renderam")


def test_a_saida_de_campo_e_declarada_e_quantificada():
    sumiu = _rodar(False)
    bloco = sumiu["saida_de_campo"]

    assert bloco["peso_ausente_medio"] == pytest.approx(1 / 3, abs=1e-6)
    assert bloco["peso_ausente_maximo"] == pytest.approx(1 / 3, abs=1e-6)
    assert bloco["periodos_com_ausencia"] == bloco["periodos"] >= 1


def test_sem_ausencia_o_resultado_nao_muda_e_o_bloco_zera():
    presente = _rodar(True)
    bloco = presente["saida_de_campo"]

    assert bloco["peso_ausente_medio"] == pytest.approx(0.0)
    assert bloco["periodos_com_ausencia"] == 0
    # Carteira igualmente ponderada, um fundo a -2%/dia e dois a +0,5%/dia.
    assert presente["mean_return"] < 0.0


def test_o_peso_ausente_entra_em_cada_observacao():
    sumiu = _rodar(False)
    for obs in sumiu["observations"]:
        assert obs["peso_ausente"] == pytest.approx(1 / 3, abs=1e-6)
