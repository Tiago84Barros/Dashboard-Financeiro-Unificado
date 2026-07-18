"""
data_pipeline/us/snapshot.py
Vitrine Empresas Americanas — constrói market_us.company_snapshots no warehouse.

O warehouse computa TUDO (score relativo, assimetria, avançado, dossiê) e grava
uma linha compacta por empresa. A publicação para o Supabase é um passo separado
e explícito (scripts/publish_us_snapshot.py), espelhando a vitrine de FIIs.

`serialize_row`/`jsonable`/`compact_financials` são puros e testados
(tests/test_us_snapshot.py); `build_snapshot` orquestra com a engine.
"""
from __future__ import annotations

import datetime as _dt
import decimal as _decimal
import json
import logging
from typing import Any, Optional, Sequence

from sqlalchemy import text

logger = logging.getLogger("us_snapshot")


def jsonable(value: Any) -> Any:
    """Converte datas/np.float/NaN para tipos JSON (mesmo espírito da vitrine FII)."""
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, _decimal.Decimal):   # NUMERIC do Postgres
        f = float(value)
        return None if f != f else f
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            return jsonable(value.item())
        except (TypeError, ValueError):
            pass
    if isinstance(value, float) and value != value:  # NaN
        return None
    return value


def compact_financials(income: Sequence[dict], balance: Sequence[dict],
                       cashflow: Sequence[dict]) -> list[dict]:
    """Série anual compacta (uma linha/ano) para a tabela do dossiê na vitrine."""
    bal_by = {r.get("fiscal_year"): r for r in balance}
    cf_by = {r.get("fiscal_year"): r for r in cashflow}
    out = []
    for r in sorted(income, key=lambda x: x.get("fiscal_year") or 0):
        y = r.get("fiscal_year")
        if y is None:
            continue
        b, c = bal_by.get(y, {}), cf_by.get(y, {})
        out.append({
            "fiscal_year": y,
            "revenue": r.get("revenue"), "net_income": r.get("net_income"),
            "ebitda": r.get("ebitda"),
            "free_cash_flow": c.get("free_cash_flow"),
            "total_equity": b.get("total_equity"), "total_debt": b.get("total_debt"),
        })
    return out


_SCORE_COLS = ("score", "score_quality", "score_growth", "score_solidity",
               "score_capital_efficiency", "score_valuation", "score_shareholder",
               "coverage")
_ASYM_KEYS = ("asymmetry_score", "confidence", "stage", "risk_class", "horizon",
              "suggested_position_pct", "positive_signals", "risks", "hypotheses",
              "invalidation", "missing_data")


def serialize_row(*, identity: dict, scored_row: dict, metrics: dict,
                  asymmetry: Optional[dict], advanced: Optional[dict],
                  dossie: Optional[dict], financials: list[dict],
                  score_version: str, generated_at) -> dict:
    """Linha pronta para upsert em company_snapshots (JSONB como json.dumps)."""
    def dumps(obj) -> Optional[str]:
        return None if obj is None else json.dumps(jsonable(obj), ensure_ascii=False)

    row = {
        "symbol": identity["symbol"],
        "cik": identity.get("cik"), "name": identity.get("name"),
        "sector": identity.get("sector"), "industry": identity.get("industry"),
        "exchange": identity.get("exchange"),
        "security_type": identity.get("security_type"),
        "is_reit": bool(identity.get("is_reit")),
        "is_active": bool(identity.get("is_active", True)),
        "metrics": dumps(metrics),
        "asymmetry": dumps(asymmetry),
        "advanced": dumps(advanced),
        "dossie": dumps(dossie),
        "financials": dumps(financials),
        "last_fiscal_year": (financials[-1]["fiscal_year"] if financials else None),
        "score_version": score_version,
        "generated_at": generated_at,
    }
    for col in _SCORE_COLS:
        v = scored_row.get(col)
        row[col] = None if v is None or v != v else float(v)
    return row


def build_snapshot(engine, *, limit_companies: int | None = None) -> dict:
    """Computa e grava a vitrine no warehouse (upsert por symbol)."""
    import core.us_read as ur
    from core.us_advanced import advanced_snapshot
    from core.us_asymmetry import build_trajectory, score_asymmetry
    from core.us_dossie import assemble_dossie
    from core.us_methodology import US_FUNDAMENTAL_SCORE_VERSION
    from core.us_score import score_cross_section
    from data_pipeline.us.repository import build_upsert

    if engine is None:
        return {"ok": False, "reason": "engine indisponível"}
    frame = ur.load_scoring_frame(limit_companies=limit_companies)
    if frame is None or frame.empty:
        return {"ok": False, "reason": "sem empresas com demonstrações no warehouse"}
    scored = score_cross_section(frame)

    with engine.connect() as conn:
        ident_rows = conn.execute(text(
            "SELECT a.symbol, c.cik, c.name, c.sector, c.industry, a.exchange, "
            "a.security_type, c.is_reit, c.is_active "
            "FROM market_us.assets a JOIN market_us.companies c ON c.id=a.company_id"
        )).fetchall()
        generated_at = conn.execute(text("SELECT NOW()")).scalar()
    identity_by = {r[0]: {"symbol": r[0], "cik": r[1], "name": r[2], "sector": r[3],
                          "industry": r[4], "exchange": r[5], "security_type": r[6],
                          "is_reit": r[7], "is_active": r[8]} for r in ident_rows}

    written = errors = 0
    rows_out: list[dict] = []
    for _, srow in scored.iterrows():
        sym = srow["symbol"]
        try:
            bundle = ur.load_company_bundle(sym)
            if not bundle:
                continue
            inc = bundle.get("income", [])
            bal = bundle.get("balance", [])
            cfw = bundle.get("cashflow", [])
            dossie = assemble_dossie(
                sym, name=bundle.get("name"), sector=bundle.get("sector"),
                industry=bundle.get("industry"), income=inc, balance=bal,
                cashflow=cfw, market_cap=bundle.get("market_cap"),
                score_row={"score": srow.get("score")})
            metrics = dossie.get("metrics", {})
            asym = score_asymmetry(metrics, build_trajectory(inc, bal, cfw)) \
                if (metrics.get("_years") or 0) >= 3 else None
            adv = advanced_snapshot(inc, bal, cfw, bundle.get("market_cap"))
            ident = identity_by.get(sym, {"symbol": sym, "name": bundle.get("name"),
                                          "sector": bundle.get("sector"),
                                          "industry": bundle.get("industry")})
            rows_out.append(serialize_row(
                identity=ident, scored_row=srow.to_dict(), metrics=metrics,
                asymmetry=asym, advanced=adv, dossie=dossie,
                financials=compact_financials(inc, bal, cfw),
                score_version=US_FUNDAMENTAL_SCORE_VERSION,
                generated_at=generated_at))
        except Exception as exc:  # noqa: BLE001 — 1 empresa ruim não derruba a vitrine
            errors += 1
            logger.warning("snapshot %s falhou: %s", sym, exc)

    if rows_out:
        sql = build_upsert("company_snapshots", list(rows_out[0].keys()),
                           conflict=["symbol"])
        with engine.begin() as conn:
            conn.execute(text(sql), rows_out)
        written = len(rows_out)
    return {"ok": True, "written": written, "errors": errors,
            "generated_at": str(generated_at)}
