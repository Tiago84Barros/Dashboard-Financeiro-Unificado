"""Enriquecimento reproduzível do warehouse americano, sem chamadas de rede.

Consolida identidade analítica, lineage/quality status, market cap PIT derivado e
métricas normalizadas. Todas as rotinas são idempotentes e preservam os dados
brutos; nenhuma delas apaga observações históricas.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import text

from core.us_methodology import US_FUNDAMENTAL_SCORE_VERSION


def classify_assets(engine) -> dict:
    """Separa universo analisável, pendências e instrumentos não operacionais."""
    sql_match = """
        WITH statement_map AS (
            SELECT symbol, MIN(company_id) AS company_id
            FROM (
                SELECT symbol, company_id FROM market_us.income_statements
                UNION ALL SELECT symbol, company_id FROM market_us.balance_sheets
                UNION ALL SELECT symbol, company_id FROM market_us.cash_flow_statements
            ) s
            WHERE symbol IS NOT NULL
            GROUP BY symbol HAVING COUNT(DISTINCT company_id) = 1
        )
        UPDATE market_us.assets a
        SET company_id = sm.company_id, updated_at = NOW()
        FROM statement_map sm
        WHERE a.company_id IS NULL AND a.symbol = sm.symbol
    """
    sql_status = """
        UPDATE market_us.assets a SET
            analysis_status = CASE
                WHEN lower(COALESCE(a.security_type, 'common')) IN ('etf','fund','spac')
                    THEN 'excluded'
                WHEN a.company_id IS NULL THEN 'unresolved'
                WHEN EXISTS (SELECT 1 FROM market_us.income_statements i
                             WHERE i.company_id=a.company_id)
                 AND EXISTS (SELECT 1 FROM market_us.balance_sheets b
                             WHERE b.company_id=a.company_id)
                 AND EXISTS (SELECT 1 FROM market_us.cash_flow_statements c
                             WHERE c.company_id=a.company_id)
                    THEN 'eligible'
                ELSE 'pending'
            END,
            status_reason = CASE
                WHEN lower(COALESCE(a.security_type, 'common')) IN ('etf','fund','spac')
                    THEN 'instrumento sem empresa operacional'
                WHEN a.company_id IS NULL THEN 'sem vínculo CIK/demonstrações'
                WHEN EXISTS (SELECT 1 FROM market_us.income_statements i
                             WHERE i.company_id=a.company_id)
                 AND EXISTS (SELECT 1 FROM market_us.balance_sheets b
                             WHERE b.company_id=a.company_id)
                 AND EXISTS (SELECT 1 FROM market_us.cash_flow_statements c
                             WHERE c.company_id=a.company_id)
                    THEN NULL
                ELSE 'demonstrações incompletas'
            END,
            classified_at = NOW()
    """
    with engine.begin() as conn:
        matched = conn.execute(text(sql_match)).rowcount
        conn.execute(text(sql_status))
        rows = conn.execute(text(
            "SELECT analysis_status, COUNT(*) FROM market_us.assets GROUP BY 1"
        )).fetchall()
    return {"matched": int(matched or 0), "statuses": {r[0]: int(r[1]) for r in rows}}


def derive_market_cap_history(engine) -> dict:
    """Calcula market cap mensal PIT usando preço e ações conhecidas na data."""
    sql = """
        INSERT INTO market_us.market_cap_history (symbol, date, market_cap, source)
        SELECT p.symbol, p.month_end,
               p.adjusted_close * sh.shares_outstanding,
               'derived_price_x_pit_shares'
        FROM market_us.prices_monthly p
        JOIN LATERAL (
            SELECT b.shares_outstanding
            FROM market_us.balance_sheets b
            WHERE b.symbol=p.symbol
              AND b.shares_outstanding > 0
              AND b.available_at IS NOT NULL
              AND b.available_at <= p.month_end
              AND b.quality_status <> 'rejected'
            ORDER BY b.available_at DESC, b.reference_date DESC
            LIMIT 1
        ) sh ON TRUE
        WHERE p.adjusted_close > 0
        ON CONFLICT (symbol, date) DO UPDATE SET
            market_cap=EXCLUDED.market_cap, source=EXCLUDED.source,
            ingested_at=NOW()
    """
    with engine.begin() as conn:
        changed = conn.execute(text(sql)).rowcount
        total, symbols = conn.execute(text(
            "SELECT COUNT(*), COUNT(DISTINCT symbol) FROM market_us.market_cap_history"
        )).one()
    return {"changed": int(changed or 0), "rows": int(total), "symbols": int(symbols)}


def promote_lineage_and_quality(engine) -> dict:
    """Preenche versão do parser e promove apenas observações que passam checks."""
    with engine.begin() as conn:
        for table in ("income_statements", "balance_sheets", "cash_flow_statements"):
            conn.execute(text(f"""
                UPDATE market_us.{table}
                SET source_version = CASE
                    WHEN source='sec_edgar' THEN 'companyfacts-parser-v1-legacy'
                    WHEN source='fmp' THEN 'fmp-normalizer-v1-legacy'
                    ELSE COALESCE(source_version, source || '-legacy') END
                WHERE source_version IS NULL
            """))

        conn.execute(text("""
            UPDATE market_us.income_statements SET quality_status = CASE
              WHEN reference_date > CURRENT_DATE OR available_at < reference_date THEN 'flagged'
              WHEN available_at IS NOT NULL AND content_hash IS NOT NULL
                   AND (revenue IS NOT NULL OR net_income IS NOT NULL) THEN 'validated'
              ELSE 'raw' END
        """))
        conn.execute(text("""
            UPDATE market_us.balance_sheets SET quality_status = CASE
              WHEN reference_date > CURRENT_DATE OR available_at < reference_date THEN 'flagged'
              WHEN total_assets IS NOT NULL AND total_liabilities IS NOT NULL
                   AND total_equity IS NOT NULL
                   AND ABS(total_assets-(total_liabilities+total_equity)) /
                       GREATEST(ABS(total_assets), ABS(total_liabilities+total_equity), 1) > 0.02
                   THEN 'flagged'
              WHEN available_at IS NOT NULL AND content_hash IS NOT NULL
                   AND total_assets IS NOT NULL THEN 'validated'
              ELSE 'raw' END
        """))
        conn.execute(text("""
            UPDATE market_us.cash_flow_statements SET quality_status = CASE
              WHEN reference_date > CURRENT_DATE OR available_at < reference_date THEN 'flagged'
              WHEN operating_cash_flow IS NOT NULL AND capex IS NOT NULL
                   AND free_cash_flow IS NOT NULL
                   AND ABS((operating_cash_flow+capex)-free_cash_flow) /
                       GREATEST(ABS(free_cash_flow), ABS(operating_cash_flow+capex), 1) > 0.02
                   THEN 'flagged'
              WHEN available_at IS NOT NULL AND content_hash IS NOT NULL
                   AND operating_cash_flow IS NOT NULL THEN 'validated'
              ELSE 'raw' END
        """))
        summary = conn.execute(text("""
            SELECT quality_status, COUNT(*) FROM (
              SELECT quality_status FROM market_us.income_statements
              UNION ALL SELECT quality_status FROM market_us.balance_sheets
              UNION ALL SELECT quality_status FROM market_us.cash_flow_statements
            ) q GROUP BY quality_status
        """)).fetchall()
    return {r[0]: int(r[1]) for r in summary}


def persist_current_metrics(engine) -> dict:
    """Materializa as métricas correntes calculadas pelo mesmo motor do score."""
    import core.us_read as ur

    frame = ur.load_scoring_frame()
    if frame is None or frame.empty:
        return {"rows": 0, "symbols": 0}
    identity = {}
    with engine.connect() as conn:
        for r in conn.execute(text("""
            SELECT DISTINCT ON (a.symbol) a.symbol, c.id,
                   COALESCE(i.fiscal_year,0), COALESCE(i.reference_date,CURRENT_DATE)
            FROM market_us.assets a JOIN market_us.companies c ON c.id=a.company_id
            LEFT JOIN LATERAL (
              SELECT fiscal_year, reference_date FROM market_us.income_statements x
              WHERE x.company_id=c.id AND x.period='annual'
              ORDER BY fiscal_year DESC LIMIT 1
            ) i ON TRUE
            ORDER BY a.symbol, c.id
        """)):
            identity[r[0]] = (int(r[1]), int(r[2]), r[3])

    excluded = {"symbol", "name", "sector", "industry", "score", "coverage"}
    rows = []
    for record in frame.to_dict("records"):
        sym = record.get("symbol")
        ident = identity.get(sym)
        if not ident:
            continue
        cid, fy, ref = ident
        for name, value in record.items():
            if name in excluded or name.startswith("_") or value is None:
                continue
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            if value != value:
                continue
            rows.append({"company_id": cid, "symbol": sym, "period": "spot",
                         "fiscal_year": fy, "fiscal_quarter": 0,
                         "metric_name": name, "metric_value": value,
                         "unit": "ratio", "reference_date": ref,
                         "available_at": date.today(),
                         "calculation_method": f"us_metrics/{US_FUNDAMENTAL_SCORE_VERSION}",
                         "source": "derived", "quality_status": "validated"})
    if not rows:
        return {"rows": 0, "symbols": 0}
    sql = text("""
        INSERT INTO market_us.key_metrics
          (company_id,symbol,period,fiscal_year,fiscal_quarter,metric_name,
           metric_value,unit,reference_date,available_at,calculation_method,source,quality_status)
        VALUES
          (:company_id,:symbol,:period,:fiscal_year,:fiscal_quarter,:metric_name,
           :metric_value,:unit,:reference_date,:available_at,:calculation_method,:source,:quality_status)
        ON CONFLICT (company_id,period,fiscal_year,fiscal_quarter,metric_name)
        DO UPDATE SET metric_value=EXCLUDED.metric_value, unit=EXCLUDED.unit,
          reference_date=EXCLUDED.reference_date, available_at=EXCLUDED.available_at,
          calculation_method=EXCLUDED.calculation_method, source=EXCLUDED.source,
          quality_status=EXCLUDED.quality_status
    """)
    with engine.begin() as conn:
        conn.execute(sql, rows)
    return {"rows": len(rows), "symbols": len({r["symbol"] for r in rows})}


def enrich_warehouse(engine) -> dict:
    from data_pipeline.us.scoring_history import derive_prices_monthly

    return {
        "assets": classify_assets(engine),
        "lineage_quality": promote_lineage_and_quality(engine),
        "prices_monthly": derive_prices_monthly(engine),
        "market_caps": derive_market_cap_history(engine),
        "key_metrics": persist_current_metrics(engine),
    }
