"""
data_pipeline/jobs/update_b3_quotes.py
Atualiza cotacoes de ativos B3 e internacionais via yfinance.

O job precisa ser tolerante a bancos em fases diferentes de migracao:
alguns usam currency, outros moeda; alguns ja possuem ativo/active, outros nao.
"""
from __future__ import annotations

import logging
import re
import time

logger = logging.getLogger(__name__)

TABLE_NAME = "asset_quotes"
SOURCE_NAME = "B3/Yahoo Finance"
JOB_NAME = "update_b3_quotes"
_B3_REGULAR_SUFFIXES = ("3", "4", "5", "6", "11", "34", "35", "39")
_NON_MARKET_CLASSES = {"fixed_income", "renda_fixa", "tesouro_direto", "tesouro", "cash"}


def _safe_table_names(inspector) -> set[str]:
    names: set[str] = set()
    try:
        names.update(inspector.get_table_names(schema="public") or [])
    except Exception:
        pass
    try:
        names.update(inspector.get_table_names() or [])
    except Exception:
        pass
    return names


def _safe_columns(inspector, table_name: str) -> set[str]:
    try:
        return {c["name"] for c in inspector.get_columns(table_name, schema="public")}
    except Exception:
        return {c["name"] for c in inspector.get_columns(table_name)}


def _ensure_assets_active_column(conn, inspector) -> set[str]:
    asset_cols = _safe_columns(inspector, "assets")
    if "ativo" in asset_cols or "active" in asset_cols:
        return asset_cols
    try:
        from sqlalchemy import text

        conn.execute(text("ALTER TABLE assets ADD COLUMN IF NOT EXISTS ativo BOOLEAN DEFAULT TRUE"))
        conn.commit()
        if hasattr(inspector, "clear_cache"):
            inspector.clear_cache()
        return _safe_columns(inspector, "assets")
    except Exception as exc:
        logger.info("update_b3_quotes: nao foi possivel adicionar coluna ativo em assets: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return asset_cols


def _list_assets_to_update(conn, inspector_factory, apenas_desatualizados: bool):
    from sqlalchemy import text

    inspector = inspector_factory(conn)
    tables = _safe_table_names(inspector)
    if "assets" not in tables:
        raise RuntimeError("Tabela assets nao encontrada")
    if "asset_quotes" not in tables:
        raise RuntimeError("Tabela asset_quotes nao encontrada")

    asset_cols = _ensure_assets_active_column(conn, inspector)
    if not {"id", "ticker"}.issubset(asset_cols):
        raise RuntimeError("Tabela assets sem colunas obrigatorias id/ticker")

    currency_expr = "a.currency AS currency" if "currency" in asset_cols else (
        "a.moeda AS currency" if "moeda" in asset_cols else "'BRL' AS currency"
    )
    class_expr = 'a."class" AS asset_class' if "class" in asset_cols else (
        "a.classe AS asset_class" if "classe" in asset_cols else "NULL AS asset_class"
    )
    active_col = "ativo" if "ativo" in asset_cols else ("active" if "active" in asset_cols else None)
    active_filter = f"(a.{active_col} IS NULL OR a.{active_col} = TRUE)" if active_col else "TRUE"
    stale_filter = """
      AND NOT EXISTS (
        SELECT 1 FROM asset_quotes aq
        WHERE aq.asset_id = a.id
          AND aq.timestamp >= NOW() - INTERVAL '2 days'
      )
    """ if apenas_desatualizados else ""

    rows = conn.execute(text(f"""
        SELECT CAST(a.id AS TEXT) AS id, a.ticker, {currency_expr}, {class_expr}
        FROM assets a
        WHERE {active_filter}
        {stale_filter}
        ORDER BY a.ticker
    """)).fetchall()
    return [r for r in rows if _is_quote_eligible(r)], active_col


def _is_quote_eligible(row) -> bool:
    ticker = str(getattr(row, "ticker", "") or "").strip().upper()
    currency = str(getattr(row, "currency", "") or "BRL").strip().upper()
    asset_class = str(getattr(row, "asset_class", "") or "").strip().lower()

    if not ticker or " " in ticker or asset_class in _NON_MARKET_CLASSES:
        return False

    if currency == "BRL":
        if not re.fullmatch(r"[A-Z]{4}[0-9]{1,2}", ticker):
            return False
        return ticker.endswith(_B3_REGULAR_SUFFIXES)

    return re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", ticker) is not None


def _download_history(yf, ticker_yf: str, periodo: str):
    return yf.download(
        ticker_yf,
        period=periodo,
        progress=False,
        auto_adjust=True,
        actions=False,
    )


def run(periodo: str = "5d", apenas_desatualizados: bool = True) -> dict:
    """
    Baixa cotacoes via yfinance e faz UPSERT em asset_quotes.

    apenas_desatualizados=True atualiza somente ativos sem cotacao recente.
    """
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

    try:
        import yfinance as yf
    except ImportError:
        result["status"] = "failed"
        result["error_message"] = "yfinance nao instalado"
        return result

    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy import text

    from data_pipeline.utils.db_utils import get_pipeline_engine

    engine = get_pipeline_engine()
    if engine is None:
        result["status"] = "failed"
        result["error_message"] = "Banco nao conectado"
        return result

    try:
        with engine.connect() as conn:
            rows, active_col = _list_assets_to_update(conn, sa_inspect, apenas_desatualizados)
    except Exception as exc:
        result["status"] = "failed"
        result["error_message"] = f"Erro ao listar ativos: {exc}"
        return result

    if not rows:
        logger.info("update_b3_quotes: nenhum ativo para atualizar")
        result["status"] = "skipped"
        return result

    sql_upsert = text("""
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
        currency = getattr(r, "currency", None) or "BRL"
        ticker_yf = f"{r.ticker}.SA" if currency == "BRL" else r.ticker
        try:
            hist = _download_history(yf, ticker_yf, periodo)
            if hist.empty:
                # Ativos pouco liquidos podem ficar sem negocio em 5 dias.
                # Antes de marcar como inativo, valida em uma janela maior.
                hist = _download_history(yf, ticker_yf, "1y")

            if hist.empty:
                logger.info("update_b3_quotes: sem dados para %s", ticker_yf)
                if active_col:
                    with engine.begin() as conn:
                        conn.execute(
                            text(f"UPDATE assets SET {active_col} = FALSE WHERE id = :id"),
                            {"id": r.id},
                        )
                continue

            if isinstance(hist.columns, __import__("pandas").MultiIndex):
                hist.columns = hist.columns.get_level_values(0)

            records = []
            for ts, row_data in hist.iterrows():
                close_val = float(row_data.get("Close", 0) or 0)
                if close_val > 0:
                    records.append({
                        "asset_id": r.id,
                        "ts": ts.to_pydatetime(),
                        "open": float(row_data.get("Open", 0) or 0) or None,
                        "high": float(row_data.get("High", 0) or 0) or None,
                        "low": float(row_data.get("Low", 0) or 0) or None,
                        "close": close_val,
                        "volume": float(row_data.get("Volume", 0) or 0) or None,
                    })

            if records:
                with engine.begin() as conn:
                    conn.execute(sql_upsert, records)
                result["records_inserted"] += len(records)

            time.sleep(0.3)

        except Exception as exc:
            logger.warning("update_b3_quotes: erro em %s: %s", ticker_yf, exc)
            result["records_failed"] += 1

    if result["records_failed"] > 0 and result["records_inserted"] == 0:
        result["status"] = "failed"
        result["error_message"] = f"{result['records_failed']} ativos falharam"
    elif result["records_failed"] > 0:
        result["status"] = "partial_success"

    return result
