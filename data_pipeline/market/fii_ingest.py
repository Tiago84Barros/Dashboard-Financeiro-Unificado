"""
data_pipeline/market/fii_ingest.py
Ingestão de FIIs (BRAPI Pro) -> market.fiis.

Fluxo: lista de fundos (type=fund, por volume) -> para cada, busca cotação +
rendimentos + perfil -> filtra ETF pelo setor (fii.is_fii) -> computa métricas
(DY 12m, P/VP, liquidez) -> rankeia -> upsert em market.fiis. Salva o payload
bruto p/ permitir re-ranking sem rede (reprocess).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from sqlalchemy import text

import core.brapi as brapi
from data_pipeline.market import fii as fz
from data_pipeline.market import repository as repo
from data_pipeline.quality import scheduler as sched

logger = logging.getLogger(__name__)


def _engine():
    from data_pipeline.utils.db_utils import get_pipeline_engine
    return get_pipeline_engine()


def _schema_ready(conn) -> bool:
    return bool(conn.execute(text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
        "WHERE table_schema='market' AND table_name='fiis')")).scalar())


def _row(m: dict) -> dict:
    return {"ticker": m["ticker"], "cnpj": m.get("cnpj"), "name": m.get("name"),
            "segmento": m.get("segmento"), "price": m.get("price"), "pvp": m.get("pvp"),
            "dy_12m": m.get("dy_12m"), "liquidez_diaria": m.get("liquidez_diaria"),
            "score": m.get("score")}


def ingest(limit: int | None = None, tickers: list[str] | None = None,
           weights: dict | None = None) -> dict:
    """Coleta FIIs (rede), classifica/computa/rankeia e grava em market.fiis."""
    engine = _engine()
    prog = {"candidatos": 0, "fiis": 0, "etfs_ignorados": 0, "erros": 0, "gravados": 0}
    if engine is None:
        return {**prog, "erros": -1}
    with engine.connect() as conn:
        if not _schema_ready(conn):
            logger.error("market.fiis ausente — rode 015_market_fiis.sql.")
            return {**prog, "erros": -1}

    if tickers:
        universe = [t.upper().replace(".SA", "") for t in tickers]
    else:
        try:
            universe = [t for t in brapi.fetch_fund_list() if t.endswith("11")]
        except Exception as exc:
            logger.error("fetch_fund_list: %s", exc)
            return {**prog, "erros": -1}
    if limit:
        universe = universe[:limit]
    prog["candidatos"] = len(universe)

    ref = datetime.now(timezone.utc).date()
    delay = float(os.getenv("MARKET_DELAY", "1.0"))
    metrics: list[dict] = []
    for i, tk in enumerate(universe):
        try:
            quote = sched.with_backoff(
                lambda: brapi.fetch_quote_full(tk),
                retries=3, base=float(os.getenv("MARKET_BACKOFF", "4.0")),
                on_block=brapi.is_rate_limited)
            if not quote:
                prog["erros"] += 1
            else:
                with engine.begin() as conn:
                    repo.save_raw_payload(conn, tk, "quote", quote, status="success")
                m = fz.compute_fii(quote, ref)
                if m is None:
                    prog["etfs_ignorados"] += 1
                else:
                    metrics.append(m)
                    prog["fiis"] += 1
        except Exception as exc:
            logger.warning("fii %s: %s", tk, exc)
            prog["erros"] += 1
        if i < len(universe) - 1:
            sched.sleep_jittered(base=delay)

    if metrics:
        ranked = fz.rank_fiis(metrics, weights=weights)
        # rankeados (elegíveis) levam score; os filtrados gravam sem score (score=None)
        ranked_by = {r["ticker"]: r for r in ranked}
        rows = [_row(ranked_by.get(m["ticker"], m)) for m in metrics]
        with engine.begin() as conn:
            prog["gravados"] = repo.upsert(conn, "fiis", rows)
    logger.info("market/fii ingest: %s", prog)
    return prog


def ingest_benchmark(ticker: str = "XFIX11") -> dict:
    """
    Persiste o histórico do benchmark de FIIs em market.historical_prices.

    A brapi NÃO serve histórico do índice IFIX puro (símbolo "IFIX" devolve só a
    cotação spot). Usamos o ETF **XFIX11** (Trend ETF IFIX Fundo de Índice), que
    replica o IFIX e tem `adjustedClose` (retorno total) com ~69 meses de série —
    proxy correto do IFIX para comparar com a carteira no backtest.
    """
    from data_pipeline.market import normalize as nz
    engine = _engine()
    prog = {"ticker": ticker, "precos": 0, "erros": 0}
    if engine is None:
        return {**prog, "erros": -1}
    try:
        quote = brapi.fetch_quote(ticker, range_="max", interval="1mo",
                                  dividends=False, fundamental=False)
    except Exception as exc:
        logger.warning("ingest_benchmark %s: %s", ticker, exc)
        return {**prog, "erros": -1}
    if not quote:
        return {**prog, "erros": -1}
    with engine.begin() as conn:
        if not _schema_ready(conn):
            return {**prog, "erros": -1}
        repo.save_raw_payload(conn, ticker, "quote", quote, status="success")
        # asset_type 'other' (o CHECK não tem 'index'); benchmark é lido por ticker.
        repo.upsert(conn, "assets", [{
            "ticker": ticker, "company_id": None, "asset_type": "other",
            "exchange": "B3", "currency": "BRL", "is_active": True}])
        prog["precos"] = repo.upsert(conn, "historical_prices", nz.price_rows(quote))
    logger.info("market/ingest_benchmark: %s", prog)
    return prog


def backfill_series() -> dict:
    """
    Persiste as SÉRIES históricas dos FIIs (preços + rendimentos) em
    market.historical_prices / market.dividends, a partir dos payloads brutos já
    salvos (SEM rede). Cria as linhas em market.assets (asset_type='fii') —
    pré-requisito do backtest da carteira. Idempotente.
    """
    import json
    from data_pipeline.market import normalize as nz
    engine = _engine()
    prog = {"fiis": 0, "precos": 0, "dividendos": 0, "erros": 0}
    if engine is None:
        return {**prog, "erros": -1}
    with engine.connect() as conn:
        if not _schema_ready(conn):
            return {**prog, "erros": -1}
        fiis = [r[0] for r in conn.execute(text("SELECT ticker FROM market.fiis")).fetchall()]
    for tk in fiis:
        try:
            with engine.begin() as conn:
                row = conn.execute(text(
                    "SELECT payload_json FROM market.brapi_raw_payloads WHERE ticker=:t "
                    "AND endpoint='quote' AND request_status='success' "
                    "ORDER BY id DESC LIMIT 1"), {"t": tk}).fetchone()
                if not row:
                    continue
                p = row[0]
                p = json.loads(p) if isinstance(p, str) else p
                quote = (p.get("results") or [p])[0] if isinstance(p, dict) else None
                if not quote:
                    continue
                # asset FII (company_id nulo; FK das séries aponta p/ assets.ticker)
                repo.upsert(conn, "assets", [{
                    "ticker": tk, "company_id": None, "asset_type": "fii",
                    "exchange": "B3", "currency": "BRL", "is_active": True}])
                prog["precos"] += repo.upsert(conn, "historical_prices", nz.price_rows(quote))
                prog["dividendos"] += repo.upsert(conn, "dividends", nz.dividend_rows(quote))
                prog["fiis"] += 1
        except Exception as exc:
            logger.warning("fii backfill_series %s: %s", tk, exc)
            prog["erros"] += 1
    logger.info("market/fii backfill_series: %s", prog)
    return prog


def enrich_cvm(year: int | None = None) -> dict:
    """
    Enriquece market.fiis com o Informe Mensal de FIIs da CVM (join por CNPJ):
    segmento real, tipo (tijolo/papel/fof/híbrido), patrimônio, VPA, nº cotistas
    e composição de ativos. Requer que o ingest da brapi já tenha gravado o CNPJ.
    """
    import core.cvm_fii as cvm
    from datetime import datetime, timezone
    engine = _engine()
    prog = {"ano": year, "fiis_no_banco": 0, "casados": 0, "gravados": 0, "erros": 0}
    if engine is None:
        return {**prog, "erros": -1}
    year = year or datetime.now(timezone.utc).year
    data = cvm.fetch_informe(year)
    used_year = year
    if not data:
        data, used_year = cvm.fetch_informe(year - 1), year - 1
    if not data:
        prog["erros"] = -1
        return prog
    prog["ano"] = used_year
    by_cnpj = cvm.parse_informe(data, used_year)
    # tickers do banco com CNPJ
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT ticker, cnpj FROM market.fiis WHERE cnpj IS NOT NULL")).fetchall()
    prog["fiis_no_banco"] = len(rows)
    out = []
    for ticker, cnpj in rows:
        rec = by_cnpj.get(cvm.only_digits(cnpj))
        if not rec:
            continue
        prog["casados"] += 1
        out.append({
            "ticker": ticker, "isin": rec.get("isin"), "segmento_cvm": rec.get("segmento"),
            "tipo": rec.get("tipo"), "tipo_gestao": rec.get("tipo_gestao"),
            "patrimonio_liquido": rec.get("patrimonio_liquido"), "vpa": rec.get("vpa"),
            "num_cotistas": int(rec["num_cotistas"]) if rec.get("num_cotistas") else None,
            "pct_imoveis": rec.get("pct_imoveis"), "pct_papel": rec.get("pct_papel"),
            "pct_caixa": rec.get("pct_caixa"), "pct_fundos": rec.get("pct_fundos"),
            "cvm_ref_date": rec.get("ref_date"),
        })
    if out:
        with engine.begin() as conn:
            prog["gravados"] = repo.upsert(conn, "fiis", out)
    logger.info("market/fii enrich_cvm: %s", prog)
    return prog


def reprocess(weights: dict | None = None) -> dict:
    """Re-rankeia a partir dos payloads brutos já salvos (SEM rede)."""
    import json
    engine = _engine()
    prog = {"candidatos": 0, "fiis": 0, "etfs_ignorados": 0, "erros": 0, "gravados": 0}
    if engine is None:
        return {**prog, "erros": -1}
    with engine.connect() as conn:
        if not _schema_ready(conn):
            return {**prog, "erros": -1}
        rows = conn.execute(text(
            "SELECT DISTINCT ON (ticker) ticker, payload_json FROM market.brapi_raw_payloads "
            "WHERE endpoint='quote' AND request_status='success' AND payload_json IS NOT NULL "
            "ORDER BY ticker, id DESC")).fetchall()
    ref = datetime.now(timezone.utc).date()
    metrics: list[dict] = []
    for tk, payload in rows:
        try:
            p = json.loads(payload) if isinstance(payload, str) else payload
            quote = (p.get("results") or [p])[0] if isinstance(p, dict) else None
            if not quote:
                continue
            prog["candidatos"] += 1
            m = fz.compute_fii(quote, ref)
            if m is None:
                prog["etfs_ignorados"] += 1
            else:
                metrics.append(m)
                prog["fiis"] += 1
        except Exception as exc:
            logger.warning("fii reprocess %s: %s", tk, exc)
            prog["erros"] += 1
    if metrics:
        ranked = fz.rank_fiis(metrics, weights=weights)
        ranked_by = {r["ticker"]: r for r in ranked}
        out = [_row(ranked_by.get(m["ticker"], m)) for m in metrics]
        with engine.begin() as conn:
            prog["gravados"] = repo.upsert(conn, "fiis", out)
    logger.info("market/fii reprocess: %s", prog)
    return prog
