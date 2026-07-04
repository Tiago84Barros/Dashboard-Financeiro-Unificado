"""
data_pipeline/market/normalize.py
Normalizadores PUROS: convertem o payload da BRAPI (results[0]) nas linhas das
tabelas market.* — sem rede e sem banco (100% testável).

Defensivo por design: a forma exata dos módulos do plano Pro pode variar, então
cada campo é extraído tentando MÚLTIPLOS nomes candidatos e cai para None quando
ausente (nunca quebra, nunca inventa).
"""
from __future__ import annotations

from datetime import datetime, timezone


def _f(v):
    """float seguro (None em falha/NaN/inf)."""
    try:
        x = float(v)
        return x if x == x and x not in (float("inf"), float("-inf")) else None
    except (TypeError, ValueError):
        return None


def _first(d: dict, names: tuple[str, ...]):
    """Primeiro valor numérico presente entre os nomes candidatos."""
    if not isinstance(d, dict):
        return None
    for n in names:
        if n in d and d[n] is not None:
            x = _f(d[n])
            if x is not None:
                return x
    return None


def _as_date(v):
    """Converte unix/ISO/'YYYY-MM-DD' em date (ou None)."""
    if v is None:
        return None
    # unix timestamp
    try:
        if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
            return datetime.fromtimestamp(int(v), tz=timezone.utc).date()
    except (ValueError, OSError, OverflowError):
        pass
    s = str(v)
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:len("2026-08-20T03:00:00.000Z")], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except Exception:
        return None


def _ticker(quote: dict) -> str:
    return str((quote or {}).get("symbol") or "").strip().upper().replace(".SA", "")


def _period_year_quarter(end, annual: bool) -> tuple[int, int] | None:
    d = _as_date(end)
    if d is None:
        return None
    if annual:
        return d.year, 0
    q = (d.month - 1) // 3 + 1
    return d.year, q


# ── Cadastro ──────────────────────────────────────────────────────────────────

def company_row(quote: dict) -> dict | None:
    tk = _ticker(quote)
    if not tk:
        return None
    prof = (quote or {}).get("summaryProfile") or {}
    name = (quote.get("longName") or quote.get("shortName") or prof.get("longName") or tk)
    return {
        "codigo_cvm": None,  # preenchido depois via cvm_to_ticker (CVM)
        "name": str(name)[:300],
        "cnpj": None,
        "sector": prof.get("sector"),
        "subsector": prof.get("industry") or prof.get("industryDisp"),
        "segment": prof.get("industryKey") or prof.get("sectorKey"),
        "website": prof.get("website"),
        "description": prof.get("longBusinessSummary"),
        "logo_url": quote.get("logourl") or quote.get("logoUrl"),
    }


def _infer_asset_type(tk: str, quote: dict) -> str:
    """Classifica o tipo de ativo a partir do payload + sufixo do ticker.

    Fix auditoria 2026-07: antes era gravado 'stock' fixo para todo payload
    (inclusive units e BDRs), sobrescrevendo classificações corretas no
    upsert. Ordem: perfil do payload (FII/ETF) → sufixo numérico do ticker.
    """
    sector = str(((quote or {}).get("summaryProfile") or {}).get("sector") or "")
    if sector.strip().lower() == "fundos imobiliários":
        return "fii"
    name = str((quote or {}).get("longName") or (quote or {}).get("shortName") or "")
    digits = "".join(ch for ch in tk if ch.isdigit())
    if digits in ("31", "32", "33", "34", "35", "36", "39"):
        return "bdr"
    if digits == "11":
        low = name.lower()
        if "etf" in low or "índice" in low or "indice" in low or "ishares" in low or "it now" in low:
            return "etf"
        if "fii" in low or "fdo inv imob" in low or "imob" in low:
            return "fii"
        return "unit"
    return "stock"


def asset_row(quote: dict) -> dict | None:
    tk = _ticker(quote)
    if not tk:
        return None
    return {
        "ticker": tk,
        "asset_type": _infer_asset_type(tk, quote),
        "exchange": "B3",
        "currency": str(quote.get("currency") or "BRL"),
        # is_active continua sem fonte confiável no payload da brapi (ativo
        # delistado simplesmente some da API); mantido True por design —
        # a exclusão de deslistados é feita via core/survivorship.py.
        "is_active": True,
    }


# ── Preços ────────────────────────────────────────────────────────────────────

def price_rows(quote: dict) -> list[dict]:
    tk = _ticker(quote)
    out: list[dict] = []
    for it in (quote or {}).get("historicalDataPrice") or []:
        d = _as_date(it.get("date"))
        if d is None:
            continue
        out.append({
            "ticker": tk, "date": d,
            "open": _f(it.get("open")), "high": _f(it.get("high")),
            "low": _f(it.get("low")), "close": _f(it.get("close")),
            "adjusted_close": _f(it.get("adjustedClose")),
            "volume": int(_f(it.get("volume")) or 0) if it.get("volume") is not None else None,
        })
    return out


# ── Demonstrações ─────────────────────────────────────────────────────────────

def _statement_items(quote: dict, annual_key: str, quarterly_key: str) -> list[tuple[dict, bool]]:
    items: list[tuple[dict, bool]] = []
    for it in (quote.get(annual_key) or []):
        if isinstance(it, dict):
            items.append((it, True))
    for it in (quote.get(quarterly_key) or []):
        if isinstance(it, dict):
            items.append((it, False))
    return items


def income_rows(quote: dict) -> list[dict]:
    tk = _ticker(quote)
    out: list[dict] = []
    for it, annual in _statement_items(quote, "incomeStatementHistory", "incomeStatementHistoryQuarterly"):
        pyq = _period_year_quarter(it.get("endDate") or it.get("date"), annual)
        if not pyq:
            continue
        year, quarter = pyq
        out.append({
            "ticker": tk, "period": "annual" if annual else "quarterly",
            "year": year, "quarter": quarter,
            "revenue": _first(it, ("totalRevenue", "revenue", "operatingRevenue")),
            "gross_profit": _first(it, ("grossProfit",)),
            "ebit": _first(it, ("ebit", "operatingIncome")),
            "ebitda": _first(it, ("ebitda", "normalizedEBITDA")),
            "net_income": _first(it, ("netIncome", "netIncomeCommonStockholders")),
            "eps": _eps(it),
        })
    return out


def _eps(it: dict):
    """
    LPA do ano. A BRAPI B3 reporta basicEarningsPerCommonShare em milireais
    (LPA×1000: PETR4 8540 => 8,54); divide por 1000. Fallbacks p/ campos já em
    reais. 0/None => None (algumas empresas, ex. VALE, não reportam).
    """
    scaled = _first(it, ("basicEarningsPerCommonShare", "dilutedEarningsPerCommonShare"))
    if scaled:
        return scaled / 1000.0
    return _first(it, ("earningsPerShare", "basicEarningsPerShare", "dilutedEarningsPerShare")) or None


def balance_rows(quote: dict) -> list[dict]:
    tk = _ticker(quote)
    out: list[dict] = []
    for it, annual in _statement_items(quote, "balanceSheetHistory", "balanceSheetHistoryQuarterly"):
        pyq = _period_year_quarter(it.get("endDate") or it.get("date"), annual)
        if not pyq:
            continue
        year, quarter = pyq
        cash = _first(it, ("cash", "cashAndCashEquivalents",
                           "cashCashEquivalentsAndShortTermInvestments"))
        # dívida bruta: campos diretos quando existem; senão soma dos componentes
        # de empréstimos/debêntures/arrendamento (padrão CVM da brapi p/ B3).
        gross_debt = _first(it, ("totalDebt", "shortLongTermDebt", "shortLongTermDebtTotal"))
        if gross_debt is None:
            parts = [_first(it, (k,)) for k in (
                "loansAndFinancing", "longTermLoansAndFinancing",
                "debentures", "longTermDebentures",
                "leaseFinancing", "longTermLeaseFinancing")]
            parts = [p for p in parts if p is not None]
            gross_debt = sum(parts) if parts else None
        net_debt = _first(it, ("netDebt",))
        if net_debt is None and gross_debt is not None and cash is not None:
            net_debt = gross_debt - cash
        out.append({
            "ticker": tk, "period": "annual" if annual else "quarterly",
            "year": year, "quarter": quarter,
            "total_assets": _first(it, ("totalAssets",)),
            "total_liabilities": _first(it, ("totalLiab", "totalLiabilities",
                                             "totalLiabilitiesNetMinorityInterest")),
            "equity": _first(it, ("totalStockholderEquity", "shareholdersEquity",
                                  "stockholdersEquity", "controllerShareholdersEquity",
                                  "totalEquityGrossMinorityInterest")),
            "cash": cash, "gross_debt": gross_debt, "net_debt": net_debt,
            # ativo/passivo circulante p/ Liquidez_Corrente (PC: total* costuma vir
            # nulo na B3 → fallback p/ currentLiabilities).
            "current_assets": _first(it, ("totalCurrentAssets", "currentAssets")),
            "current_liabilities": _first(it, ("totalCurrentLiabilities", "currentLiabilities")),
        })
    return out


def cashflow_rows(quote: dict) -> list[dict]:
    tk = _ticker(quote)
    out: list[dict] = []
    for it, annual in _statement_items(quote, "cashflowHistory", "cashflowHistoryQuarterly"):
        pyq = _period_year_quarter(it.get("endDate") or it.get("date"), annual)
        if not pyq:
            continue
        year, quarter = pyq
        fco = _first(it, ("totalCashFromOperatingActivities", "operatingCashFlow"))
        capex = _first(it, ("capitalExpenditures", "capitalExpenditure"))
        fcf = _first(it, ("freeCashFlow",))
        if fcf is None and fco is not None and capex is not None:
            fcf = fco + capex  # capex já vem negativo no padrão Yahoo/brapi
        out.append({
            "ticker": tk, "period": "annual" if annual else "quarterly",
            "year": year, "quarter": quarter,
            "operating_cash_flow": fco,
            "investing_cash_flow": _first(it, ("totalCashflowsFromInvestingActivities", "investingCashFlow")),
            "financing_cash_flow": _first(it, ("totalCashFromFinancingActivities", "financingCashFlow")),
            "capex": capex, "free_cash_flow": fcf,
        })
    return out


# ── Dividendos ────────────────────────────────────────────────────────────────

def _sane_payment_date(pay, ex):
    """
    Anula payment_date claramente impossível para o event_date gerado
    (COALESCE(payment_date, ex_date)) não herdar lixo da brapi: ano além do
    próximo ano (placeholder 9999 ou futuros 2028 quando estamos em 2026).
    Datas de pagamento atrasadas no passado (JCP a pagar) são legítimas e ficam
    intactas; o ex_date assume o event_date quando o payment é anulado.
    """
    if pay is None:
        return None
    try:
        if pay.year > datetime.now(timezone.utc).year + 1:
            return None
    except Exception:
        return None
    return pay


def dividend_rows(quote: dict) -> list[dict]:
    tk = _ticker(quote)
    out: list[dict] = []
    items = ((quote or {}).get("dividendsData") or {}).get("cashDividends") or []
    for it in items:
        amount = _f(it.get("rate"))
        if amount is None or amount <= 0:      # 0/negativo não é provento útil
            continue
        ex = _as_date(it.get("lastDatePrior") or it.get("approvedOn"))
        out.append({
            "ticker": tk,
            "payment_date": _sane_payment_date(_as_date(it.get("paymentDate")), ex),
            "ex_date": ex,
            "amount": amount,
            "type": str(it.get("label") or "DIVIDENDO")[:40],
            "source": "brapi.dev",
        })
    return out


# ── Indicadores (calculated_metrics) ──────────────────────────────────────────

def metric_rows(quote: dict) -> list[dict]:
    """Indicadores 'spot' fornecidos/derivados pela brapi (P/L, P/VP, EV/EBITDA, marketCap)."""
    tk = _ticker(quote)
    if not tk:
        return []
    dks = (quote or {}).get("defaultKeyStatistics") or {}
    fin = (quote or {}).get("financialData") or {}
    cand = {
        "P/L": _first(quote, ("priceEarnings",)) or _first(dks, ("trailingPE", "forwardPE")),
        "P/VP": _first(dks, ("priceToBook",)),
        "EV/EBITDA": _first(dks, ("enterpriseToEbitda", "enterpriseValueEbitda")),
        "marketCap": _first(quote, ("marketCap",)),
        "LPA": _first(quote, ("earningsPerShare",)) or _first(dks, ("trailingEps",)),
        "ROE": _first(fin, ("returnOnEquity",)),
        "ROA": _first(fin, ("returnOnAssets",)),
        "Margem_Liquida": _first(fin, ("profitMargins",)),
        "Margem_Operacional": _first(fin, ("operatingMargins",)),
        # DY trailing da própria brapi (consenso) — evita over-count da janela
        # de dividendos somada (ex.: SAPR3 cluster de JCP em junho).
        "DY": _first(dks, ("dividendYield", "yield")),
    }
    rows: list[dict] = []
    for name, val in cand.items():
        if val is None:
            continue
        rows.append({
            "ticker": tk, "period": "spot", "year": 0, "quarter": 0,
            "metric_name": name, "metric_value": val,
            "calculation_method": "brapi_field",
            "source": "brapi.dev", "confidence_score": 80.0,
        })
    return rows


def normalize_all(quote: dict) -> dict[str, list[dict]]:
    """Converte um payload completo em todas as linhas market.* (dicionário por tabela)."""
    comp = company_row(quote)
    ast = asset_row(quote)
    return {
        "companies": [comp] if comp else [],
        "assets": [ast] if ast else [],
        "historical_prices": price_rows(quote),
        "income_statements": income_rows(quote),
        "balance_sheets": balance_rows(quote),
        "cash_flow_statements": cashflow_rows(quote),
        "dividends": dividend_rows(quote),
        "calculated_metrics": metric_rows(quote),
    }
