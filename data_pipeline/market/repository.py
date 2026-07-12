"""
data_pipeline/market/repository.py
Persistência no schema market.* com UPSERT idempotente (ON CONFLICT).
Inclui salvamento do payload bruto e log de qualidade.
"""
from __future__ import annotations

import json
import logging
import hashlib

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
             "score_version", "score_calculated_at", "metrics_fetched_at", "mandate",
             "administrator_name", "administrator_cnpj"),
    # snapshot mensal de score+inputs (migração 020, auditoria FII 2026-07):
    # as colunas de score só atualizam quando presentes na linha — o backfill
    # CVM e o snapshot de score preservam as colunas um do outro.
    "fii_metrics_monthly": ("vpa", "patrimonio_liquido", "num_cotistas",
                            "dy_patrimonial_mes", "pct_imoveis", "pct_papel",
                            "pct_caixa", "pct_fundos",
                            "score", "score_version", "price", "dy_12m",
                            "pvp", "liquidez_diaria"),
    "fii_imoveis": ("area_m2", "vacancia", "cidade", "uf", "regiao",
                    "segmento_imovel", "pct_receita", "fonte"),
    "fii_metric_observations": ("value_numeric", "value_text", "value_json",
                                "source_url", "raw_payload_id", "quality_status",
                                "metadata_json", "observed_at"),
    "fii_exposures": ("exposure_weight", "raw_payload_id", "metadata_json"),
    "fii_universe_history": ("active_status", "successor_ticker", "source", "metadata_json"),
    "fii_score_snapshots": ("formula_version", "fii_type", "type_score", "confidence",
                            "coverage", "components_json", "inputs_json",
                            "missing_metrics_json", "publication_status",
                            "publication_reasons_json", "validation_run_id"),
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
    "fii_metric_observations": "ticker, metric_name, reference_date, available_at, vintage, source",
    "fii_exposures": "ticker, exposure_type, exposure_name, reference_date, available_at, vintage, source",
    "fii_universe_history": "ticker, reference_date, available_at",
    "fii_score_snapshots": "ticker, reference_date, available_at, methodology_version",
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
    "fii_metric_observations": ("ticker", "metric_name", "reference_date", "available_at", "vintage", "source"),
    "fii_exposures": ("ticker", "exposure_type", "exposure_name", "reference_date", "available_at", "vintage", "source"),
    "fii_universe_history": ("ticker", "reference_date", "available_at"),
    "fii_score_snapshots": ("ticker", "reference_date", "available_at", "methodology_version"),
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
    assignments = []
    for c in upd:
        if c not in cols:
            continue
        if table == "assets" and c == "asset_type":
            # Não rebaixa uma classificação forte já confirmada por pipelines
            # especializados (FII/ETF/BDR) para inferências fracas de quote.
            assignments.append(
                '"asset_type" = CASE '
                "WHEN market.assets.asset_type IN ('fii','etf','bdr') "
                " AND EXCLUDED.asset_type IN ('stock','unit','other') "
                "THEN market.assets.asset_type ELSE EXCLUDED.asset_type END"
            )
        else:
            assignments.append(f'"{c}" = EXCLUDED."{c}"')
    setlist = ", ".join(assignments)
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


def append_metric_vintages(conn, rows: list[dict]) -> int:
    """Acrescenta versões imutáveis quando uma métrica efetivamente muda.

    Bancos sem a migration 021 continuam operando, mas retornam zero. Para
    demonstrações anuais, ``available_at`` usa o maior ``first_seen_at`` das
    três demonstrações do exercício; isso é um proxy conservador da data em
    que o conjunto necessário ao cálculo estava disponível.
    """
    if not rows:
        return 0
    exists = conn.execute(text("""
        SELECT to_regclass('market.calculated_metric_vintages') IS NOT NULL
    """)).scalar()
    if not exists:
        return 0

    deduped = _dedup("calculated_metrics", rows)
    tickers = sorted({str(row.get("ticker") or "") for row in deduped})
    availability: dict[tuple[str, int], object] = {}
    if tickers:
        available_rows = conn.execute(text("""
            SELECT ticker, year, MAX(first_seen_at) AS available_at
            FROM (
                SELECT ticker, year, first_seen_at FROM market.income_statements
                 WHERE period='annual' AND ticker = ANY(:tickers)
                UNION ALL
                SELECT ticker, year, first_seen_at FROM market.balance_sheets
                 WHERE period='annual' AND ticker = ANY(:tickers)
                UNION ALL
                SELECT ticker, year, first_seen_at FROM market.cash_flow_statements
                 WHERE period='annual' AND ticker = ANY(:tickers)
            ) s
            GROUP BY ticker, year
        """), {"tickers": tickers}).fetchall()
        availability = {(str(t), int(y)): value for t, y, value in available_rows}

    has_cutover = conn.execute(text("""
        SELECT to_regclass('market.pipeline_cutovers') IS NOT NULL
    """)).scalar()
    cutoff = (
        conn.execute(text("""
            SELECT cutoff_at FROM market.pipeline_cutovers
            WHERE name='point_in_time_v1'
        """)).scalar()
        if has_cutover else None
    )
    incoming = []
    for row in deduped:
        ticker = str(row.get("ticker") or "")
        period = str(row.get("period") or "")
        year = int(row.get("year") or 0)
        available_at = availability.get((ticker, year)) if period == "annual" else None
        quality = (
            "first_seen_proxy"
            if available_at is not None and cutoff is not None and available_at > cutoff
            else "migration_baseline"
        )
        incoming.append({
            **row,
            "quarter": int(row.get("quarter") or 0),
            "available_at": available_at.isoformat() if available_at else None,
            "availability_quality": quality,
        })

    result = conn.execute(text("""
        WITH incoming AS (
            SELECT *
            FROM jsonb_to_recordset(CAST(:payload AS jsonb)) AS i(
                ticker text, period text, year int, quarter int,
                metric_name text, metric_value numeric,
                calculation_method text, source text, confidence_score numeric,
                available_at timestamptz, availability_quality text
            )
        )
        INSERT INTO market.calculated_metric_vintages (
            ticker, period, year, quarter, metric_name, metric_value,
            calculation_method, source, confidence_score,
            available_at, availability_quality
        )
        SELECT i.ticker, i.period, i.year, i.quarter, i.metric_name,
               i.metric_value, i.calculation_method, i.source,
               i.confidence_score, COALESCE(i.available_at, NOW()),
               i.availability_quality
        FROM incoming i
        LEFT JOIN LATERAL (
            SELECT v.id, v.metric_value, v.calculation_method, v.confidence_score
            FROM market.calculated_metric_vintages v
            WHERE v.ticker=i.ticker AND v.period=i.period
              AND v.year=i.year AND v.quarter=i.quarter
              AND v.metric_name=i.metric_name
            ORDER BY v.recorded_at DESC, v.id DESC
            LIMIT 1
        ) latest ON TRUE
        WHERE latest.id IS NULL
           OR latest.metric_value IS DISTINCT FROM i.metric_value
           OR latest.calculation_method IS DISTINCT FROM i.calculation_method
           OR latest.confidence_score IS DISTINCT FROM i.confidence_score
    """), {
        "payload": json.dumps(incoming, ensure_ascii=False, default=str),
    })
    return max(int(result.rowcount or 0), 0)


def replace_metric_snapshot(
    conn,
    ticker: str,
    rows: list[dict],
    periods: tuple[str, ...] = ("ttm", "annual"),
) -> int:
    """Substitui atomicamente o snapshot calculado e remove métricas obsoletas.

    UPSERT isolado não remove valores que deixaram de ser calculáveis. Isso
    preservava valuations aproximados antigos mesmo depois de o ETL passar a
    exigir ações históricas confiáveis.
    """
    filtered = [
        row for row in rows
        if str(row.get("period") or "") in periods
    ]
    keys = [{
        "period": str(row.get("period") or ""),
        "year": int(row.get("year") or 0),
        "quarter": int(row.get("quarter") or 0),
        "metric_name": str(row.get("metric_name") or ""),
    } for row in filtered]
    payload = json.dumps(keys, ensure_ascii=False)
    result = conn.execute(text("""
        DELETE FROM market.calculated_metrics cm
        WHERE cm.ticker=:ticker
          AND cm.period = ANY(:periods)
          AND NOT EXISTS (
              SELECT 1
              FROM jsonb_to_recordset(CAST(:keys AS jsonb))
                   AS k(period text, year int, quarter int, metric_name text)
              WHERE k.period=cm.period AND k.year=cm.year
                AND k.quarter=cm.quarter AND k.metric_name=cm.metric_name
          )
    """), {
        "ticker": str(ticker).upper(),
        "periods": list(periods),
        "keys": payload,
    })
    upsert(conn, "calculated_metrics", filtered)
    return max(int(result.rowcount or 0), 0)


def save_raw_payload(conn, ticker, endpoint, payload, status="success", error=None, *,
                     request_params: dict | None = None,
                     response_headers: dict | None = None,
                     http_status: int | None = None,
                     collected_at=None,
                     source_published_at=None,
                     request_fingerprint: str | None = None) -> int | None:
    """Persiste payload bruto de forma idempotente e retorna sua proveniência.

    A mesma resposta para a mesma requisição reutiliza o id anterior. Uma
    resposta diferente cria nova versão e aponta para a anterior por
    ``supersedes_id``. Bancos sem a migration 024 usam o formato legado.
    """
    payload_text = (json.dumps(payload, ensure_ascii=False, default=str,
                               sort_keys=True, separators=(",", ":"))
                    if payload is not None else None)
    params_text = json.dumps(request_params or {}, ensure_ascii=False, default=str,
                             sort_keys=True, separators=(",", ":"))
    semantic_payload = dict(payload) if isinstance(payload, dict) else payload
    if isinstance(semantic_payload, dict):
        semantic_payload.pop("requestedAt", None)
        semantic_payload.pop("took", None)
        pagination = semantic_payload.get("pagination")
        if isinstance(pagination, dict):
            semantic_payload["pagination"] = {
                key: value for key, value in pagination.items()
                if key not in {"fetchedPages"}
            }
    semantic_text = (json.dumps(semantic_payload, ensure_ascii=False, default=str,
                                sort_keys=True, separators=(",", ":"))
                     if semantic_payload is not None else None)
    content_sha = hashlib.sha256(semantic_text.encode("utf-8")).hexdigest() if semantic_text else None
    fingerprint = request_fingerprint or hashlib.sha256(
        f"{endpoint}|{ticker or ''}|{params_text}".encode("utf-8")).hexdigest()
    db_cols = _db_cols(conn, "brapi_raw_payloads")
    if "content_sha256" not in db_cols:
        res = conn.execute(text("""
            INSERT INTO market.brapi_raw_payloads
              (ticker, endpoint, payload_json, source, request_status, error_message)
            VALUES (:tk, :ep, CAST(:pl AS jsonb), 'brapi.dev', :st, :err)
            RETURNING id
        """), {"tk": ticker, "ep": endpoint, "pl": payload_text,
               "st": status, "err": (str(error)[:500] if error else None)})
        value = res.scalar()
        return int(value) if value is not None else None

    if status == "success" and content_sha:
        existing = conn.execute(text("""
            SELECT id FROM market.brapi_raw_payloads
            WHERE endpoint=:ep AND request_fingerprint=:fp
              AND content_sha256=:sha AND request_status='success'
            ORDER BY id DESC LIMIT 1
        """), {"ep": endpoint, "fp": fingerprint, "sha": content_sha}).scalar()
        if existing is not None:
            return int(existing)
    previous = conn.execute(text("""
        SELECT id FROM market.brapi_raw_payloads
        WHERE endpoint=:ep AND request_fingerprint=:fp AND request_status='success'
        ORDER BY collected_at DESC NULLS LAST, id DESC LIMIT 1
    """), {"ep": endpoint, "fp": fingerprint}).scalar()
    res = conn.execute(text("""
        INSERT INTO market.brapi_raw_payloads (
          ticker, endpoint, payload_json, source, request_status, error_message,
          request_fingerprint, request_params_json, response_headers_json,
          content_sha256, http_status, source_published_at, collected_at,
          supersedes_id, revision_detected
        ) VALUES (
          :tk, :ep, CAST(:pl AS jsonb), 'brapi.dev', :st, :err,
          :fp, CAST(:params AS jsonb), CAST(:headers AS jsonb),
          :sha, :http, :published, COALESCE(:collected, now()), :previous,
          CASE WHEN :previous IS NULL THEN false ELSE true END
        )
        ON CONFLICT DO NOTHING
        RETURNING id
    """), {
        "tk": ticker, "ep": endpoint, "pl": payload_text, "st": status,
        "err": (str(error)[:500] if error else None), "fp": fingerprint,
        "params": params_text,
        "headers": json.dumps(response_headers or {}, ensure_ascii=False, default=str),
        "sha": content_sha, "http": http_status, "published": source_published_at,
        "collected": collected_at, "previous": previous,
    })
    value = res.scalar()
    if value is not None:
        return int(value)
    if content_sha:
        value = conn.execute(text("""
            SELECT id FROM market.brapi_raw_payloads
            WHERE endpoint=:ep AND request_fingerprint=:fp AND content_sha256=:sha
            ORDER BY id DESC LIMIT 1
        """), {"ep": endpoint, "fp": fingerprint, "sha": content_sha}).scalar()
    return int(value) if value is not None else None


def record_lineage_for_raw_payload(conn, raw_payload_id: int | None) -> int:
    """Liga o payload às métricas e exposições materializadas por ele."""
    if raw_payload_id is None or not conn.execute(text(
            "SELECT to_regclass('market.fii_lineage_edges') IS NOT NULL")).scalar():
        return 0
    result = conn.execute(text("""
        INSERT INTO market.fii_lineage_edges
            (parent_type, parent_id, child_type, child_id, relation)
        SELECT 'brapi_raw_payload', CAST(:raw AS text), 'fii_metric_observation',
               CAST(id AS text), 'normalized_into'
        FROM market.fii_metric_observations WHERE raw_payload_id=:raw
        UNION ALL
        SELECT 'brapi_raw_payload', CAST(:raw AS text), 'fii_exposure',
               CAST(id AS text), 'normalized_into'
        FROM market.fii_exposures WHERE raw_payload_id=:raw
        ON CONFLICT DO NOTHING
    """), {"raw": int(raw_payload_id)})
    return max(int(result.rowcount or 0), 0)


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
