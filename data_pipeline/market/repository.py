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
    # point-in-time (019): period_end_date/raw_payload_id ATUALIZAM no conflito;
    # first_seen_at NUNCA entra aqui nem nas linhas — o DEFAULT NOW() preenche
    # no primeiro INSERT e re-ingestões não apagam o histórico de disponibilidade.
    "income_statements": ("revenue", "gross_profit", "ebit", "ebitda", "net_income", "eps",
                          "period_end_date", "raw_payload_id"),
    "balance_sheets": ("total_assets", "total_liabilities", "equity", "cash",
                       "gross_debt", "net_debt", "current_assets", "current_liabilities",
                       "period_end_date", "raw_payload_id"),
    "cash_flow_statements": ("operating_cash_flow", "investing_cash_flow",
                             "financing_cash_flow", "capex", "free_cash_flow",
                             "period_end_date", "raw_payload_id"),
    "dividends": ("source",),
    "macro_indicators": ("value", "source"),
    "calculated_metrics": ("metric_value", "calculation_method", "source", "confidence_score"),
    "ticker_cvm": ("codigo_cvm",),
    "fiis": ("name", "segmento", "price", "pvp", "dy_12m", "liquidez_diaria", "score",
             "cnpj", "isin", "segmento_cvm", "tipo", "tipo_gestao", "patrimonio_liquido",
             "vpa", "num_cotistas", "pct_imoveis", "pct_papel", "pct_caixa", "pct_fundos",
             "cvm_ref_date", "vacancia", "vacancia_ref_date", "num_imoveis",
             "score_version", "score_calculated_at", "metrics_fetched_at"),
    "fii_metrics_monthly": ("vpa", "patrimonio_liquido", "num_cotistas",
                            "dy_patrimonial_mes", "pct_imoveis", "pct_papel",
                            "pct_caixa", "pct_fundos"),
    "fii_imoveis": ("area_m2", "vacancia", "cidade", "uf", "regiao",
                    "segmento_imovel", "pct_receita", "fonte"),
}
_CONFLICT = {
    "ticker_cvm": "ticker",
    "companies": "codigo_cvm",
    "assets": "ticker",
    "historical_prices": "ticker, date",
    "income_statements": "ticker, period, year, quarter",
    "balance_sheets": "ticker, period, year, quarter",
    "cash_flow_statements": "ticker, period, year, quarter",
    "dividends": "ticker, event_date, type, amount",
    "macro_indicators": "indicator, date",
    "calculated_metrics": "ticker, period, year, quarter, metric_name",
    "fiis": "ticker",
    "fii_metrics_monthly": "ticker, ref_month",
    "fii_imoveis": "ticker, nome_imovel",
}


# chaves naturais por tabela (para checagem de duplicidade pós-upsert)
_NATURAL_KEY = {
    "companies": ("codigo_cvm",),
    "assets": ("ticker",),
    "historical_prices": ("ticker", "date"),
    "income_statements": ("ticker", "period", "year", "quarter"),
    "balance_sheets": ("ticker", "period", "year", "quarter"),
    "cash_flow_statements": ("ticker", "period", "year", "quarter"),
    "dividends": ("ticker", "event_date", "type", "amount"),
    "macro_indicators": ("indicator", "date"),
    "calculated_metrics": ("ticker", "period", "year", "quarter", "metric_name"),
    "ticker_cvm": ("ticker",),
    "fiis": ("ticker",),
    "fii_metrics_monthly": ("ticker", "ref_month"),
    "fii_imoveis": ("ticker", "nome_imovel"),
}


def market_table_stats(conn) -> dict:
    """Contagem e duplicidade (count - count distinct da chave) por tabela market.*."""
    out: dict[str, dict] = {}
    for table in ("companies", "assets", "historical_prices", "income_statements",
                  "balance_sheets", "cash_flow_statements", "dividends",
                  "calculated_metrics", "macro_indicators", "ticker_cvm",
                  "brapi_raw_payloads", "data_quality_logs"):
        try:
            total = conn.execute(text(f"SELECT count(*) FROM market.{table}")).scalar() or 0
        except Exception:
            out[table] = {"total": None, "duplicados": None}
            continue
        dups = None
        key = _NATURAL_KEY.get(table)
        if key:
            kcols = ", ".join(f'"{c}"' for c in key)
            try:
                distinct = conn.execute(text(
                    f"SELECT count(*) FROM (SELECT 1 FROM market.{table} GROUP BY {kcols}) s"
                )).scalar() or 0
                dups = int(total) - int(distinct)
            except Exception:
                dups = None
        out[table] = {"total": int(total), "duplicados": dups}
    return out


def schema_exists(conn) -> bool:
    return bool(conn.execute(text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name='market')"
    )).scalar())


def _row_key(table: str, row: dict):
    """Chave natural da linha (para dedup intra-lote). dividends usa event_date
    derivado = COALESCE(payment_date, ex_date) e amount ARREDONDADO à escala da
    coluna NUMERIC(18,6) — senão dois floats que arredondam ao mesmo valor viram
    a mesma chave no banco mas escapam do dedup, quebrando o ON CONFLICT."""
    if table == "dividends":
        amt = row.get("amount")
        try:
            amt = round(float(amt), 6)
        except (TypeError, ValueError):
            amt = None
        return (row.get("ticker"), row.get("payment_date") or row.get("ex_date"),
                row.get("type"), amt)
    key = _NATURAL_KEY.get(table)
    return tuple(row.get(c) for c in key) if key else None


def _dedup(table: str, rows: list[dict]) -> list[dict]:
    """Remove duplicatas pela chave natural dentro do lote (último vence).
    Evita o erro 'ON CONFLICT cannot affect row a second time' no execute_values."""
    if table not in _NATURAL_KEY and table != "dividends":
        return rows
    seen: dict = {}
    for r in rows:
        seen[_row_key(table, r)] = r
    return list(seen.values())


# cache de colunas existentes por tabela (1 consulta por processo) — permite
# que o código novo rode contra banco SEM a migração 019 aplicada: colunas
# ausentes no banco são simplesmente omitidas do INSERT em vez de quebrá-lo.
_DB_COLS_CACHE: dict[str, set[str]] = {}


def reset_db_cols_cache() -> None:
    """Invalida o cache de colunas — chamar no início de cada run do
    pipeline: a migração 019 pode ter sido aplicada com o processo vivo."""
    _DB_COLS_CACHE.clear()


def _db_cols(conn, table: str) -> set[str]:
    cached = _DB_COLS_CACHE.get(table)
    if cached is not None:
        return cached
    try:
        res = conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='market' AND table_name=:t"), {"t": table}).fetchall()
        cols = {r[0] for r in res}
    except Exception as exc:
        # Fix revisão 2026-07: NÃO cachear falha transitória — set() vazio
        # ficava para sempre e desativava o filtro exatamente no cenário
        # (banco sem a migração 019) para o qual ele existe. Sem cache,
        # a próxima chamada re-tenta a introspecção.
        logger.warning("_db_cols %s: introspecção falhou (%s) — sem filtro "
                       "nesta chamada, retry na próxima", table, str(exc)[:120])
        return set()
    _DB_COLS_CACHE[table] = cols
    return cols


def _upsert(conn, table: str, rows: list[dict], page_size: int = 500) -> int:
    """
    UPSERT em LOTE via psycopg2.execute_values (centenas de linhas por statement,
    na mesma transação do SQLAlchemy). Deduplica por chave natural antes (chaves
    repetidas no mesmo lote quebram ON CONFLICT). Fallback só se execute_values
    não existir (ImportError) — erros de SQL reais propagam.
    """
    if not rows:
        return 0
    rows = _dedup(table, rows)
    cols = list(rows[0].keys())
    db_cols = _db_cols(conn, table)
    if db_cols:
        ausentes = [c for c in cols if c not in db_cols]
        if ausentes:
            logger.info("upsert %s: colunas %s ausentes no banco (migração "
                        "pendente?) — omitidas do INSERT", table, ausentes)
            cols = [c for c in cols if c in db_cols]
            if not cols:
                return 0
    collist = ", ".join(f'"{c}"' for c in cols)
    upd = _UPDATE_COLS[table]
    setlist = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in upd if c in cols)
    conflict = _CONFLICT[table]
    action = f"DO UPDATE SET {setlist}" if setlist else "DO NOTHING"

    vals = ", ".join(f":{c}" for c in cols)
    single_sql = (f'INSERT INTO market.{table} ({collist}) VALUES ({vals}) '
                  f'ON CONFLICT ({conflict}) {action}')

    def _row_by_row():
        for r in rows:
            conn.execute(text(single_sql), r)

    try:
        from psycopg2.extras import execute_values
    except ImportError:  # lib ausente → linha-a-linha
        _row_by_row()
        return len(rows)

    batch_sql = (f'INSERT INTO market.{table} ({collist}) VALUES %s '
                 f'ON CONFLICT ({conflict}) {action}')
    values = [tuple(r.get(c) for c in cols) for r in rows]
    sp = conn.begin_nested()  # SAVEPOINT: isola falha do lote
    try:
        cur = conn.connection.cursor()
        try:
            execute_values(cur, batch_sql, values, page_size=page_size)
        finally:
            cur.close()
        sp.commit()
    except Exception as exc:  # rede de segurança: duplicata intra-lote imprevista
        sp.rollback()
        logger.warning("upsert %s: lote falhou (%s) — fallback linha-a-linha",
                       table, str(exc)[:120])
        _row_by_row()
    return len(rows)


def upsert(conn, table: str, rows: list[dict]) -> int:
    """Upsert genérico para uma tabela market.* conhecida."""
    if table not in _CONFLICT:
        raise ValueError(f"tabela desconhecida: {table}")
    return _upsert(conn, table, rows)


def save_raw_payload(conn, ticker, endpoint, payload, status="success", error=None) -> int | None:
    """Salva o payload bruto e retorna o id (proveniência p/ raw_payload_id)."""
    res = conn.execute(text("""
        INSERT INTO market.brapi_raw_payloads
          (ticker, endpoint, payload_json, source, request_status, error_message)
        VALUES (:tk, :ep, CAST(:pl AS jsonb), 'brapi.dev', :st, :err)
        RETURNING id
    """), {"tk": ticker, "ep": endpoint,
           "pl": json.dumps(payload, ensure_ascii=False, default=str) if payload is not None else None,
           "st": status, "err": (str(error)[:500] if error else None)})
    try:
        return int(res.scalar())
    except Exception:
        return None


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
    """
    Mapa ticker->codigo_cvm. Fonte primária: public.cvm_to_ticker (colunas
    "Ticker"/"CVM"); reforçado por public.docs_corporativos (codigo_cvm,ticker)
    para maximizar a cobertura de empresas.
    """
    out: dict[str, int] = {}
    try:  # fonte primária: market.ticker_cvm (CVM cad+FCA, muitos tickers/empresa)
        rows = conn.execute(text("SELECT ticker, codigo_cvm FROM market.ticker_cvm")).fetchall()
        for t, c in rows:
            if t and c is not None:
                out[str(t).upper().replace(".SA", "")] = int(c)
    except Exception:
        pass
    try:  # legado (não sobrescreve o primário)
        rows = conn.execute(text('SELECT "Ticker", "CVM" FROM public.cvm_to_ticker')).fetchall()
        for t, c in rows:
            if t and c is not None:
                out.setdefault(str(t).upper().replace(".SA", ""), int(c))
    except Exception as exc:
        logger.warning("load_cvm_to_ticker (cvm_to_ticker): %s", exc)
    try:
        rows = conn.execute(text(
            "SELECT DISTINCT ticker, codigo_cvm FROM public.docs_corporativos "
            "WHERE codigo_cvm IS NOT NULL AND ticker IS NOT NULL"
        )).fetchall()
        for t, c in rows:
            out.setdefault(str(t).upper().replace(".SA", ""), int(c))
    except Exception:
        pass
    return out
