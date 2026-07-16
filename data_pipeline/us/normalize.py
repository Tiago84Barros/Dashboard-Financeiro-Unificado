"""
data_pipeline/us/normalize.py
Camada explícita de normalização dos dados FMP → schema market_us.*.

Regras invioláveis (do enunciado):
  - Ausência NUNCA vira zero: campo faltante → None (rank neutro depois).
  - Zero só é preservado se vier explícito da fonte.
  - Nunca misturar anual/trimestral/TTM sem rótulo: period + fiscal_quarter.
  - Temporalidade financeira REAL: reference_date (fim do período) e available_at
    (data em que o dado poderia ser conhecido = acceptedDate/filingDate).
  - Unidade explícita por registro (FMP entrega valores absolutos em USD).

Tudo aqui é puro (sem rede/DB) e coberto por tests/test_us_normalize.py.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any, Optional


# ── Coerção segura ────────────────────────────────────────────────────────────
def to_float(value: Any) -> Optional[float]:
    """Converte para float preservando a distinção ausente(None) vs zero(0.0).

    '', None, 'None', 'NaN', '-' → None. 0 e '0' → 0.0.
    """
    if value is None:
        return None
    if isinstance(value, bool):  # bool é subtipo de int; não é número financeiro
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        return None if f != f else f  # NaN → None
    s = str(value).strip().replace(",", "")
    if s == "" or s.lower() in {"none", "nan", "null", "-", "n/a"}:
        return None
    try:
        f = float(s)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def to_int(value: Any) -> Optional[int]:
    f = to_float(value)
    return None if f is None else int(f)


# ── Unidades / percentuais ────────────────────────────────────────────────────
_UNIT_FACTOR = {"absolute": 1, "thousands": 1_000, "millions": 1_000_000}


def scale_to_absolute(value: Any, unit: str = "absolute") -> Optional[float]:
    """Converte um valor monetário para a unidade absoluta (USD inteiro)."""
    f = to_float(value)
    if f is None:
        return None
    factor = _UNIT_FACTOR.get((unit or "absolute").lower())
    if factor is None:
        raise ValueError(f"unidade desconhecida: {unit!r}")
    return f * factor


def normalize_percent(value: Any, assume: str = "auto") -> Optional[float]:
    """Normaliza percentual para RATIO (0–1).

    assume='ratio' → já está 0–1; 'pct' → dividir por 100; 'auto' → heurística
    (|v|>1.5 assume 0–100). Ausência preserva None.
    """
    f = to_float(value)
    if f is None:
        return None
    mode = (assume or "auto").lower()
    if mode == "ratio":
        return f
    if mode == "pct":
        return f / 100.0
    # auto: valores como 0.23 já são ratio; 23 é 23% → 0.23
    return f / 100.0 if abs(f) > 1.5 else f


# ── Datas / temporalidade ─────────────────────────────────────────────────────
def parse_date(value: Any) -> Optional[date]:
    """Aceita 'YYYY-MM-DD', 'YYYY-MM-DD HH:MM:SS' ou date/datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "")).date()
    except ValueError:
        pass
    # fallback: só a parte YYYY-MM-DD do começo da string
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def available_date(row: dict) -> Optional[date]:
    """Data PIT (knowledge date): quando o filing ficou público.

    Preferência: acceptedDate > fillingDate/filingDate > date (fim do período).
    Isso previne look-ahead — backtests filtram por este campo.
    """
    for key in ("acceptedDate", "fillingDate", "filingDate"):
        d = parse_date(row.get(key))
        if d is not None:
            return d
    return parse_date(row.get("date"))


def parse_period(row: dict) -> tuple[str, int, int]:
    """Retorna (period, fiscal_year, fiscal_quarter).

    period ∈ {'annual','quarterly'}; fiscal_quarter 0 (anual) ou 1..4.
    FMP: period 'FY'/'annual' → anual; 'Q1'..'Q4' → trimestral.
    """
    raw = str(row.get("period") or "").strip().upper()
    year = to_int(row.get("calendarYear"))
    if year is None:
        d = parse_date(row.get("date"))
        year = d.year if d else 0
    if raw in {"Q1", "Q2", "Q3", "Q4"}:
        return "quarterly", int(year), int(raw[1])
    return "annual", int(year), 0


def content_hash(payload: dict) -> str:
    """Hash estável do conteúdo (detecta restatement sem depender de datas)."""
    blob = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ── Mapeadores FMP → colunas market_us.* ──────────────────────────────────────
def _pit_common(row: dict) -> dict:
    period, fy, fq = parse_period(row)
    return {
        "period": period,
        "fiscal_year": fy,
        "fiscal_quarter": fq,
        "reference_date": parse_date(row.get("date")),
        "published_date": parse_date(row.get("fillingDate") or row.get("filingDate")),
        "available_at": available_date(row),
        "currency": (row.get("reportedCurrency") or "USD") or "USD",
        "unit": "absolute",
        "source": "fmp",
    }


def map_income_statement(row: dict) -> dict:
    out = _pit_common(row)
    out.update({
        "revenue":          to_float(row.get("revenue")),
        "cost_of_revenue":  to_float(row.get("costOfRevenue")),
        "gross_profit":     to_float(row.get("grossProfit")),
        "rnd_expenses":     to_float(row.get("researchAndDevelopmentExpenses")),
        "sga_expenses":     to_float(row.get("sellingGeneralAndAdministrativeExpenses")),
        "operating_income": to_float(row.get("operatingIncome")),
        "ebitda":           to_float(row.get("ebitda")),
        "ebit": to_float(row.get("operatingIncome")) if row.get("ebit") is None
                else to_float(row.get("ebit")),
        "interest_expense": to_float(row.get("interestExpense")),
        "income_tax":       to_float(row.get("incomeTaxExpense")),
        "net_income":       to_float(row.get("netIncome")),
        "eps":              to_float(row.get("eps")),
        "eps_diluted":      to_float(row.get("epsdiluted") or row.get("epsDiluted")),
        "weighted_shares":  to_float(row.get("weightedAverageShsOut")),
        "weighted_shares_diluted": to_float(row.get("weightedAverageShsOutDil")),
    })
    out["content_hash"] = content_hash(out)
    return out


def map_balance_sheet(row: dict) -> dict:
    out = _pit_common(row)
    out.update({
        "cash_and_equivalents":   to_float(row.get("cashAndCashEquivalents")),
        "short_term_investments": to_float(row.get("shortTermInvestments")),
        "current_assets":         to_float(row.get("totalCurrentAssets")),
        "total_assets":           to_float(row.get("totalAssets")),
        "goodwill":               to_float(row.get("goodwill")),
        "intangibles":            to_float(row.get("intangibleAssets")),
        "short_term_debt":        to_float(row.get("shortTermDebt")),
        "long_term_debt":         to_float(row.get("longTermDebt")),
        "total_debt":             to_float(row.get("totalDebt")),
        "net_debt":               to_float(row.get("netDebt")),
        "current_liabilities":    to_float(row.get("totalCurrentLiabilities")),
        "total_liabilities":      to_float(row.get("totalLiabilities")),
        "total_equity":           to_float(row.get("totalStockholdersEquity")),
        "shares_outstanding":     to_float(row.get("weightedAverageShsOut")
                                           or row.get("commonStock")),
        "invested_capital":       to_float(row.get("investedCapital")),
    })
    out["content_hash"] = content_hash(out)
    return out


def map_cash_flow(row: dict) -> dict:
    out = _pit_common(row)
    out.update({
        "operating_cash_flow": to_float(row.get("operatingCashFlow")
                                        or row.get("netCashProvidedByOperatingActivities")),
        "capex":               to_float(row.get("capitalExpenditure")),
        "free_cash_flow":      to_float(row.get("freeCashFlow")),
        "acquisitions":        to_float(row.get("acquisitionsNet")),
        "investments":         to_float(row.get("purchasesOfInvestments")),
        "stock_issuance":      to_float(row.get("commonStockIssued")),
        "stock_repurchase":    to_float(row.get("commonStockRepurchased")),
        "debt_issuance":       to_float(row.get("debtIssuance") or row.get("netDebtIssuance")),
        "debt_repayment":      to_float(row.get("debtRepayment")),
        "dividends_paid":      to_float(row.get("dividendsPaid")),
        "stock_based_compensation": to_float(row.get("stockBasedCompensation")),
    })
    out["content_hash"] = content_hash(out)
    return out


# ── Perfil / classificação de tipo de ativo ───────────────────────────────────
_REIT_HINT = ("reit", "real estate investment trust")


def classify_security_type(profile: dict) -> str:
    """Classifica o tipo do ativo (regra: REITs/ADRs/ETFs têm tratamento próprio)."""
    if profile.get("isEtf") or profile.get("isFund"):
        return "etf" if profile.get("isEtf") else "fund"
    if profile.get("isAdr"):
        return "adr"
    industry = str(profile.get("industry") or "").lower()
    sector = str(profile.get("sector") or "").lower()
    if any(h in industry or h in sector for h in _REIT_HINT):
        return "reit"
    return "common"


def map_profile(row: dict) -> dict:
    sec_type = classify_security_type(row)
    return {
        "cik":       (str(row.get("cik")).strip() or None) if row.get("cik") else None,
        "isin":      row.get("isin") or None,
        "cusip":     row.get("cusip") or None,
        "name":      row.get("companyName") or row.get("name") or row.get("symbol"),
        "symbol":    (row.get("symbol") or "").upper() or None,
        "exchange":  (row.get("exchangeShortName") or row.get("exchange") or "").upper() or None,
        "sector":    row.get("sector") or None,
        "industry":  row.get("industry") or None,
        "country":   row.get("country") or None,
        "currency":  row.get("currency") or "USD",
        "description": row.get("description") or None,
        "website":   row.get("website") or None,
        "ceo":       row.get("ceo") or None,
        "employees": to_int(row.get("fullTimeEmployees")),
        "ipo_date":  parse_date(row.get("ipoDate")),
        "security_type": sec_type,
        "is_reit":   sec_type == "reit",
        "is_adr":    bool(row.get("isAdr")),
        "is_active": not bool(row.get("isActivelyTrading") is False),
    }
