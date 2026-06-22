"""
data_pipeline/market/repository.py
Persistência no schema market.* com UPSERT idempotente (ON CONFLICT).
Inclui salvamento do payload bruto e log de qualidade.
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)

# colunas atualizadas no ON CONFLICT (exclui chaves e created_at)
_UPDATE_COLS = {
    "companies": ("name", "cnpj", "sector", "subsector", "segment", "website",
                  "description", "logo_url", "codigo_cvm"),
    "assets": ("company_id", "asset_type", "exchange", "currency", "is_active"),
    "historical_prices": ("open", "high", "low", "close", "adjusted_close", "volume"),
    "income_statements": ("revenue", "gross_profit", "ebit", "ebitda", "net_income"),
    "balance_sheets": ("total_assets", "total_liabilities", "equity", "cash",
                       "gross_debt", "net_debt"),
    "cash_flow_statements": ("operating_cash_flow", "investing_cash_flow",
                             "financing_cash_flow", "capex", "free_cash_flow"),
    "dividends": ("source",),
    "macro_indicators": ("value", "source"),
    "calculated_metrics": ("metric_value", "calculation_method", "source", "confidence_score"),
}
_CONFLICT = {
    "companies": "codigo_cvm",
    "assets": "ticker",
    "historical_prices": "ticker, date",
    "income_statements": "ticker, period, year, quarter",
    "balance_sheets": "ticker, period, year, quarter",
    "cash_flow_statements": "ticker, period, year, quarter",
    "dividends": "ticker, event_date, type, amount",
    "macro_indicators": "indicator, date",
    "calculated_metrics": "ticker, period, year, quarter, metric_name",
}


def schema_exists(conn) -> bool:
    return bool(conn.execute(text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name='market')"
    )).scalar())


def _upsert(conn, table: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    cols = list(rows[0].keys())
    collist = ", ".join(f'"{c}"' for c in cols)
    vals = ", ".join(f":{c}" for c in cols)
    upd = _UPDATE_COLS[table]
    setlist = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in upd if c in cols)
    conflict = _CONFLICT[table]
    action = f"DO UPDATE SET {setlist}" if setlist else "DO NOTHING"
    sql = (f'INSERT INTO market.{table} ({collist}) VALUES ({vals}) '
           f'ON CONFLICT ({conflict}) {action}')
    conn.execute(text(sql), rows)
    return len(rows)


def upsert(conn, table: str, rows: list[dict]) -> int:
    """Upsert genérico para uma tabela market.* conhecida."""
    if table not in _CONFLICT:
        raise ValueError(f"tabela desconhecida: {table}")
    return _upsert(conn, table, rows)


def save_raw_payload(conn, ticker, endpoint, payload, status="success", error=None) -> None:
    conn.execute(text("""
        INSERT INTO market.brapi_raw_payloads
          (ticker, endpoint, payload_json, source, request_status, error_message)
        VALUES (:tk, :ep, CAST(:pl AS jsonb), 'brapi.dev', :st, :err)
    """), {"tk": ticker, "ep": endpoint,
           "pl": json.dumps(payload, ensure_ascii=False, default=str) if payload is not None else None,
           "st": status, "err": (str(error)[:500] if error else None)})


def log_quality(conn, *, ticker=None, table_name, field_name=None, issue_type,
                old_value=None, new_value=None, severity="info", source="brapi.dev") -> None:
    conn.execute(text("""
        INSERT INTO market.data_quality_logs
          (ticker, table_name, field_name, issue_type, old_value, new_value, severity, source)
        VALUES (:tk, :tb, :fn, :it, :ov, :nv, :sev, :src)
    """), {"tk": ticker, "tb": table_name, "fn": field_name, "it": issue_type,
           "ov": (str(old_value)[:200] if old_value is not None else None),
           "nv": (str(new_value)[:200] if new_value is not None else None),
           "sev": severity, "src": source})


def company_id_by_codigo(conn, codigo_cvm: int) -> int | None:
    if codigo_cvm is None:
        return None
    return conn.execute(text(
        "SELECT id FROM market.companies WHERE codigo_cvm = :c"), {"c": int(codigo_cvm)}).scalar()


def load_cvm_to_ticker(conn) -> dict[str, int]:
    """Mapa ticker->codigo_cvm a partir da tabela existente public.cvm_to_ticker."""
    try:
        rows = conn.execute(text("SELECT ticker, codigo_cvm FROM public.cvm_to_ticker")).fetchall()
        return {str(t).upper().replace(".SA", ""): int(c) for t, c in rows if t and c is not None}
    except Exception:
        return {}
