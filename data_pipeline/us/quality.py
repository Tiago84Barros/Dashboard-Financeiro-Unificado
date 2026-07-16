"""
data_pipeline/us/quality.py
Testes de qualidade dos dados market_us.* (auditoria).

Checks puros (testáveis sem DB) + um runner que os aplica sobre o warehouse e
grava resultados em market_us.data_quality_audit. Espelha o espírito de
data_pipeline/quality e core/data_quality do lado B3.
"""
from __future__ import annotations

from typing import Optional

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


def run_audit(engine, limit: int = 5000) -> dict:
    """Roda os checks sobre o warehouse e registra em data_quality_audit."""
    if engine is None:
        return {"ran": False, "reason": "engine indisponível"}
    passed = failed = skipped = 0
    with engine.begin() as conn:
        rows = conn.execute(text(
            "SELECT symbol, total_assets, total_liabilities, total_equity "
            "FROM market_us.balance_sheets ORDER BY fiscal_year DESC LIMIT :l"),
            {"l": limit}).fetchall()
        for r in rows:
            res = check_balance_identity(_f(r[1]), _f(r[2]), _f(r[3]))
            if res is None:
                skipped += 1
                continue
            passed += int(res)
            failed += int(not res)
            if not res:
                conn.execute(text(
                    "INSERT INTO market_us.data_quality_audit "
                    "(symbol, table_name, check_name, severity, passed, detail) "
                    "VALUES (:s,'balance_sheets','balance_identity','warn',FALSE,:d)"),
                    {"s": r[0], "d": "ativos != passivos + PL"})
    return {"ran": True, "checked": passed + failed, "passed": passed,
            "failed": failed, "skipped": skipped}


def _f(v):
    return None if v is None else float(v)
