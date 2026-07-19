"""Transformações puras para a análise individual de empresas americanas."""
from __future__ import annotations

import numpy as np
import pandas as pd


def numeric_financials(frame: pd.DataFrame | None) -> pd.DataFrame:
    """Normaliza a série anual sem transformar ausência em zero."""
    if frame is None or frame.empty:
        return pd.DataFrame()
    out = frame.copy()
    for column in out.columns:
        if column != "fiscal_year":
            out[column] = pd.to_numeric(out[column], errors="coerce")
    out["fiscal_year"] = pd.to_numeric(out["fiscal_year"], errors="coerce")
    return out.dropna(subset=["fiscal_year"]).sort_values("fiscal_year").reset_index(drop=True)


def last_value(frame: pd.DataFrame, column: str) -> float | None:
    if frame is None or frame.empty or column not in frame:
        return None
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    return float(values.iloc[-1]) if not values.empty else None


def regression_cagr(frame: pd.DataFrame, column: str) -> float | None:
    """Crescimento anual pela regressão logarítmica usada na seção B3."""
    if frame is None or frame.empty or column not in frame or "fiscal_year" not in frame:
        return None
    values = frame[["fiscal_year", column]].copy()
    values["fiscal_year"] = pd.to_numeric(values["fiscal_year"], errors="coerce")
    values[column] = pd.to_numeric(values[column], errors="coerce")
    values = values.dropna().query(f"`{column}` > 0").sort_values("fiscal_year")
    if len(values) < 2:
        return None
    x = values["fiscal_year"].to_numpy(dtype=float)
    x = x - x[0]
    slope, _ = np.polyfit(x, np.log(values[column].to_numpy(dtype=float)), deg=1)
    result = float(np.exp(slope) - 1.0)
    return result if np.isfinite(result) else None


def annual_price_returns(prices: pd.DataFrame | None) -> pd.DataFrame:
    columns = ["Ano", "Retorno"]
    if prices is None or prices.empty:
        return pd.DataFrame(columns=columns)
    out = prices[["date", "price"]].copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["price"] = pd.to_numeric(out["price"], errors="coerce")
    out = out.dropna().sort_values("date")
    if out.empty:
        return pd.DataFrame(columns=columns)
    annual = out.assign(Ano=out["date"].dt.year).groupby("Ano")["price"].last()
    returns = (annual.pct_change().dropna() * 100).rename("Retorno").reset_index()
    return returns


def annual_dividends(financials: pd.DataFrame,
                     dividends: pd.DataFrame | None = None) -> pd.DataFrame:
    """Dividendos por ação anuais; eventos têm prioridade sobre a estimativa GAAP."""
    if dividends is not None and not dividends.empty:
        out = dividends[["date", "amount"]].copy()
        out["date"] = pd.to_datetime(out["date"], errors="coerce")
        out["amount"] = pd.to_numeric(out["amount"], errors="coerce")
        out = out.dropna()
        if not out.empty:
            return (out.assign(fiscal_year=out["date"].dt.year)
                    .groupby("fiscal_year", as_index=False)["amount"].sum()
                    .rename(columns={"amount": "dividends_per_share"}))
    if financials is not None and not financials.empty and "dividends_per_share" in financials:
        return financials[["fiscal_year", "dividends_per_share"]].dropna().copy()
    return pd.DataFrame(columns=["fiscal_year", "dividends_per_share"])


def derive_metric_history(financials: pd.DataFrame,
                          prices: pd.DataFrame | None = None,
                          dividends: pd.DataFrame | None = None) -> pd.DataFrame:
    """Deriva margens, retornos, capital e múltiplos por exercício GAAP."""
    f = numeric_financials(financials)
    if f.empty:
        return f

    def div(num: str, den: str) -> pd.Series:
        numerator = f[num] if num in f else pd.Series(np.nan, index=f.index)
        denominator = f[den] if den in f else pd.Series(np.nan, index=f.index)
        return numerator.div(denominator.replace(0, np.nan))

    op = f.get("operating_income", f.get("ebit", pd.Series(np.nan, index=f.index)))
    f["operating_margin"] = op.div(f.get("revenue", pd.Series(np.nan, index=f.index)).replace(0, np.nan))
    f["net_margin"] = div("net_income", "revenue")
    f["roe"] = div("net_income", "total_equity")
    f["roa"] = div("net_income", "total_assets")
    ebit = f.get("ebit", op)
    f["roic"] = (ebit * 0.79).div(
        f.get("invested_capital", pd.Series(np.nan, index=f.index)).replace(0, np.nan))
    f["fcf_margin"] = div("free_cash_flow", "revenue")
    f["debt_to_equity"] = div("total_debt", "total_equity")
    f["financial_leverage"] = div("total_assets", "total_equity")
    f["current_ratio"] = div("current_assets", "current_liabilities")
    paid = f.get("dividends_paid", pd.Series(np.nan, index=f.index)).abs()
    f["payout"] = paid.div(
        f.get("net_income", pd.Series(np.nan, index=f.index)).replace(0, np.nan))

    if prices is not None and not prices.empty:
        p = prices[["date", "price"]].copy()
        p["date"] = pd.to_datetime(p["date"], errors="coerce")
        p["price"] = pd.to_numeric(p["price"], errors="coerce")
        year_end = p.dropna().assign(fiscal_year=p["date"].dt.year).groupby(
            "fiscal_year", as_index=False)["price"].last()
        f = f.merge(year_end, on="fiscal_year", how="left")
        market_cap = f["price"] * f.get(
            "shares_outstanding", pd.Series(np.nan, index=f.index))
        cash = f.get("cash_and_equivalents", pd.Series(0.0, index=f.index)).fillna(0)
        enterprise_value = market_cap + f.get(
            "total_debt", pd.Series(np.nan, index=f.index)) - cash
        f["p_b"] = market_cap.div(f.get("total_equity", pd.Series(np.nan, index=f.index)).replace(0, np.nan))
        f["pe"] = market_cap.div(f.get("net_income", pd.Series(np.nan, index=f.index)).replace(0, np.nan))
        f["ev_ebit"] = enterprise_value.div(ebit.replace(0, np.nan))
        f["p_fcf"] = market_cap.div(f.get("free_cash_flow", pd.Series(np.nan, index=f.index)).replace(0, np.nan))
        annual_div = annual_dividends(f, dividends)
        if not annual_div.empty:
            f = f.merge(annual_div, on="fiscal_year", how="left", suffixes=("", "_events"))
            div_col = "dividends_per_share_events" if "dividends_per_share_events" in f else "dividends_per_share"
            f["dividend_yield"] = f[div_col].div(f["price"].replace(0, np.nan))
    return f
