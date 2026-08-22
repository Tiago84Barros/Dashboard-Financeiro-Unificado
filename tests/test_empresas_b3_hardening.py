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
    _simular_backtest,
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
    score_history = {
        pd.Timestamp(year, 4, 1): {
            "A": 90 - (year % 3), "B": 80, "C": 70, "D": 60, "E": 50,
        }
        for year in range(2018, 2025)
    }
    params, diagnostics = purged_walk_forward_calibration(
        prices,
        score_history,
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
    assert diagnostics["development_folds"] >= 1
    assert "final_audit_objective" in diagnostics
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


def test_score_historico_barra_baseline_quando_prazo_cvm_nao_venceu():
    """Fail-closed do achado A-002: sem vintage medida, a disponibilidade é
    MODELADA pelo prazo legal de publicação (31/03 do ano seguinte). Decisão
    anterior a esse prazo não pode aceitar a linha — era look-ahead latente.
    """
    hist = {
        "AAA3": pd.DataFrame([{
            "Ticker": "AAA3", "Data": pd.Timestamp("2021-12-31"), "ROE": 0.20,
        }]),
        "BBB3": pd.DataFrame([{
            "Ticker": "BBB3", "Data": pd.Timestamp("2021-12-31"), "ROE": 0.10,
        }]),
    }
    kwargs = dict(pesos={"ROE": (1.0, True)}, lag=1)
    assert _score_historico_ano(hist, ["AAA3", "BBB3"], ano_ref=2022,
                                rebal_month=1, **kwargs) == {}
    assert _score_historico_ano(hist, ["AAA3", "BBB3"], ano_ref=2022,
                                rebal_month=4, **kwargs) != {}


def test_backtest_nao_roda_quando_a_decisao_antecede_a_publicacao():
    tickers = ["AAAA3", "BBBB3", "CCCC3", "DDDD3", "EEEE3"]
    index = pd.date_range("2022-01-31", periods=3, freq="ME")
    prices = pd.DataFrame(
        [[10.0] * 5] * 3, index=index, columns=tickers,
    )
    history = {
        ticker: pd.DataFrame([{
            "Ticker": ticker,
            "Data": pd.Timestamp("2021-12-31"),
            "ROE": 0.10 + idx * 0.01,
        }])
        for idx, ticker in enumerate(tickers)
    }
    comum = dict(
        aporte=1000.0, data_inicio=pd.Timestamp("2022-01-01"),
        taxa_selic_aa=0.0, pesos={"ROE": (1.0, True)},
        tk_grupos={ticker: {} for ticker in tickers}, top_n_max=5, cap=0.25,
    )
    # janeiro/2022: o balanço FY2021 ainda não é público e não há vintage real
    vazio, _, _ = _simular_backtest(
        prices, pd.DataFrame(), history, tickers, rebal_month=1, **comum)
    assert vazio.empty
    # abril/2022: prazo vencido — a simulação existe, mas MODELADA
    prices_abr = pd.DataFrame(
        [[10.0] * 5] * 3,
        index=pd.date_range("2022-04-30", periods=3, freq="ME"),
        columns=tickers,
    )
    cheio, _, _ = _simular_backtest(
        prices_abr, pd.DataFrame(), history, tickers, rebal_month=4, **comum)
    assert not cheio.empty
    assert cheio.iloc[-1]["Estratégia"] == pytest.approx(3000.0)
    assert cheio.attrs["pit_disponibilidade"] == "modelada"
    assert cheio.attrs["pit_cobertura_medida"] == 0.0


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


def test_backtest_preserva_aporte_sem_cotacao_como_caixa():
    tickers = ["AAAA3", "BBBB3", "CCCC3", "DDDD3", "EEEE3"]
    index = pd.to_datetime(["2022-04-30", "2022-05-31", "2022-06-30"])
    prices = pd.DataFrame(
        [[10.0] * 5, [np.nan] * 5, [10.0] * 5],
        index=index,
        columns=tickers,
    )
    history = {
        ticker: pd.DataFrame([{
            "Ticker": ticker,
            "Data": pd.Timestamp("2021-12-31"),
            "ROE": 0.10 + idx * 0.01,
        }])
        for idx, ticker in enumerate(tickers)
    }
    result, _, _ = _simular_backtest(
        prices,
        pd.DataFrame(),
        history,
        tickers,
        aporte=1000.0,
        data_inicio=pd.Timestamp("2022-01-01"),
        taxa_selic_aa=0.0,
        pesos={"ROE": (1.0, True)},
        tk_grupos={ticker: {} for ticker in tickers},
        top_n_max=5,
        usar_gamma=True,
        cap=0.25,
    )
    assert result.iloc[1]["Estratégia"] == pytest.approx(2000.0)
    assert result.iloc[-1]["Estratégia"] == pytest.approx(3000.0)
    assert result.iloc[-1]["Benchmark"] == pytest.approx(3000.0)
    assert result.iloc[-1]["Tesouro Selic"] == pytest.approx(3000.0)


def test_benjamini_hochberg_monotono_e_conservador():
    from views.portfolio_b3 import _benjamini_hochberg

    adjusted = _benjamini_hochberg([0.001, 0.02, 0.20, 0.90])
    assert adjusted[0] <= adjusted[1] <= adjusted[2] <= adjusted[3]
    assert all(q >= p for p, q in zip([0.001, 0.02, 0.20, 0.90], adjusted))


def test_aplicar_cheapness_mistura_qualidade_e_preco():
    from views.portfolio_b3 import _aplicar_cheapness

    base = {"ROE": (0.6, True), "Margem_Liquida": (0.4, True)}
    # peso 0 → não mexe (comportamento padrão)
    assert _aplicar_cheapness(base, 0.0) == base

    out = _aplicar_cheapness(base, 0.3)
    qualidade = out["ROE"][0] + out["Margem_Liquida"][0]
    cheapness = out["P/L"][0] + out["P/VP"][0] + out["EV_EBIT"][0]
    assert abs(qualidade - 0.7) < 1e-9      # qualidade reduzida por (1-w)
    assert abs(cheapness - 0.3) < 1e-9      # cheapness recebe w
    assert abs((qualidade + cheapness) - 1.0) < 1e-9
    # múltiplos de preço entram como "menor é melhor"
    assert out["P/L"][1] is False and out["P/VP"][1] is False
