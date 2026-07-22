"""
data_pipeline/us/quality.py
Testes de qualidade dos dados market_us.* (auditoria).

Checks puros (testáveis sem DB) + um runner que os aplica sobre o warehouse e
grava resultados em market_us.data_quality_audit. Espelha o espírito de
data_pipeline/quality e core/data_quality do lado B3.
"""
from __future__ import annotations

from typing import Optional
import json
from datetime import datetime, timezone

from sqlalchemy import text

# Tolerância relativa padrão para identidades contábeis.
DEFAULT_TOL = 0.02


def _rel_diff(a: float, b: float) -> float:
    scale = max(abs(a), abs(b), 1.0)
    return abs(a - b) / scale


def check_balance_identity(total_assets: Optional[float],
                           total_liabilities: Optional[float],
                           total_equity: Optional[float],
                           tol: float = DEFAULT_TOL) -> Optional[bool]:
    """Ativos ≈ Passivos + PL (tolerância relativa). None se faltar dado."""
    if None in (total_assets, total_liabilities, total_equity):
        return None
    return _rel_diff(total_assets, total_liabilities + total_equity) <= tol


def check_fcf_coherence(operating_cash_flow: Optional[float],
                        capex: Optional[float],
                        free_cash_flow: Optional[float],
                        tol: float = DEFAULT_TOL) -> Optional[bool]:
    """FCF ≈ CFO + capex (capex vem negativo na FMP). None se faltar dado."""
    if None in (operating_cash_flow, capex, free_cash_flow):
        return None
    return _rel_diff(operating_cash_flow + capex, free_cash_flow) <= tol


def check_market_cap_coherence(market_cap: Optional[float], price: Optional[float],
                               shares: Optional[float],
                               tol: float = 0.05) -> Optional[bool]:
    """market_cap ≈ price × shares. None se faltar dado ou shares<=0."""
    if None in (market_cap, price, shares) or not shares:
        return None
    return _rel_diff(market_cap, price * shares) <= tol


def check_margin_plausible(value: Optional[float], lo: float = -1.0,
                           hi: float = 1.0) -> Optional[bool]:
    """Margem (ratio) dentro de [lo, hi]. None se ausente."""
    if value is None:
        return None
    return lo <= value <= hi


def run_audit(engine, limit: int = 100000) -> dict:
    """Roda checks agregados, preservando taxas e um identificador de execução."""
    if engine is None:
        return {"ran": False, "reason": "engine indisponível"}
    run_key = datetime.now(timezone.utc).strftime("quality-v4-%Y%m%dT%H%M%SZ")
    checks: list[dict] = []

    def add(name: str, table: str, total: int, failed: int, skipped: int = 0,
            severity: str = "warn", threshold: float = 0.0, gate: bool = True):
        checked = max(0, int(total) - int(skipped))
        rate = (failed / checked) if checked else 0.0
        checks.append({"name": name, "table": table, "total": int(total),
                       "checked": checked, "failed": int(failed),
                       "skipped": int(skipped), "failure_rate": rate,
                       "passed": (rate <= threshold) if gate else None,
                       "severity": severity, "gate": gate})

    with engine.begin() as conn:
        b = conn.execute(text("""
          SELECT COUNT(*) total,
            COUNT(*) FILTER (WHERE total_assets IS NULL OR total_liabilities IS NULL OR total_equity IS NULL) skipped,
            COUNT(*) FILTER (WHERE total_assets IS NOT NULL AND total_liabilities IS NOT NULL
              AND total_equity IS NOT NULL AND ABS(total_assets-(total_liabilities+total_equity)) /
              GREATEST(ABS(total_assets),ABS(total_liabilities+total_equity),1) > .02) failed
          FROM (SELECT * FROM market_us.balance_sheets ORDER BY reference_date DESC LIMIT :l) x
        """), {"l": limit}).one()
        add("balance_identity_source", "balance_sheets", b[0], b[2], b[1],
            severity="info", gate=False)

        bu = conn.execute(text("""
          SELECT COUNT(*) total,
            COUNT(*) FILTER (WHERE total_assets IS NULL OR total_liabilities IS NULL OR total_equity IS NULL) skipped,
            COUNT(*) FILTER (WHERE total_assets IS NOT NULL AND total_liabilities IS NOT NULL
              AND total_equity IS NOT NULL AND ABS(total_assets-(total_liabilities+total_equity)) /
              GREATEST(ABS(total_assets),ABS(total_liabilities+total_equity),1) > .02) failed
          FROM (SELECT * FROM market_us.balance_sheets
                WHERE quality_status IN ('raw','validated')
                ORDER BY reference_date DESC LIMIT :l) x
        """), {"l": limit}).one()
        add("balance_identity_usable", "balance_sheets", bu[0], bu[2], bu[1],
            severity="critical", threshold=0.0)

        c = conn.execute(text("""
          SELECT COUNT(*) total,
            COUNT(*) FILTER (WHERE operating_cash_flow IS NULL OR capex IS NULL OR free_cash_flow IS NULL) skipped,
            COUNT(*) FILTER (WHERE operating_cash_flow IS NOT NULL AND capex IS NOT NULL
              AND free_cash_flow IS NOT NULL AND ABS((operating_cash_flow+capex)-free_cash_flow) /
              GREATEST(ABS(free_cash_flow),ABS(operating_cash_flow+capex),1) > .02) failed
          FROM (SELECT * FROM market_us.cash_flow_statements ORDER BY reference_date DESC LIMIT :l) x
        """), {"l": limit}).one()
        add("fcf_coherence", "cash_flow_statements", c[0], c[2], c[1], threshold=0.01)

        d_source = conn.execute(text("""
          SELECT COUNT(*), COUNT(*) FILTER (WHERE available_at IS NULL OR content_hash IS NULL),
            COUNT(*) FILTER (WHERE reference_date>CURRENT_DATE OR available_at<reference_date)
          FROM (
            SELECT reference_date,available_at,content_hash FROM market_us.income_statements
            UNION ALL SELECT reference_date,available_at,content_hash FROM market_us.balance_sheets
            UNION ALL SELECT reference_date,available_at,content_hash FROM market_us.cash_flow_statements
          ) x
        """)).one()
        add("pit_quarantine_source", "financial_statements", d_source[0], d_source[2],
            d_source[1], severity="info", gate=False)

        d = conn.execute(text("""
          SELECT COUNT(*), COUNT(*) FILTER (WHERE available_at IS NULL OR content_hash IS NULL),
            COUNT(*) FILTER (WHERE reference_date>CURRENT_DATE OR available_at<reference_date)
          FROM (
            SELECT reference_date,available_at,content_hash FROM market_us.income_statements
              WHERE quality_status IN ('raw','validated')
            UNION ALL SELECT reference_date,available_at,content_hash FROM market_us.balance_sheets
              WHERE quality_status IN ('raw','validated')
            UNION ALL SELECT reference_date,available_at,content_hash FROM market_us.cash_flow_statements
              WHERE quality_status IN ('raw','validated')
          ) x
        """)).one()
        add("pit_dates_and_hash_usable", "financial_statements", d[0], d[2], d[1],
            severity="critical", threshold=0.0)

        p = conn.execute(text("""
          SELECT COUNT(*), COUNT(*) FILTER (WHERE COALESCE(adjusted_close,close) IS NULL
                                             OR COALESCE(adjusted_close,close)<=0)
          FROM market_us.prices_daily
        """)).one()
        add("valid_adjusted_price", "prices_daily", p[0], p[1], 0, threshold=0.001)

        a = conn.execute(text("""
          SELECT COUNT(*) FILTER (WHERE analysis_status='eligible'),
                 COUNT(*) FILTER (WHERE analysis_status='eligible' AND company_id IS NULL)
          FROM market_us.assets
        """)).one()
        add("eligible_asset_identity", "assets", a[0], a[1], 0,
            severity="critical", threshold=0.0)

        s = conn.execute(text("""
          SELECT COUNT(*), COUNT(*) FILTER (WHERE source_version IS NULL)
          FROM (
            SELECT source_version FROM market_us.income_statements
            UNION ALL SELECT source_version FROM market_us.balance_sheets
            UNION ALL SELECT source_version FROM market_us.cash_flow_statements
          ) x
        """)).one()
        add("source_version", "financial_statements", s[0], s[1], 0,
            severity="critical", threshold=0.0)

        for item in checks:
            conn.execute(text("""
              INSERT INTO market_us.data_quality_audit
                (run_key,table_name,check_name,severity,passed,detail)
              VALUES (:run_key,:table,:name,:severity,:passed,:detail)
            """), {"run_key": run_key, "table": item["table"], "name": item["name"],
                    "severity": item["severity"], "passed": item["passed"],
                    "detail": json.dumps({k: v for k, v in item.items()
                                          if k not in {"name", "table", "severity", "passed"}})})
    return {"ran": True, "run_key": run_key, "checks": checks,
            "passed": sum(int(bool(x["passed"])) for x in checks if x["gate"]),
            "total": sum(int(x["gate"]) for x in checks)}


def _f(v):
    return None if v is None else float(v)
