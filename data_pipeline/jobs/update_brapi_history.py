"""
data_pipeline/jobs/update_brapi_history.py
Backfill histórico de DY (dividend yield anual) via brapi.dev — em gotejamento.

Para cada empresa (poucas por execução), busca o histórico de dividendos + preços
na brapi, calcula o DY por ano e PREENCHE apenas as lacunas do `multiplos`
(linhas-ano com DY ausente/inválido). Nunca sobrescreve um DY válido (respeita
"não trocar dado confiável") — é backfill de buraco, fonte única auditada.

Anti-bloqueio: N empresas/execução, atraso aleatório, backoff e disjuntor.
Estado em `brapi_backfill_state` para não reprocessar. Requer BRAPI_TOKEN para o
universo completo (sem token, só os 4 tickers gratuitos da brapi).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from data_pipeline.quality import scheduler as _sched

logger = logging.getLogger(__name__)

JOB_NAME = "update_brapi_history"
TABLE_NAME = "multiplos"
SOURCE_NAME = "brapi.dev (histórico DY)"

_STATE_TABLE = "brapi_backfill_state"
_BACKUP_TABLE = "multiplos_healing_backup"
_AUDIT_TABLE = "data_healing_audit"


def _enabled() -> bool:
    return os.getenv("BRAPI_HISTORY_ENABLE", "true").strip().lower() in ("1", "true", "yes", "sim")


def _cfg_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _ensure_state(conn) -> None:
    from sqlalchemy import text
    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {_STATE_TABLE} (
            ticker TEXT PRIMARY KEY,
            last_run TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            anos_preenchidos INTEGER NOT NULL DEFAULT 0
        )
    """))


def _select_pending(conn, limit: int, refresh_days: int) -> list[str]:
    from sqlalchemy import text
    rows = conn.execute(text(f"""
        SELECT s.ticker
        FROM public.setores s
        LEFT JOIN (SELECT DISTINCT ticker FROM public.b3_portfolio_model_items) c
               ON c.ticker = s.ticker
        LEFT JOIN {_STATE_TABLE} st ON st.ticker = s.ticker
        WHERE s.ticker IS NOT NULL
          AND (st.ticker IS NULL OR st.last_run < NOW() - (:rd || ' days')::interval)
        ORDER BY (c.ticker IS NOT NULL) DESC, st.last_run ASC NULLS FIRST, s.ticker
        LIMIT :lim
    """), {"rd": int(refresh_days), "lim": int(limit)}).fetchall()
    return [str(r[0]).upper().replace(".SA", "") for r in rows if r[0]]


def run() -> dict:
    result = {
        "status": "success", "table_name": TABLE_NAME, "source_name": SOURCE_NAME,
        "job_name": JOB_NAME, "records_inserted": 0, "records_updated": 0,
        "records_failed": 0, "error_message": None,
    }
    if not _enabled():
        result["status"] = "skipped"
        result["error_message"] = "BRAPI_HISTORY_ENABLE=false."
        return result

    try:
        from sqlalchemy import text

        import core.brapi as brapi
        import core.data_quality as dq
        from data_pipeline.utils.db_utils import get_pipeline_engine
    except Exception as exc:
        result["status"] = "failed"
        result["error_message"] = f"import: {exc}"[:500]
        return result

    engine = get_pipeline_engine()
    if engine is None:
        result["status"] = "failed"
        result["error_message"] = "Banco não conectado"
        return result

    max_t = _cfg_int("BRAPI_HISTORY_MAX", 20)
    delay = float(os.getenv("BRAPI_HISTORY_DELAY", "2.0"))
    max_blocks = _cfg_int("BRAPI_HISTORY_MAX_BLOCKS", 3)
    refresh_days = _cfg_int("BRAPI_HISTORY_REFRESH_DAYS", 120)

    try:
        with engine.begin() as conn:
            _ensure_state(conn)
            from core.data_healing import _ensure_aux_tables
            _ensure_aux_tables(conn)
            tickers = _select_pending(conn, max_t, refresh_days)
    except Exception as exc:
        result["status"] = "failed"
        result["error_message"] = f"seleção: {exc}"[:500]
        return result

    if not tickers:
        result["status"] = "skipped"
        result["error_message"] = "Nenhuma empresa pendente de backfill (ciclo completo)."
        return result

    f"brapi_hist_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    preenchidos = falhas = 0
    blocos = 0
    interrompido = False

    for i, tk in enumerate(tickers):
        try:
            quote = _sched.with_backoff(
                lambda t=tk: brapi.fetch_quote(t),
                retries=3, base=float(os.getenv("BRAPI_HISTORY_BACKOFF", "4.0")),
                on_block=brapi.is_rate_limited,
            )
            blocos = 0
        except Exception as exc:
            if brapi.is_rate_limited(exc):
                blocos += 1
                if blocos >= max_blocks:
                    interrompido = True
                    break
            falhas += 1
            continue

        anos_dy = brapi.annual_dy(quote or {})
        n_ticker = 0
        try:
            with engine.begin() as conn:
                # linhas-ano existentes do ticker
                rows = conn.execute(text(
                    'SELECT data, "DY" FROM public.multiplos '
                    'WHERE ("Ticker" = :tk OR "Ticker" = :tks) AND data IS NOT NULL'
                ), {"tk": tk, "tks": f"{tk}.SA"}).fetchall()
                by_year = {d.year: (d, dy) for d, dy in
                           [(r[0], r[1]) for r in rows] if d is not None}
                for ano, dy_novo in anos_dy.items():
                    if ano not in by_year:
                        continue
                    data_row, dy_atual = by_year[ano]
                    # só preenche se o DY atual for ausente/ inválido (não sobrescreve válido)
                    if dq.is_valid_value("DY", dy_atual):
                        continue
                    conn.execute(text(f"""
                        INSERT INTO {_BACKUP_TABLE} (run_ts, ticker, data, indicador, valor_antigo)
                        VALUES (:ts, :tk, :dt, 'DY', :old)
                    """), {"ts": ts, "tk": tk, "dt": data_row,
                           "old": float(dy_atual) if dq.to_float(dy_atual) is not None else None})
                    conn.execute(text(f"""
                        INSERT INTO {_AUDIT_TABLE}
                          (run_ts, ticker, data, indicador, valor_antigo, valor_novo,
                           fonte, acao, n_fontes, motivo)
                        VALUES (:ts, :tk, :dt, 'DY', :old, :new, :fonte, 'backfill_historico', 1, :motivo)
                    """), {"ts": ts, "tk": tk, "dt": data_row,
                           "old": float(dy_atual) if dq.to_float(dy_atual) is not None else None,
                           "new": float(dy_novo), "fonte": brapi.SOURCE_NAME,
                           "motivo": f"DY {ano} ausente no banco; preenchido via brapi (dividendos/preço)."})
                    conn.execute(text(
                        'UPDATE public.multiplos SET "DY" = :v '
                        'WHERE ("Ticker" = :tk OR "Ticker" = :tks) AND data = :dt'
                    ), {"v": float(dy_novo), "tk": tk, "tks": f"{tk}.SA", "dt": data_row})
                    n_ticker += 1
                # marca estado
                conn.execute(text(f"""
                    INSERT INTO {_STATE_TABLE} (ticker, last_run, anos_preenchidos)
                    VALUES (:tk, NOW(), :n)
                    ON CONFLICT (ticker) DO UPDATE SET last_run = NOW(),
                        anos_preenchidos = {_STATE_TABLE}.anos_preenchidos + :n
                """), {"tk": tk, "n": n_ticker})
            preenchidos += n_ticker
        except Exception as exc:
            falhas += 1
            logger.warning("brapi_history: gravação falhou %s: %s", tk, exc)

        if i < len(tickers) - 1:
            _sched.sleep_jittered(base=delay)

    try:
        from core import b3_db as _db
        _db.load_multiplos_todos.clear()
    except Exception:
        pass

    result["records_updated"] = preenchidos
    result["records_failed"] = falhas
    msg = f"{preenchidos} DY anuais preenchidos em {len(tickers)} empresas; {falhas} falhas"
    if interrompido:
        result["status"] = "partial_success"
        msg += " — disjuntor acionado (429); retoma depois"
    elif falhas and not preenchidos:
        result["status"] = "partial_success"
    result["error_message"] = msg if (interrompido or falhas) else None
    logger.info("update_brapi_history: %s", msg)
    return result
