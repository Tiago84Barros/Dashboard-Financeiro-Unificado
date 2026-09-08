"""Overlay B3 compartilhado pela carteira corrente e pelo histórico."""
import pandas as pd

from core.b3_holdings_health import classify_cycle
from core.macro_data.portfolio_tilt import apply_macro_tilt, bound_macro_weights
from core.portfolio_constraints import project_capped_simplex, project_dual_capped


def apply_b3_macro(holdings: pd.DataFrame, impacts: dict, mode: str, *,
                   cap: float, sector_cap: float, cyclical_cap: float) -> pd.DataFrame:
    result = apply_macro_tilt(holdings, impacts, symbol_column="symbol",
                              score_column="score", mode=mode)
    targets = dict(zip(result.symbol, result.weight))
    groups = dict(zip(result.symbol, result.sector))
    warnings = []
    if sector_cap < 1 or cyclical_cap < 1:
        cyclicals = {s: classify_cycle(groups[s]) == "ciclico" for s in groups}
        targets, warnings = project_dual_capped(targets, groups, cyclicals,
                                               cap, sector_cap, cyclical_cap)
    else:
        targets = project_capped_simplex(targets, cap)
    result["weight"] = bound_macro_weights(result.weight_before_macro, result.symbol.map(targets))
    result.attrs["macro_warnings"] = warnings
    result.attrs["macro_turnover"] = float(.5 * (result.weight - result.weight_before_macro).abs().sum())
    return result
