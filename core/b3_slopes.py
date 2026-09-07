"""Slopes log-lineares dos indicadores da B3 — proxy da trilha de crescimento.

Definição única: a tela de Empresas B3 e a Análise do Portfólio precisam do
MESMO crescimento, senão a mesma empresa recebe duas notas e nenhuma das duas
é auditável. O módulo é puro (pandas/numpy), sem Streamlit e sem banco.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

SLOPE_COLS: tuple[str, ...] = (
    "ROE", "ROIC", "Margem_Liquida", "Margem_Operacional",
)


def compute_slope_log(s: pd.Series) -> float | None:
    """Slope da regressão log-linear — proxy de crescimento anualizado do indicador."""
    s = pd.to_numeric(s, errors="coerce").dropna()
    s = s[s > 0]
    if len(s) < 3:
        return None
    x = np.arange(len(s), dtype=float)
    try:
        slope, _ = np.polyfit(x, np.log(s.values), 1)
        return float(slope) if np.isfinite(slope) else None
    except Exception:
        return None


def enrich_com_slopes(
    df_mult: pd.DataFrame,
    hist_batch: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """Acrescenta colunas ``{col}_slope_log`` calculadas do histórico.

    Colunas ausentes são silenciosamente ignoradas pelo scoring — a falta vira
    cobertura menor, não desempenho ruim.
    """
    if not hist_batch or df_mult.empty:
        return df_mult
    slope_data: dict[str, dict[str, float]] = {}
    for tk, df_h in hist_batch.items():
        if df_h is None or df_h.empty:
            continue
        row: dict[str, float] = {}
        for c in SLOPE_COLS:
            if c not in df_h.columns:
                continue
            v = compute_slope_log(df_h[c])
            if v is not None:
                row[f"{c}_slope_log"] = v
        if row:
            slope_data[tk] = row
    if not slope_data:
        return df_mult
    df_sl = pd.DataFrame.from_dict(slope_data, orient="index")
    df_sl.index.name = "Ticker"
    return df_mult.merge(df_sl.reset_index(), on="Ticker", how="left")
