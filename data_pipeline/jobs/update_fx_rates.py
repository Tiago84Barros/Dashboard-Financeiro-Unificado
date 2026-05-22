"""
data_pipeline/jobs/update_fx_rates.py
=====================================
Atualiza a cotação USD/BRL diariamente.

Cria (uma única vez) um ativo sintético em `assets` com ticker `USDBRL`,
class='other', currency='BRL' (porque o "preço" do par está em BRL por USD).
Em seguida, baixa a série histórica de `USDBRL=X` no yfinance e faz UPSERT
em `asset_quotes`.

Esse ativo sintético serve como FONTE da taxa de conversão para a camada
de visualização: posições em ativos de currency='USD' (Nomad) são
convertidas multiplicando o `unit_price` pela cotação mais recente do
ativo USDBRL.

Frequência sugerida: diária. PTAX pública (awesomeapi) é usada como
fallback se o yfinance não responder.
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

TABLE_NAME = "asset_quotes"
SOURCE_NAME = "FX USD/BRL (yfinance + awesomeapi)"
JOB_NAME = "update_fx_rates"

FX_TICKER = "USDBRL"           # ticker interno no app4
FX_YF_SYMBOL = "USDBRL=X"      # símbolo no yfinance
FX_ASSET_NAME = "USD/BRL — câmbio comercial"


def _ensure_fx_asset(conn) -> str:
    """Garante asset 'USDBRL' em assets. Retorna o UUID."""
    from sqlalchemy import text
    row = conn.execute(
        text("SELECT id FROM assets WHERE ticker = :t LIMIT 1"),
        {"t": FX_TICKER},
    ).fetchone()
    if row:
        return str(row[0])

    row = conn.execute(
        text("""
            INSERT INTO assets (ticker, name, class, currency, exchange)
            VALUES (:t, :n, 'other', 'BRL', 'FX')
            ON CONFLICT (ticker) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
        """),
        {"t": FX_TICKER, "n": FX_ASSET_NAME},
    ).fetchone()
    return str(row[0])


def _fetch_history_yfinance(periodo: str = "1mo"):
    """Tenta baixar via yfinance. Retorna DataFrame ou None."""
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("update_fx_rates: yfinance nao instalado")
        return None

    try:
        hist = yf.download(
            FX_YF_SYMBOL,
            period=periodo,
            progress=False,
            auto_adjust=True,
            actions=False,
        )
        return hist
    except Exception as exc:  # noqa: BLE001
        logger.warning("update_fx_rates: yfinance falhou: %s", exc)
        return None


def _fetch_today_awesomeapi() -> float | None:
    """Fallback diário (sem histórico) via awesomeapi.com.br."""
    try:
        import requests
        r = requests.get(
            "https://economia.awesomeapi.com.br/json/last/USD-BRL",
            timeout=10,
        )
        r.raise_for_status()
        v = r.json().get("USDBRL", {}).get("bid")
        return float(v) if v else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("update_fx_rates: awesomeapi falhou: %s", exc)
        return None


def run(periodo: str = "1mo") -> dict:
    result = {
        "status": "success",
        "table_name": TABLE_NAME,
        "source_name": SOURCE_NAME,
        "job_name": JOB_NAME,
        "records_inserted": 0,
        "records_updated": 0,
        "records_failed": 0,
        "error_message": None,
    }

    from data_pipeline.utils.db_utils import get_pipeline_engine
    from sqlalchemy import text

    engine = get_pipeline_engine()
    if engine is None:
        result["status"] = "failed"
        result["error_message"] = "Banco nao conectado"
        return result

    try:
        with engine.begin() as conn:
            asset_id = _ensure_fx_asset(conn)
    except Exception as exc:  # noqa: BLE001
        result["status"] = "failed"
        result["error_message"] = f"Falha ao criar asset USDBRL: {exc}"
        return result

    hist = _fetch_history_yfinance(periodo)

    upsert_sql = text("""
        INSERT INTO asset_quotes
            (asset_id, timestamp, open, high, low, close, volume)
        VALUES
            (:asset_id, :ts, :open, :high, :low, :close, NULL)
        ON CONFLICT (asset_id, timestamp) DO UPDATE
            SET close  = EXCLUDED.close,
                open   = EXCLUDED.open,
                high   = EXCLUDED.high,
                low    = EXCLUDED.low
    """)

    records: list[dict] = []

    if hist is not None and not hist.empty:
        try:
            if isinstance(hist.columns, __import__("pandas").MultiIndex):
                hist.columns = hist.columns.get_level_values(0)
            for ts, row_data in hist.iterrows():
                close_val = float(row_data.get("Close", 0) or 0)
                if close_val <= 0:
                    continue
                records.append({
                    "asset_id": asset_id,
                    "ts":       ts.to_pydatetime(),
                    "open":     float(row_data.get("Open", 0) or 0) or None,
                    "high":     float(row_data.get("High", 0) or 0) or None,
                    "low":      float(row_data.get("Low", 0) or 0) or None,
                    "close":    close_val,
                })
        except Exception as exc:  # noqa: BLE001
            logger.warning("update_fx_rates: erro processando hist: %s", exc)

    # Fallback se yfinance falhou completamente
    if not records:
        bid = _fetch_today_awesomeapi()
        if bid:
            from datetime import datetime, timezone
            records.append({
                "asset_id": asset_id,
                "ts":       datetime.now(tz=timezone.utc),
                "open":     bid,
                "high":     bid,
                "low":      bid,
                "close":    bid,
            })

    if not records:
        result["status"] = "failed"
        result["error_message"] = "Nem yfinance nem awesomeapi retornaram cotacao"
        return result

    try:
        with engine.begin() as conn:
            conn.execute(upsert_sql, records)
        result["records_inserted"] = len(records)
    except Exception as exc:  # noqa: BLE001
        result["status"] = "failed"
        result["error_message"] = f"Erro no UPSERT: {exc}"
        return result

    time.sleep(0.1)
    return result
