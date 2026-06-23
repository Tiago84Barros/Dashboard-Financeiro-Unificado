"""
data_pipeline/market/parity.py
Fase 2 — validação de paridade legado (public.*) × market.* para os loaders já
migrados (load_multiplos_todos, load_setores). Núcleo PURO (recebe DataFrames)
+ runner que puxa das duas fontes e gera relatório JSON.

Decide o cutover: se a taxa de concordância por métrica for alta nos tickers
comuns e a cobertura do market.* for suficiente, pode-se virar a flag p/ market.
"""
from __future__ import annotations

import json
import logging

import pandas as pd

logger = logging.getLogger(__name__)

# Métricas comparadas (mesma grafia nas duas fontes). Liquidez_Corrente entra
# para evidenciar o gap (ausente em market.* → compared=0).
_METRICS = ["P/L", "P/VP", "DY", "ROE", "ROA", "ROIC",
            "Margem_Liquida", "Margem_Operacional", "Endividamento_Total",
            "Liquidez_Corrente", "EV_EBIT", "P_FCO", "Payout"]


def _norm(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty or "Ticker" not in df.columns:
        return pd.DataFrame()
    out = df.copy()
    out["Ticker"] = (out["Ticker"].astype(str).str.replace(".SA", "", regex=False)
                     .str.strip().str.upper())
    return out.drop_duplicates(subset=["Ticker"], keep="first").set_index("Ticker")


def _rel_diff(a: float, b: float) -> float:
    denom = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / denom


def _zero_rate(s: pd.Series):
    s = s.dropna()
    return round(float((s == 0).mean()), 4) if len(s) else None


def _is_constant(s: pd.Series) -> bool:
    """True se todos os valores não-nulos forem idênticos (sinal de placeholder/bug)."""
    s = s.dropna()
    return bool(len(s) >= 3 and s.nunique() == 1)


def compare_multiplos(legacy: pd.DataFrame, market: pd.DataFrame, *,
                      metrics: list[str] | None = None,
                      rel_tol: float = 0.10, abs_floor: float = 0.005,
                      worst_n: int = 5) -> dict:
    """
    Compara múltiplos por ticker. Concorda se rel_diff<=rel_tol OU |a-b|<=abs_floor.
    Retorna cobertura + estatísticas por métrica + piores divergências.
    """
    metrics = metrics or _METRICS
    L, M = _norm(legacy), _norm(market)
    lt, mt = set(L.index), set(M.index)
    common = sorted(lt & mt)
    report: dict = {
        "coverage": {
            "legacy": len(lt), "market": len(mt), "common": len(common),
            "only_legacy": len(lt - mt), "only_market": len(mt - lt),
            "only_market_tickers": sorted(mt - lt)[:50],
        },
        "metrics": {}, "params": {"rel_tol": rel_tol, "abs_floor": abs_floor},
    }
    tot_cmp = tot_agree = 0
    for m in metrics:
        if m not in L.columns or m not in M.columns:
            report["metrics"][m] = {"compared": 0, "motivo": "coluna ausente"}
            continue
        lvals = pd.to_numeric(L.loc[common, m], errors="coerce")
        mvals = pd.to_numeric(M.loc[common, m], errors="coerce")
        # diagnóstico do legado: zero/constante denunciam placeholder/bug, não
        # divergência real (ex.: DY/Payout=0, Endividamento=1.0 fixo no legado).
        diag = {
            "legacy_zero_rate": _zero_rate(lvals), "market_zero_rate": _zero_rate(mvals),
            "legacy_constant": _is_constant(lvals), "market_constant": _is_constant(mvals),
        }
        if diag["legacy_constant"]:
            diag["legacy_constant_value"] = round(float(lvals.dropna().iloc[0]), 6)
        rows = []
        for tk in common:
            a, b = lvals.get(tk), mvals.get(tk)
            if pd.isna(a) or pd.isna(b):
                continue
            rd = _rel_diff(float(a), float(b))
            agree = rd <= rel_tol or abs(float(a) - float(b)) <= abs_floor
            rows.append((tk, float(a), float(b), rd, agree))
        n = len(rows)
        if n == 0:
            report["metrics"][m] = {
                "compared": 0, "missing_market": int(mvals.isna().sum()), **diag}
            continue
        agree = sum(1 for r in rows if r[4])
        rds = sorted((r[3] for r in rows))
        worst = sorted(rows, key=lambda r: r[3], reverse=True)[:worst_n]
        report["metrics"][m] = {
            "compared": n, "agree": agree, "diverge": n - agree,
            "agree_rate": round(agree / n, 4),
            "mean_rel_diff": round(sum(rds) / n, 4),
            "median_rel_diff": round(rds[n // 2], 4),
            "p90_rel_diff": round(rds[min(n - 1, int(n * 0.9))], 4),
            "worst": [{"ticker": t, "legacy": round(a, 4), "market": round(b, 4),
                       "rel_diff": round(rd, 4)} for t, a, b, rd, _ in worst],
            **diag,
        }
        tot_cmp += n
        tot_agree += agree
    report["overall"] = {
        "compared": tot_cmp, "agree": tot_agree,
        "agree_rate": round(tot_agree / tot_cmp, 4) if tot_cmp else None,
    }
    return report


def compare_setores(legacy: pd.DataFrame, market: pd.DataFrame) -> dict:
    """Cobertura de empresas e divergências de SETOR entre as fontes."""
    def _idx(df):
        if df is None or df.empty or "ticker" not in df.columns:
            return pd.DataFrame()
        d = df.copy()
        d["ticker"] = d["ticker"].astype(str).str.upper().str.replace(".SA", "", regex=False)
        return d.drop_duplicates(subset=["ticker"]).set_index("ticker")
    L, M = _idx(legacy), _idx(market)
    common = sorted(set(L.index) & set(M.index))
    setor_mismatch = []
    if "SETOR" in getattr(L, "columns", []) and "SETOR" in getattr(M, "columns", []):
        for tk in common:
            sl, sm = str(L.at[tk, "SETOR"]).strip(), str(M.at[tk, "SETOR"]).strip()
            if sl and sm and sl.lower() != sm.lower():
                setor_mismatch.append({"ticker": tk, "legado": sl, "market": sm})
    return {
        "legacy": len(L), "market": len(M), "common": len(common),
        "only_legacy": len(set(L.index) - set(M.index)),
        "only_market": len(set(M.index) - set(L.index)),
        "setor_mismatch": len(setor_mismatch), "exemplos_mismatch": setor_mismatch[:20],
    }


def run_parity(save_path: str | None = None, rel_tol: float = 0.10) -> dict:
    """Puxa legado×market via os loaders e gera o relatório de paridade."""
    import core.b3_db as legacy
    import core.market_read as market
    rep = {
        "multiplos": compare_multiplos(legacy.load_multiplos_todos(),
                                       market.load_multiplos_todos(), rel_tol=rel_tol),
        "setores": compare_setores(legacy.load_setores(), market.load_setores()),
    }
    if save_path:
        try:
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(rep, f, indent=2, ensure_ascii=False, default=str)
            logger.info("parity salvo em %s", save_path)
        except Exception as exc:
            logger.warning("falha ao salvar parity: %s", exc)
    return rep
