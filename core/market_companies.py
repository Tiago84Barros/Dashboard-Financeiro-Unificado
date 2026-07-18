"""Contrato comum para vitrines de empresas B3 e Estados Unidos.

Mantém a normalização e os filtros fora da interface. Cada linha normalizada
representa um ticker negociável; classes diferentes nunca são consolidadas.
"""
from __future__ import annotations

import re
import unicodedata

import pandas as pd


US_SECTOR_LABELS = {
    "Basic Materials": "Materiais Básicos",
    "Communication Services": "Comunicações",
    "Consumer Cyclical": "Consumo Cíclico",
    "Consumer Defensive": "Consumo Defensivo",
    "Energy": "Energia",
    "Financial Services": "Serviços Financeiros",
    "Healthcare": "Saúde",
    "Industrials": "Indústria",
    "Real Estate": "Imobiliário",
    "Technology": "Tecnologia",
    "Utilities": "Serviços Públicos",
}

_US_EXCHANGE_TOKENS = ("NASDAQ", "NYSE", "AMEX", "NEW YORK STOCK EXCHANGE")
_NON_EQUITY_TOKENS = (
    "ETF", "FUND", "INDEX", "OPTION", "WARRANT", "RIGHT", "UNIT",
    "NOTE", "BOND", "PREFERRED ETF", "SPAC WARRANT",
)


def _text(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _fold(value) -> str:
    normalized = unicodedata.normalize("NFKD", _text(value).casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def us_logo_url(ticker: str) -> str:
    """URL pública; a interface sempre mantém um placeholder por baixo."""
    safe = re.sub(r"[^A-Z0-9-]", "-", _text(ticker).upper().replace(".", "-"))
    return f"https://companiesmarketcap.com/img/company-logos/64/{safe}.png"


def is_valid_us_equity(row: pd.Series | dict) -> bool:
    symbol = _text(row.get("symbol")).upper()
    name = _text(row.get("name")).upper()
    exchange = _text(row.get("exchange")).upper()
    security_type = _text(row.get("security_type")).upper()
    active = row.get("is_active", True)
    if not symbol or len(symbol) > 12 or not re.fullmatch(r"[A-Z0-9.-]+", symbol):
        return False
    if active is False or (isinstance(active, str) and active.lower() in {"false", "0", "no"}):
        return False
    if exchange and not any(token in exchange for token in _US_EXCHANGE_TOKENS):
        return False
    haystack = f"{security_type} {name}"
    if any(re.search(rf"\b{re.escape(token)}\b", haystack) for token in _NON_EQUITY_TOKENS):
        return False
    return True


def normalize_us_companies(df: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "ticker", "company_name", "sector", "sector_raw", "industry",
        "card_tag", "logo_url", "exchange", "currency", "country",
        "market_cap", "asset_type",
    ]
    if df is None or df.empty:
        return pd.DataFrame(columns=columns)
    source = df.copy()
    source = source[source.apply(is_valid_us_equity, axis=1)].copy()
    if source.empty:
        return pd.DataFrame(columns=columns)
    out = pd.DataFrame(index=source.index)
    out["ticker"] = source["symbol"].map(_text).str.upper()
    out["company_name"] = source.get("name", source["symbol"]).map(_text)
    out["sector_raw"] = source.get("sector", pd.Series("", index=source.index)).map(_text)
    out["sector"] = out["sector_raw"].map(lambda x: US_SECTOR_LABELS.get(x, x))
    out["industry"] = source.get("industry", pd.Series("", index=source.index)).map(_text)
    out["card_tag"] = out.apply(
        lambda row: " · ".join(p for p in (row["sector"], row["industry"]) if p) or "—",
        axis=1,
    )
    if "logo_url" in source:
        out["logo_url"] = source["logo_url"].map(_text)
        out.loc[out["logo_url"] == "", "logo_url"] = out.loc[
            out["logo_url"] == "", "ticker"].map(us_logo_url)
    else:
        out["logo_url"] = out["ticker"].map(us_logo_url)
    out["exchange"] = source.get("exchange", pd.Series("", index=source.index)).map(_text)
    out["currency"] = "USD"
    out["country"] = "United States"
    out["market_cap"] = pd.to_numeric(
        source.get("_market_cap", source.get("market_cap", pd.Series(float("nan"), index=source.index))),
        errors="coerce",
    )
    out["asset_type"] = source.get(
        "security_type", pd.Series("stock", index=source.index)).map(_text)
    return out.drop_duplicates(subset=["ticker"], keep="first").reset_index(drop=True)


def normalize_b3_companies(df: pd.DataFrame, logo_builder) -> pd.DataFrame:
    columns = [
        "ticker", "company_name", "sector", "sector_raw", "industry",
        "card_tag", "logo_url", "exchange", "currency", "country",
        "market_cap", "asset_type",
    ]
    if df is None or df.empty:
        return pd.DataFrame(columns=columns)
    source = df.copy()
    out = pd.DataFrame(index=source.index)
    out["ticker"] = source["ticker"].map(_text).str.upper().str.replace(".SA", "", regex=False)
    out["company_name"] = source.get("nome_empresa", source["ticker"]).map(_text)
    out["sector_raw"] = source.get("SETOR", pd.Series("", index=source.index)).map(_text)
    out["sector"] = out["sector_raw"]
    subsetor = source.get("SUBSETOR", pd.Series("", index=source.index)).map(_text)
    segmento = source.get("SEGMENTO", pd.Series("", index=source.index)).map(_text)
    out["industry"] = segmento.where(segmento != "", subsetor)
    out["card_tag"] = [
        " · ".join(p for p in (sub, seg) if p) or "—"
        for sub, seg in zip(subsetor, segmento)
    ]
    out["logo_url"] = out["ticker"].map(logo_builder)
    out["exchange"] = "B3"
    out["currency"] = "BRL"
    out["country"] = "Brazil"
    out["market_cap"] = float("nan")
    out["asset_type"] = "stock"
    return out.drop_duplicates(subset=["ticker"], keep="first").reset_index(drop=True)


def filter_market_companies(df: pd.DataFrame, query: str) -> pd.DataFrame:
    """Busca case/accent-insensitive por ticker, nome, setor ou indústria."""
    if df is None or df.empty or not _text(query):
        return df.copy() if df is not None else pd.DataFrame()
    q = _fold(query)
    mask = pd.Series(False, index=df.index)
    for col in ("ticker", "company_name", "sector", "sector_raw", "industry"):
        if col in df:
            mask |= df[col].fillna("").map(_fold).str.contains(q, regex=False)
    return df[mask].reset_index(drop=True)
