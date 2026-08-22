"""
core/us_asymmetry.py
Score de ASSIMETRIA — aba "Empresas Fora da Curva" (retorno assimétrico).

NÃO substitui a carteira fundamentalista principal. Aceita maior incerteza e maior
taxa de erro: grandes vencedoras são raras e poucas posições podem responder por
grande parte do retorno. A saída é uma HIPÓTESE com sinais, riscos e condições de
invalidação — nunca uma recomendação/certeza (regra do enunciado).

Tudo aqui é determinístico e puro (sem DB/rede/LLM). Coberto por
tests/test_us_asymmetry.py.
"""
from __future__ import annotations

from typing import Sequence


def _series_pairs(series: Sequence[dict], field: str) -> list[tuple[int, float]]:
    out = []
    for r in series:
        v, y = r.get(field), r.get("fiscal_year")
        if v is not None and y is not None:
            out.append((int(y), float(v)))
    out.sort()
    return out


def build_trajectory(income: Sequence[dict], balance: Sequence[dict],
                     cashflow: Sequence[dict]) -> dict:
    """Sinais de TRAJETÓRIA (tendência), não só o nível do último ano. Puro."""
    traj: dict = {"op_margin_trend": None, "revenue_growth_persistence": None,
                  "shares_change": None, "sbc_to_revenue": None,
                  "fcf_positive_ratio": None, "roic_trend": None}

    rev = _series_pairs(income, "revenue")
    opinc = dict(_series_pairs(income, "operating_income"))
    margins = [(y, opinc[y] / r) for y, r in rev if y in opinc and r not in (0, None)]
    if len(margins) >= 2:
        traj["op_margin_trend"] = margins[-1][1] - margins[0][1]

    if len(rev) >= 2:
        yoy = [(rev[i][1] / rev[i - 1][1] - 1.0)
               for i in range(1, len(rev)) if rev[i - 1][1] > 0]
        if yoy:
            traj["revenue_growth_persistence"] = sum(1 for g in yoy if g > 0) / len(yoy)

    shares = _series_pairs(balance, "shares_outstanding")
    if len(shares) >= 2 and shares[0][1] > 0:
        traj["shares_change"] = shares[-1][1] / shares[0][1] - 1.0

    sbc = _series_pairs(cashflow, "stock_based_compensation")
    if sbc and rev:
        last_rev = rev[-1][1]
        if last_rev > 0:
            traj["sbc_to_revenue"] = abs(sbc[-1][1]) / last_rev

    fcf = _series_pairs(cashflow, "free_cash_flow")
    if fcf:
        traj["fcf_positive_ratio"] = sum(1 for _, v in fcf if v > 0) / len(fcf)
    return traj


# ── Sinais (nome, peso, predicado) ────────────────────────────────────────────
def _positive_signals(m: dict, t: dict) -> list[tuple[str, float, bool]]:
    g3, g5 = m.get("revenue_cagr_3y"), m.get("revenue_cagr_5y")
    return [
        ("Crescimento de receita elevado (3a ≥ 20%)", 1.5, g3 is not None and g3 >= 0.20),
        ("Crescimento persistente (5a ≥ 15%)", 1.2, g5 is not None and g5 >= 0.15),
        ("Aceleração (3a > 5a)", 0.8, g3 is not None and g5 is not None and g3 > g5),
        ("FCF positivo e crescente", 1.0,
         (m.get("_fcf") or 0) > 0 and (m.get("fcf_cagr_3y") or -1) > 0),
        ("Expansão de margem operacional", 1.0,
         t.get("op_margin_trend") is not None and t["op_margin_trend"] > 0.01),
        ("ROIC alto (≥ 15%)", 1.2, m.get("roic") is not None and m["roic"] >= 0.15),
        ("Baixa diluição / recompra", 1.0,
         t.get("shares_change") is not None and t["shares_change"] <= 0.03),
        ("SBC controlada (< 10% receita)", 0.6,
         t.get("sbc_to_revenue") is not None and t["sbc_to_revenue"] < 0.10),
        ("Alavancagem baixa (DL/EBITDA < 2)", 0.8,
         m.get("net_debt_ebitda") is not None and m["net_debt_ebitda"] < 2),
        ("Crescimento de receita consistente", 0.8,
         t.get("revenue_growth_persistence") is not None
         and t["revenue_growth_persistence"] >= 0.8),
    ]


def _negative_signals(m: dict, t: dict) -> list[tuple[str, bool]]:
    g3 = m.get("revenue_cagr_3y")
    return [
        ("FCF persistentemente negativo",
         t.get("fcf_positive_ratio") is not None and t["fcf_positive_ratio"] < 0.34),
        ("Diluição excessiva (> 10% no período)",
         t.get("shares_change") is not None and t["shares_change"] > 0.10),
        ("SBC descontrolada (> 15% receita)",
         t.get("sbc_to_revenue") is not None and t["sbc_to_revenue"] > 0.15),
        ("Dívida elevada (DL/EBITDA > 3)",
         m.get("net_debt_ebitda") is not None and m["net_debt_ebitda"] > 3),
        ("Deterioração de margem",
         t.get("op_margin_trend") is not None and t["op_margin_trend"] < -0.01),
        ("Crescimento sem retorno sobre capital",
         g3 is not None and g3 >= 0.20 and (m.get("roic") is None or m.get("roic", 0) < 0.08)),
    ]


_INPUTS = ("revenue_cagr_3y", "revenue_cagr_5y", "roic", "net_debt_ebitda", "_fcf")


def classify_stage(m: dict, t: dict) -> str:
    g3 = m.get("revenue_cagr_3y") or 0
    mcap = m.get("_market_cap")
    fcf = m.get("_fcf") or 0
    if g3 >= 0.30 and (mcap is None or mcap < 2e9):
        return "early"
    if g3 >= 0.20 and fcf > 0:
        return "scaling"
    if g3 >= 0.12:
        return "growth"
    return "mature"


def score_asymmetry(m: dict, trajectory: dict | None = None) -> dict:
    """Score de assimetria (0–100) + sinais, riscos, invalidação e tamanho sugerido."""
    t = trajectory or {}
    pos = _positive_signals(m, t)
    neg = _negative_signals(m, t)

    total_w = sum(w for _, w, _ in pos)
    got_w = sum(w for _, w, ok in pos if ok)
    pos_score = (got_w / total_w * 100) if total_w else 0.0
    neg_count = sum(1 for _, ok in neg if ok)
    score = max(0.0, min(100.0, pos_score - neg_count * 9.0))

    missing = [k.lstrip("_") for k in _INPUTS if m.get(k) is None]
    confidence = round(100 * (1 - len(missing) / len(_INPUTS)), 0)

    risk_class = "muito alta" if neg_count >= 3 else "alta" if neg_count >= 1 else "média"
    # subcarteira pequena: posição escala com score×confiança, teto baixo
    suggested = round(min(3.0, max(0.5, score / 100 * (confidence / 100) * 3.0)), 2)

    invalidation = [
        "Receita desacelera de forma persistente",
        "Margem operacional deteriora",
        "Diluição/SBC saem de controle",
        "Alavancagem cresce sem retorno correspondente",
    ]
    hypotheses = [
        "O crescimento atual se sustenta por vários anos.",
        "A empresa converte crescimento em caixa e retorno sobre capital.",
        "Não há destruição de valor por diluição/aquisições ruins.",
    ]
    return {
        "asymmetry_score": round(score, 1),
        "confidence": confidence,
        "stage": classify_stage(m, t),
        "risk_class": risk_class,
        "horizon": "longo (3–10 anos)",
        "suggested_position_pct": suggested,
        "positive_signals": [label for label, _, ok in pos if ok],
        "risks": [label for label, ok in neg if ok],
        "hypotheses": hypotheses,
        "invalidation": invalidation,
        "missing_data": missing,
    }
