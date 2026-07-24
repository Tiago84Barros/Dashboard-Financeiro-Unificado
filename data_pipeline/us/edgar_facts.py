"""
data_pipeline/us/edgar_facts.py
Tradução XBRL (SEC EDGAR companyfacts) → linhas do schema market_us.*.

Por que EDGAR: dados públicos, oficiais e de domínio público — sem licença
restritiva, sem cláusula de deleção e sem limite de exibição (a FMP proíbe cópia/
armazenamento e exige apagar tudo ao cancelar; ver docs/empresas_americanas.md).

Três diferenças importantes em relação à FMP, tratadas aqui:

  1. CONCEITOS: o mesmo número aparece sob tags diferentes conforme a empresa
     (ex.: receita como Revenues ou RevenueFromContractWithCustomer...). Resolvido
     por lista de aliases em ordem de prioridade.
  2. SINAIS: no XBRL, saídas de caixa são POSITIVAS (capex = pagamento). O resto
     do projeto segue a convenção capex negativo (fcf = cfo + capex). As tags de
     pagamento são negadas aqui, uma única vez, na fronteira.
  3. PIT: cada fato traz `filed` — a data em que o filing ficou público. É a
     melhor `available_at` possível (mais rigorosa que o acceptedDate da FMP).

Puro (sem rede/DB). Coberto por tests/test_us_edgar_facts.py.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from data_pipeline.us.normalize import content_hash, parse_date, to_float

_USD = "USD"
_SHARES = "shares"
_USD_PER_SHARE = "USD/shares"

# Faixa plausível de exercício fiscal. Alguns filers (ex.: PRTH, TNET) gravam
# o `fy` do XBRL como serial de data estilo Excel (43465 = 2018-12-31); a SEC
# repassa o valor cru no companyfacts.
_FY_MIN, _FY_MAX = 1990, 2100
_EXCEL_SERIAL_MIN, _EXCEL_SERIAL_MAX = 20000, 80000
_EXCEL_EPOCH = date(1899, 12, 30)

# Duração aceita como "anual" (10-K com exercícios de ~12 meses).
_ANNUAL_MIN_DAYS, _ANNUAL_MAX_DAYS = 350, 380
_QUARTERLY_MIN_DAYS, _QUARTERLY_MAX_DAYS = 70, 110
PARSER_VERSION = "companyfacts-parser-v4"

# ── Mapas de conceitos (ordem = prioridade) ───────────────────────────────────
INCOME_CONCEPTS: dict[str, list[str]] = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax",
                "Revenues", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax"],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"],
    "gross_profit": ["GrossProfit"],
    "rnd_expenses": ["ResearchAndDevelopmentExpense"],
    "sga_expenses": ["SellingGeneralAndAdministrativeExpense",
                     "GeneralAndAdministrativeExpense"],
    "operating_income": ["OperatingIncomeLoss"],
    "interest_expense": ["InterestExpense", "InterestExpenseDebt"],
    "income_tax": ["IncomeTaxExpenseBenefit"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
}
INCOME_SHARE_CONCEPTS = {
    "eps": (["EarningsPerShareBasic"], _USD_PER_SHARE),
    "eps_diluted": (["EarningsPerShareDiluted"], _USD_PER_SHARE),
    "weighted_shares": (["WeightedAverageNumberOfSharesOutstandingBasic",
                         "WeightedAverageNumberOfSharesOutstanding"], _SHARES),
    "weighted_shares_diluted": (["WeightedAverageNumberOfDilutedSharesOutstanding"], _SHARES),
}

BALANCE_CONCEPTS: dict[str, list[str]] = {
    "cash_and_equivalents": ["CashAndCashEquivalentsAtCarryingValue",
                             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "short_term_investments": ["ShortTermInvestments", "MarketableSecuritiesCurrent"],
    "current_assets": ["AssetsCurrent"],
    "total_assets": ["Assets"],
    "goodwill": ["Goodwill"],
    "intangibles": ["IntangibleAssetsNetExcludingGoodwill", "FiniteLivedIntangibleAssetsNet"],
    "short_term_debt": ["LongTermDebtCurrent", "ShortTermBorrowings", "DebtCurrent"],
    "long_term_debt": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "total_liabilities": ["Liabilities"],
    "total_equity": ["StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
                     "StockholdersEquity"],
    "retained_earnings": ["RetainedEarningsAccumulatedDeficit"],
}
BALANCE_SHARE_CONCEPTS = {
    "shares_outstanding": (["CommonStockSharesOutstanding", "CommonStockSharesIssued"], _SHARES),
}

CASHFLOW_CONCEPTS: dict[str, list[str]] = {
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities",
                            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    "acquisitions": ["PaymentsToAcquireBusinessesNetOfCashAcquired"],
    "investments": ["PaymentsToAcquireInvestments"],
    "stock_issuance": ["ProceedsFromIssuanceOfCommonStock"],
    "stock_repurchase": ["PaymentsForRepurchaseOfCommonStock"],
    "debt_issuance": ["ProceedsFromIssuanceOfLongTermDebt", "ProceedsFromNotesPayable"],
    "debt_repayment": ["RepaymentsOfLongTermDebt", "RepaymentsOfDebt"],
    "dividends_paid": ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"],
    "stock_based_compensation": ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"],
    "depreciation_and_amortization": [
        "DepreciationDepletionAndAmortization",
        "DepreciationDepletionAndAmortizationPropertyPlantAndEquipmentAndIntangibleAssets",
        "DepreciationDepletionAndAmortizationPropertyPlantAndEquipment",
        "Depreciation",
    ],
}

# Campos cujo XBRL é "pagamento" (positivo) e que o projeto guarda NEGATIVO,
# para casar com a convenção usada no resto do código (fcf = cfo + capex).
NEGATE_FIELDS = frozenset({"capex", "acquisitions", "investments", "stock_repurchase",
                           "debt_repayment", "dividends_paid"})


def _is_annual_duration(start: Optional[str], end: str) -> bool:
    """True para fatos instantâneos (sem start) ou durações de ~12 meses."""
    if not start:
        return True
    d0, d1 = parse_date(start), parse_date(end)
    if d0 is None or d1 is None:
        return False
    return _ANNUAL_MIN_DAYS <= (d1 - d0).days <= _ANNUAL_MAX_DAYS


def _is_quarterly_duration(start: Optional[str], end: str) -> bool:
    """Aceita fatos instantâneos ou duração de aproximadamente três meses."""
    if not start:
        return True
    d0, d1 = parse_date(start), parse_date(end)
    if d0 is None or d1 is None:
        return False
    return _QUARTERLY_MIN_DAYS <= (d1 - d0).days <= _QUARTERLY_MAX_DAYS


def _valid_filing_timing(end: Any, filed: Any) -> bool:
    """Rejeita fatos cujo período termina depois da própria divulgação."""
    period_end, filing_date = parse_date(end), parse_date(filed)
    return bool(period_end and filing_date and filing_date >= period_end)


def _entries(cf: dict, tag: str, unit: str) -> list[dict]:
    facts = (cf or {}).get("facts", {})
    for taxonomy in ("us-gaap", "ifrs-full", "dei"):
        node = facts.get(taxonomy, {}).get(tag)
        if node:
            return node.get("units", {}).get(unit, []) or []
    return []


def _annual_points(entries: list[dict]) -> dict[str, dict]:
    """{period_end: {val, filed}} de 10-K, escolhendo o filing MAIS ANTIGO.

    O 10-K de 2023 traz 2022 como comparativo; o original de 2022 foi arquivado
    antes. Pegar o `filed` mais antigo por período dá a data em que o número
    ficou conhecível pela primeira vez — que é o que o PIT exige.
    """
    by_end: dict[str, dict] = {}
    for e in entries:
        if not str(e.get("form") or "").startswith("10-K"):
            continue
        end, filed = e.get("end"), e.get("filed")
        if not end or not filed or not _valid_filing_timing(end, filed):
            continue
        if not _is_annual_duration(e.get("start"), end):
            continue
        cur = by_end.get(end)
        if cur is None or str(filed) < str(cur["filed"]):
            by_end[end] = {"val": e.get("val"), "filed": filed}
    return by_end


def _sane_fiscal_year(fy: Any, end: Any) -> int:
    """Ano fiscal validado; serial-Excel é convertido; resto cai no ano do `end`."""
    from datetime import timedelta
    try:
        year = int(fy)
    except (TypeError, ValueError):
        year = 0
    if _FY_MIN <= year <= _FY_MAX:
        return year
    if _EXCEL_SERIAL_MIN <= year <= _EXCEL_SERIAL_MAX:
        return (_EXCEL_EPOCH + timedelta(days=year)).year
    d = parse_date(end)
    return d.year if d else 0


def _quarterly_points(entries: list[dict], *, instant: bool = False) -> dict[tuple[int, int], dict]:
    """Fatos 10-Q trimestrais por (fiscal_year, fiscal_quarter), sempre PIT.

    Fluxos acumulados de seis/nove meses são rejeitados pela duração. Isso reduz
    cobertura, mas evita tratar YTD como trimestre isolado.
    """
    out: dict[tuple[int, int], dict] = {}
    for e in entries:
        if not str(e.get("form") or "").startswith("10-Q"):
            continue
        fp = str(e.get("fp") or "").upper()
        if fp not in {"Q1", "Q2", "Q3", "Q4"}:
            continue
        end, filed = e.get("end"), e.get("filed")
        if not end or not filed or not _valid_filing_timing(end, filed):
            continue
        # Balanços são fatos instantâneos e normalmente não têm `start`.
        # Fluxos/resultados precisam ter duração de um trimestre isolado.
        if not instant and not _is_quarterly_duration(e.get("start"), end):
            continue
        fy = _sane_fiscal_year(e.get("fy"), end)
        if not fy:
            continue
        key = (fy, int(fp[1]))
        cur = out.get(key)
        # Um 10-Q também carrega comparativos de períodos anteriores com o
        # mesmo FY/FP do filing. Primeiro escolhemos o maior `end` (período
        # corrente); entre revisões desse mesmo período, preservamos o filing
        # mais antigo para manter o point-in-time.
        if (cur is None or str(end) > str(cur["end"])
                or (str(end) == str(cur["end"]) and str(filed) < str(cur["filed"]))):
            out[key] = {"val": e.get("val"), "filed": filed, "end": end}
    return out


def _collect(cf: dict, concepts: dict[str, list[str]], unit: str = _USD) -> dict[str, dict]:
    """{field: {end: {val, filed}}} — primeiro alias que tiver dado para o período."""
    out: dict[str, dict] = {}
    for field, tags in concepts.items():
        merged: dict[str, dict] = {}
        for tag in tags:
            for end, point in _annual_points(_entries(cf, tag, unit)).items():
                merged.setdefault(end, point)   # alias anterior tem prioridade
        out[field] = merged
    return out


def _collect_units(cf: dict, concepts: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for field, (tags, unit) in concepts.items():
        merged: dict[str, dict] = {}
        for tag in tags:
            for end, point in _annual_points(_entries(cf, tag, unit)).items():
                merged.setdefault(end, point)
        out[field] = merged
    return out


def _collect_quarterly(cf: dict, concepts: dict[str, list[str]], unit: str = _USD,
                       *, instant: bool = False) -> dict:
    out: dict[str, dict] = {}
    for field, tags in concepts.items():
        merged: dict[tuple[int, int], dict] = {}
        for tag in tags:
            for key, point in _quarterly_points(
                    _entries(cf, tag, unit), instant=instant).items():
                merged.setdefault(key, point)
        out[field] = merged
    return out


def _collect_quarterly_units(cf: dict, concepts: dict, *, instant: bool = False) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for field, (tags, unit) in concepts.items():
        merged: dict[tuple[int, int], dict] = {}
        for tag in tags:
            for key, point in _quarterly_points(
                    _entries(cf, tag, unit), instant=instant).items():
                merged.setdefault(key, point)
        out[field] = merged
    return out


def _build_rows(collected: dict[str, dict], symbol: str | None = None) -> list[dict]:
    """Monta uma linha por período, com available_at conservador (filing mais tardio)."""
    ends: set[str] = set()
    for per_field in collected.values():
        ends.update(per_field.keys())

    rows = []
    for end in sorted(ends):
        d = parse_date(end)
        if d is None:
            continue
        row: dict[str, Any] = {}
        filings: list[str] = []
        for field, per_field in collected.items():
            point = per_field.get(end)
            if point is None:
                row[field] = None            # ausente ≠ zero
                continue
            val = to_float(point["val"])
            if val is not None and field in NEGATE_FIELDS:
                val = -val                    # pagamento (XBRL +) → saída (projeto −)
            row[field] = val
            filings.append(str(point["filed"]))
        if not filings:
            continue
        # conservador: só é conhecível quando o ÚLTIMO insumo da linha foi arquivado
        available = max(filings)
        row.update({
            "period": "annual", "fiscal_year": d.year, "fiscal_quarter": 0,
            "reference_date": d, "published_date": parse_date(available),
            "available_at": parse_date(available),
            "currency": "USD", "unit": "absolute", "source": "sec_edgar",
            "source_version": PARSER_VERSION, "quality_status": "raw",
        })
        if symbol:
            row["symbol"] = symbol
        row["content_hash"] = content_hash(row)
        rows.append(row)
    return rows


def _build_quarterly_rows(collected: dict[str, dict], symbol: str | None = None) -> list[dict]:
    periods: set[tuple[int, int]] = set()
    for per_field in collected.values():
        periods.update(per_field.keys())
    rows = []
    for fy, fq in sorted(periods):
        row: dict[str, Any] = {}
        filings: list[str] = []
        ends: list[str] = []
        for field, per_field in collected.items():
            point = per_field.get((fy, fq))
            if point is None:
                row[field] = None
                continue
            val = to_float(point["val"])
            if val is not None and field in NEGATE_FIELDS:
                val = -val
            row[field] = val
            filings.append(str(point["filed"]))
            ends.append(str(point["end"]))
        if not filings or not ends:
            continue
        available = max(filings)
        reference = max(ends)
        row.update({
            "period": "quarterly", "fiscal_year": fy, "fiscal_quarter": fq,
            "reference_date": parse_date(reference),
            "published_date": parse_date(available), "available_at": parse_date(available),
            "currency": "USD", "unit": "absolute", "source": "sec_edgar",
            "source_version": PARSER_VERSION, "quality_status": "raw",
        })
        if symbol:
            row["symbol"] = symbol
        row["content_hash"] = content_hash(row)
        rows.append(row)
    return rows


# ── API pública ───────────────────────────────────────────────────────────────
def build_income_rows(cf: dict, symbol: str | None = None) -> list[dict]:
    collected = _collect(cf, INCOME_CONCEPTS, _USD)
    collected.update(_collect_units(cf, INCOME_SHARE_CONCEPTS))
    rows = _build_rows(collected, symbol)
    for r in rows:
        # EBIT não é tag XBRL: usa o resultado operacional (mesma definição do projeto)
        r["ebit"] = r.get("operating_income")
        r["ebitda"] = None        # exigiria D&A do fluxo; ausente > inventado
    return rows


def build_balance_rows(cf: dict, symbol: str | None = None) -> list[dict]:
    collected = _collect(cf, BALANCE_CONCEPTS, _USD)
    collected.update(_collect_units(cf, BALANCE_SHARE_CONCEPTS))
    rows = _build_rows(collected, symbol)
    for r in rows:
        std, ltd = r.get("short_term_debt"), r.get("long_term_debt")
        r["total_debt"] = None if std is None and ltd is None else (std or 0) + (ltd or 0)
        cash = r.get("cash_and_equivalents")
        r["net_debt"] = None if r["total_debt"] is None or cash is None \
            else r["total_debt"] - cash
        eq = r.get("total_equity")
        r["invested_capital"] = None if eq is None or r["total_debt"] is None \
            else eq + r["total_debt"] - (cash or 0.0)
    return rows


def build_cashflow_rows(cf: dict, symbol: str | None = None) -> list[dict]:
    rows = _build_rows(_collect(cf, CASHFLOW_CONCEPTS, _USD), symbol)
    for r in rows:
        ocf, capex = r.get("operating_cash_flow"), r.get("capex")
        # FCF não existe no XBRL: derivado (capex já está negativo aqui)
        r["free_cash_flow"] = None if ocf is None or capex is None else ocf + capex
    return rows


def build_income_quarterly_rows(cf: dict, symbol: str | None = None) -> list[dict]:
    collected = _collect_quarterly(cf, INCOME_CONCEPTS, _USD)
    collected.update(_collect_quarterly_units(cf, INCOME_SHARE_CONCEPTS))
    rows = _build_quarterly_rows(collected, symbol)
    for r in rows:
        r["ebit"] = r.get("operating_income")
        r["ebitda"] = None
    return rows


def build_balance_quarterly_rows(cf: dict, symbol: str | None = None) -> list[dict]:
    collected = _collect_quarterly(cf, BALANCE_CONCEPTS, _USD, instant=True)
    collected.update(_collect_quarterly_units(
        cf, BALANCE_SHARE_CONCEPTS, instant=True))
    rows = _build_quarterly_rows(collected, symbol)
    for r in rows:
        std, ltd = r.get("short_term_debt"), r.get("long_term_debt")
        r["total_debt"] = None if std is None and ltd is None else (std or 0) + (ltd or 0)
        cash = r.get("cash_and_equivalents")
        r["net_debt"] = None if r["total_debt"] is None or cash is None \
            else r["total_debt"] - cash
        eq = r.get("total_equity")
        r["invested_capital"] = None if eq is None or r["total_debt"] is None \
            else eq + r["total_debt"] - (cash or 0.0)
    return rows


def build_cashflow_quarterly_rows(cf: dict, symbol: str | None = None) -> list[dict]:
    rows = _build_quarterly_rows(_collect_quarterly(cf, CASHFLOW_CONCEPTS, _USD), symbol)
    for r in rows:
        ocf, capex = r.get("operating_cash_flow"), r.get("capex")
        r["free_cash_flow"] = None if ocf is None or capex is None else ocf + capex
    return rows


def cik_from_facts(cf: dict) -> Optional[str]:
    cik = (cf or {}).get("cik")
    return None if cik is None else str(cik).zfill(10)
