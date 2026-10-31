"""Enriquecimento reproduzível do warehouse americano, sem chamadas de rede.

Consolida identidade analítica, lineage/quality status, market cap PIT derivado e
métricas normalizadas. Todas as rotinas são idempotentes e preservam os dados
brutos; nenhuma delas apaga observações históricas.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import text

from core.us_instrumento import (
    MOTIVO_OUTRO_TICKER_DA_EMISSORA,
    motivo_exclusao_ativo,
    ticker_principal_da_emissora,
)
from core.us_methodology import US_FUNDAMENTAL_SCORE_VERSION

# A-140: a regra de quem e ativo analisavel mora em core/us_instrumento.py --
# a leitura da vitrine precisa da MESMA regra, e duplica-la aqui garantiria que
# as duas divergissem na primeira alteracao.
instrument_exclusion_reason = motivo_exclusao_ativo


# EUA-A (auditoria 04/10/2026): o vínculo de `classify_assets` casa pelo símbolo
# das DEMONSTRAÇÕES e exige símbolo único. Duke Energy tinha as demonstrações
# gravadas sob DUKB (um título de dívida da própria Duke), então DUK ficava
# 'unresolved' com company_id nulo enquanto DUKB aparecia no ranking com nota
# 65,6; o mesmo com MCHP/MCHPP (preferencial, P/L 155). Medido no armazém em
# 04/10/2026: 3.863 ativos sem company_id, dos quais 590 têm CIK na SEC e a
# companhia já está em `companies` -- esses o CIK resolve sem rede de dados.
ERRO_SEM_CIK_SEC = "sem_cik_sec"
ERRO_CIK_SEM_EMPRESA = "cik_sem_empresa"


def link_assets_by_cik(engine, ticker_cik: dict[str, str]) -> dict:
    """Preenche `assets.company_id` pelo CIK que a SEC atribui ao ticker.

    `ticker_cik` é {símbolo: CIK de 10 dígitos} (`EdgarProvider.sec_ticker_cik_map`).
    Só toca ativo SEM vínculo, só liga a companhia que já existe em `companies`
    (não inventa empresa) e é idempotente. Não decide elegibilidade: o vínculo
    por CIK faz DUK e DUKB caírem na mesma companhia, e quem exclui o título de
    dívida é `classe_adicional_da_mesma_companhia`, na classificação.
    """
    pares = [{"s": str(k).upper(), "c": str(v)} for k, v in (ticker_cik or {}).items()
             if k and v]
    if not pares:
        return {"linked": 0, "map_size": 0}
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TEMP TABLE _sec_ticker_cik (symbol TEXT PRIMARY KEY, cik TEXT) "
            "ON COMMIT DROP"))
        conn.execute(text("INSERT INTO _sec_ticker_cik VALUES (:s, :c)"), pares)
        linked = conn.execute(text("""
            UPDATE market_us.assets a
            SET company_id = c.id, updated_at = NOW()
            FROM _sec_ticker_cik s
            JOIN (SELECT cik, MIN(id) AS id FROM market_us.companies
                  WHERE cik IS NOT NULL GROUP BY cik) c ON c.cik = s.cik
            WHERE a.company_id IS NULL AND a.symbol = s.symbol
        """)).rowcount
    return {"linked": int(linked or 0), "map_size": len(pares)}


def log_unlinked_assets(engine, ticker_cik: dict[str, str]) -> dict:
    """Grava em `ingestion_errors` o ativo que a classificação deixou 'unresolved'.

    Antes, 3.268 ativos sem vínculo (297 com mais de US$ 100 mi/dia) não deixavam
    rastro nenhum: a tabela só tinha empty_facts e erros de perfil. Dois tipos,
    porque a ação corretiva é diferente: `sem_cik_sec` (a SEC não lista o
    ticker; cheque manual) e `cik_sem_empresa` (a SEC lista, mas a companhia
    nunca foi ingerida; rodar a ingestão de fundamentos). Idempotente: não
    repete erro aberto do mesmo símbolo e tipo, e fecha o que passou a ter vínculo.
    """
    ciks = {str(k).upper(): str(v) for k, v in (ticker_cik or {}).items() if k and v}
    with engine.begin() as conn:
        pendentes = conn.execute(text(
            "SELECT symbol FROM market_us.assets "
            "WHERE analysis_status='unresolved' AND company_id IS NULL")).fetchall()
        conhecidos = {r[0] for r in conn.execute(text(
            "SELECT cik FROM market_us.companies WHERE cik IS NOT NULL"))}
        abertos = {(r[0], r[1]) for r in conn.execute(text(
            "SELECT symbol, error_type FROM market_us.ingestion_errors "
            "WHERE resolved = FALSE AND error_type IN (:a, :b)"),
            {"a": ERRO_SEM_CIK_SEC, "b": ERRO_CIK_SEM_EMPRESA})}
        novos = []
        for (sym,) in pendentes:
            cik = ciks.get(sym)
            if cik is None:
                tipo, msg = ERRO_SEM_CIK_SEC, "ticker ausente nas listagens da SEC"
            elif cik not in conhecidos:
                tipo = ERRO_CIK_SEM_EMPRESA
                msg = f"CIK {cik} listado pela SEC, companhia ainda não ingerida"
            else:
                continue
            if (sym, tipo) not in abertos:
                novos.append({"s": sym, "t": tipo, "m": msg})
        if novos:
            conn.execute(text(
                "INSERT INTO market_us.ingestion_errors "
                "(symbol, domain, error_type, attempts, message) "
                "VALUES (:s, 'assets', :t, 1, :m)"), novos)
        fechados = conn.execute(text(
            "UPDATE market_us.ingestion_errors e SET resolved = TRUE "
            "FROM market_us.assets a WHERE e.symbol = a.symbol "
            "AND a.company_id IS NOT NULL AND e.resolved = FALSE "
            "AND e.error_type IN (:a, :b)"),
            {"a": ERRO_SEM_CIK_SEC, "b": ERRO_CIK_SEM_EMPRESA}).rowcount
    return {"logged": len(novos), "resolved": int(fechados or 0)}


def link_classify_and_log(engine, ticker_cik: dict[str, str]) -> dict:
    """Vincula por CIK, reclassifica e registra o que continuou sem vínculo."""
    vinculo = link_assets_by_cik(engine, ticker_cik)
    classes = classify_assets(engine)
    return {"link": vinculo, "classify": classes,
            "errors": log_unlinked_assets(engine, ticker_cik)}


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
    with engine.begin() as conn:
        matched = conn.execute(text(sql_match)).rowcount
        assets = conn.execute(text("""
            SELECT a.id,a.company_id,a.symbol,a.security_type,c.sector,
              c.industry,c.name,c.is_reit,c.is_investment_company,c.reit_election,
              EXISTS (SELECT 1 FROM market_us.income_statements i
                      WHERE i.company_id=a.company_id) has_income,
              EXISTS (SELECT 1 FROM market_us.balance_sheets b
                      WHERE b.company_id=a.company_id) has_balance,
              EXISTS (SELECT 1 FROM market_us.cash_flow_statements f
                      WHERE f.company_id=a.company_id) has_cashflow,
              -- A-157: tri-estado. NULL quando nao ha demonstracao de resultado
              -- para apurar (duvida nao exclui); FALSE quando ha exercicio
              -- arquivado e nenhum traz receita positiva.
              CASE WHEN NOT EXISTS (SELECT 1 FROM market_us.income_statements i
                                     WHERE i.company_id=a.company_id
                                       AND i.period='annual') THEN NULL
                   ELSE EXISTS (SELECT 1 FROM market_us.income_statements i
                                 WHERE i.company_id=a.company_id
                                   AND i.period='annual'
                                   AND i.revenue IS NOT NULL AND i.revenue > 0)
              END has_revenue
            FROM market_us.assets a
            LEFT JOIN market_us.companies c ON c.id=a.company_id
        """)).mappings().all()
        # Giro financeiro médio dos últimos 60 dias de pregão com fechamento:
        # critério do ticker principal da emissora (ver us_instrumento).
        giro = {r[0]: r[1] for r in conn.execute(text("""
            SELECT symbol, AVG(close * volume) FROM market_us.prices_daily
            WHERE close IS NOT NULL AND volume IS NOT NULL
              AND date >= (SELECT MAX(date) - 60 FROM market_us.prices_daily
                           WHERE close IS NOT NULL)
            GROUP BY symbol"""))}
        symbols_by_company: dict[int, tuple[str, ...]] = {}
        for row in assets:
            if row["company_id"] is not None:
                cid = int(row["company_id"])
                symbols_by_company[cid] = (*symbols_by_company.get(cid, ()), row["symbol"])
        updates = []
        for row in assets:
            cid = int(row["company_id"]) if row["company_id"] is not None else None
            reason = instrument_exclusion_reason(
                row["symbol"], row["security_type"], row["sector"],
                symbols_by_company.get(cid, ()),
                industry=row["industry"], name=row["name"],
                is_reit=row["is_reit"],
                is_investment_company=row["is_investment_company"],
                reit_election=row["reit_election"],
                tem_receita=row["has_revenue"])
            if reason:
                status = "excluded"
            elif cid is None:
                status, reason = "unresolved", "sem vínculo CIK/demonstrações"
            elif row["has_income"] and row["has_balance"] and row["has_cashflow"]:
                status, reason = "eligible", None
            else:
                status, reason = "pending", "demonstrações incompletas"
            updates.append({"id": int(row["id"]), "status": status, "reason": reason,
                            "symbol": row["symbol"], "cid": cid})
        # Uma companhia, um ticker elegível: o que sobra do mesmo CIK perde para
        # o de maior giro (nota/preferencial que a regra de sufixo não pegou).
        elegiveis: dict[int, dict] = {}
        for u in updates:
            if u["status"] == "eligible" and u["cid"] is not None:
                elegiveis.setdefault(u["cid"], {})[u["symbol"]] = giro.get(u["symbol"])
        for u in updates:
            irmaos = elegiveis.get(u["cid"], {}) if u["status"] == "eligible" else {}
            if len(irmaos) > 1:
                principal = ticker_principal_da_emissora(irmaos)
                if principal != str(u["symbol"]).upper():
                    u["status"] = "excluded"
                    u["reason"] = MOTIVO_OUTRO_TICKER_DA_EMISSORA.format(base=principal)
        conn.execute(text("""
            UPDATE market_us.assets SET analysis_status=:status,
              status_reason=:reason,classified_at=NOW() WHERE id=:id
        """), [{"id": u["id"], "status": u["status"], "reason": u["reason"]}
               for u in updates])
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
              WHEN reference_date > CURRENT_DATE OR available_at < reference_date THEN 'rejected'
              WHEN available_at IS NOT NULL AND content_hash IS NOT NULL
                   AND (revenue IS NOT NULL OR net_income IS NOT NULL) THEN 'validated'
              ELSE 'raw' END
        """))
        conn.execute(text("""
            UPDATE market_us.balance_sheets SET quality_status = CASE
              WHEN reference_date > CURRENT_DATE OR available_at < reference_date THEN 'rejected'
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
              WHEN reference_date > CURRENT_DATE OR available_at < reference_date THEN 'rejected'
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
