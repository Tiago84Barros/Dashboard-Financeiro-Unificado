"""
core/us_portfolio.py
Construção de carteira-modelo americana a partir do universo com score.

Puro (pandas em memória), determinístico e testável. As restrições de peso por
posição e por setor são aplicadas por CAPPING ITERATIVO (water-filling) — é uma
HEURÍSTICA de projeção, não um otimizador de média-variância; rotulada como tal
para não superprometer (aprendizado do lado B3).

Coberto por tests/test_us_portfolio.py.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

import pandas as pd


@dataclass
class PortfolioConstraints:
    top_n: int = 20
    weighting: str = "score"          # 'score' | 'equal' | 'inverse_vol'
    max_weight: float = 0.10          # teto por posição
    max_sector_weight: float = 0.30   # teto por setor
    min_assets: int = 5
    max_assets: int = 30
    min_coverage: float = 40.0        # cobertura mínima de métricas (%)
    min_market_cap: float | None = None
    exclude_no_coverage: bool = True


def _cap_positions(w: pd.Series, cap: float) -> pd.Series:
    w = w.copy()
    for _ in range(200):
        over = w > cap + 1e-12
        if not over.any():
            break
        excess = float((w[over] - cap).sum())
        w[over] = cap
        under = w < cap - 1e-12
        if not under.any() or w[under].sum() == 0:
            break
        w[under] += excess * (w[under] / w[under].sum())
    return w


def _cap_sectors(w: pd.Series, sectors: pd.Series, cap: float) -> pd.Series:
    w = w.copy()
    for _ in range(200):
        totals = w.groupby(sectors).sum()
        over = totals[totals > cap + 1e-9].index
        if len(over) == 0:
            break
        over_mask = sectors.isin(over)
        freed = 0.0
        for s in over:
            mask = sectors == s
            cur = float(w[mask].sum())
            if cur <= 0:
                continue
            w[mask] *= cap / cur
            freed += cur - cap
        under_mask = ~over_mask
        if under_mask.any() and w[under_mask].sum() > 0:
            w[under_mask] += freed * (w[under_mask] / w[under_mask].sum())
        else:
            break
    return w


def _base_weights(df: pd.DataFrame, weighting: str) -> pd.Series:
    n = len(df)
    if weighting == "equal":
        return pd.Series(1.0 / n, index=df.index)
    if weighting == "inverse_vol" and "volatility" in df.columns:
        inv = 1.0 / df["volatility"].clip(lower=1e-6)
        return inv / inv.sum()
    # 'score' (default): proporcional ao score acima do piso do grupo selecionado
    s = df["score"].astype(float)
    base = (s - s.min()) + 1e-6
    return base / base.sum()


def build_portfolio(scored: pd.DataFrame,
                    constraints: PortfolioConstraints | None = None) -> pd.DataFrame:
    """Monta a carteira a partir do cross-section com score.

    Retorna holdings com colunas symbol/name/sector/industry/score/weight
    (weight soma ~1). Vazio se não houver candidatos elegíveis.
    """
    c = constraints or PortfolioConstraints()
    if scored is None or scored.empty or "score" not in scored.columns:
        return pd.DataFrame(columns=["symbol", "sector", "score", "weight"])

    df = scored.copy()
    df = df[df["score"].notna()]
    if c.exclude_no_coverage and "coverage" in df.columns:
        df = df[df["coverage"].fillna(0) >= c.min_coverage]
    if c.min_market_cap is not None and "_market_cap" in df.columns:
        df = df[df["_market_cap"].fillna(0) >= c.min_market_cap]
    if df.empty:
        return pd.DataFrame(columns=["symbol", "sector", "score", "weight"])

    df = df.sort_values("score", ascending=False)
    n = max(c.min_assets, min(c.top_n, c.max_assets, len(df)))
    df = df.head(n).reset_index(drop=True)

    w = _base_weights(df, c.weighting)
    w = w / w.sum()
    # Capping iterativo: alterna setor→posição para convergir a ambos. Termina na
    # POSIÇÃO (restrição dura de risco): o teto por ativo é sempre respeitado; o
    # teto por setor é atingido quando viável (heurística, não otimizador).
    sectors = df["sector"].fillna("—") if "sector" in df.columns else None
    for _ in range(8):
        if sectors is not None:
            w = _cap_sectors(w, sectors, c.max_sector_weight)
        w = _cap_positions(w, c.max_weight)

    out_cols = [col for col in ("symbol", "name", "sector", "industry", "score")
                if col in df.columns]
    holdings = df[out_cols].copy()
    holdings["weight"] = w.values
    return holdings.sort_values("weight", ascending=False).reset_index(drop=True)


def plan_hash(holdings: pd.DataFrame, params: dict) -> str:
    """Hash determinístico da carteira (dedup em portfolio_models)."""
    items = sorted((str(r["symbol"]), round(float(r["weight"]), 6))
                   for _, r in holdings.iterrows()) if not holdings.empty else []
    blob = json.dumps({"items": items, "params": params}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
