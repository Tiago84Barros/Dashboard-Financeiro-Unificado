"""Testes da construção de carteira (restrições de posição/setor)."""
import pandas as pd
import pytest

import core.us_portfolio as up


def _scored(n=12, sectors=("Tech", "Health", "Energy")):
    rows = []
    for i in range(n):
        rows.append({"symbol": f"S{i}", "sector": sectors[i % len(sectors)],
                     "industry": "X", "score": 100 - i * 5, "coverage": 80.0})
    return pd.DataFrame(rows)


def test_weights_somam_um():
    h = up.build_portfolio(_scored(), up.PortfolioConstraints(top_n=10))
    assert abs(h["weight"].sum() - 1.0) < 1e-6
    assert len(h) == 10


def test_teto_por_posicao():
    h = up.build_portfolio(_scored(), up.PortfolioConstraints(
        top_n=10, max_weight=0.15, weighting="score"))
    assert h["weight"].max() <= 0.15 + 1e-6


def test_teto_por_setor():
    cons = up.PortfolioConstraints(top_n=12, max_weight=0.5,
                                   max_sector_weight=0.40, weighting="equal")
    h = up.build_portfolio(_scored(12), cons)
    sector_tot = h.groupby("sector")["weight"].sum()
    assert sector_tot.max() <= 0.40 + 1e-6
    assert abs(h["weight"].sum() - 1.0) < 1e-6


def test_equal_weight():
    h = up.build_portfolio(_scored(10), up.PortfolioConstraints(
        top_n=5, weighting="equal", max_weight=1.0, max_sector_weight=1.0))
    assert h["weight"].round(4).nunique() == 1     # todos iguais


def test_exclui_baixa_cobertura():
    df = _scored(10)
    df.loc[df["symbol"] == "S0", "coverage"] = 10.0    # abaixo do mínimo
    h = up.build_portfolio(df, up.PortfolioConstraints(top_n=10, min_coverage=40))
    assert "S0" not in set(h["symbol"])


def test_score_weight_favorece_maiores():
    h = up.build_portfolio(_scored(6), up.PortfolioConstraints(
        top_n=6, weighting="score", max_weight=1.0, max_sector_weight=1.0))
    top = h.sort_values("score", ascending=False).iloc[0]
    bot = h.sort_values("score", ascending=False).iloc[-1]
    assert top["weight"] >= bot["weight"]


def test_vazio():
    assert up.build_portfolio(pd.DataFrame()).empty


def test_plan_hash_deterministico():
    h = up.build_portfolio(_scored(6), up.PortfolioConstraints(top_n=5))
    a = up.plan_hash(h, {"top_n": 5})
    b = up.plan_hash(h, {"top_n": 5})
    assert a == b and len(a) == 16
