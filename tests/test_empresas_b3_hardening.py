import numpy as np
import pandas as pd
import pytest

from core.allocation_calibration import purged_walk_forward_calibration
from core.portfolio_constraints import (
    InfeasiblePortfolioConstraint,
    minimum_assets_for_cap,
    project_capped_simplex,
)
from views.empresas_b3 import (
    _aplicar_diversificacao_setorial,
    _apply_cap_soft,
    _score_historico_ano,
)


def test_capped_simplex_respeita_invariantes():
    out = project_capped_simplex(
        {"A": 0.80, "B": 0.10, "C": 0.05, "D": 0.03, "E": 0.02},
        0.25,
    )
    assert sum(out.values()) == pytest.approx(1.0)
    assert min(out.values()) >= 0
    assert max(out.values()) <= 0.25 + 1e-9


@pytest.mark.parametrize("n,cap", [(1, 0.25), (2, 0.25), (3, 0.25)])
def test_cap_inviavel_nao_e_relaxado_silenciosamente(n, cap):
    weights = {f"T{i}": 1 / n for i in range(n)}
    with pytest.raises(InfeasiblePortfolioConstraint):
        _apply_cap_soft(weights, cap=cap)
    assert minimum_assets_for_cap(cap) == 4


def test_diversificacao_setorial_nao_completa_com_overflow():
    ranked = ["A", "B", "C", "D"]
    groups = {"A": "X", "B": "X", "C": "X", "D": "Y"}
    assert _aplicar_diversificacao_setorial(ranked, groups, 4, 1) == ["A", "D"]


def test_walk_forward_tem_holdouts_separados_e_pesos_viaveis():
    rng = np.random.default_rng(42)
    idx = pd.date_range("2018-01-31", periods=84, freq="ME")
    returns = rng.normal(0.008, 0.04, size=(84, 5))
    prices = pd.DataFrame(
        100 * np.cumprod(1 + returns, axis=0),
        index=idx,
        columns=list("ABCDE"),
    )
    params, diagnostics = purged_walk_forward_calibration(
        prices,
        {"A": 90, "B": 80, "C": 70, "D": 60, "E": 50},
        list("ABCDE"),
        gamma_grid=(0.5, 1.0),
        cap_grid=(0.20, 0.25),
        soft_grid=(0.03, 0.05),
        defaults=(0.9, 0.25, 0.05),
        n_folds=4,
        min_train_months=24,
        purge_months=3,
        embargo_months=2,
    )
    assert diagnostics["folds"] >= 2
    assert diagnostics["purge_months"] == 3
    assert diagnostics["embargo_months"] == 2
    assert len(params) == 3


def test_score_historico_respeita_available_at():
    hist = {
        "AAA3": pd.DataFrame([
            {
                "Ticker": "AAA3",
                "Data": pd.Timestamp("2020-12-31"),
                "AvailableAt": pd.Timestamp("2024-01-01", tz="UTC"),
                "ROE": 0.20,
            }
        ]),
        "BBB3": pd.DataFrame([
            {
                "Ticker": "BBB3",
                "Data": pd.Timestamp("2020-12-31"),
                "AvailableAt": pd.Timestamp("2021-03-15", tz="UTC"),
                "ROE": 0.10,
            }
        ]),
    }
    result = _score_historico_ano(
        hist,
        ["AAA3", "BBB3"],
        ano_ref=2022,
        pesos={"ROE": (1.0, True)},
        lag=1,
    )
    assert "AAA3" not in result
    assert "BBB3" in result


def test_plan_hash_muda_quando_peso_ou_score_muda():
    from core.b3_portfolio_model import _plan_hash

    base = [{"tk": "AAA3", "peso": 0.6, "score": 80}]
    assert _plan_hash(base, {}) != _plan_hash(
        [{"tk": "AAA3", "peso": 0.5, "score": 80}], {}
    )
    assert _plan_hash(base, {}) != _plan_hash(
        [{"tk": "AAA3", "peso": 0.6, "score": 81}], {}
    )


def test_inferencia_ticker_11_nao_chama_fundo_de_unit():
    from data_pipeline.market.normalize import _infer_asset_type

    assert _infer_asset_type(
        "XPML11",
        {
            "longName": "XP Malls Fundo Investimento Imobiliario Investor",
            "summaryProfile": {"sector": "Fundos Imobiliários"},
        },
    ) == "fii"
    assert _infer_asset_type(
        "WRLD11",
        {"longName": "Investo FTSE Global Equities ETF"},
    ) == "etf"
    assert _infer_asset_type(
        "KLBN11",
        {"longName": "Klabin SA Ctf de Deposito de Acoes Cons of 1 Sh + 4 Pfd Shs"},
    ) == "unit"
    assert _infer_asset_type(
        "DESCON11",
        {"longName": "Ativo sem classificação confiável"},
    ) == "other"
