"""Avaliação determinística de carteiras de ações americanas."""
from __future__ import annotations

import numpy as np
import pandas as pd

TRACKS = (
    "score_quality", "score_growth", "score_solidity",
    "score_capital_efficiency", "score_valuation", "score_shareholder",
)


def _classification(score: float) -> str:
    if score >= 75:
        return "Excelente"
    if score >= 65:
        return "Forte"
    if score >= 50:
        return "Neutra"
    if score >= 35:
        return "Fraca"
    return "Crítica"


def normalize_holdings(holdings: pd.DataFrame) -> pd.DataFrame:
    """Normaliza ticker/peso; aceita peso em fração ou percentual."""
    if holdings is None or holdings.empty:
        return pd.DataFrame(columns=["symbol", "weight"])
    out = holdings.copy()
    out.columns = [str(c).strip().lower() for c in out.columns]
    if "ticker" in out.columns and "symbol" not in out.columns:
        out = out.rename(columns={"ticker": "symbol"})
    if "peso" in out.columns and "weight" not in out.columns:
        out = out.rename(columns={"peso": "weight"})
    if not {"symbol", "weight"}.issubset(out.columns):
        return pd.DataFrame(columns=["symbol", "weight"])
    out["symbol"] = out["symbol"].astype(str).str.strip().str.upper()
    out["weight"] = pd.to_numeric(out["weight"], errors="coerce")
    out = out[(out["symbol"] != "") & out["weight"].gt(0)]
    out = out.groupby("symbol", as_index=False)["weight"].sum()
    if out.empty:
        return out
    if out["weight"].sum() > 1.5:
        out["weight"] /= 100.0
    total = float(out["weight"].sum())
    if total > 0:
        out["weight"] /= total
    return out


def evaluate_portfolio(holdings: pd.DataFrame, scored: pd.DataFrame,
                       macro: dict | None = None) -> dict:
    """Avalia qualidade, concentração, setores e cada posição da carteira."""
    h = normalize_holdings(holdings)
    if h.empty or scored is None or scored.empty:
        return {"ok": False, "reason": "carteira ou universo de score vazio"}
    universe = scored.copy()
    universe["symbol"] = universe["symbol"].astype(str).str.upper()
    merged = h.merge(universe, on="symbol", how="left", suffixes=("", "_score"))
    missing = merged.loc[merged["score"].isna(), "symbol"].tolist()
    covered = merged[merged["score"].notna()].copy()
    if covered.empty:
        return {"ok": False, "reason": "nenhum ticker possui score", "missing": missing}

    covered_weight = float(covered["weight"].sum())
    analysis_weight = covered["weight"] / covered_weight
    base_score = float(np.average(covered["score"], weights=analysis_weight))
    macro_adjustment = 0.0
    if macro and macro.get("sector_impacts") and "sector" in covered:
        impacts = covered["sector"].map(macro["sector_impacts"]).fillna(0.0)
        macro_adjustment = float(np.average(impacts, weights=analysis_weight))
    adjusted_score = float(np.clip(base_score + macro_adjustment, 0, 100))

    hhi = float((h["weight"] ** 2).sum())
    effective_n = 1.0 / hhi if hhi else 0.0
    sectors = covered.assign(_w=analysis_weight).groupby(
        covered["sector"].fillna("Não classificado"))["_w"].sum().sort_values(ascending=False)
    max_sector = float(sectors.iloc[0]) if not sectors.empty else 1.0
    diversification = float(np.clip(100 * (0.55 * min(effective_n / 15, 1)
                                             + 0.45 * min((1 - max_sector) / 0.75, 1)), 0, 100))

    track_scores = {}
    for track in TRACKS:
        if track in covered and covered[track].notna().any():
            vals = covered[track].fillna(50.0)
            track_scores[track] = round(float(np.average(vals, weights=analysis_weight)), 1)

    median = float(universe["score"].median())
    rows = []
    for idx, row in covered.iterrows():
        weight = float(row["weight"])
        score = float(row["score"])
        if score >= 70 and weight < 0.12:
            action = "Considerar aumentar"
        elif score < 45 or weight > 0.18:
            action = "Revisar / reduzir"
        else:
            action = "Manter / monitorar"
        rows.append({
            "symbol": row["symbol"], "name": row.get("name"),
            "sector": row.get("sector"), "weight": weight, "score": score,
            "classification": _classification(score), "action": action,
            "vs_universe_median": round(score - median, 1),
        })
    positions = pd.DataFrame(rows).sort_values("weight", ascending=False)
    alerts = []
    if hhi > 0.15:
        alerts.append("Concentração elevada por posição (HHI acima de 0,15).")
    if max_sector > 0.35:
        alerts.append("Concentração setorial acima de 35%.")
    if covered_weight < 0.90:
        alerts.append("Menos de 90% do peso possui score fundamentalista válido.")
    if adjusted_score < 50:
        alerts.append("Score fundamentalista consolidado abaixo da faixa neutra.")
    return {
        "ok": True, "score": round(base_score, 1),
        "adjusted_score": round(adjusted_score, 1),
        "macro_adjustment": round(macro_adjustment, 1),
        "classification": _classification(adjusted_score),
        "coverage_weight": round(covered_weight * 100, 1),
        "hhi": round(hhi, 4), "effective_assets": round(effective_n, 1),
        "diversification_score": round(diversification, 1),
        "max_sector_weight": round(max_sector * 100, 1),
        "sector_weights": sectors, "track_scores": track_scores,
        "positions": positions, "alerts": alerts, "missing": missing,
    }
