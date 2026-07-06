"""
data_pipeline/market/fii_ingest.py
Ingestão de FIIs (BRAPI Pro) -> market.fiis.

Fluxo: lista de fundos (type=fund, por volume) -> para cada, busca cotação +
rendimentos + perfil -> filtra ETF pelo setor (fii.is_fii) -> computa métricas
(DY 12m, P/VP, liquidez) -> rankeia -> upsert em market.fiis. Salva o payload
bruto p/ permitir re-ranking sem rede (reprocess).
"""
from __future__ import annotations

import json
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


def _score_metadata_ready(conn) -> bool:
    return bool(conn.execute(text(
        "SELECT COUNT(*) = 3 FROM information_schema.columns "
        "WHERE table_schema='market' AND table_name='fiis' "
        "AND column_name IN ('score_version','score_calculated_at','metrics_fetched_at')"
    )).scalar())


def _row(m: dict, *, include_score: bool = True, metadata_ready: bool = False,
         calculated_at=None) -> dict:
    price = m.get("price")
    pvp = m.get("pvp")
    dy = m.get("dy_12m")
    price = price if price is not None and price > 0 else None
    pvp = pvp if pvp is not None and pvp > 0 else None
    dy = dy if dy is not None and 0 <= dy <= 1 else None
    row = {"ticker": m["ticker"], "cnpj": m.get("cnpj"), "name": m.get("name"),
           "segmento": m.get("segmento"), "price": price, "pvp": pvp,
           "dy_12m": dy, "liquidez_diaria": m.get("liquidez_diaria")}
    if include_score:
        row["score"] = m.get("score")
        if metadata_ready:
            row["score_version"] = fz.SCORE_VERSION
            row["score_calculated_at"] = calculated_at
    if metadata_ready:
        row["metrics_fetched_at"] = calculated_at
    return row


def _extract_quote(payload) -> dict | None:
    p = json.loads(payload) if isinstance(payload, str) else payload
    if not isinstance(p, dict):
        return None
    quote = (p.get("results") or [p])[0]
    return quote if isinstance(quote, dict) else None


def _latest_fii_payloads(conn) -> list[tuple[str, dict]]:
    """Último payload realmente completo de FII, ignorando cotações genéricas."""
    rows = conn.execute(text(
        "SELECT ticker, payload_json FROM market.brapi_raw_payloads "
        "WHERE endpoint IN ('quote_fii_full', 'quote') "
        "AND request_status='success' AND payload_json IS NOT NULL "
        "ORDER BY ticker, "
        "CASE WHEN endpoint='quote_fii_full' THEN 0 ELSE 1 END, id DESC"
    )).fetchall()
    selected: list[tuple[str, dict]] = []
    seen: set[str] = set()
    for ticker, payload in rows:
        if ticker in seen:
            continue
        try:
            quote = _extract_quote(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if quote and fz.is_fii(quote):
            selected.append((ticker, quote))
            seen.add(ticker)
    return selected


def _ranking_coverage_ok(found: int, expected: int) -> bool:
    minimum = float(os.getenv("FII_RANK_MIN_COVERAGE", "0.85"))
    return expected > 0 and found / expected >= minimum


def ingest(limit: int | None = None, tickers: list[str] | None = None,
           weights: dict | None = None) -> dict:
    """Coleta FIIs (rede), classifica/computa/rankeia e grava em market.fiis."""
    engine = _engine()
    repo.reset_db_cols_cache()  # migração 020 pode ter sido aplicada com processo vivo
    prog = {"candidatos": 0, "fiis": 0, "etfs_ignorados": 0, "erros": 0,
            "gravados": 0, "ranking_aplicado": False, "cobertura": 0.0}
    if engine is None:
        return {**prog, "erros": -1}
    with engine.connect() as conn:
        if not _schema_ready(conn):
            logger.error("market.fiis ausente — rode 015_market_fiis.sql.")
            return {**prog, "erros": -1}
        metadata_ready = _score_metadata_ready(conn)
        # VPA do último enrich_cvm (roda DEPOIS de `fiis` no cron) — no run
        # diário, é o VPA de ontem: correto (VPA é mensal). 1º run: vazio →
        # pvp_efetivo cai no priceToBook da brapi.
        vpa_map = _vpa_map(conn)

    full_run = not tickers and limit is None
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
                    repo.save_raw_payload(
                        conn, tk, "quote_fii_full", quote, status="success"
                    )
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
        # P/VP efetivo (fix auditoria FII 2026-07): preço ÷ VPA CVM quando
        # disponível (VPA do último enrich_cvm) — fonte oficial em vez do
        # priceToBook da brapi. O valor derivado é gravado em market.fiis.pvp;
        # o priceToBook bruto permanece recuperável nos payloads.
        metrics = [
            {**m, "pvp": fz.pvp_efetivo(m.get("price"),
                                        vpa_map.get(m["ticker"]),
                                        m.get("pvp"))}
            for m in metrics
        ]
        calculated_at = datetime.now(timezone.utc)
        successful = prog["fiis"] + prog["etfs_ignorados"]
        prog["cobertura"] = round(successful / max(len(universe), 1), 4)
        apply_ranking = full_run and _ranking_coverage_ok(successful, len(universe))
        ranked_by = {}
        if apply_ranking:
            ranked_by = {
                r["ticker"]: r for r in fz.rank_fiis(metrics, weights=weights)
            }
            prog["ranking_aplicado"] = True
        rows = [
            _row(
                ranked_by.get(m["ticker"], m),
                include_score=apply_ranking,
                metadata_ready=metadata_ready,
                calculated_at=calculated_at,
            )
            for m in metrics
        ]
        with engine.begin() as conn:
            prog["gravados"] = repo.upsert(conn, "fiis", rows)
            if not apply_ranking:
                repo.log_quality(
                    conn, table_name="fiis", field_name="score",
                    issue_type="ranking_preservado_cobertura_insuficiente",
                    old_value=f"{successful}/{len(universe)}",
                    severity="warning", source="brapi.dev",
                )
            prog["snapshot_mensal"] = _snapshot_score_mensal(
                conn, ranked_by, ref) if apply_ranking else 0
    logger.info("market/fii ingest: %s", prog)
    return prog


def _vpa_map(conn) -> dict[str, float]:
    """VPA CVM por ticker — p/ P/VP efetivo no ranking (auditoria FII 2026-07)."""
    try:
        return {str(t).upper(): float(v) for t, v in conn.execute(text(
            "SELECT ticker, vpa FROM market.fiis "
            "WHERE vpa IS NOT NULL AND vpa > 0")).fetchall()}
    except Exception as exc:
        logger.warning("_vpa_map: %s — ranking segue com priceToBook brapi", exc)
        return {}


def _snapshot_score_mensal(conn, ranked_by: dict, ref) -> int:
    """Snapshot mensal point-in-time do score e seus inputs (migração 020).

    Grava score/price/dy_12m/pvp/liquidez em market.fii_metrics_monthly
    (chave ticker+ref_month; runs no mesmo mês sobrescrevem — 'último
    cálculo do mês'). Sem isso a metodologia FII nunca poderá ser validada
    (rank-IC exige inputs históricos). Colunas CVM da mesma linha são
    preservadas (o upsert só atualiza as colunas presentes).
    """
    if not ranked_by:
        return 0
    if "score" not in repo._db_cols(conn, "fii_metrics_monthly"):
        logger.info("snapshot mensal de score pulado — migração 020 pendente")
        return 0
    ref_month = ref.replace(day=1)
    snap = [{
        "ticker": r["ticker"], "ref_month": ref_month,
        "score": r.get("score"), "score_version": r.get("score_version"),
        "price": r.get("price"), "dy_12m": r.get("dy_12m"),
        "pvp": r.get("pvp"), "liquidez_diaria": r.get("liquidez_diaria"),
    } for r in ranked_by.values()]
    return repo.upsert(conn, "fii_metrics_monthly", snap)


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
    from data_pipeline.market import normalize as nz
    engine = _engine()
    prog = {"fiis": 0, "precos": 0, "dividendos": 0, "erros": 0}
    if engine is None:
        return {**prog, "erros": -1}
    with engine.connect() as conn:
        if not _schema_ready(conn):
            return {**prog, "erros": -1}
        fiis = [r[0] for r in conn.execute(text("SELECT ticker FROM market.fiis")).fetchall()]
        payloads = dict(_latest_fii_payloads(conn))
    for tk in fiis:
        try:
            quote = payloads.get(tk)
            if not quote:
                continue
            with engine.begin() as conn:
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
    prog = {"ano": year, "anos_consultados": [], "fiis_no_banco": 0,
            "casados": 0, "gravados": 0, "erros": 0}
    if engine is None:
        return {**prog, "erros": -1}
    year = year or datetime.now(timezone.utc).year
    # Une o ano anterior ao atual. O arquivo do ano corrente pode existir ainda
    # sem conter todos os fundos; registros mais novos sobrescrevem os antigos.
    by_cnpj: dict[str, dict] = {}
    for candidate_year in (year - 1, year):
        data = cvm.fetch_informe(candidate_year)
        if not data:
            continue
        prog["anos_consultados"].append(candidate_year)
        by_cnpj.update(cvm.parse_informe(data, candidate_year))
    if not by_cnpj:
        prog["erros"] = -1
        return prog
    prog["ano"] = max(prog["anos_consultados"])
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


def backfill_metrics_monthly(years: int = 3) -> dict:
    """
    Série MENSAL de fundamentos do FII (VPA/PL/cotistas/DY/composição) a partir do
    Informe Mensal da CVM dos últimos `years` anos -> market.fii_metrics_monthly.
    Casa por CNPJ com market.fiis (só grava tickers conhecidos). VPA mensal + preço
    bruto (market.historical_prices) dão o P/VP histórico na leitura. Idempotente.
    """
    import core.cvm_fii as cvm
    from datetime import datetime, timezone
    engine = _engine()
    prog = {"anos": [], "fiis_no_banco": 0, "linhas": 0, "gravados": 0, "erros": 0}
    if engine is None:
        return {**prog, "erros": -1}
    with engine.connect() as conn:
        if not _schema_ready(conn):
            return {**prog, "erros": -1}
        rows = conn.execute(text(
            "SELECT ticker, cnpj FROM market.fiis WHERE cnpj IS NOT NULL")).fetchall()
    tickers_by_cnpj: dict[str, str] = {cvm.only_digits(c): t for t, c in rows}
    prog["fiis_no_banco"] = len(tickers_by_cnpj)
    if not tickers_by_cnpj:
        return prog

    cur_year = datetime.now(timezone.utc).year
    out: list[dict] = []
    for year in range(cur_year, cur_year - max(1, years), -1):
        data = cvm.fetch_informe(year)
        if not data:
            continue
        prog["anos"].append(year)
        by_cnpj = cvm.parse_informe_monthly(data, year)
        for cnpj, series in by_cnpj.items():
            tk = tickers_by_cnpj.get(cnpj)
            if not tk:
                continue
            for rec in series:
                out.append({"ticker": tk, **rec})
    prog["linhas"] = len(out)
    if out:
        with engine.begin() as conn:
            prog["gravados"] = repo.upsert(conn, "fii_metrics_monthly", out)
    logger.info("market/fii backfill_metrics_monthly: %s", prog)
    return prog


def enrich_vacancia() -> dict:
    """
    Recalcula a vacância do fundo a partir dos imóveis já coletados
    (market.fii_imoveis), ponderada pela área, e grava em market.fiis.vacancia.
    SEM rede — rode após `fiis-imoveis`. Útil para refrescar o agregado.
    """
    from datetime import datetime, timezone
    engine = _engine()
    prog = {"fiis_com_imoveis": 0, "com_vacancia": 0, "gravados": 0, "erros": 0}
    if engine is None:
        return {**prog, "erros": -1}
    with engine.connect() as conn:
        if not _schema_ready(conn):
            return {**prog, "erros": -1}
        rows = conn.execute(text("""
            SELECT ticker,
                   CASE WHEN SUM(area_m2) FILTER (WHERE vacancia IS NOT NULL) > 0
                        THEN SUM(vacancia * area_m2) FILTER (WHERE vacancia IS NOT NULL AND area_m2 > 0)
                             / NULLIF(SUM(area_m2) FILTER (WHERE vacancia IS NOT NULL AND area_m2 > 0), 0)
                        ELSE AVG(vacancia) END AS vac
            FROM market.fii_imoveis GROUP BY ticker
        """)).fetchall()
    prog["fiis_com_imoveis"] = len(rows)
    ref = datetime.now(timezone.utc).date().isoformat()
    out = [{"ticker": tk, "vacancia": round(float(vac), 4), "vacancia_ref_date": ref}
           for tk, vac in rows if vac is not None]
    prog["com_vacancia"] = len(out)
    if out:
        with engine.begin() as conn:
            prog["gravados"] = repo.upsert(conn, "fiis", out)
    logger.info("market/fii enrich_vacancia: %s", prog)
    return prog


def ingest_imoveis() -> dict:
    """
    Carteira de imóveis (scraping Status Invest) p/ FIIs de tijolo/híbrido ->
    market.fii_imoveis. Grava num_imoveis e a vacância do fundo (média ponderada
    pela área) em market.fiis. Best-effort: cobertura varia por fundo.
    """
    import core.fii_imoveis as fim
    from datetime import datetime, timezone
    engine = _engine()
    prog = {"fiis": 0, "com_imoveis": 0, "imoveis": 0, "gravados": 0, "erros": 0}
    if engine is None:
        return {**prog, "erros": -1}
    with engine.connect() as conn:
        if not _schema_ready(conn):
            return {**prog, "erros": -1}
        rows = conn.execute(text(
            "SELECT ticker FROM market.fiis "
            "WHERE tipo IN ('tijolo', 'hibrido')")).fetchall()
    tickers = [r[0] for r in rows]
    prog["fiis"] = len(tickers)
    ref = datetime.now(timezone.utc).date().isoformat()
    delay = float(os.getenv("MARKET_DELAY", "1.0"))
    for i, tk in enumerate(tickers):
        try:
            imoveis = fim.fetch_fii_imoveis(tk)
            if imoveis:
                vac = fim.vacancia_media(imoveis)
                with engine.begin() as conn:
                    rows_db = [{"ticker": tk, **im} for im in imoveis]
                    prog["gravados"] += repo.upsert(conn, "fii_imoveis", rows_db)
                    fii_upd = {"ticker": tk, "num_imoveis": len(imoveis)}
                    if vac is not None:
                        fii_upd.update(vacancia=vac, vacancia_ref_date=ref)
                    repo.upsert(conn, "fiis", [fii_upd])
                prog["com_imoveis"] += 1
                prog["imoveis"] += len(imoveis)
        except Exception as exc:
            logger.warning("imoveis %s: %s", tk, exc)
            prog["erros"] += 1
        if i < len(tickers) - 1:
            sched.sleep_jittered(base=delay)
    logger.info("market/fii ingest_imoveis: %s", prog)
    return prog


def reprocess(weights: dict | None = None) -> dict:
    """Re-rankeia a partir dos payloads brutos já salvos (SEM rede)."""
    engine = _engine()
    repo.reset_db_cols_cache()  # migração 020 pode ter sido aplicada com processo vivo
    prog = {"candidatos": 0, "fiis": 0, "etfs_ignorados": 0, "erros": 0,
            "gravados": 0, "ranking_aplicado": False, "cobertura": 0.0}
    if engine is None:
        return {**prog, "erros": -1}
    with engine.connect() as conn:
        if not _schema_ready(conn):
            return {**prog, "erros": -1}
        metadata_ready = _score_metadata_ready(conn)
        expected = int(conn.execute(text("SELECT COUNT(*) FROM market.fiis")).scalar() or 0)
        rows = _latest_fii_payloads(conn)
        vpa_map = _vpa_map(conn)
    ref = datetime.now(timezone.utc).date()
    metrics: list[dict] = []
    for tk, quote in rows:
        try:
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
        # P/VP efetivo com VPA CVM — mesmo tratamento do ingest (auditoria FII)
        metrics = [
            {**m, "pvp": fz.pvp_efetivo(m.get("price"),
                                        vpa_map.get(m["ticker"]),
                                        m.get("pvp"))}
            for m in metrics
        ]
        calculated_at = datetime.now(timezone.utc)
        prog["cobertura"] = round(len(metrics) / max(expected, 1), 4)
        apply_ranking = _ranking_coverage_ok(len(metrics), expected)
        ranked_by = (
            {r["ticker"]: r for r in fz.rank_fiis(metrics, weights=weights)}
            if apply_ranking else {}
        )
        prog["ranking_aplicado"] = apply_ranking
        out = [
            _row(
                ranked_by.get(m["ticker"], m), include_score=apply_ranking,
                metadata_ready=metadata_ready, calculated_at=calculated_at,
            )
            for m in metrics
        ]
        with engine.begin() as conn:
            prog["gravados"] = repo.upsert(conn, "fiis", out)
            if not apply_ranking:
                repo.log_quality(
                    conn, table_name="fiis", field_name="score",
                    issue_type="reprocessamento_incompleto_score_preservado",
                    old_value=f"{len(metrics)}/{expected}", severity="error",
                    source="brapi_raw_payloads",
                )
    logger.info("market/fii reprocess: %s", prog)
    return prog
