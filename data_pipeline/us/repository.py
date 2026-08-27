"""
data_pipeline/us/repository.py
Gravação idempotente em market_us.* (warehouse local).

Padrão (espelha data_pipeline/market/repository.py):
  - Chave natural + INSERT ... ON CONFLICT DO UPDATE (upsert).
  - Gravação atômica por lote (transação); falha parcial não corrompe válidos.
  - Estado de execução em ingestion_runs (retomada) e erros em ingestion_errors.

O construtor de SQL (build_upsert) é puro e coberto por teste; a execução usa a
engine central (core.database.get_engine) — nenhuma engine nova é criada aqui.
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

from sqlalchemy import text

logger = logging.getLogger("us_repository")

SCHEMA = "market_us"


# ── Construtor de upsert (puro) ───────────────────────────────────────────────
def build_upsert(table: str, columns: Sequence[str], conflict: Sequence[str],
                 update: Optional[Sequence[str]] = None) -> str:
    """Monta um INSERT ... ON CONFLICT (...) DO UPDATE parametrizado (:col).

    `update=None` atualiza todas as colunas exceto as de conflito. `update=[]`
    faz DO NOTHING (útil para tabelas append-only com dedup).
    """
    if not columns:
        raise ValueError("columns vazio")
    cols = ", ".join(columns)
    binds = ", ".join(f":{c}" for c in columns)
    conflict_cols = ", ".join(conflict)
    if update is None:
        update = [c for c in columns if c not in set(conflict)]
    if update:
        setters = ", ".join(f"{c} = EXCLUDED.{c}" for c in update)
        action = f"DO UPDATE SET {setters}"
    else:
        action = "DO NOTHING"
    return (f"INSERT INTO {SCHEMA}.{table} ({cols}) VALUES ({binds}) "
            f"ON CONFLICT ({conflict_cols}) {action}")


def _engine():
    from core.database import get_engine
    eng = get_engine()
    if eng is None:
        raise RuntimeError("engine indisponível — configure SUPABASE_UNIFICADO_URL "
                           "apontando para o warehouse local (127.0.0.1:5433).")
    return eng


def _exec_many(conn, table: str, rows: list[dict], conflict: Sequence[str],
               update: Optional[Sequence[str]] = None) -> int:
    if not rows:
        return 0
    columns = list(rows[0].keys())
    sql = build_upsert(table, columns, conflict, update)
    conn.execute(text(sql), rows)
    return len(rows)


# ── Empresa / ativo (identidade) ──────────────────────────────────────────────
def upsert_company(conn, profile: dict) -> int:
    """Upsert por cik (ou name quando cik ausente). Retorna company_id."""
    fields = {
        "cik": profile.get("cik"), "isin": profile.get("isin"),
        "cusip": profile.get("cusip"), "name": profile.get("name") or "?",
        "sector": profile.get("sector"), "industry": profile.get("industry"),
        "country": profile.get("country"), "currency": profile.get("currency") or "USD",
        "description": profile.get("description"), "website": profile.get("website"),
        "ceo": profile.get("ceo"), "employees": profile.get("employees"),
        "ipo_date": profile.get("ipo_date"), "is_reit": bool(profile.get("is_reit")),
        "is_investment_company": bool(profile.get("is_investment_company")),
        "is_adr": bool(profile.get("is_adr")), "is_active": bool(profile.get("is_active", True)),
        "source": profile.get("source") or "fmp",
    }
    if fields["cik"]:
        _exec_many(conn, "companies", [fields], conflict=["cik"])
        row = conn.execute(text(f"SELECT id FROM {SCHEMA}.companies WHERE cik = :cik"),
                           {"cik": fields["cik"]}).fetchone()
    else:
        # sem CIK: usa name como âncora fraca (não cria índice único — busca-e-insere)
        row = conn.execute(text(f"SELECT id FROM {SCHEMA}.companies WHERE cik IS NULL AND name = :n"),
                           {"n": fields["name"]}).fetchone()
        if row is None:
            conn.execute(text(build_upsert("companies", list(fields.keys()),
                                           conflict=["cik"])), fields)
            row = conn.execute(text(f"SELECT id FROM {SCHEMA}.companies WHERE cik IS NULL AND name = :n"),
                               {"n": fields["name"]}).fetchone()
    return int(row[0]) if row else 0


def upsert_asset(conn, company_id: int, profile: dict) -> None:
    fields = {
        "company_id": company_id,
        "symbol": profile.get("symbol"),
        "exchange": profile.get("exchange") or "NASDAQ",
        "security_type": profile.get("security_type") or "common",
        "currency": profile.get("currency") or "USD",
        "is_active": bool(profile.get("is_active", True)),
        "is_delisted": not bool(profile.get("is_active", True)),
    }
    _exec_many(conn, "assets", [fields], conflict=["symbol", "exchange"])


# ── Demonstrações / métricas ──────────────────────────────────────────────────
_STMT_CONFLICT = ["company_id", "period", "fiscal_year", "fiscal_quarter"]


def upsert_statements(conn, table: str, company_id: int, symbol: str,
                      rows: list[dict]) -> int:
    payload = [{**r, "company_id": company_id, "symbol": symbol} for r in rows]
    return _exec_many(conn, table, payload, conflict=_STMT_CONFLICT)


def upsert_prices_daily(conn, symbol: str, rows: list[dict]) -> int:
    payload = [{
        "symbol": symbol, "date": r.get("date"),
        "open": r.get("open"), "high": r.get("high"), "low": r.get("low"),
        "close": r.get("close"), "adjusted_close": r.get("adjClose") or r.get("adjusted_close"),
        "volume": r.get("volume"), "source": "fmp",
    } for r in rows if r.get("date")]
    return _exec_many(conn, "prices_daily", payload, conflict=["symbol", "date"])


def upsert_dividends(conn, symbol: str, rows: list[dict]) -> int:
    payload = [{
        "symbol": symbol, "ex_date": r.get("date") or r.get("ex_date"),
        "payment_date": r.get("paymentDate"), "record_date": r.get("recordDate"),
        "declaration_date": r.get("declarationDate"),
        "amount": r.get("dividend") or r.get("amount"),
        "adjusted_amount": r.get("adjDividend"), "currency": "USD", "source": "fmp",
    } for r in rows if (r.get("dividend") or r.get("amount")) is not None]
    # dividendos: dedup por chave natural, sem sobrescrever (append idempotente)
    return _exec_many(conn, "dividends", payload,
                      conflict=["symbol", "event_date", "amount"], update=[])


def upsert_splits(conn, symbol: str, rows: list[dict]) -> int:
    payload = [{
        "symbol": symbol, "split_date": r.get("date") or r.get("split_date"),
        "numerator": r.get("numerator"), "denominator": r.get("denominator"),
        "source": "fmp",
    } for r in rows if r.get("date") or r.get("split_date")]
    return _exec_many(conn, "splits", payload, conflict=["symbol", "split_date"])


# ── Estado de ingestão / erros / qualidade ────────────────────────────────────
def start_run(conn, run_key: str, domain: str, params: dict | None = None) -> int:
    sql = build_upsert("ingestion_runs",
                       ["run_key", "domain", "status", "params"],
                       conflict=["run_key", "domain"],
                       update=["status", "params", "cursor", "calls_made",
                               "rows_written", "started_at", "finished_at", "note"])
    conn.execute(text(sql), {"run_key": run_key, "domain": domain,
                             "status": "running", "params": _json(params),
                             "cursor": None, "calls_made": 0, "rows_written": 0,
                             "started_at": _now(), "finished_at": None, "note": None})
    row = conn.execute(text(f"SELECT id FROM {SCHEMA}.ingestion_runs "
                            f"WHERE run_key=:k AND domain=:d"),
                       {"k": run_key, "d": domain}).fetchone()
    return int(row[0]) if row else 0


def checkpoint_run(conn, run_id: int, cursor: str | None,
                   calls: int = 0, rows: int = 0) -> None:
    conn.execute(text(
        f"UPDATE {SCHEMA}.ingestion_runs SET cursor=:c, "
        f"calls_made=calls_made+:calls, rows_written=rows_written+:rows "
        f"WHERE id=:id"),
        {"c": cursor, "calls": calls, "rows": rows, "id": run_id})


def finish_run(conn, run_id: int, status: str = "completed",
               note: str | None = None) -> None:
    conn.execute(text(
        f"UPDATE {SCHEMA}.ingestion_runs SET status=:s, finished_at=NOW(), note=:n "
        f"WHERE id=:id"), {"s": status, "n": note, "id": run_id})


def log_error(conn, run_id: int | None, *, symbol=None, domain=None, endpoint=None,
              error_type=None, http_status=None, attempts=1, message=None) -> None:
    conn.execute(text(
        f"INSERT INTO {SCHEMA}.ingestion_errors "
        f"(run_id, symbol, domain, endpoint, error_type, http_status, attempts, message) "
        f"VALUES (:r,:s,:d,:e,:t,:h,:a,:m)"),
        {"r": run_id, "s": symbol, "d": domain, "e": endpoint, "t": error_type,
         "h": http_status, "a": attempts, "m": (message or "")[:2000]})


def log_quality(conn, *, table_name: str, check_name: str, passed: bool | None,
                severity: str = "info", symbol=None, field_name=None,
                detail=None) -> None:
    conn.execute(text(
        f"INSERT INTO {SCHEMA}.data_quality_audit "
        f"(symbol, table_name, field_name, check_name, severity, passed, detail) "
        f"VALUES (:s,:t,:f,:c,:sev,:p,:d)"),
        {"s": symbol, "t": table_name, "f": field_name, "c": check_name,
         "sev": severity, "p": passed, "d": detail})


def get_open_run(conn, run_key: str, domain: str) -> dict | None:
    row = conn.execute(text(
        f"SELECT id, status, cursor FROM {SCHEMA}.ingestion_runs "
        f"WHERE run_key=:k AND domain=:d"), {"k": run_key, "d": domain}).fetchone()
    if row is None:
        return None
    return {"id": int(row[0]), "status": row[1], "cursor": row[2]}


def _json(obj: Any) -> Optional[str]:
    if obj is None:
        return None
    import json
    return json.dumps(obj, default=str)


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)
