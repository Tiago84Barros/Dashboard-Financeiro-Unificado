"""
data_pipeline/jobs/update_b3_quotes.py
Atualiza cotações de ativos (B3 e internacionais) via yfinance.

Reutiliza a lógica de views/configuracoes.py._executar_update_cotacoes()
mas de forma headless (sem Streamlit).
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

TABLE_NAME  = "asset_quotes"
SOURCE_NAME = "B3/Yahoo Finance"
JOB_NAME    = "update_b3_quotes"


def run(periodo: str = "5d", apenas_desatualizados: bool = True) -> dict:
    """
    Baixa cotações via yfinance e faz UPSERT em asset_quotes.

    apenas_desatualizados=True: só atualiza ativos cuja última cotação
    tem mais de 1 dia de diferença do dia atual.
    """
    result = {
        "status":           "success",
        "table_name":       TABLE_NAME,
        "source_name":      SOURCE_NAME,
        "job_name":         JOB_NAME,
        "records_inserted": 0,
        "records_updated":  0,
        "records_failed":   0,
        "error_message":    None,
    }

    try:
        import yfinance as yf
    except ImportError:
        result["status"] = "failed"
        result["error_message"] = "yfinance não instalado"
        return result

    from data_pipeline.utils.db_utils import get_pipeline_engine
    from sqlalchemy import text

    engine = get_pipeline_engine()
    if engine is None:
        result["status"] = "failed"
        result["error_message"] = "Banco não conectado"
        return result

    try:
        with engine.connect() as conn:
            if apenas_desatualizados:
                rows = conn.execute(text("""
                    SELECT a.id::text AS id, a.ticker, a.currency
                    FROM   assets a
                    WHERE  NOT EXISTS (
                        SELECT 1 FROM asset_quotes aq
                        WHERE  aq.asset_id = a.id
                          AND  aq.timestamp >= NOW() - INTERVAL '2 days'
                    )
                    ORDER BY a.ticker
                """)).fetchall()
            else:
                rows = conn.execute(text(
                    "SELECT id::text AS id, ticker, currency FROM assets ORDER BY ticker"
                )).fetchall()
    except Exception as exc:
        result["status"] = "failed"
        result["error_message"] = f"Erro ao listar ativos: {exc}"
        return result

    if not rows:
        logger.info("update_b3_quotes: nenhum ativo para atualizar")
        result["status"] = "skipped"
        return result

    _SQL_UPSERT = text("""
        INSERT INTO asset_quotes
            (asset_id, timestamp, open, high, low, close, volume)
        VALUES
            (:asset_id, :ts, :open, :high, :low, :close, :volume)
        ON CONFLICT (asset_id, timestamp) DO UPDATE
            SET close  = EXCLUDED.close,
                open   = EXCLUDED.open,
                high   = EXCLUDED.high,
                low    = EXCLUDED.low,
                volume = EXCLUDED.volume
    """)

    for r in rows:
        ticker_yf = f"{r.ticker}.SA" if (r.currency or "BRL") == "BRL" else r.ticker
        try:
            hist = yf.download(
                ticker_yf, period=periodo, progress=False,
                auto_adjust=True, actions=False,
            )
            if hist.empty:
                result["records_failed"] += 1
                continue

            records = []
            for ts, row_data in hist.iterrows():
                close_val = float(row_data.get("Close", 0) or 0)
                if close_val > 0:
                    records.append({
                        "asset_id": r.id,
                        "ts":       ts.to_pydatetime(),
                        "open":     float(row_data.get("Open",   0) or 0) or None,
                        "high":     float(row_data.get("High",   0) or 0) or None,
                        "low":      float(row_data.get("Low",    0) or 0) or None,
                        "close":    close_val,
                        "volume":   float(row_data.get("Volume", 0) or 0) or None,
                    })

            if records:
                with engine.begin() as conn:
                    conn.execute(_SQL_UPSERT, records)
                result["records_inserted"] += len(records)
            else:
                result["records_failed"] += 1

            time.sleep(0.3)  # respeita rate limit do yfinance

        except Exception as exc:
            logger.warning("update_b3_quotes: erro em %s: %s", ticker_yf, exc)
            result["records_failed"] += 1

    if result["records_failed"] > 0 and result["records_inserted"] == 0:
        result["status"] = "failed"
        result["error_message"] = f"{result['records_failed']} ativos falharam"
    elif result["records_failed"] > 0:
        result["status"] = "partial_success"

    return result
