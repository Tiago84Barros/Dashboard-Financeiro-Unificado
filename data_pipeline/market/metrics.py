"""
data_pipeline/market/metrics.py
Cálculo de indicadores derivados a partir das demonstrações + preço + dividendos
de market.* (sem rede). Núcleo PURO e testável.

Snapshot (último ano disponível) → calculated_metrics (period='ttm', year=0):
  Margem_Liquida, Margem_Operacional, ROE, ROA, ROIC, Endividamento_Total,
  P/L, P/VP, EV_EBIT, P_FCO, DY, Payout.

Cada valor passa pela faixa coerente de core.data_quality (fora da faixa → omitido,
nunca grava lixo). Usa as mesmas convenções do ETL legado (safe_div, escala decimal).
"""
from __future__ import annotations

import core.data_quality as _dq


def _safe_div(a, b):
    a = _dq.to_float(a)
    b = _dq.to_float(b)
    if a is None or b is None or abs(b) < 1e-12:
        return None
    return a / b


# fórmula/insumos de cada indicador (para auditoria e clareza)
def compute_snapshot(f: dict) -> dict[str, tuple[float, str]]:
    """
    f: insumos do ÚLTIMO ano + mercado:
       net_income, revenue, ebit, ebitda, total_assets, equity, cash,
       gross_debt, net_debt, fco (op. cash flow), market_cap;
       div_ttm (dividendos POR AÇÃO, 12m), price (último preço), eps (LPA) —
       DY e Payout usam base por ação (os dividendos da BRAPI são por ação,
       enquanto market_cap/net_income são absolutos: misturar daria ~0).
    Retorna {indicador: (valor, metodo)} apenas com valores válidos na faixa coerente.
    """
    ni  = f.get("net_income"); rev = f.get("revenue"); ebit = f.get("ebit")
    ta  = f.get("total_assets"); eq = f.get("equity"); cash = f.get("cash")
    gd  = f.get("gross_debt"); nd = f.get("net_debt"); fco = f.get("fco")
    mc  = f.get("market_cap")
    div_ps = f.get("div_ttm"); price = f.get("price"); eps = f.get("eps")
    ca  = f.get("current_assets"); cl = f.get("current_liabilities")

    inv_capital = None
    if all(_dq.to_float(x) is not None for x in (eq, gd)):
        inv_capital = _dq.to_float(eq) + _dq.to_float(gd) - (_dq.to_float(cash) or 0.0)
    ev = None
    if mc is not None and nd is not None:
        ev = _dq.to_float(mc) + _dq.to_float(nd)

    candidates = {
        "Margem_Liquida":      (_safe_div(ni, rev),  "net_income/revenue"),
        "Margem_Operacional":  (_safe_div(ebit, rev), "ebit/revenue"),
        "ROE":                 (_safe_div(ni, eq),   "net_income/equity"),
        "ROA":                 (_safe_div(ni, ta),   "net_income/total_assets"),
        "ROIC":                (_safe_div(ebit, inv_capital), "ebit/(equity+gross_debt-cash)"),
        "Endividamento_Total": (_safe_div(gd, eq),   "gross_debt/equity"),
        "Liquidez_Corrente":   (_safe_div(ca, cl),   "current_assets/current_liabilities"),
        "P/L":                 (_safe_div(mc, ni),   "market_cap/net_income"),
        "P/VP":                (_safe_div(mc, eq),   "market_cap/equity"),
        "EV_EBIT":             (_safe_div(ev, ebit), "(market_cap+net_debt)/ebit"),
        "P_FCO":               (_safe_div(mc, fco),  "market_cap/operating_cash_flow"),
        "DY":                  (_safe_div(div_ps, price), "dividendos_ps_12m/preco"),
        "Payout":              (_safe_div(div_ps, eps),   "dividendos_ps_12m/LPA"),
    }
    out: dict[str, tuple[float, str]] = {}
    for name, (val, method) in candidates.items():
        if val is None:
            continue
        if _dq.is_valid_value(name, val):   # respeita faixa coerente; descarta absurdos
            out[name] = (round(float(val), 8), method)
    return out


# Múltiplos de valuation por ANO derivados de ações em circulação ATUAIS
# (não temos ações históricas) — aproximação válida em janelas curtas; recebem
# confiança menor. Fundamentais (margens/ROE/ROA/ROIC/Endiv/Liquidez) e DY são
# exatos das demonstrações/preço e mantêm confiança cheia.
ANNUAL_APPROX = frozenset({"P/L", "P/VP", "EV_EBIT", "P_FCO", "Payout"})


def to_metric_rows(ticker: str, snapshot: dict[str, tuple[float, str]],
                   confidence: float = 85.0, *, period: str = "ttm", year: int = 0,
                   low_conf: frozenset[str] | set[str] | None = None,
                   low_conf_value: float = 60.0) -> list[dict]:
    """
    Converte o snapshot em linhas para market.calculated_metrics.
    low_conf: métricas que recebem confiança reduzida (ex.: valuation anual via
    ações atuais aproximadas). O método ganha o sufixo '~aprox' p/ rastreio.
    """
    low_conf = low_conf or frozenset()
    rows = []
    for name, (val, method) in snapshot.items():
        approx = name in low_conf
        rows.append({
            "ticker": ticker, "period": period, "year": year, "quarter": 0,
            "metric_name": name, "metric_value": val,
            "calculation_method": (method + " ~aprox" if approx else method),
            "source": "market.compute",
            "confidence_score": low_conf_value if approx else confidence,
        })
    return rows
