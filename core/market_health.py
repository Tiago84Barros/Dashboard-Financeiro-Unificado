"""
core/market_health.py
Saúde do schema market.* (BRAPI Pro): cobertura, completude, anomalias e frescor.

Substitui, no cutover, a antiga abordagem de "healing" por scraping cruzado
(Fundamentus / Status Invest), que tinha muitos problemas. Aqui não se corrige
valor cruzando sites: confia-se na fonte (brapi), valida-se faixa coerente
(core.data_quality, já usado no cálculo) e MONITORA-SE o que falta/destoa.

Núcleo de agregação puro e testável (completeness_rows); leitura via engine
unificada (onde vive o market.*).
"""
from __future__ import annotations

import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)

# Métricas-chave do snapshot ttm — completude aqui mede o impacto direto no
# ranking/score (campo ausente = empresa subavaliada no comparativo).
_KEY_METRICS = ["ROE", "ROA", "Margem_Liquida", "Margem_Operacional",
                "Endividamento_Total", "Liquidez_Corrente", "DY", "P/L", "P/VP"]


def _engine():
    try:
        from core.database import get_engine
        return get_engine()
    except Exception:
        return None


def completeness_rows(present: dict[str, int], total: int) -> list[dict]:
    """
    Puro/testável: {metric: nº de tickers com valor} + total de tickers ttm
    → linhas {campo, preenchidos, total, pct} na ordem canônica.
    """
    out: list[dict] = []
    for m in _KEY_METRICS:
        n = int(present.get(m, 0) or 0)
        out.append({"campo": m, "preenchidos": n, "total": int(total),
                    "pct": round(n / total * 100, 1) if total else 0.0})
    return out


def market_health_summary() -> dict:
    """Resumo da saúde do market.* p/ o painel. Defensivo (nunca quebra a UI)."""
    eng = _engine()
    if eng is None:
        return {"schema_ok": False}
    try:
        with eng.connect() as conn:
            if not conn.execute(text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.schemata "
                "WHERE schema_name='market')")).scalar():
                return {"schema_ok": False}

            def sc(q: str, p: dict | None = None):
                try:
                    return conn.execute(text(q), p or {}).scalar()
                except Exception:
                    return None

            universo = {
                "companies": sc("SELECT count(*) FROM market.companies") or 0,
                "assets": sc("SELECT count(*) FROM market.assets") or 0,
                "ttm_tickers": sc("SELECT count(DISTINCT ticker) FROM market.calculated_metrics "
                                  "WHERE period='ttm'") or 0,
                "annual_tickers": sc("SELECT count(DISTINCT ticker) FROM market.income_statements "
                                     "WHERE period='annual'") or 0,
            }
            present: dict[str, int] = {}
            try:
                for m, n in conn.execute(text(
                    "SELECT metric_name, count(DISTINCT ticker) FROM market.calculated_metrics "
                    "WHERE period='ttm' AND metric_name = ANY(:ms) GROUP BY metric_name"),
                        {"ms": _KEY_METRICS}).fetchall():
                    present[str(m)] = int(n)
            except Exception:
                pass

            por_sev = []
            try:
                por_sev = [(str(s or "info"), int(n)) for s, n in conn.execute(text(
                    "SELECT severity, count(*) FROM market.data_quality_logs "
                    "GROUP BY 1 ORDER BY 2 DESC")).fetchall()]
            except Exception:
                pass

            return {
                "schema_ok": True,
                "universo": universo,
                "completude": completeness_rows(present, universo["ttm_tickers"]),
                "qualidade": {
                    "total": int(sc("SELECT count(*) FROM market.data_quality_logs") or 0),
                    "por_severidade": por_sev,
                },
                "frescor": {
                    "ultimo_calc": sc("SELECT max(updated_at) FROM market.calculated_metrics"),
                    "bootstrap_ok": int(sc("SELECT count(*) FROM market.bootstrap_state "
                                           "WHERE status='ok'") or 0),
                },
            }
    except Exception as exc:
        logger.warning("market_health_summary: %s", exc)
        return {"schema_ok": False}
