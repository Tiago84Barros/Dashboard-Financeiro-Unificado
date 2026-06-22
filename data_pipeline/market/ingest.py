"""
data_pipeline/market/ingest.py
Ingestão BRAPI Pro -> Supabase (schema market.*).

Fluxo por ticker:
  BRAPI Pro -> raw payload (market.brapi_raw_payloads) -> normalização
  -> upsert nas tabelas market.* -> log de qualidade.

Comandos (ver run_market_ingest.py):
  bootstrap  — baixa 16 anos de histórico (range=max, módulos completos)
  daily      — atualização leve (preços recentes + dividendos + indicadores spot)
  annual     — refresh de demonstrações (range=1y, módulos completos)
  reprocess  — recalcula indicadores a partir das tabelas market.* (sem rede)

Em lotes, com atraso aleatório, backoff e disjuntor (anti rate-limit).
Não usa Fundamentus/Status Invest. BRAPI é a fonte principal.
"""
from __future__ import annotations

import logging
import os

from sqlalchemy import text

import core.brapi as brapi
from data_pipeline.market import normalize as nz
from data_pipeline.market import repository as repo
from data_pipeline.quality import scheduler as sched

logger = logging.getLogger(__name__)

_FACT_TABLES = ("historical_prices", "income_statements", "balance_sheets",
                "cash_flow_statements", "dividends", "calculated_metrics")


def _engine():
    from data_pipeline.utils.db_utils import get_pipeline_engine
    return get_pipeline_engine()


def _universe(engine, source: str = "setores", limit: int | None = None) -> list[str]:
    if source == "brapi":
        try:
            tks = brapi.fetch_list()
        except Exception as exc:
            logger.warning("fetch_list falhou: %s", exc)
            tks = []
    else:
        with engine.connect() as c:
            rows = c.execute(text(
                "SELECT DISTINCT ticker FROM public.setores WHERE ticker IS NOT NULL ORDER BY ticker"
            )).fetchall()
        tks = [str(r[0]).upper().replace(".SA", "") for r in rows if r[0]]
    return tks[:limit] if limit else tks


def _new_progress() -> dict:
    return {"empresas": 0, "precos": 0, "demonstracoes": 0, "dividendos": 0,
            "indicadores": 0, "erros": 0, "tickers": 0}


def ingest_ticker(engine, ticker: str, *, range_: str, full: bool,
                  cvm_map: dict[str, int], prog: dict) -> None:
    """Busca 1 ticker na brapi e grava tudo em market.* (1 transação)."""
    tk = ticker.upper().replace(".SA", "")
    fetch = (lambda: brapi.fetch_quote_full(tk, range_=range_)) if full else \
            (lambda: brapi.fetch_quote(tk, range_=range_, dividends=True, fundamental=True))
    try:
        quote = sched.with_backoff(fetch, retries=3,
                                   base=float(os.getenv("MARKET_BACKOFF", "4.0")),
                                   on_block=brapi.is_rate_limited)
    except Exception as exc:
        with engine.begin() as conn:
            repo.save_raw_payload(conn, tk, "quote", None,
                                  status="rate_limited" if brapi.is_rate_limited(exc) else "failed",
                                  error=exc)
        prog["erros"] += 1
        raise

    if not quote:
        with engine.begin() as conn:
            repo.save_raw_payload(conn, tk, "quote", None, status="empty")
        prog["erros"] += 1
        return

    data = nz.normalize_all(quote)
    with engine.begin() as conn:
        repo.save_raw_payload(conn, tk, "quote", quote, status="success")
        # empresa (enriquece codigo_cvm pelo mapa CVM)
        comp = data["companies"]
        cod = cvm_map.get(tk)
        if comp and cod is not None:
            comp[0]["codigo_cvm"] = cod
            repo.upsert(conn, "companies", comp)
            prog["empresas"] += 1
        # asset (liga company_id se houver)
        ast = data["assets"]
        if ast:
            ast[0]["company_id"] = repo.company_id_by_codigo(conn, cod) if cod is not None else None
            repo.upsert(conn, "assets", ast)
        # fatos
        prog["precos"] += repo.upsert(conn, "historical_prices", data["historical_prices"])
        dem = 0
        for t in ("income_statements", "balance_sheets", "cash_flow_statements"):
            dem += repo.upsert(conn, t, data[t])
        prog["demonstracoes"] += dem
        prog["dividendos"] += repo.upsert(conn, "dividends", data["dividends"])
        prog["indicadores"] += repo.upsert(conn, "calculated_metrics", data["calculated_metrics"])
        # qualidade: sinaliza ausências relevantes
        if full and dem == 0:
            repo.log_quality(conn, ticker=tk, table_name="income_statements",
                             issue_type="missing", severity="warn",
                             new_value="sem demonstrações no payload")
    prog["tickers"] += 1


def _run(engine, tickers: list[str], *, range_: str, full: bool,
         batch_label: str, delay: float | None = None) -> dict:
    prog = _new_progress()
    delay = float(os.getenv("MARKET_DELAY", "1.5")) if delay is None else delay
    max_blocks = int(os.getenv("MARKET_MAX_BLOCKS", "3"))
    with engine.connect() as conn:
        if not repo.schema_exists(conn):
            logger.error("schema market.* não existe — rode 013_market_brapi_schema.sql primeiro.")
            prog["erros"] = -1
            return prog
        cvm_map = repo.load_cvm_to_ticker(conn)

    blocks = 0
    for i, tk in enumerate(tickers):
        try:
            ingest_ticker(engine, tk, range_=range_, full=full, cvm_map=cvm_map, prog=prog)
            blocks = 0
        except Exception as exc:
            if brapi.is_rate_limited(exc):
                blocks += 1
                if blocks >= max_blocks:
                    logger.warning("%s: disjuntor (429) após %d bloqueios — encerrando run.",
                                   batch_label, blocks)
                    break
            else:
                logger.warning("%s: %s falhou: %s", batch_label, tk, exc)
        if i < len(tickers) - 1:
            sched.sleep_jittered(base=delay)
    logger.info("market/%s: %s", batch_label, prog)
    return prog


# ── Comandos ──────────────────────────────────────────────────────────────────

def bootstrap(tickers: list[str] | None = None, source: str = "setores",
              limit: int | None = None) -> dict:
    """Baixa 16 anos de histórico (range=max + módulos completos)."""
    engine = _engine()
    if engine is None:
        return {**_new_progress(), "erros": -1}
    tks = tickers or _universe(engine, source, limit)
    return _run(engine, tks, range_="max", full=True, batch_label="bootstrap")


def daily(tickers: list[str] | None = None, source: str = "setores",
          limit: int | None = None) -> dict:
    """Atualização leve: preços recentes + dividendos + indicadores spot."""
    engine = _engine()
    if engine is None:
        return {**_new_progress(), "erros": -1}
    tks = tickers or _universe(engine, source, limit)
    return _run(engine, tks, range_="1mo", full=False, batch_label="daily")


def annual(tickers: list[str] | None = None, source: str = "setores",
           limit: int | None = None) -> dict:
    """Refresh de demonstrações (range=1y + módulos completos)."""
    engine = _engine()
    if engine is None:
        return {**_new_progress(), "erros": -1}
    tks = tickers or _universe(engine, source, limit)
    return _run(engine, tks, range_="1y", full=True, batch_label="annual")


# ── Validação controlada (sem bootstrap) ─────────────────────────────────────

def _token_status() -> str:
    """Status do token SEM expor o valor."""
    tok = brapi._token()
    if not tok:
        return "ausente (modo free — só PETR4, VALE3, ITUB4, MGLU3)"
    return f"presente (len={len(tok)}, ••••{tok[-2:] if len(tok) >= 2 else ''})"


def validate(tickers: list[str], persist: bool = True) -> dict:
    """
    Validação controlada de poucos tickers. NÃO faz bootstrap.
    - confirma carregamento do token (mascarado);
    - testa conexão e busca cada ticker (módulos completos);
    - persiste (raw + market.*) quando o schema existe e persist=True;
    - reporta presença de cada bloco e registra ausências em data_quality_logs.
    Retorna relatório estruturado (sem segredos).
    """
    engine = _engine()
    report: dict = {"brapi_token": _token_status(), "schema_market": False,
                    "persistido": False, "tickers": {}}
    if engine is None:
        report["erro"] = "banco não conectado"
        return report

    with engine.connect() as conn:
        schema_ok = repo.schema_exists(conn)
        cvm_map = repo.load_cvm_to_ticker(conn) if schema_ok else {}
    report["schema_market"] = schema_ok
    do_persist = persist and schema_ok

    for tk in tickers:
        tk = tk.upper().replace(".SA", "")
        entry: dict = {"erro": None}
        try:
            quote = sched.with_backoff(lambda t=tk: brapi.fetch_quote_full(t),
                                       retries=3, base=4.0, on_block=brapi.is_rate_limited)
        except Exception as exc:
            entry["erro"] = ("rate_limited" if brapi.is_rate_limited(exc) else f"erro: {exc}")[:200]
            report["tickers"][tk] = entry
            continue
        if not quote:
            entry["erro"] = "sem retorno (ticker free? requer token Pro?)"
            report["tickers"][tk] = entry
            continue

        data = nz.normalize_all(quote)
        blocos = {
            "perfil": bool((data["companies"] or [{}])[0].get("sector")),
            "cotacao": bool((quote or {}).get("regularMarketPrice")),
            "historico": len(data["historical_prices"]),
            "dre": len(data["income_statements"]),
            "bp": len(data["balance_sheets"]),
            "dfc": len(data["cash_flow_statements"]),
            "dividendos": len(data["dividends"]),
            "indicadores": len(data["calculated_metrics"]),
        }
        entry["blocos"] = blocos
        entry["faltando"] = [k for k, v in blocos.items() if not v]

        if do_persist:
            try:
                prog = _new_progress()
                ingest_ticker(engine, tk, range_="max", full=True, cvm_map=cvm_map, prog=prog)
                entry["persistido"] = True
                report["persistido"] = True
                # registra ausências relevantes
                with engine.begin() as conn:
                    for bloco in ("dre", "bp", "dfc", "dividendos"):
                        if not blocos[bloco]:
                            repo.log_quality(conn, ticker=tk, table_name=f"market/{bloco}",
                                             issue_type="missing", severity="warn",
                                             new_value=f"{bloco} ausente no payload BRAPI")
            except Exception as exc:
                entry["erro"] = f"persistência: {exc}"[:200]
        report["tickers"][tk] = entry
    return report


# ── Reprocessamento de indicadores (sem rede) ─────────────────────────────────

def reprocess_metrics(tickers: list[str] | None = None, limit: int | None = None) -> dict:
    """
    Recalcula indicadores derivados a partir de market.* (sem rede).
    Implementado: DY anual (dividendos do ano ÷ preço de fim de ano) e DY spot
    (12m ÷ último preço). Estende-se facilmente para P/VP, EV/EBITDA, etc.
    """
    import core.data_quality as dq
    engine = _engine()
    prog = _new_progress()
    if engine is None:
        return {**prog, "erros": -1}
    with engine.connect() as conn:
        if not repo.schema_exists(conn):
            return {**prog, "erros": -1}
        if tickers is None:
            rows = conn.execute(text("SELECT ticker FROM market.assets ORDER BY ticker")).fetchall()
            tickers = [r[0] for r in rows]
            if limit:
                tickers = tickers[:limit]

    for tk in tickers:
        try:
            with engine.begin() as conn:
                divs = conn.execute(text(
                    "SELECT event_date, amount FROM market.dividends WHERE ticker=:t"), {"t": tk}).fetchall()
                prices = conn.execute(text(
                    "SELECT date, COALESCE(adjusted_close, close) FROM market.historical_prices "
                    "WHERE ticker=:t AND COALESCE(adjusted_close, close) IS NOT NULL ORDER BY date"),
                    {"t": tk}).fetchall()
                if not divs or not prices:
                    continue
                year_end = {}
                for d, px in prices:
                    if d is not None and px:
                        year_end[d.year] = float(px)
                div_year: dict[int, float] = {}
                for d, amt in divs:
                    if d is not None and amt:
                        div_year[d.year] = div_year.get(d.year, 0.0) + float(amt)
                metric_rows = []
                for y, total in div_year.items():
                    px = year_end.get(y)
                    if px and px > 0:
                        dy = total / px
                        if dq.is_valid_value("DY", dy):
                            metric_rows.append({
                                "ticker": tk, "period": "annual", "year": y, "quarter": 0,
                                "metric_name": "DY", "metric_value": dy,
                                "calculation_method": "dividends/year_end_price",
                                "source": "market.dividends+historical_prices",
                                "confidence_score": 90.0,
                            })
                if metric_rows:
                    prog["indicadores"] += repo.upsert(conn, "calculated_metrics", metric_rows)
                    prog["tickers"] += 1
        except Exception as exc:
            logger.warning("reprocess %s: %s", tk, exc)
            prog["erros"] += 1
    logger.info("market/reprocess: %s", prog)
    return prog
