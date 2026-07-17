"""
data_pipeline/us/prices_yf.py
Preços/dividendos/splits via yfinance — implementa MarketDataProvider.

A SEC não publica cotação (só filings), então a fonte de preço é separada da de
fundamentos. O yfinance já é dependência do projeto (requirements.txt) e é usado
no lado B3, então não adiciona nada novo à stack.

Limitação honesta: yfinance raspa a API pública do Yahoo Finance — não tem SLA,
pode mudar sem aviso e não é fonte oficial. Para preço (dado amplamente
replicado) o risco é aceitável; para fundamento, não — por isso fundamento vem
da EDGAR.

O factory de Ticker é injetável (`ticker_factory`) para teste sem rede.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from data_pipeline.us.identity import normalize_symbol
from data_pipeline.us.providers import MarketDataProvider, ProviderError

logger = logging.getLogger("us_prices_yf")


class YFinanceProvider(MarketDataProvider):
    def __init__(self, ticker_factory: Optional[Callable[[str], Any]] = None):
        self._factory = ticker_factory
        self.calls_made = 0

    def _ticker(self, symbol: str):
        if self._factory is not None:
            return self._factory(symbol)
        try:
            import yfinance as yf
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("yfinance não instalado — veja requirements.txt") from exc
        return yf.Ticker(symbol)

    def _history(self, symbol: str, start=None, end=None):
        self.calls_made += 1
        t = self._ticker(normalize_symbol(symbol) or symbol)
        try:
            return t.history(start=start, end=end, period=None if start else "max",
                             auto_adjust=False, actions=True)
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"yfinance falhou em {symbol}: {exc}") from exc

    def get_prices_daily(self, symbol: str, start=None, end=None) -> list[dict]:
        df = self._history(symbol, start, end)
        if df is None or getattr(df, "empty", True):
            return []
        out = []
        for idx, row in df.iterrows():
            d = idx.date() if hasattr(idx, "date") else idx
            out.append({
                "date": str(d),
                "open": _f(row.get("Open")), "high": _f(row.get("High")),
                "low": _f(row.get("Low")), "close": _f(row.get("Close")),
                # 'Adj Close' já reflete splits e dividendos
                "adjClose": _f(row.get("Adj Close", row.get("Close"))),
                "volume": _i(row.get("Volume")),
            })
        return out

    def get_dividends(self, symbol: str) -> list[dict]:
        df = self._history(symbol)
        if df is None or getattr(df, "empty", True) or "Dividends" not in df:
            return []
        out = []
        for idx, val in df["Dividends"].items():
            v = _f(val)
            if v:                     # 0 = dia sem provento, não é dado ausente
                d = idx.date() if hasattr(idx, "date") else idx
                out.append({"date": str(d), "dividend": v})
        return out

    def get_splits(self, symbol: str) -> list[dict]:
        df = self._history(symbol)
        if df is None or getattr(df, "empty", True) or "Stock Splits" not in df:
            return []
        out = []
        for idx, val in df["Stock Splits"].items():
            v = _f(val)
            if v:                     # yfinance dá a razão (ex.: 4.0 para 4:1)
                d = idx.date() if hasattr(idx, "date") else idx
                out.append({"date": str(d), "numerator": v, "denominator": 1.0})
        return out


def _f(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _i(v) -> Optional[int]:
    f = _f(v)
    return None if f is None else int(f)
