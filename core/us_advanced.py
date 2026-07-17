"""
core/us_advanced.py
Análise Avançada — Piotroski F-Score, Altman Z-Score, accruals de Sloan e
retorno incremental sobre capital.

Determinístico e puro (sem DB/rede). Regra do projeto: dado ausente NUNCA vira
zero nem "critério atendido" — um critério que não pode ser avaliado retorna None
e reduz a quantidade de critérios avaliáveis (reportada), em vez de inflar o score.

Referências: Piotroski (2000); Altman (1968); Sloan (1996).
Coberto por tests/test_us_advanced.py.
"""
from __future__ import annotations

from typing import Optional

from core.us_metrics import safe_div

_TAX_DEFAULT = 0.21


# ── Piotroski F-Score (0–9) ───────────────────────────────────────────────────
def piotroski_f_score(cur: dict, prev: dict) -> dict:
    """9 critérios binários de Piotroski (2000). Requer ano atual e anterior.

    Retorna score (soma dos critérios ATENDIDOS entre os avaliáveis), quantos
    puderam ser avaliados e o detalhe por critério (True/False/None).
    """
    def g(d: dict, k: str) -> Optional[float]:
        v = (d or {}).get(k)
        return None if v is None else float(v)

    roa_c = safe_div(g(cur, "net_income"), g(cur, "total_assets"))
    roa_p = safe_div(g(prev, "net_income"), g(prev, "total_assets"))
    cfo_c = g(cur, "operating_cash_flow")
    ta_c = g(cur, "total_assets")
    cfo_ta = safe_div(cfo_c, ta_c)

    cr_c = safe_div(g(cur, "current_assets"), g(cur, "current_liabilities"))
    cr_p = safe_div(g(prev, "current_assets"), g(prev, "current_liabilities"))
    ltd_c = safe_div(g(cur, "long_term_debt"), g(cur, "total_assets"))
    ltd_p = safe_div(g(prev, "long_term_debt"), g(prev, "total_assets"))
    sh_c, sh_p = g(cur, "shares_outstanding"), g(prev, "shares_outstanding")

    gm_c = safe_div(g(cur, "gross_profit"), g(cur, "revenue"))
    gm_p = safe_div(g(prev, "gross_profit"), g(prev, "revenue"))
    at_c = safe_div(g(cur, "revenue"), g(cur, "total_assets"))
    at_p = safe_div(g(prev, "revenue"), g(prev, "total_assets"))

    def cmp_gt(a, b):
        return None if a is None or b is None else bool(a > b)

    signals: dict[str, Optional[bool]] = {
        # Rentabilidade (4)
        "roa_positivo":        None if roa_c is None else bool(roa_c > 0),
        "cfo_positivo":        None if cfo_c is None else bool(cfo_c > 0),
        "roa_crescente":       cmp_gt(roa_c, roa_p),
        "accruals_saudaveis":  cmp_gt(cfo_ta, roa_c),   # CFO/AT > ROA
        # Alavancagem / liquidez (3)
        "alavancagem_caiu":    None if ltd_c is None or ltd_p is None else bool(ltd_c < ltd_p),
        "liquidez_subiu":      cmp_gt(cr_c, cr_p),
        "sem_emissao_acoes":   None if sh_c is None or sh_p is None else bool(sh_c <= sh_p),
        # Eficiência (2)
        "margem_bruta_subiu":  cmp_gt(gm_c, gm_p),
        "giro_ativos_subiu":   cmp_gt(at_c, at_p),
    }
    evaluable = [v for v in signals.values() if v is not None]
    missing = [k for k, v in signals.items() if v is None]
    return {
        "score": sum(1 for v in evaluable if v) if evaluable else None,
        "evaluable": len(evaluable),
        "max_score": 9,
        "signals": signals,
        "missing": missing,
        "partial": len(evaluable) < 9,
    }


# ── Altman Z-Score ────────────────────────────────────────────────────────────
# Z = 1.2·X1 + 1.4·X2 + 3.3·X3 + 0.6·X4 + 1.0·X5  (Altman 1968, empresas listadas)
_Z_WEIGHTS = {"x1": 1.2, "x2": 1.4, "x3": 3.3, "x4": 0.6, "x5": 1.0}


def altman_z_score(balance: dict, income: dict,
                   market_cap: Optional[float] = None) -> dict:
    """Z-Score clássico. Exige retained_earnings e market cap; sem eles → None.

    Zonas: Z > 2.99 segura; 1.81 ≤ Z ≤ 2.99 cinzenta; Z < 1.81 aflição.
    """
    ta = balance.get("total_assets")
    wc = None
    if balance.get("current_assets") is not None and balance.get("current_liabilities") is not None:
        wc = float(balance["current_assets"]) - float(balance["current_liabilities"])

    x1 = safe_div(wc, ta)
    x2 = safe_div(balance.get("retained_earnings"), ta)
    x3 = safe_div(income.get("ebit"), ta)
    x4 = safe_div(market_cap, balance.get("total_liabilities"))
    x5 = safe_div(income.get("revenue"), ta)

    comps = {"x1": x1, "x2": x2, "x3": x3, "x4": x4, "x5": x5}
    missing = [k for k, v in comps.items() if v is None]
    if missing:
        return {"z": None, "zone": None, "components": comps, "missing": missing}
    z = sum(_Z_WEIGHTS[k] * comps[k] for k in comps)
    zone = "segura" if z > 2.99 else "aflição" if z < 1.81 else "cinzenta"
    return {"z": round(z, 3), "zone": zone, "components": comps, "missing": []}


# ── Accruals de Sloan ─────────────────────────────────────────────────────────
def sloan_accruals(net_income: Optional[float], operating_cash_flow: Optional[float],
                   total_assets: Optional[float],
                   total_assets_prev: Optional[float] = None) -> Optional[float]:
    """(Lucro líquido − CFO) / ativos médios. MENOR é melhor (lucro mais em caixa).

    Sloan (1996): accruals altos antecipam reversão do lucro.
    """
    if net_income is None or operating_cash_flow is None or total_assets is None:
        return None
    avg_ta = (float(total_assets) + float(total_assets_prev)) / 2 \
        if total_assets_prev is not None else float(total_assets)
    return safe_div(float(net_income) - float(operating_cash_flow), avg_ta)


# ── Retorno incremental sobre capital ─────────────────────────────────────────
def incremental_roic(cur: dict, prev: dict, tax_rate: float = _TAX_DEFAULT) -> Optional[float]:
    """ΔNOPAT / ΔCapital investido — retorno do capital NOVO alocado.

    None se faltar dado ou se o capital investido não cresceu (métrica não é
    interpretável quando o denominador é ≤ 0).
    """
    ebit_c, ebit_p = cur.get("ebit"), prev.get("ebit")
    ic_c, ic_p = cur.get("invested_capital"), prev.get("invested_capital")
    if None in (ebit_c, ebit_p, ic_c, ic_p):
        return None
    d_nopat = (float(ebit_c) - float(ebit_p)) * (1 - tax_rate)
    d_ic = float(ic_c) - float(ic_p)
    if d_ic <= 0:
        return None
    return d_nopat / d_ic


def advanced_snapshot(income: list[dict], balance: list[dict], cashflow: list[dict],
                      market_cap: Optional[float] = None) -> dict:
    """Consolida os indicadores avançados dos 2 últimos anos anuais disponíveis."""
    def last_two(rows: list[dict]) -> tuple[dict, dict]:
        rows = sorted([r for r in rows if r.get("fiscal_year") is not None],
                      key=lambda r: r["fiscal_year"])
        if len(rows) >= 2:
            return rows[-1], rows[-2]
        if len(rows) == 1:
            return rows[-1], {}
        return {}, {}

    inc_c, inc_p = last_two(income)
    bal_c, bal_p = last_two(balance)
    cf_c, cf_p = last_two(cashflow)

    # Piotroski precisa de campos das 3 demonstrações no mesmo ano
    cur = {**inc_c, **bal_c, **cf_c}
    prev = {**inc_p, **bal_p, **cf_p}

    f = piotroski_f_score(cur, prev)
    z = altman_z_score(bal_c, inc_c, market_cap)
    acc = sloan_accruals(inc_c.get("net_income"), cf_c.get("operating_cash_flow"),
                         bal_c.get("total_assets"), bal_p.get("total_assets"))
    inc_roic = incremental_roic({**inc_c, **bal_c}, {**inc_p, **bal_p})
    return {
        "f_score": f["score"], "f_evaluable": f["evaluable"],
        "f_partial": f["partial"], "f_signals": f["signals"],
        "z_score": z["z"], "z_zone": z["zone"],
        "sloan_accruals": acc, "incremental_roic": inc_roic,
    }
