"""
core/us_metrics.py
Cálculo determinístico de métricas fundamentalistas dos EUA (puro, sem DB/rede).

Recebe séries anuais já normalizadas (colunas de market_us.*) e devolve um dict
de indicadores por empresa. Ausência NUNCA vira zero: divisão inválida → None
(rank neutro depois). Coberto por tests/test_us_metrics.py.
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

_TAX_DEFAULT = 0.21  # alíquota corporativa federal EUA (aproximação p/ NOPAT)


def _f(v: Any) -> Optional[float]:
    """Coage para float, tolerando Decimal (NUMERIC do Postgres). None se inválido."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def safe_div(num: Any, den: Any) -> Optional[float]:
    """Divisão que preserva ausência: None se faltar dado ou denominador ~0.

    Coage os operandos a float — o warehouse devolve NUMERIC como Decimal, e
    float/Decimal levantaria TypeError.
    """
    n, d = _f(num), _f(den)
    if n is None or d is None or d == 0:
        return None
    return n / d


def div_if_den_positive(num: Any, den: Any) -> Optional[float]:
    """Como safe_div, mas exige denominador POSITIVO em vez de apenas != 0.

    Razão cujo denominador troca de sinal deixa de ser ordenável: ROE de lucro
    -50 sobre patrimônio -200 dá +25%, e passaria por rentabilidade boa; EV/EBIT
    com EBIT negativo dá um número negativo, que o ranqueador lê como o múltiplo
    mais barato do universo. Nesses casos o valor não é "ruim", é indefinido
    (n/m) — e ausência é o que o score já sabe tratar, reduzindo cobertura e
    confiança. Ver tests/test_score_sinal_de_denominador.py (achado A-101).

    O prejuízo em si não fica impune: margem líquida, ROA e earnings yield têm
    denominador sempre positivo (receita, ativo, valor de mercado) e continuam
    marcando o resultado negativo com o sinal certo.
    """
    n, d = _f(num), _f(den)
    if n is None or d is None or d <= 0:
        return None
    return n / d


def cagr(first: Optional[float], last: Optional[float], years: int) -> Optional[float]:
    """CAGR entre first e last em `years` períodos. None se inválido.

    Exige base positiva (crescimento composto não é definido com base <= 0).
    """
    if first is None or last is None or years <= 0:
        return None
    if first <= 0 or last <= 0:
        return None
    return (last / first) ** (1.0 / years) - 1.0


def _latest(series: Sequence[dict], field: str) -> Optional[float]:
    for row in reversed(series):
        v = row.get(field)
        if v is not None:
            return float(v)
    return None


def _series_values(series: Sequence[dict], field: str) -> list[tuple[int, float]]:
    out = []
    for row in series:
        v = row.get(field)
        y = row.get("fiscal_year")
        if v is not None and y is not None:
            out.append((int(y), float(v)))
    out.sort()
    return out


def _growth(series: Sequence[dict], field: str, window: int) -> Optional[float]:
    vals = _series_values(series, field)
    if len(vals) < 2:
        return None
    last_year, last_val = vals[-1]
    # procura o ponto ~window anos antes; senão usa o mais antigo disponível
    target_year = last_year - window
    base = None
    for y, v in vals:
        if y <= target_year:
            base = (y, v)
    if base is None:
        base = vals[0]
    span = last_year - base[0]
    if span <= 0:
        return None
    return cagr(base[1], last_val, span)


def compute_company_metrics(
    income: Sequence[dict], balance: Sequence[dict], cashflow: Sequence[dict], *,
    price: Optional[float] = None, market_cap: Optional[float] = None,
    shares: Optional[float] = None,
) -> dict:
    """Deriva o snapshot de métricas de UMA empresa a partir das séries anuais.

    As séries vêm ordenadas por ano; usamos o último ano com dado para cada campo.
    market_cap pode ser dado direto ou derivado de price*shares.
    """
    # NUMERIC do Postgres chega como Decimal; coage os escalares externos a float
    # (há aritmética direta abaixo, não só safe_div).
    price, market_cap, shares = _f(price), _f(market_cap), _f(shares)
    revenue     = _latest(income, "revenue")
    gross       = _latest(income, "gross_profit")
    op_income   = _latest(income, "operating_income")
    ebit        = _latest(income, "ebit") or op_income
    ebitda      = _latest(income, "ebitda")
    depreciation = _latest(cashflow, "depreciation_and_amortization")
    ebitda_derived = False
    if ebitda is None and op_income is not None and depreciation is not None:
        ebitda = op_income + abs(depreciation)
        ebitda_derived = True
    net_income  = _latest(income, "net_income")
    interest    = _latest(income, "interest_expense")
    _latest(income, "eps")

    total_assets = _latest(balance, "total_assets")
    equity       = _latest(balance, "total_equity")
    total_debt   = _latest(balance, "total_debt")
    net_debt     = _latest(balance, "net_debt")
    cash         = _latest(balance, "cash_and_equivalents")
    cur_assets   = _latest(balance, "current_assets")
    cur_liab     = _latest(balance, "current_liabilities")
    invested_cap = _latest(balance, "invested_capital")
    shares_out   = shares or _latest(balance, "shares_outstanding")

    ocf   = _latest(cashflow, "operating_cash_flow")
    capex = _latest(cashflow, "capex")
    fcf   = _latest(cashflow, "free_cash_flow")
    if fcf is None and ocf is not None and capex is not None:
        fcf = ocf + capex  # capex vem negativo
    div_paid  = _latest(cashflow, "dividends_paid")
    buyback   = _latest(cashflow, "stock_repurchase")
    issuance  = _latest(cashflow, "stock_issuance")
    sbc       = _latest(cashflow, "stock_based_compensation")

    # SBC é despesa real do acionista (paga em participação, não em caixa) que
    # o FCF GAAP devolve como se fosse ganho: sai do lucro e volta somada no
    # fluxo operacional. Sem esta linha, empresas que remuneram em ações
    # aparentam margem de caixa melhor do que a economia do negócio entrega.
    fcf_ex_sbc = None if fcf is None or sbc is None else fcf - abs(sbc)

    if market_cap is None and price is not None and shares_out is not None:
        market_cap = price * shares_out
    if net_debt is None and total_debt is not None and cash is not None:
        net_debt = total_debt - cash
    if invested_cap is None and equity is not None and total_debt is not None:
        invested_cap = equity + total_debt - (cash or 0.0)

    ev = None
    if market_cap is not None and total_debt is not None:
        ev = market_cap + total_debt - (cash or 0.0)

    nopat = None if ebit is None else ebit * (1 - _TAX_DEFAULT)

    m = {
        # Qualidade
        "gross_margin":     safe_div(gross, revenue),
        "operating_margin": safe_div(op_income, revenue),
        "net_margin":       safe_div(net_income, revenue),
        "fcf_margin":       safe_div(fcf, revenue),
        # Denominador precisa ser positivo: ver div_if_den_positive (A-101).
        "cash_conversion":  div_if_den_positive(fcf, net_income),
        "roe":              div_if_den_positive(net_income, equity),
        "roa":              safe_div(net_income, total_assets),
        "roic":             div_if_den_positive(nopat, invested_cap),
        # Crescimento
        "revenue_cagr_3y":  _growth(income, "revenue", 3),
        "revenue_cagr_5y":  _growth(income, "revenue", 5),
        "op_income_cagr_3y": _growth(income, "operating_income", 3),
        "eps_cagr_3y":      _growth(income, "eps", 3),
        "fcf_cagr_3y":      _growth(cashflow, "free_cash_flow", 3),
        # Solidez
        "net_debt_ebitda":  div_if_den_positive(net_debt, ebitda),
        "interest_coverage": safe_div(ebit, abs(interest)) if interest else None,
        "current_ratio":    safe_div(cur_assets, cur_liab),
        "debt_to_equity":   div_if_den_positive(total_debt, equity),
        # Valuation
        "pe":            safe_div(market_cap, net_income),
        "earnings_yield": safe_div(net_income, market_cap),
        "ev_ebit":       safe_div(ev, ebit),
        "ev_ebitda":     safe_div(ev, ebitda),
        "p_fcf":         safe_div(market_cap, fcf),
        "fcf_yield":     safe_div(fcf, market_cap),
        "p_s":           safe_div(market_cap, revenue),
        # Qualidade dos lucros: peso da remuneração em ações e caixa livre
        # depois de absorvê-la (menor SBC/receita é melhor).
        "sbc_to_revenue":   safe_div(abs(sbc) if sbc is not None else None, revenue),
        "fcf_ex_sbc_margin": safe_div(fcf_ex_sbc, revenue),
        # Retorno ao acionista (buyback/dividendo vêm negativos no CF → sinal +)
        "shareholder_yield": _shareholder_yield(div_paid, buyback, issuance, market_cap),
        # Payout: distribuir acima do lucro não se sustenta. Em REIT é normal
        # (distribui FFO, e a depreciação deprime o lucro contábil) — quem
        # consome a métrica precisa tratar esse caso, ver us_advanced_lab.
        "payout_ratio": (safe_div(abs(div_paid), net_income)
                         if div_paid is not None and net_income and net_income > 0
                         else None),
        # Diluição: recompra sem olhar a contagem de ações engana — a emissão
        # por SBC pode anular o buyback. Crescimento do share count: menor é
        # melhor (negativo = recompra líquida efetiva).
        "share_count_cagr_3y": _growth(balance, "shares_outstanding", 3),
        # Balanço/geração estruturalmente quebrados. Sem isto, as razões
        # anuladas por div_if_den_positive chegariam ao score como simples
        # ausência — e ausência é puxada para o neutro, o que premiaria a
        # empresa em pior situação. Ver us_score.score_cross_section (A-101).
        "impairment_flags": tuple(
            nome for nome, quebrado in (
                ("patrimonio_liquido_negativo", equity is not None and equity <= 0),
                ("ebitda_nao_positivo", ebitda is not None and ebitda <= 0),
                ("capital_investido_negativo",
                 invested_cap is not None and invested_cap <= 0),
            ) if quebrado
        ),
        # contexto (não entram no score, ajudam classificação/dossiê)
        "_revenue": revenue, "_net_income": net_income, "_fcf": fcf,
        "_equity": equity, "_net_debt": net_debt, "_market_cap": market_cap,
        "_ebit": ebit, "_ebitda": ebitda, "_ebitda_derived": ebitda_derived,
        "_years": len(_series_values(income, "revenue")),
    }
    return m


def _shareholder_yield(div_paid, buyback, issuance, market_cap) -> Optional[float]:
    if market_cap is None or market_cap == 0:
        return None
    parts = [abs(x) for x in (div_paid, buyback) if x is not None]
    if not parts:
        return None
    returned = sum(parts) - (abs(issuance) if issuance is not None else 0.0)
    return returned / market_cap


# métricas em que MENOR é melhor (para o ranqueamento no score)
LOWER_IS_BETTER = frozenset({
    "net_debt_ebitda", "debt_to_equity", "pe", "ev_ebit", "ev_ebitda", "p_fcf", "p_s",
    # SBC pesada corrói o acionista; share count crescente é diluição.
    "sbc_to_revenue", "share_count_cagr_3y",
})
