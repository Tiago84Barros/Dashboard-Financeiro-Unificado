"""
Backfill controlled gaps in B3 fundamentals tables.

Targets:
  - public."Demonstracoes_Financeiras"
  - public.multiplos

Sources:
  - yfinance annual statements and dividends for historical rows
  - Fundamentus/Status Invest reconciliation for latest multiples only

Safety:
  - dry-run by default
  - updates only NULL/empty/invalid values
  - inserts missing annual rows only when at least one useful metric exists
  - writes an audit CSV with all proposed/applied changes
"""
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf
from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.b3_db import _resolve_url  # noqa: E402
from core.data_reconciliacao import get_multiplos_reconciliados  # noqa: E402


DEMO_TABLE = '"Demonstracoes_Financeiras"'
MULT_TABLE = "multiplos"

DEMO_COLS = {
    "Receita_Liquida",
    "EBIT",
    "Lucro_Liquido",
    "Patrimonio_Liquido",
    "Divida_Liquida",
    "Divida_Total",
    "Ativo_Total",
    "Dividendos",
    "FCO",
    "FCI",
    "FCF",
    "Fluxo_Caixa_Operacional",
    "Fluxo_Caixa_Investimento",
    "Fluxo_Caixa_Livre",
}

MULT_COLS = {
    "P/L",
    "P/VP",
    "DY",
    "ROE",
    "ROA",
    "ROIC",
    "Margem_Liquida",
    "Margem_Operacional",
    "Endividamento_Total",
    "Liquidez_Corrente",
    "EV_EBIT",
    "P_FCO",
    "Payout",
}

PCT_FIELDS = {
    "DY",
    "ROE",
    "ROA",
    "ROIC",
    "Margem_Liquida",
    "Margem_Operacional",
    "Payout",
}

VALID_RANGES = {
    "DY": (0.0, 0.50),
    "ROE": (-3.0, 5.0),
    "ROA": (-1.0, 1.5),
    "ROIC": (-2.0, 3.0),
    "Margem_Liquida": (-2.0, 2.0),
    "Margem_Operacional": (-2.0, 2.0),
    "Payout": (-2.0, 5.0),
    "P/L": (0.01, 200.0),
    "P/VP": (0.01, 50.0),
    "EV_EBIT": (0.01, 200.0),
    "P_FCO": (0.01, 200.0),
    "Endividamento_Total": (0.0, 20.0),
    "Liquidez_Corrente": (0.0, 20.0),
}


@dataclass
class Change:
    table: str
    ticker: str
    data: date
    column: str
    old: Any
    new: Any
    source: str
    action: str


def finite(v: Any) -> bool:
    try:
        x = float(v)
        return math.isfinite(x)
    except (TypeError, ValueError):
        return False


def clean_ticker(ticker: str) -> str:
    return str(ticker).strip().upper().replace(".SA", "")


def usable(field: str, value: Any) -> bool:
    if value is None or not finite(value):
        return False
    x = float(value)
    lo, hi = VALID_RANGES.get(field, (None, None))
    if lo is not None and x < lo:
        return False
    if hi is not None and x > hi:
        return False
    return True


def missing_or_invalid(field: str, value: Any, replace_invalid: bool) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except Exception:
        pass
    if not finite(value):
        return True
    if replace_invalid and field in VALID_RANGES:
        return not usable(field, value)
    return False


def first_value(df: pd.DataFrame | pd.Series, names: tuple[str, ...]) -> float | None:
    if df is None or df.empty:
        return None
    if isinstance(df, pd.Series):
        for name in names:
            if name in df.index and finite(df.get(name)):
                return float(df.get(name))
        return None
    idx = {str(c).lower().replace(" ", "").replace("_", ""): c for c in df.index}
    for name in names:
        key = name.lower().replace(" ", "").replace("_", "")
        col = idx.get(key)
        if col is not None and finite(df.loc[col]):
            return float(df.loc[col])
    return None


def safe_div(a: Any, b: Any) -> float | None:
    if not finite(a) or not finite(b):
        return None
    a_f, b_f = float(a), float(b)
    if abs(b_f) < 1e-12:
        return None
    out = a_f / b_f
    return out if math.isfinite(out) else None


def stmt_col(stmt: pd.DataFrame, dt: pd.Timestamp) -> pd.Series:
    if stmt is None or stmt.empty:
        return pd.Series(dtype=float)
    cols = pd.to_datetime(stmt.columns, errors="coerce")
    if pd.isna(dt):
        return pd.Series(dtype=float)
    try:
        pos = list(cols).index(dt)
    except ValueError:
        return pd.Series(dtype=float)
    return pd.to_numeric(stmt.iloc[:, pos], errors="coerce")


def annual_dividends(tkr: yf.Ticker) -> dict[int, float]:
    try:
        divs = tkr.dividends
        if divs is None or divs.empty:
            return {}
        if getattr(divs.index, "tz", None) is not None:
            divs.index = divs.index.tz_localize(None)
        annual = divs.resample("YE").sum()
        return {int(idx.year): float(v) for idx, v in annual.items() if finite(v) and float(v) > 0}
    except Exception:
        return {}


def annual_prices(ticker: str, start_year: int) -> dict[int, float]:
    try:
        raw = yf.download(
            f"{ticker}.SA",
            start=f"{start_year}-01-01",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=False,
        )
        if raw is None or raw.empty or "Close" not in raw.columns:
            return {}
        close = raw["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        if getattr(close.index, "tz", None) is not None:
            close.index = close.index.tz_localize(None)
        by_year = close.resample("YE").last().dropna()
        return {int(idx.year): float(v) for idx, v in by_year.items() if finite(v) and float(v) > 0}
    except Exception:
        return {}


def yf_annual_rows(ticker: str, years: int) -> dict[date, dict[str, float]]:
    tkr = yf.Ticker(f"{ticker}.SA")
    try:
        fin = tkr.financials
    except Exception:
        fin = pd.DataFrame()
    try:
        bs = tkr.balance_sheet
    except Exception:
        bs = pd.DataFrame()
    try:
        cf = tkr.cashflow
    except Exception:
        cf = pd.DataFrame()

    all_dates = sorted(
        {
            pd.Timestamp(c).tz_localize(None) if getattr(pd.Timestamp(c), "tz", None) else pd.Timestamp(c)
            for frame in (fin, bs, cf)
            for c in getattr(frame, "columns", [])
            if not pd.isna(pd.Timestamp(c))
        }
    )
    if years > 0:
        all_dates = all_dates[-years:]

    div_by_year = annual_dividends(tkr)
    price_by_year = annual_prices(ticker, min((d.year for d in all_dates), default=date.today().year) - 1)

    try:
        info = tkr.info or {}
    except Exception:
        info = {}
    shares = first_value(pd.Series(info), ("sharesOutstanding", "impliedSharesOutstanding"))

    rows: dict[date, dict[str, float]] = {}
    for dt in all_dates:
        f = stmt_col(fin, dt)
        b = stmt_col(bs, dt)
        c = stmt_col(cf, dt)

        revenue = first_value(f, ("Total Revenue", "Operating Revenue"))
        ebit = first_value(f, ("EBIT", "Operating Income"))
        net_income = first_value(f, ("Net Income", "Net Income Common Stockholders"))
        equity = first_value(b, ("Stockholders Equity", "Total Equity Gross Minority Interest"))
        assets = first_value(b, ("Total Assets",))
        debt = first_value(b, ("Total Debt", "Long Term Debt And Capital Lease Obligation"))
        cash = first_value(b, ("Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"))
        current_assets = first_value(b, ("Current Assets", "Total Current Assets"))
        current_liab = first_value(b, ("Current Liabilities", "Total Current Liabilities"))
        fco = first_value(c, ("Operating Cash Flow", "Total Cash From Operating Activities"))
        fci = first_value(c, ("Investing Cash Flow", "Total Cashflows From Investing Activities"))
        fcf = first_value(c, ("Free Cash Flow",))
        if fcf is None and fco is not None:
            capex = first_value(c, ("Capital Expenditure", "Capital Expenditures"))
            fcf = fco + capex if capex is not None else None

        dividends = div_by_year.get(dt.year)
        price = price_by_year.get(dt.year)
        market_cap = price * shares if price and shares else None
        net_debt = (debt - cash) if debt is not None and cash is not None else None
        invested_capital = None
        if equity is not None and debt is not None:
            invested_capital = equity + debt - (cash or 0.0)

        row = {
            "Receita_Liquida": revenue,
            "EBIT": ebit,
            "Lucro_Liquido": net_income,
            "Patrimonio_Liquido": equity,
            "Divida_Liquida": net_debt,
            "Divida_Total": debt,
            "Ativo_Total": assets,
            "Dividendos": dividends,
            "FCO": fco,
            "FCI": fci,
            "FCF": fcf,
            "Fluxo_Caixa_Operacional": fco,
            "Fluxo_Caixa_Investimento": fci,
            "Fluxo_Caixa_Livre": fcf,
            "Margem_Liquida": safe_div(net_income, revenue),
            "Margem_Operacional": safe_div(ebit, revenue),
            "ROE": safe_div(net_income, equity),
            "ROA": safe_div(net_income, assets),
            "ROIC": safe_div(ebit, invested_capital),
            "Endividamento_Total": safe_div(debt, equity),
            "Liquidez_Corrente": safe_div(current_assets, current_liab),
            "Payout": safe_div(dividends, net_income),
            "DY": safe_div(dividends, price),
            "P/L": safe_div(market_cap, net_income),
            "P/VP": safe_div(market_cap, equity),
            "EV_EBIT": safe_div((market_cap + (net_debt or 0.0)) if market_cap is not None else None, ebit),
            "P_FCO": safe_div(market_cap, fco),
        }
        rows[dt.date()] = {k: float(v) for k, v in row.items() if finite(v)}
    return rows


def table_columns(conn, table_name: str) -> set[str]:
    insp = inspect(conn)
    plain = table_name.strip('"')
    return {c["name"] for c in insp.get_columns(plain, schema="public")}


def fetch_existing(conn, table_name: str, ticker: str) -> dict[date, dict[str, Any]]:
    df = pd.read_sql_query(
        text(f'SELECT * FROM public.{table_name} WHERE "Ticker" = :tk OR "Ticker" = :tks'),
        conn,
        params={"tk": ticker, "tks": f"{ticker}.SA"},
    )
    if df.empty:
        return {}
    data_col = next((c for c in df.columns if c.lower() == "data"), None)
    if not data_col:
        return {}
    df[data_col] = pd.to_datetime(df[data_col], errors="coerce")
    out: dict[date, dict[str, Any]] = {}
    for _, row in df.dropna(subset=[data_col]).iterrows():
        out[row[data_col].date()] = row.to_dict()
    return out


def existing_tickers(conn, requested: list[str] | None) -> list[str]:
    if requested:
        return [clean_ticker(t) for t in requested if clean_ticker(t)]
    df = pd.read_sql_query(
        text('SELECT DISTINCT ticker FROM public.setores WHERE ticker IS NOT NULL ORDER BY ticker'),
        conn,
    )
    return [clean_ticker(t) for t in df["ticker"].tolist()]


def current_web_row(ticker: str) -> dict[str, float]:
    try:
        recon = get_multiplos_reconciliados(ticker)
    except Exception:
        return {}
    out = {}
    for col in MULT_COLS:
        val = recon.get(col)
        if usable(col, val):
            out[col] = float(val)
    return out


def collect_changes_for_table(
    table_name: str,
    ticker: str,
    generated: dict[date, dict[str, float]],
    existing: dict[date, dict[str, Any]],
    allowed_cols: set[str],
    replace_invalid: bool,
    source: str,
) -> list[Change]:
    changes: list[Change] = []
    for dt, row in generated.items():
        existing_row = existing.get(dt, {})
        for col, new in row.items():
            if col not in allowed_cols:
                continue
            if table_name == MULT_TABLE and col in VALID_RANGES and not usable(col, new):
                continue
            old = existing_row.get(col)
            if not existing_row or missing_or_invalid(col, old, replace_invalid):
                changes.append(Change(table_name, ticker, dt, col, old, new, source, "insert" if not existing_row else "update"))
    return changes


def apply_changes(conn, changes: list[Change], cols_by_table: dict[str, set[str]]) -> None:
    grouped: dict[tuple[str, str, date, str], dict[str, Change]] = {}
    for ch in changes:
        key = (ch.table, ch.ticker, ch.data, ch.action)
        grouped.setdefault(key, {})[ch.column] = ch

    for (table, ticker, dt, action), cols in grouped.items():
        existing = conn.execute(
            text(f'SELECT 1 FROM public.{table} WHERE "Ticker" = :tk AND data = :dt LIMIT 1'),
            {"tk": ticker, "dt": dt},
        ).fetchone()
        table_cols = cols_by_table[table]
        payload = {c: ch.new for c, ch in cols.items() if c in table_cols}
        if not payload:
            continue

        if existing:
            param_names = {col: f"v{i}" for i, col in enumerate(payload)}
            sets = ", ".join(f'"{col}" = :{param_names[col]}' for col in payload)
            params = {param_names[col]: val for col, val in payload.items()}
            params.update({"tk": ticker, "dt": dt})
            conn.execute(
                text(f'UPDATE public.{table} SET {sets} WHERE "Ticker" = :tk AND data = :dt'),
                params,
            )
        else:
            insert_payload = {"Ticker": ticker, "data": dt, **payload}
            valid_payload = {k: v for k, v in insert_payload.items() if k in table_cols}
            cols_sql = ", ".join(f'"{c}"' for c in valid_payload)
            param_names = {col: f"v{i}" for i, col in enumerate(valid_payload)}
            vals_sql = ", ".join(f":{param_names[col]}" for col in valid_payload)
            params = {param_names[col]: val for col, val in valid_payload.items()}
            conn.execute(
                text(f"INSERT INTO public.{table} ({cols_sql}) VALUES ({vals_sql})"),
                params,
            )


def write_audit(changes: list[Change], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame([ch.__dict__ for ch in changes])
    if df.empty:
        df = pd.DataFrame(columns=["table", "ticker", "data", "column", "old", "new", "source", "action"])
    df.to_csv(path, index=False, encoding="utf-8")


def read_audit(path: Path) -> list[Change]:
    df = pd.read_csv(path)
    changes: list[Change] = []
    for _, row in df.iterrows():
        old = None if pd.isna(row.get("old")) else row.get("old")
        data_val = pd.to_datetime(row.get("data"), errors="coerce")
        if pd.isna(data_val):
            continue
        changes.append(Change(
            table=str(row.get("table")),
            ticker=clean_ticker(str(row.get("ticker"))),
            data=data_val.date(),
            column=str(row.get("column")),
            old=old,
            new=row.get("new"),
            source=str(row.get("source")),
            action=str(row.get("action")),
        ))
    return changes


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backfill B3 fundamental gaps safely.")
    p.add_argument("--tickers", nargs="*", help="Tickers to process. Default: all tickers from setores.")
    p.add_argument("--limit", type=int, default=0, help="Limit ticker count for testing.")
    p.add_argument("--years", type=int, default=8, help="Annual statement years to fetch from yfinance.")
    p.add_argument("--apply", action="store_true", help="Write changes to the database. Default is dry-run.")
    p.add_argument("--replace-invalid", action="store_true", default=True, help="Replace zero/outlier cells, not only NULL.")
    p.add_argument("--no-current-web", action="store_true", help="Do not patch latest multiples with web reconciliation.")
    p.add_argument("--audit", default="artifacts/b3_backfill_audit.csv", help="Audit CSV path.")
    p.add_argument("--apply-audit", help="Apply changes from an existing audit CSV without fetching sources.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    url = _resolve_url()
    if not url:
        print("Database URL not configured. Set SUPABASE_DB_URL_B3, SUPABASE_DB_URL, or DATABASE_URL.")
        return 2

    engine = create_engine(
        url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 10, "sslmode": "require"} if not url.startswith("sqlite") else {},
    )

    changes: list[Change] = []
    with engine.begin() as conn:
        cols_by_table = {
            DEMO_TABLE: table_columns(conn, DEMO_TABLE),
            MULT_TABLE: table_columns(conn, MULT_TABLE),
        }
        if args.apply_audit:
            changes = read_audit(ROOT / args.apply_audit)
            print(f"Loaded changes from audit: {len(changes)}")
            if args.apply and changes:
                apply_changes(conn, changes, cols_by_table)
                print("Database updated from audit.")
            else:
                print("Dry-run only. Add --apply to write audit changes.")
            return 0

        tickers = existing_tickers(conn, args.tickers)
        if args.limit and args.limit > 0:
            tickers = tickers[: args.limit]

        for pos, tk in enumerate(tickers, 1):
            print(f"[{pos}/{len(tickers)}] {tk}")
            try:
                generated = yf_annual_rows(tk, args.years)
            except Exception as exc:
                print(f"  warning: yfinance historical fetch failed for {tk}: {exc}")
                generated = {}
            demo_generated = {dt: {k: v for k, v in row.items() if k in DEMO_COLS} for dt, row in generated.items()}
            mult_generated = {dt: {k: v for k, v in row.items() if k in MULT_COLS} for dt, row in generated.items()}

            demo_existing = fetch_existing(conn, DEMO_TABLE, tk)
            mult_existing = fetch_existing(conn, MULT_TABLE, tk)

            changes.extend(collect_changes_for_table(
                DEMO_TABLE, tk, demo_generated, demo_existing,
                cols_by_table[DEMO_TABLE], args.replace_invalid, "yfinance",
            ))
            changes.extend(collect_changes_for_table(
                MULT_TABLE, tk, mult_generated, mult_existing,
                cols_by_table[MULT_TABLE], args.replace_invalid, "yfinance-derived",
            ))

            if not args.no_current_web:
                latest_dt = max(mult_existing.keys(), default=date.today())
                web = current_web_row(tk)
                if web:
                    changes.extend(collect_changes_for_table(
                        MULT_TABLE, tk, {latest_dt: web}, mult_existing,
                        cols_by_table[MULT_TABLE], args.replace_invalid, "web-current",
                    ))

        write_audit(changes, ROOT / args.audit)
        print(f"Audit written to {ROOT / args.audit}")
        print(f"Changes {'to apply' if args.apply else 'proposed'}: {len(changes)}")

        if args.apply and changes:
            apply_changes(conn, changes, cols_by_table)
            print("Database updated.")
        else:
            print("Dry-run only. Re-run with --apply to write changes.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
