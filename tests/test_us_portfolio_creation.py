"""Regressão do motor institucional de Criação de Portfólio dos EUA."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core.us_portfolio_creation import (
    USPortfolioCreationParams,
    build_industry_audit,
    build_portfolio_creation,
    prepare_eligible_universe,
)


def _universe(groups: int = 9, names_per_group: int = 6) -> pd.DataFrame:
    sectors = ["Technology", "Healthcare", "Industrials"]
    rows = []
    for group in range(groups):
        for rank in range(names_per_group):
            strength = 88 - group * 1.5 - rank * 5
            rows.append({
                "symbol": f"U{group:02d}{rank}",
                "name": f"Empresa {group}-{rank}",
                "sector": sectors[group % len(sectors)],
                "industry": f"Industry {group}",
                "exchange": "NASDAQ" if group % 2 else "NYSE",
                "security_type": "common",
                "is_active": True,
                "is_reit": False,
                "score": strength,
                "score_quality": strength,
                "score_growth": strength - 2,
                "score_solidity": strength - 1,
                "score_capital_efficiency": strength + 1,
                "score_valuation": strength - 3,
                "score_shareholder": strength - 4,
                "coverage": 88.0,
                "_years": 10,
                "_market_cap": 4_000_000_000 + group * 10_000_000,
                "roic": .12,
                "roe": .18,
                "fcf_yield": .06,
                "cash_conversion": 1.0,
                "current_ratio": 1.5,
                "net_margin": .15,
                "interest_coverage": 8.0,
            })
    return pd.DataFrame(rows)


def test_filtros_sequenciais_reconciliam_universo():
    frame = _universe(groups=2, names_per_group=5)
    frame.loc[0, "is_active"] = False
    frame.loc[1, "exchange"] = "OTC"
    frame.loc[2, "_market_cap"] = np.nan
    frame.loc[3, "coverage"] = 10
    frame.loc[4, "_years"] = 1
    params = USPortfolioCreationParams(
        min_market_cap=1_000_000_000, min_coverage=50, min_years=5,
        min_fundamental_score=40,
    )
    eligible, audit = prepare_eligible_universe(frame, params)
    assert len(eligible) == 5
    assert int(audit["count"].sum()) + len(eligible) == len(frame)
    assert set(eligible["exchange"]) <= {"NYSE", "NASDAQ"}


def test_auditoria_aprova_industrias_com_lideres_fortes():
    params = USPortfolioCreationParams(
        min_companies_per_industry=4, min_entry_score=55, min_score_edge=0,
        min_market_cap=1_000_000_000,
    )
    eligible, _ = prepare_eligible_universe(_universe(), params)
    audit = build_industry_audit(eligible, params)
    assert not audit.empty
    assert (audit["company_count"] == 6).all()
    assert audit["status"].eq("Aprovada").any()
    assert audit["history_periods"].eq(0).all()


def test_historico_pit_e_exigido_sem_inventar_fallback():
    params = USPortfolioCreationParams(
        min_companies_per_industry=4, min_entry_score=50, min_score_edge=0,
        require_historical_signal=True, min_history_periods=2,
    )
    eligible, _ = prepare_eligible_universe(_universe(groups=2), params)
    audit = build_industry_audit(eligible, params, pd.DataFrame())
    assert audit["status"].eq("Excluída").all()
    assert audit["reason"].eq("Histórico PIT indisponível").all()


def test_carteira_respeita_limites_e_soma_um():
    params = USPortfolioCreationParams(
        top_n=15, leaders_per_industry=2, min_companies_per_industry=4,
        min_entry_score=50, min_score_edge=0, max_weight=.10,
        max_industry_weight=.18, max_sector_weight=.40,
        min_market_cap=1_000_000_000,
    )
    result = build_portfolio_creation(_universe(), params)
    holdings = result["holdings"]
    assert result["ok"] is True
    assert len(holdings) == 15
    assert holdings["weight"].sum() == pytest.approx(1.0, abs=1e-8)
    assert holdings["weight"].max() <= .10 + 1e-7
    assert holdings.groupby("industry_group")["weight"].sum().max() <= .18 + 1e-7
    assert holdings.groupby("sector_group")["weight"].sum().max() <= .40 + 1e-7
    assert result["metrics"]["effective_assets"] > 10


def test_cap_adaptativo_documenta_ajuste_matematico():
    params = USPortfolioCreationParams(
        top_n=10, leaders_per_industry=2, min_companies_per_industry=4,
        min_entry_score=50, min_score_edge=0, max_weight=.05,
        max_industry_weight=.10, max_sector_weight=.20,
        adaptive_caps=True,
    )
    result = build_portfolio_creation(_universe(), params)
    assert result["ok"] is True
    assert any("ajustado" in warning for warning in result["warnings"])
    assert result["holdings"]["weight"].sum() == pytest.approx(1.0, abs=1e-8)


def test_interface_expoe_paridade_e_equivalentes_americanos():
    root = Path(__file__).resolve().parents[1]
    source = (root / "views" / "empresas_americanas.py").read_text(encoding="utf-8")
    for token in (
        "Etapa 2 de 3 · Aplicação em escala",
        "Auditoria por Indústria",
        "Treasury 3 meses",
        "S&P 500 (SPY)",
        "Peso máximo por indústria",
        "Usar na Avaliação de Portfólio",
        "Metodologia e equivalências B3 × Estados Unidos",
    ):
        assert token in source
