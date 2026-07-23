"""Sincroniza, de forma idempotente, tickers B3 do warehouse para o Supabase.

Destina-se a códigos que já foram coletados e auditados localmente, mas cuja
reconsulta na fonte falha (por exemplo, após mudança de ticker). O payload bruto
permanece no warehouse; a nuvem recebe somente as tabelas compactas consumidas
pelo App4.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from data_pipeline.market import repository
from scripts.publish_fii_selection_from_local import _warehouse_url
from scripts.publish_us_snapshot import _engine


TABLES = (
    "historical_prices",
    "income_statements",
    "balance_sheets",
    "cash_flow_statements",
    "dividends",
    "calculated_metrics",
)
EXCLUDED_COLUMNS = {"id", "created_at", "updated_at", "raw_payload_id"}


def _remote_url() -> str:
    """Lê a URL sem importar Streamlit em um processo ETL de linha de comando."""
    return str(
        os.getenv("SUPABASE_UNIFICADO_URL")
        or os.getenv("DATABASE_URL")
        or os.getenv("SUPABASE_DB_URL")
        or ""
    )


def _columns(conn, table: str) -> list[str]:
    return [
        str(row[0])
        for row in conn.execute(text("""
            SELECT column_name,is_generated
            FROM information_schema.columns
            WHERE table_schema='market' AND table_name=:table
            ORDER BY ordinal_position
        """), {"table": table})
        if str(row[0]) not in EXCLUDED_COLUMNS and str(row[1]) != "ALWAYS"
    ]


def _rows(conn, table: str, columns: list[str], tickers: list[str]) -> list[dict]:
    selected = ",".join(f'"{column}"' for column in columns)
    return [dict(row) for row in conn.execute(
        text(f"SELECT {selected} FROM market.{table} WHERE ticker=ANY(:tickers)"),
        {"tickers": tickers},
    ).mappings()]


def publish(tickers: list[str]) -> dict:
    normalized = sorted({str(t).upper().replace(".SA", "") for t in tickers if t})
    if not normalized:
        raise ValueError("informe ao menos um ticker")
    remote_url = _remote_url()
    if not remote_url:
        raise RuntimeError("Supabase não configurado")

    source = create_engine(_warehouse_url(), pool_pre_ping=True)
    target = _engine(remote_url)
    result: dict[str, object] = {"tickers": normalized, "tables": {}}
    repository.reset_db_cols_cache()

    with source.connect() as src, target.begin() as dst:
        company_rows = [dict(row) for row in src.execute(text("""
            SELECT c.*
            FROM market.companies c
            JOIN market.assets a ON a.company_id=c.id
            WHERE a.ticker=ANY(:tickers)
        """), {"tickers": normalized}).mappings()]
        company_rows = [
            {k: v for k, v in row.items() if k not in EXCLUDED_COLUMNS}
            for row in company_rows
        ]
        repository.upsert(dst, "companies", company_rows)

        source_assets = [dict(row) for row in src.execute(text("""
            SELECT a.ticker,a.asset_type,a.exchange,a.currency,a.is_active,c.codigo_cvm
            FROM market.assets a
            JOIN market.companies c ON c.id=a.company_id
            WHERE a.ticker=ANY(:tickers)
        """), {"tickers": normalized}).mappings()]
        company_ids = dict(dst.execute(text("""
            SELECT codigo_cvm,id FROM market.companies
            WHERE codigo_cvm=ANY(:codes)
        """), {"codes": [row["codigo_cvm"] for row in source_assets]}).all())
        asset_rows = [
            {
                "company_id": company_ids[row["codigo_cvm"]],
                "ticker": row["ticker"],
                "asset_type": row["asset_type"],
                "exchange": row["exchange"],
                "currency": row["currency"],
                "is_active": row["is_active"],
            }
            for row in source_assets
        ]
        repository.upsert(dst, "assets", asset_rows)
        result["assets"] = len(asset_rows)

        target_columns = {table: set(_columns(dst, table)) for table in TABLES}
        for table in TABLES:
            columns = [
                column for column in _columns(src, table)
                if column in target_columns[table]
            ]
            rows = _rows(src, table, columns, normalized)
            copied = repository.upsert(dst, table, rows)
            result["tables"][table] = copied

        present = set(dst.execute(text("""
            SELECT ticker FROM market.assets WHERE ticker=ANY(:tickers)
        """), {"tickers": normalized}).scalars())
        result["verified"] = len(present)
        result["missing"] = sorted(set(normalized) - present)

    source.dispose()
    target.dispose()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tickers", nargs="+")
    args = parser.parse_args()
    print(json.dumps(publish(args.tickers), default=str, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
