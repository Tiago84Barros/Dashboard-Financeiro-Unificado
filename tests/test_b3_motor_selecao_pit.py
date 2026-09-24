"""B3: motor único de score, teste de seleção com intervalo e universo por época."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from core import b3_selecao as sel
from core import b3_universo_pit as upit

# ── Motor único: múltiplo vira rendimento no motor oficial ────────────────────


def _mult() -> pd.DataFrame:
    return pd.DataFrame([
        {"Ticker": "LUC1", "P/L": 6.0, "Margem_Liquida": 0.15, "SEGMENTO": "S"},
        {"Ticker": "LUC2", "P/L": 12.0, "Margem_Liquida": 0.10, "SEGMENTO": "S"},
        {"Ticker": "LUC3", "P/L": 20.0, "Margem_Liquida": 0.05, "SEGMENTO": "S"},
        {"Ticker": "NEG1", "P/L": -4.0, "Margem_Liquida": -0.20, "SEGMENTO": "S"},
        # A fonte apagou o P/L negativo; a margem ainda diz o sinal.
        {"Ticker": "NEG2", "P/L": np.nan, "Margem_Liquida": -0.10, "SEGMENTO": "S"},
    ])


def test_prejuizo_vai_ao_fundo_e_nao_ao_neutro():
    from views.empresas_b3 import _score_universo

    df = _mult()
    out = _score_universo(df, df["Ticker"].tolist(), {"P/L": (1.0, False)},
                          group_col_prefer="SEGMENTO")
    raw = dict(zip(out["Ticker"], out["score_raw"]))
    # Entre lucrativas, a ordem do múltiplo se mantém (P/L menor = melhor).
    assert raw["LUC1"] > raw["LUC2"] > raw["LUC3"]
    # Deficitárias abaixo de TODAS as lucrativas — antes ficavam no neutro 50.
    assert max(raw["NEG1"], raw["NEG2"]) < raw["LUC3"]
    assert raw["NEG2"] < 50.0


def test_motor_oficial_e_diagnostico_concordam_na_ordem_do_valuation():
    from core.b3_company_score import _numeric_metric
    from views.empresas_b3 import _score_universo

    df = _mult()
    out = _score_universo(df, df["Ticker"].tolist(), {"P/L": (1.0, False)},
                          group_col_prefer="SEGMENTO")
    ordem_oficial = out.sort_values("score_raw", ascending=False)["Ticker"].tolist()
    y = _numeric_metric(df, "P/L")
    ordem_diag = df.assign(y=y).sort_values("y", ascending=False)["Ticker"].tolist()
    assert ordem_oficial[:3] == ordem_diag[:3] == ["LUC1", "LUC2", "LUC3"]


# ── Teste de seleção ──────────────────────────────────────────────────────────


def test_intervalo_classifica_vantagem_contra_e_inconclusivo():
    rng = np.random.default_rng(7)
    ruido = rng.normal(0, 0.002, 36)
    assert sel.intervalo_excesso(0.01 + ruido)["veredito"] == sel.VANTAGEM
    assert sel.intervalo_excesso(-0.01 + ruido)["veredito"] == sel.CONTRA
    assert sel.intervalo_excesso(rng.normal(0, 0.03, 24))["veredito"] in {
        sel.INCONCLUSIVO, sel.VANTAGEM, sel.CONTRA}
    iv = sel.intervalo_excesso([0.02, -0.02] * 12)
    assert iv["veredito"] == sel.INCONCLUSIVO and iv["lo"] < 0 < iv["hi"]


def test_intervalo_bate_com_o_t_da_scipy():
    from scipy import stats

    xs = [0.01, -0.004, 0.012, 0.003, 0.0, 0.007, -0.002, 0.009, 0.004, 0.001,
          0.006, -0.001, 0.005]
    iv = sel.intervalo_excesso(xs, confianca=0.90)
    lo, hi = stats.t.interval(0.90, len(xs) - 1, loc=np.mean(xs), scale=stats.sem(xs))
    assert iv["lo"] == pytest.approx(lo) and iv["hi"] == pytest.approx(hi)


def test_pouca_amostra_ou_sem_dispersao_nao_decide():
    assert sel.intervalo_excesso([0.01] * 11)["veredito"] == sel.SEM_AMOSTRA
    assert sel.intervalo_excesso([0.01] * 24)["veredito"] == sel.SEM_AMOSTRA
    assert sel.intervalo_excesso([float("nan")] * 30)["n"] == 0


def test_portao_so_contra_reprova_por_padrao():
    assert sel.reprova({"veredito": sel.CONTRA})
    assert not sel.reprova({"veredito": sel.INCONCLUSIVO})
    assert not sel.reprova({"veredito": sel.SEM_AMOSTRA})
    assert not sel.reprova(None)
    # Exigindo vantagem, só o intervalo acima de zero passa.
    assert sel.reprova({"veredito": sel.INCONCLUSIVO}, exigir_vantagem=True)
    assert sel.reprova({"veredito": sel.SEM_AMOSTRA}, exigir_vantagem=True)
    assert not sel.reprova({"veredito": sel.VANTAGEM}, exigir_vantagem=True)


def test_rotulo_em_percentual_ao_mes():
    iv = {"media": 0.0042, "lo": -0.001, "hi": 0.0095}
    assert sel.rotulo(iv) == "+0,42% [-0,10; +0,95]"
    assert sel.rotulo(sel.intervalo_excesso([])) == "—"


# ── Universo por época ────────────────────────────────────────────────────────


def _vol(linhas):
    return pd.DataFrame(linhas, columns=["ticker", "mes", "financeiro"])


def test_janela_e_os_seis_meses_antes_do_rebalanceamento():
    assert upit.janela_decisao(2015, 4, 6) == (date(2014, 10, 1), date(2015, 4, 1))


def test_papel_iliquido_na_epoca_fica_fora_so_naquele_ano():
    piso = 1_000_000.0
    linhas = []
    for m in (10, 11, 12):
        linhas += [("ILIQ3", date(2014, m, 1), 21 * 50_000.0),
                   ("LIQD3", date(2014, m, 1), 21 * 5_000_000.0)]
    for m in (1, 2, 3):
        linhas += [("ILIQ3", date(2015, m, 1), 21 * 50_000.0),
                   ("LIQD3", date(2015, m, 1), 21 * 5_000_000.0)]
        linhas += [("ILIQ3", date(2016, m, 1), 21 * 3_000_000.0)]
    el = upit.elegiveis_por_ano(_vol(linhas), [2015, 2016, 2017], piso)
    assert el[2015]["abaixo"] == {"ILIQ3"}
    assert el[2016]["abaixo"] == set()
    assert not el[2017]["medido"]
    tks = ["ILIQ3", "LIQD3"]
    assert upit.filtrar(tks, el, 2015) == ["LIQD3"]
    assert upit.filtrar(tks, el, 2016) == tks
    # Ano sem medição não filtra ninguém.
    assert upit.filtrar(tks, el, 2017) == tks


def test_ausencia_na_janela_nao_e_iliquidez():
    linhas = [("LIQD3", date(2015, 2, 1), 21 * 5_000_000.0)]
    el = upit.elegiveis_por_ano(_vol(linhas), [2015], 1_000_000.0)
    assert upit.filtrar(["SEMDADO3", "LIQD3"], el, 2015) == ["SEMDADO3", "LIQD3"]


def test_mediana_resiste_a_um_mes_atipico():
    linhas = [("BLOC3", date(2015, m, 1), 21 * 10_000.0) for m in (1, 2, 3)]
    linhas.append(("BLOC3", date(2014, 12, 1), 21 * 900_000_000.0))
    el = upit.elegiveis_por_ano(_vol(linhas), [2015], 1_000_000.0)
    assert el[2015]["abaixo"] == {"BLOC3"}


def test_chave_normaliza_sufixo():
    el = {2015: {"medido": True, "abaixo": {"ILIQ3"}, "tickers_medidos": 1}}
    assert upit.filtrar(["ILIQ3.SA", "X"], el, 2015,
                        chave=lambda t: t.replace(".SA", "")) == ["X"]
