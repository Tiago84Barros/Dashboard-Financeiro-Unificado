"""
Importa posicoes internacionais (Nomad/Apex/DriveWealth) para o App4.

Uso:
  python scripts/import_nomad_positions.py --input arquivo.xlsx
  python scripts/import_nomad_positions.py --input arquivo.xlsx --fx 5.10 --apply

Observacoes:
  - Dry run por padrao: valida e mostra o que seria importado.
  - `market_value` e `invested_value` sao gravados em BRL para manter o
    dashboard consolidado; os valores originais ficam preservados nas colunas
    `original_*` quando a fonte estiver em USD.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import unicodedata
import uuid
import warnings
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

PORTFOLIO_NAME = "Carteira Principal"
SOURCE_SYSTEM = "nomad"

DDL_SQL = [
    """
    CREATE TABLE IF NOT EXISTS portfolio_position_snapshots (
        id              UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id         UUID          NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        portfolio_id    UUID          NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
        asset_id        UUID          NOT NULL REFERENCES assets(id) ON DELETE RESTRICT,
        report_date     DATE          NOT NULL,
        quantity        NUMERIC(18,8) NOT NULL,
        market_price    NUMERIC(15,6),
        market_value    NUMERIC(15,2) NOT NULL,
        invested_value  NUMERIC(15,2),
        original_market_value  NUMERIC(15,2),
        original_invested_value NUMERIC(15,2),
        fx_rate_to_brl  NUMERIC(15,6),
        asset_name      VARCHAR(300),
        asset_type      VARCHAR(50),
        is_loaned       BOOLEAN       NOT NULL DEFAULT FALSE,
        institution     VARCHAR(150),
        currency        CHAR(3)       NOT NULL DEFAULT 'BRL',
        country         CHAR(2)       NOT NULL DEFAULT 'BR',
        source_system   VARCHAR(30)   NOT NULL DEFAULT 'app2',
        source_table    VARCHAR(50)   NOT NULL DEFAULT 'xp_positions',
        source_id       TEXT,
        imported_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
        UNIQUE (portfolio_id, asset_id, report_date, source_system, source_table, source_id)
    )
    """,
    "ALTER TABLE portfolio_position_snapshots ADD COLUMN IF NOT EXISTS original_market_value NUMERIC(15,2)",
    "ALTER TABLE portfolio_position_snapshots ADD COLUMN IF NOT EXISTS original_invested_value NUMERIC(15,2)",
    "ALTER TABLE portfolio_position_snapshots ADD COLUMN IF NOT EXISTS fx_rate_to_brl NUMERIC(15,6)",
    "CREATE INDEX IF NOT EXISTS idx_position_snapshots_user_date ON portfolio_position_snapshots (user_id, report_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_position_snapshots_asset_date ON portfolio_position_snapshots (asset_id, report_date DESC)",
]


@dataclass
class PositionRow:
    report_date: date
    ticker: str
    asset_name: str
    asset_type: str
    quantity: float
    market_price: float | None
    market_value_brl: float
    invested_value_brl: float | None
    original_market_value: float | None
    original_invested_value: float | None
    fx_rate_to_brl: float | None
    institution: str
    source_table: str
    source_id: str


@dataclass
class TradeRow:
    trade_date: date
    ticker: str
    asset_name: str
    action: str
    quantity: float
    price_usd: float
    net_usd: float
    source_file: str
    source_id: str


def _norm(value: object) -> str:
    text_value = str(value or "").strip().lower()
    text_value = unicodedata.normalize("NFKD", text_value)
    text_value = "".join(ch for ch in text_value if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", text_value)


def _find_col(df: pd.DataFrame, aliases: Iterable[str]) -> str | None:
    wanted = {_norm(a) for a in aliases}
    for col in df.columns:
        if _norm(col) in wanted:
            return col
    for col in df.columns:
        norm_col = _norm(col)
        if any(alias in norm_col for alias in wanted):
            return col
    return None


def _parse_number(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value).strip()
    if not raw or raw in {"-", "nan", "None"}:
        return None
    raw = re.sub(r"[^\d,.\-]", "", raw)
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_date(value: object, fallback: date) -> date:
    parsed = pd.to_datetime(value, errors="coerce", dayfirst=True)
    if pd.isna(parsed):
        return fallback
    return parsed.date()


def _parse_iso_date(value: str) -> date:
    return datetime.strptime(value.strip(), "%Y-%m-%d").date()


def _parse_us_date(value: str) -> date:
    return datetime.strptime(value.strip(), "%m/%d/%Y").date()


def _clean_ticker(value: object) -> str:
    ticker = str(value or "").strip().upper()
    ticker = re.sub(r"\.(US|NYSE|NASDAQ)$", "", ticker)
    ticker = re.sub(r"[^A-Z0-9.\-]", "", ticker)
    return ticker


def _asset_type_from_ticker(ticker: str, name: str) -> str:
    text_value = f"{ticker} {name}".lower()
    if "etf" in text_value or ticker in {"VOO", "IVV", "SPY", "QQQ", "VTI", "VEA", "IEFA", "BND", "AGG", "SGOV", "TFLO"}:
        return "etf"
    return "stock"


_ETF_NAMES: dict[str, str] = {
    "SPY": "SPDR S&P 500 ETF Trust",
    "IEFA": "iShares Core MSCI EAFE ETF",
    "SGOV": "iShares 0-3 Month Treasury Bond ETF",
    "TFLO": "iShares Treasury Floating Rate Bond ETF",
    "QQQ": "Invesco QQQ Trust",
    "VTI": "Vanguard Total Stock Market ETF",
    "VEA": "Vanguard FTSE Developed Markets ETF",
    "BND": "Vanguard Total Bond Market ETF",
    "AGG": "iShares Core U.S. Aggregate Bond ETF",
    "IVV": "iShares Core S&P 500 ETF",
    "VOO": "Vanguard S&P 500 ETF",
}


def _trade_id(prefix: str, *parts: object) -> str:
    raw = "|".join(str(p) for p in parts)
    digest = hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _extract_pdf_text(path: Path) -> str:
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError("Instale pdfplumber para importar PDFs Nomad: python -m pip install pdfplumber") from exc

    with pdfplumber.open(str(path)) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


def _parse_apex_trades(text: str, path: Path) -> list[TradeRow]:
    row_re = re.compile(
        r"(\d{4}-\d{2}-\d{2})\s+(\d{4}-\d{2}-\d{2})\s+"
        r"([A-Z]{1,10})\s+"
        r"([\d.,]+)\s+([\d.,]+)\s+"
        r"([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+"
        r"([\d.,]+)"
    )
    desc_re = re.compile(r"DESC:\s*(.+?)(?:\s+Trade#:\s*(\S+))?(?:\s+(?:CAP|TETO):|$)")
    trades: list[TradeRow] = []
    current_action = "buy"
    lines = text.splitlines()

    for i, line in enumerate(lines):
        low = line.lower()
        if "you bought" in low or "você comprou" in low or "voce comprou" in low:
            current_action = "buy"
        elif "you sold" in low or "você vendeu" in low or "voce vendeu" in low:
            current_action = "sell"

        match = row_re.search(line)
        if not match:
            continue

        trade_date_s, _, ticker, qty_s, price_s, _, _, _, _, net_s = match.groups()
        ticker = _clean_ticker(ticker)
        asset_name = _ETF_NAMES.get(ticker, ticker)
        trade_number = ""
        if i + 1 < len(lines):
            desc = desc_re.search(lines[i + 1])
            if desc:
                asset_name = desc.group(1).strip() or asset_name
                trade_number = desc.group(2) or ""

        qty = _parse_number(qty_s) or 0.0
        price = _parse_number(price_s) or 0.0
        net = _parse_number(net_s) or round(qty * price, 6)
        if not ticker or qty <= 0 or price <= 0:
            continue

        trades.append(TradeRow(
            trade_date=_parse_iso_date(trade_date_s),
            ticker=ticker,
            asset_name=asset_name,
            action=current_action,
            quantity=abs(qty),
            price_usd=price,
            net_usd=abs(net),
            source_file=path.name,
            source_id=_trade_id("nomad-apex", trade_number or path.name, trade_date_s, ticker, qty_s, price_s, net_s),
        ))
    return trades


def _parse_drivewealth_trades(text: str, path: Path) -> list[TradeRow]:
    row_re = re.compile(
        r"^([A-Z]{2,10})\s+"
        r"(.+?)\s+"
        r"(?:M|C)\s+"
        r"(Buy|Sell)\s+"
        r"\d+:\d+:\d+\s+(?:AM|PM)\s+"
        r"(-?[\d.]+)\s+"
        r"([\d.]+)\s+"
        r"(\d{1,2}/\d{1,2}/\d{4})\s+"
        r"(\d{1,2}/\d{1,2}/\d{4})"
    )
    net_re = re.compile(r"Net\s+Amount\s+(\(?\$?[\d,]+(?:\.\d+)?\)?)")
    lines = text.splitlines()
    trades: list[TradeRow] = []

    for i, line in enumerate(lines):
        match = row_re.match(line.strip())
        if not match:
            continue

        ticker, name, action, qty_s, price_s, trade_date_s, _ = match.groups()
        ticker = _clean_ticker(ticker)
        qty = abs(_parse_number(qty_s) or 0.0)
        price = _parse_number(price_s) or 0.0
        net = qty * price
        for j in range(i + 1, min(i + 9, len(lines))):
            net_match = net_re.search(lines[j])
            if net_match:
                net = abs(_parse_number(net_match.group(1)) or net)
                break

        if not ticker or qty <= 0 or price <= 0:
            continue

        trades.append(TradeRow(
            trade_date=_parse_us_date(trade_date_s),
            ticker=ticker,
            asset_name=_ETF_NAMES.get(ticker, name.strip() or ticker),
            action="buy" if action.lower() == "buy" else "sell",
            quantity=qty,
            price_usd=price,
            net_usd=abs(net),
            source_file=path.name,
            source_id=_trade_id("nomad-dw", path.name, trade_date_s, ticker, qty_s, price_s, action),
        ))
    return trades


def _extract_pdf_trades(path: Path) -> list[TradeRow]:
    text = _extract_pdf_text(path)
    if re.search(r"\d{4}-\d{2}-\d{2}\s+\d{4}-\d{2}-\d{2}", text):
        return _parse_apex_trades(text, path)
    return _parse_drivewealth_trades(text, path)


def _current_usd_brl(fallback: float | None = None) -> float:
    if fallback and fallback > 0:
        return fallback
    try:
        response = requests.get("https://economia.awesomeapi.com.br/json/last/USD-BRL", timeout=10)
        response.raise_for_status()
        value = response.json().get("USDBRL", {}).get("bid")
        if value:
            return float(value)
    except Exception:
        pass
    return 5.70


def _current_us_prices(tickers: Iterable[str]) -> dict[str, float]:
    prices: dict[str, float] = {}
    for ticker in sorted(set(tickers)):
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=5d&interval=1d"
            response = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            response.raise_for_status()
            result = response.json()["chart"]["result"][0]
            quote = result["indicators"]["quote"][0]
            closes = [float(v) for v in quote.get("close", []) if v is not None]
            if closes:
                prices[ticker] = closes[-1]
        except Exception:
            continue
    return prices


def _consolidate_trades_to_positions(trades: list[TradeRow], fx: float | None) -> list[PositionRow]:
    if not trades:
        return []

    buckets: dict[str, dict] = {}
    last_price: dict[str, float] = {}
    for trade in sorted(trades, key=lambda t: (t.trade_date, t.source_id)):
        bucket = buckets.setdefault(trade.ticker, {
            "ticker": trade.ticker,
            "asset_name": trade.asset_name,
            "quantity": 0.0,
            "cost_usd": 0.0,
            "last_date": trade.trade_date,
        })
        last_price[trade.ticker] = trade.price_usd
        bucket["asset_name"] = trade.asset_name or bucket["asset_name"]
        bucket["last_date"] = max(bucket["last_date"], trade.trade_date)

        if trade.action == "buy":
            if bucket["quantity"] <= 0.000001:
                bucket["quantity"] = 0.0
                bucket["cost_usd"] = 0.0
            bucket["quantity"] += trade.quantity
            bucket["cost_usd"] += trade.net_usd
        elif trade.action == "sell":
            if bucket["quantity"] > 0:
                avg_cost = bucket["cost_usd"] / bucket["quantity"] if bucket["quantity"] else 0.0
                sold_qty = min(trade.quantity, bucket["quantity"])
                bucket["cost_usd"] -= avg_cost * sold_qty
            bucket["quantity"] -= trade.quantity
            if bucket["quantity"] < 0:
                bucket["quantity"] = 0.0
            if bucket["cost_usd"] < 0:
                bucket["cost_usd"] = 0.0

    fx_rate = _current_usd_brl(fx)
    current_prices = _current_us_prices(buckets.keys())
    report_date = date.today()
    rows: list[PositionRow] = []
    for ticker, bucket in buckets.items():
        qty = float(bucket["quantity"])
        cost_usd = float(bucket["cost_usd"])
        if qty <= 0.000001 or cost_usd <= 0:
            continue
        price_usd = current_prices.get(ticker) or last_price.get(ticker) or (cost_usd / qty)
        market_usd = qty * price_usd
        rows.append(PositionRow(
            report_date=report_date,
            ticker=ticker,
            asset_name=bucket["asset_name"] or _ETF_NAMES.get(ticker, ticker),
            asset_type=_asset_type_from_ticker(ticker, bucket["asset_name"]),
            quantity=round(qty, 8),
            market_price=round(price_usd * fx_rate, 6),
            market_value_brl=round(market_usd * fx_rate, 2),
            invested_value_brl=round(cost_usd * fx_rate, 2),
            original_market_value=round(market_usd, 2),
            original_invested_value=round(cost_usd, 2),
            fx_rate_to_brl=round(fx_rate, 6),
            institution="Nomad",
            source_table="pdf_consolidated",
            source_id=f"nomad-pdf-consolidated:{ticker}",
        ))
    return rows


def _looks_foreign(df: pd.DataFrame, file_name: str, sheet_name: str) -> bool:
    marker = f"{file_name} {sheet_name} " + " ".join(str(c) for c in df.columns)
    if re.search(r"nomad|apex|drivewealth|exterior|usd|dolar|dólar|nasdaq|nyse", marker, re.I):
        return True
    sample = df.head(50).astype(str).to_string()
    return bool(re.search(r"nomad|apex|drivewealth|usd|nasdaq|nyse", sample, re.I))


def _read_frames(path: Path) -> Iterable[tuple[str, pd.DataFrame]]:
    if path.suffix.lower() in {".csv", ".txt"}:
        yield path.stem, pd.read_csv(path, dtype=object)
        return
    xls = pd.ExcelFile(path)
    for sheet in xls.sheet_names:
        yield sheet, pd.read_excel(path, sheet_name=sheet, dtype=object)


def _extract_positions(path: Path, fx: float | None) -> list[PositionRow]:
    rows: list[PositionRow] = []
    fallback_date = date.fromtimestamp(path.stat().st_mtime)
    for sheet, df in _read_frames(path):
        if df.empty or not _looks_foreign(df, path.name, sheet):
            continue

        ticker_col = _find_col(df, ["ticker", "symbol", "codigo", "ativo", "asset", "símbolo"])
        qty_col = _find_col(df, ["quantidade", "quantity", "qty", "qtd"])
        value_col = _find_col(df, ["valor de mercado", "market value", "valor atual", "saldo bruto", "valor"])
        price_col = _find_col(df, ["preco atual", "preço atual", "market price", "price", "cotacao", "cotação"])
        invested_col = _find_col(df, ["valor investido", "custo total", "cost basis", "total cost", "invested"])
        name_col = _find_col(df, ["nome", "name", "produto", "descricao", "descrição"])
        date_col = _find_col(df, ["data", "report date", "reference date", "posicao em", "posição em"])
        currency_col = _find_col(df, ["moeda", "currency"])
        broker_col = _find_col(df, ["instituicao", "instituição", "broker", "corretora", "custodiante"])
        fx_col = _find_col(df, ["cambio", "câmbio", "fx", "taxa usd", "usd brl", "dolar"])

        if not ticker_col or not qty_col or not value_col:
            continue

        for idx, rec in df.iterrows():
            ticker = _clean_ticker(rec.get(ticker_col))
            qty = _parse_number(rec.get(qty_col))
            original_value = _parse_number(rec.get(value_col))
            if not ticker or ticker.endswith("11") or not qty or qty <= 0 or not original_value or original_value <= 0:
                continue

            currency = str(rec.get(currency_col) or "USD").strip().upper() if currency_col else "USD"
            if currency not in {"USD", "US$"} and fx is None:
                continue

            row_fx = _parse_number(rec.get(fx_col)) if fx_col else None
            fx_used = row_fx or fx
            value_brl = original_value * fx_used if fx_used else original_value
            invested_original = _parse_number(rec.get(invested_col)) if invested_col else None
            invested_brl = invested_original * fx_used if invested_original is not None and fx_used else invested_original
            name = str(rec.get(name_col) or ticker).strip() if name_col else ticker

            rows.append(PositionRow(
                report_date=_parse_date(rec.get(date_col), fallback_date) if date_col else fallback_date,
                ticker=ticker,
                asset_name=name,
                asset_type=_asset_type_from_ticker(ticker, name),
                quantity=qty,
                market_price=_parse_number(rec.get(price_col)) if price_col else None,
                market_value_brl=round(value_brl, 2),
                invested_value_brl=round(invested_brl, 2) if invested_brl is not None else None,
                original_market_value=round(original_value, 2),
                original_invested_value=round(invested_original, 2) if invested_original is not None else None,
                fx_rate_to_brl=fx_used,
                institution=str(rec.get(broker_col) or "Nomad").strip() if broker_col else "Nomad",
                source_table=sheet[:50],
                source_id=f"{path.name}:{sheet}:{idx}:{ticker}",
            ))
    return rows


def _ensure_portfolio(conn, owner_id: str, apply: bool) -> str:
    from sqlalchemy import text

    row = conn.execute(
        text("SELECT id FROM portfolios WHERE user_id = :uid AND name = :name LIMIT 1"),
        {"uid": owner_id, "name": PORTFOLIO_NAME},
    ).fetchone()
    if row:
        return str(row[0])
    if not apply:
        return "dry_portfolio"
    new_id = str(uuid.uuid4())
    conn.execute(
        text("INSERT INTO portfolios (id, user_id, name, type, active) VALUES (:id, :uid, :name, 'personal', true)"),
        {"id": new_id, "uid": owner_id, "name": PORTFOLIO_NAME},
    )
    return new_id


def _ensure_assets(conn, rows: list[PositionRow], apply: bool) -> dict[str, str]:
    from sqlalchemy import text

    existing = {
        ticker.upper(): str(asset_id)
        for ticker, asset_id in conn.execute(text("SELECT ticker, id FROM assets")).fetchall()
    }
    for row in rows:
        if row.ticker in existing:
            continue
        if not apply:
            existing[row.ticker] = f"dry_{row.ticker}"
            continue
        asset_id = str(uuid.uuid4())
        created = conn.execute(
            text("""
                INSERT INTO assets (id, ticker, name, class, currency, exchange)
                VALUES (:id, :ticker, :name, :class, 'USD', :exchange)
                ON CONFLICT (ticker) DO UPDATE
                SET name = EXCLUDED.name,
                    class = EXCLUDED.class,
                    currency = 'USD',
                    exchange = COALESCE(assets.exchange, EXCLUDED.exchange)
                RETURNING id
            """),
            {
                "id": asset_id,
                "ticker": row.ticker,
                "name": row.asset_name,
                "class": row.asset_type,
                "exchange": "US",
            },
        ).fetchone()
        existing[row.ticker] = str(created[0])
    return existing


def _import_rows(conn, rows: list[PositionRow], asset_map: dict[str, str], portfolio_id: str, owner_id: str, apply: bool) -> int:
    from sqlalchemy import text

    count = 0
    for row in rows:
        asset_id = asset_map.get(row.ticker)
        if not asset_id:
            continue
        count += 1
        if not apply:
            continue
        conn.execute(
            text("""
                INSERT INTO portfolio_position_snapshots (
                    user_id, portfolio_id, asset_id, report_date, quantity,
                    market_price, market_value, invested_value,
                    original_market_value, original_invested_value, fx_rate_to_brl,
                    asset_name, asset_type, institution, currency, country,
                    source_system, source_table, source_id
                )
                VALUES (
                    :uid, :portfolio_id, :asset_id, :report_date, :quantity,
                    :market_price, :market_value, :invested_value,
                    :original_market_value, :original_invested_value, :fx_rate_to_brl,
                    :asset_name, :asset_type, :institution, 'USD', 'US',
                    :source_system, :source_table, :source_id
                )
                ON CONFLICT (portfolio_id, asset_id, report_date, source_system, source_table, source_id)
                DO UPDATE SET
                    quantity = EXCLUDED.quantity,
                    market_price = EXCLUDED.market_price,
                    market_value = EXCLUDED.market_value,
                    invested_value = EXCLUDED.invested_value,
                    original_market_value = EXCLUDED.original_market_value,
                    original_invested_value = EXCLUDED.original_invested_value,
                    fx_rate_to_brl = EXCLUDED.fx_rate_to_brl,
                    asset_name = EXCLUDED.asset_name,
                    asset_type = EXCLUDED.asset_type,
                    institution = EXCLUDED.institution,
                    currency = EXCLUDED.currency,
                    country = EXCLUDED.country,
                    imported_at = NOW()
            """),
            {
                "uid": owner_id,
                "portfolio_id": portfolio_id,
                "asset_id": asset_id,
                "report_date": row.report_date,
                "quantity": row.quantity,
                "market_price": row.market_price,
                "market_value": row.market_value_brl,
                "invested_value": row.invested_value_brl,
                "original_market_value": row.original_market_value,
                "original_invested_value": row.original_invested_value,
                "fx_rate_to_brl": row.fx_rate_to_brl,
                "asset_name": row.asset_name,
                "asset_type": row.asset_type,
                "institution": row.institution,
                "source_system": SOURCE_SYSTEM,
                "source_table": row.source_table,
                "source_id": row.source_id,
            },
        )
    return count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", nargs="+", type=Path, required=True, help="Arquivos PDF/XLSX/CSV exportados da Nomad/Apex/DriveWealth.")
    parser.add_argument("--fx", type=float, default=None, help="Cambio USD/BRL para arquivos que trazem valores em USD sem conversao.")
    parser.add_argument("--apply", action="store_true", help="Grava no App4.")
    args = parser.parse_args()

    all_rows: list[PositionRow] = []
    all_trades: list[TradeRow] = []
    for path in args.input:
        if not path.exists():
            print(f"Arquivo inexistente: {path}")
            continue
        if path.suffix.lower() == ".pdf":
            trades = _extract_pdf_trades(path)
            print(f"{path.name}: {len(trades)} operacoes PDF")
            all_trades.extend(trades)
        else:
            all_rows.extend(_extract_positions(path, args.fx))

    if all_trades:
        all_rows.extend(_consolidate_trades_to_positions(all_trades, args.fx))
        print(f"Operacoes PDF totais: {len(all_trades)}")

    print(f"Posicoes internacionais detectadas: {len(all_rows)}")
    for row in all_rows[:20]:
        print(
            f"- {row.report_date} {row.ticker} {row.quantity:g} "
            f"USD {row.original_market_value} / custo USD {row.original_invested_value} "
            f"-> BRL {row.market_value_brl}"
        )

    if not all_rows:
        print("Nenhum registro Nomad/USD importavel encontrado nesses arquivos.")
        return 0

    from core.config import settings

    if not settings.db_url or not settings.OWNER_USER_ID:
        print("Banco App4 ou OWNER_USER_ID ausente.")
        return 1

    from sqlalchemy import create_engine, text

    engine = create_engine(settings.db_url, pool_pre_ping=True)
    with engine.begin() as conn:
        if args.apply:
            for sql in DDL_SQL:
                conn.execute(text(sql))
        portfolio_id = _ensure_portfolio(conn, settings.OWNER_USER_ID, args.apply)
        asset_map = _ensure_assets(conn, all_rows, args.apply)
        imported = _import_rows(conn, all_rows, asset_map, portfolio_id, settings.OWNER_USER_ID, args.apply)
    print(("Gravados/atualizados" if args.apply else "Seriam importados") + f": {imported}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
