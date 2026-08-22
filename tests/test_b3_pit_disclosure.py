"""Interface: o resultado quantitativo declara a ORIGEM da disponibilidade.

Achado A-002 (2026-08): o backtest da aba Empresas B3 roda hoje com 100 % das
vintages em `migration_baseline` — a data de disponibilidade é MODELADA pelo
prazo legal da CVM, não medida. Enquanto for assim, o resultado não pode ser
apresentado como "backtest point-in-time validado".

Estes testes fixam o contrato de interface: cobertura point-in-time exposta em
`attrs`, rótulo coerente com a cobertura, risco residual de restatement nomeado
e informação renderizada em card CSS (padrão do app), nunca solta.
"""
from __future__ import annotations

import inspect

import numpy as np
import pandas as pd

import views.empresas_b3 as b3
from views.empresas_b3 import (
    PITCoverage,
    _pit_card_html,
    _pit_rotulo_resultado,
    _rank_ic_por_ano,
    _simular_backtest,
)


def _hist(tickers, anos, availableat=None):
    out = {}
    for idx, tk in enumerate(tickers):
        df = pd.DataFrame({
            "Ticker": tk,
            "Data": [pd.Timestamp(a, 12, 31) for a in anos],
            "ROE": [0.10 + 0.01 * idx] * len(anos),
        })
        if availableat is not None:
            df["AvailableAt"] = availableat
        out[tk] = df
    return out


def _precos(tickers, periodos=30, inicio="2019-04-30"):
    idx = pd.date_range(inicio, periods=periodos, freq="ME")
    rng = np.random.default_rng(7)
    base = 100 * np.cumprod(
        1 + rng.normal(0.005, 0.02, size=(periodos, len(tickers))), axis=0)
    return pd.DataFrame(base, index=idx, columns=list(tickers))


_TKS = ["AAAA3", "BBBB3", "CCCC3", "DDDD3", "EEEE3"]


# ── Rótulo (função pura) ─────────────────────────────────────────────────────

def test_cobertura_medida_zero_nao_pode_ser_chamada_de_point_in_time_validado():
    info = _pit_rotulo_resultado(PITCoverage(snapshots_modelados=12, decisoes=4))
    assert info["nivel"] == "modelada"
    assert info["cobertura_pct"] == "0%"
    assert "SIMULAÇÃO" in info["titulo"]
    assert "MODELADA" in info["titulo"]
    assert "não é backtest point-in-time validado" in info["titulo"]
    assert "prazo" in info["detalhe"].lower()


def test_rotulo_de_cobertura_total_e_de_cobertura_parcial():
    total = _pit_rotulo_resultado(PITCoverage(snapshots_medidos=8, decisoes=2))
    assert total["nivel"] == "medida"
    assert total["cobertura_pct"] == "100%"
    assert "point-in-time validado" in total["titulo"]
    assert "SIMULAÇÃO" not in total["titulo"]

    parcial = _pit_rotulo_resultado(
        PITCoverage(snapshots_medidos=1, snapshots_modelados=3, decisoes=2))
    assert parcial["nivel"] == "mista"
    assert parcial["cobertura_pct"] == "25%"
    assert "PARCIALMENTE" in parcial["titulo"]


def test_risco_de_restatement_e_nomeado_em_qualquer_nivel():
    for cov in (PITCoverage(snapshots_medidos=3),
                PITCoverage(snapshots_modelados=3),
                PITCoverage()):
        risco = _pit_rotulo_resultado(cov)["risco"]
        assert "metric_value" in risco
        assert "restatement" in risco.lower()
        assert "reapresent" in risco.lower()


def test_contexto_entra_no_rotulo():
    assert "Rank-IC" in _pit_rotulo_resultado(
        PITCoverage(snapshots_modelados=2), "Rank-IC")["titulo"]


# ── Card CSS (padrão do app: nada de informação solta) ───────────────────────

def test_card_usa_css_do_app_e_expoe_cobertura_e_risco():
    html_card = _pit_card_html(
        PITCoverage(snapshots_medidos=1, snapshots_modelados=3,
                    linhas_barradas_vintage=2, linhas_barradas_prazo=5,
                    decisoes=2),
        "Backtest",
    )
    assert 'class="b3-pit-card"' in html_card
    assert 'class="b3-ind-label"' in html_card
    assert "25% medida" in html_card
    assert "1/4" in html_card                      # snapshots medidos/usados
    assert "metric_value" in html_card
    assert "restatement" in html_card.lower()


def test_css_do_card_esta_declarado_no_modulo():
    assert ".b3-pit-card" in b3._CSS
    assert ".b3-pit-title" in b3._CSS


# ── Contrato dos produtores (attrs) ──────────────────────────────────────────

def test_backtest_declara_cobertura_modelada_quando_so_ha_baseline():
    anos = [2018, 2019, 2020, 2021]
    hist = _hist(_TKS, anos, availableat=[pd.NaT] * len(anos))
    df_bt, _top, _n = _simular_backtest(
        _precos(_TKS), pd.DataFrame(), hist, _TKS,
        aporte=1000.0, data_inicio=pd.Timestamp("2019-01-01"),
        taxa_selic_aa=0.0, pesos={"ROE": (1.0, True)},
        tk_grupos={tk: {} for tk in _TKS}, top_n_max=5, cap=0.25,
    )
    assert not df_bt.empty
    cov = df_bt.attrs["pit_coverage"]
    assert isinstance(cov, PITCoverage)
    assert cov.decisoes >= 1
    assert cov.snapshots_medidos == 0
    assert df_bt.attrs["pit_cobertura_medida"] == 0.0
    assert df_bt.attrs["pit_disponibilidade"] == "modelada"


def test_backtest_declara_cobertura_medida_quando_ha_vintage_real():
    anos = [2018, 2019, 2020, 2021]
    av = [pd.Timestamp(a + 1, 3, 20, tz="UTC") for a in anos]
    hist = _hist(_TKS, anos, availableat=av)
    df_bt, _top, _n = _simular_backtest(
        _precos(_TKS), pd.DataFrame(), hist, _TKS,
        aporte=1000.0, data_inicio=pd.Timestamp("2019-01-01"),
        taxa_selic_aa=0.0, pesos={"ROE": (1.0, True)},
        tk_grupos={tk: {} for tk in _TKS}, top_n_max=5, cap=0.25,
    )
    assert df_bt.attrs["pit_cobertura_medida"] == 1.0
    assert df_bt.attrs["pit_disponibilidade"] == "medida"


def test_rank_ic_declara_a_mesma_cobertura():
    anos = [2018, 2019, 2020, 2021]
    hist = _hist(_TKS, anos, availableat=[pd.NaT] * len(anos))
    df_ic = _rank_ic_por_ano(
        _precos(_TKS, periodos=48, inicio="2018-01-31"), hist, _TKS,
        pesos={"ROE": (1.0, True)}, tk_grupos={tk: {} for tk in _TKS},
    )
    cov = df_ic.attrs["pit_coverage"]
    assert isinstance(cov, PITCoverage)
    assert cov.snapshots_modelados > 0
    assert df_ic.attrs["pit_cobertura_medida"] == 0.0
    assert df_ic.attrs["pit_disponibilidade"] == "modelada"


# ── A view realmente renderiza a declaração ──────────────────────────────────

def test_aba_avancada_renderiza_o_card_no_backtest_e_no_rank_ic():
    corpo = inspect.getsource(b3._tab_avancada)
    assert corpo.count("_pit_card_html") == 2
    assert 'df_bt.attrs.get("pit_coverage")' in corpo
    assert 'df_ic.attrs.get("pit_coverage")' in corpo
