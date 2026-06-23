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
    elif source == "ticker_cvm":
        with engine.connect() as c:
            rows = c.execute(text(
                "SELECT ticker FROM market.ticker_cvm ORDER BY ticker")).fetchall()
        tks = [str(r[0]).upper().replace(".SA", "") for r in rows if r[0]]
    else:
        with engine.connect() as c:
            rows = c.execute(text(
                "SELECT DISTINCT ticker FROM public.setores WHERE ticker IS NOT NULL ORDER BY ticker"
            )).fetchall()
        tks = [str(r[0]).upper().replace(".SA", "") for r in rows if r[0]]
    return tks[:limit] if limit else tks


# ── Estado do bootstrap (retomada por ticker) ────────────────────────────────

def _ensure_bootstrap_state(conn) -> None:
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS market.bootstrap_state (
            ticker TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'ok',
            error  TEXT,
            done_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))


def _bootstrap_done(conn) -> set[str]:
    rows = conn.execute(text(
        "SELECT ticker FROM market.bootstrap_state WHERE status='ok'")).fetchall()
    return {str(r[0]).upper() for r in rows}


def _carteira_tickers(conn) -> set[str]:
    try:
        rows = conn.execute(text(
            "SELECT DISTINCT ticker FROM public.b3_portfolio_model_items")).fetchall()
        return {str(r[0]).upper().replace(".SA", "") for r in rows if r[0]}
    except Exception:
        return set()


def _mark_bootstrap(engine, ticker: str, status: str, error: str | None = None) -> None:
    with engine.begin() as conn:
        _ensure_bootstrap_state(conn)
        conn.execute(text("""
            INSERT INTO market.bootstrap_state (ticker, status, error, done_at)
            VALUES (:t, :s, :e, NOW())
            ON CONFLICT (ticker) DO UPDATE SET status=EXCLUDED.status,
                error=EXCLUDED.error, done_at=NOW()
        """), {"t": ticker, "s": status, "e": (str(error)[:500] if error else None)})


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

def bootstrap(tickers: list[str] | None = None, source: str = "ticker_cvm",
              batch: int = 50) -> dict:
    """
    Baixa 16 anos de histórico (range=max + módulos), RETOMÁVEL por ticker.
    Processa até `batch` empresas pendentes por execução (carteira primeiro),
    marca o estado em market.bootstrap_state e reporta quantas faltam. Re-execute
    (ou agende) até `restantes`=0. Circuito anti-bloqueio e throttle inclusos.
    """
    engine = _engine()
    prog = _new_progress()
    if engine is None:
        return {**prog, "erros": -1, "restantes": None}
    with engine.begin() as conn:
        if not repo.schema_exists(conn):
            return {**prog, "erros": -1, "restantes": None, "msg": "schema market.* ausente"}
        _ensure_bootstrap_state(conn)

    universe = [t.upper().replace(".SA", "") for t in tickers] if tickers \
        else _universe(engine, source)
    with engine.connect() as conn:
        done = _bootstrap_done(conn)
        carteira = _carteira_tickers(conn)
        cvm_map = repo.load_cvm_to_ticker(conn)
    pending = [t for t in universe if t not in done]
    pending.sort(key=lambda t: (t not in carteira, t))  # carteira primeiro
    lote = pending[:batch]

    delay = float(os.getenv("MARKET_DELAY", "1.5"))
    max_blocks = int(os.getenv("MARKET_MAX_BLOCKS", "3"))
    blocks = 0
    for i, tk in enumerate(lote):
        try:
            ingest_ticker(engine, tk, range_="max", full=True, cvm_map=cvm_map, prog=prog)
            _mark_bootstrap(engine, tk, "ok")
            blocks = 0
        except Exception as exc:
            _mark_bootstrap(engine, tk, "error", str(exc))
            if brapi.is_rate_limited(exc):
                blocks += 1
                if blocks >= max_blocks:
                    logger.warning("bootstrap: disjuntor (429) — encerrando run.")
                    break
        if i < len(lote) - 1:
            sched.sleep_jittered(base=delay)

    prog["restantes"] = max(0, len(set(universe) - done) - prog["tickers"])
    prog["universo"] = len(universe)
    logger.info("market/bootstrap: %s", prog)
    return prog


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


# ── Cadastro CVM: completa ticker->codigo_cvm e market.companies ──────────────

def cadastro(apply: bool = True, years: list[int] | None = None) -> dict:
    """
    Completa o mapa ticker->codigo_cvm (public.cvm_to_ticker) e popula
    market.companies a partir do cadastro oficial da CVM (cad + FCA).
    apply=False = dry-run (só conta). Idempotente.
    """
    import core.cvm_cadastro as cad
    from datetime import datetime, timezone
    engine = _engine()
    rep = {"tickers_no_mapa": 0, "novos_cvm_to_ticker": 0, "companies_upsert": 0,
           "apply": apply, "erro": None}
    if engine is None:
        rep["erro"] = "banco não conectado"
        return rep

    cad_bytes = cad.fetch_cad()
    if not cad_bytes:
        rep["erro"] = "falha ao baixar cad_cia_aberta"
        return rep
    cad_map = cad.parse_cad(cad_bytes)

    now_year = datetime.now(timezone.utc).year
    years = years or [now_year, now_year - 1, now_year - 2]
    fca_all: list[dict] = []
    for y in years:
        b = cad.fetch_fca_valmob(y)
        if b:
            fca_all.extend(cad.parse_fca_valmob(b))

    ticker_to_cod, companies = cad.build_map(cad_map, fca_all)
    rep["tickers_no_mapa"] = len(ticker_to_cod)

    if not apply:
        rep["companies_calculadas"] = len(companies)
        return rep

    with engine.begin() as conn:
        if not repo.schema_exists(conn):
            rep["erro"] = "schema market.* ausente (rode 013)"
            return rep
        # tabela do mapa (muitos tickers por empresa) — idempotente
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS market.ticker_cvm (
                ticker TEXT PRIMARY KEY, codigo_cvm INTEGER NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())
        """))
        # garante índice único CHEIO em codigo_cvm (troca o parcial legado, se houver)
        conn.execute(text("DROP INDEX IF EXISTS market.uq_companies_codigo_cvm"))
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_companies_codigo_cvm "
                          "ON market.companies (codigo_cvm)"))
        # 1) mapa ticker->codigo_cvm (upsert por ticker)
        map_rows = [{"ticker": tk, "codigo_cvm": cod} for tk, cod in ticker_to_cod.items()]
        rep["novos_cvm_to_ticker"] = repo.upsert(conn, "ticker_cvm", map_rows)
        # 2) empresas (upsert por codigo_cvm)
        if companies:
            rep["companies_upsert"] = repo.upsert(conn, "companies", list(companies.values()))
    logger.info("market/cadastro: %s", rep)
    return rep


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
    import time
    t0 = time.time()
    engine = _engine()
    report: dict = {"brapi_token": _token_status(), "schema_market": False,
                    "persistido": False, "tickers": {}, "requisicoes_estimadas": 0}
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

        report["requisicoes_estimadas"] += 1
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

    # contagem/duplicidade por tabela + tempo
    try:
        with engine.connect() as conn:
            report["contagem_tabelas"] = repo.market_table_stats(conn)
    except Exception:
        report["contagem_tabelas"] = {}
    report["duplicidades"] = {t: s["duplicados"] for t, s in
                              report.get("contagem_tabelas", {}).items()
                              if s.get("duplicados")}
    report["tempo_s"] = round(time.time() - t0, 1)
    return report


# ── Reprocessamento de indicadores (sem rede) ─────────────────────────────────

def renormalize(tickers: list[str] | None = None, limit: int | None = None) -> dict:
    """
    Reaplica o normalizador atual aos payloads brutos JÁ salvos em
    market.brapi_raw_payloads (SEM rede) e regrava market.*. Use após corrigir
    nz.normalize_all para backfill sem novas chamadas à BRAPI.
    """
    import json
    engine = _engine()
    prog = _new_progress()
    if engine is None:
        return {**prog, "erros": -1}
    with engine.connect() as conn:
        if not repo.schema_exists(conn):
            return {**prog, "erros": -1}
        cvm_map = repo.load_cvm_to_ticker(conn)
        q = ("SELECT DISTINCT ON (ticker) ticker, payload_json FROM market.brapi_raw_payloads "
             "WHERE endpoint='quote' AND request_status='success' AND payload_json IS NOT NULL")
        params: dict = {}
        if tickers:
            q += " AND ticker = ANY(:tks)"
            params["tks"] = [t.upper().replace(".SA", "") for t in tickers]
        q += " ORDER BY ticker, id DESC"
        rows = conn.execute(text(q), params).fetchall()
    if limit:
        rows = rows[:limit]

    for tk, payload in rows:
        try:
            p = json.loads(payload) if isinstance(payload, str) else payload
            quote = (p.get("results") or [p])[0] if isinstance(p, dict) else None
            if not quote:
                continue
            data = nz.normalize_all(quote)
            cod = cvm_map.get(tk)
            with engine.begin() as conn:
                comp = data["companies"]
                if comp and cod is not None:
                    comp[0]["codigo_cvm"] = cod
                    repo.upsert(conn, "companies", comp)
                    prog["empresas"] += 1
                ast = data["assets"]
                if ast:
                    ast[0]["company_id"] = repo.company_id_by_codigo(conn, cod) if cod is not None else None
                    repo.upsert(conn, "assets", ast)
                prog["precos"] += repo.upsert(conn, "historical_prices", data["historical_prices"])
                for t in ("income_statements", "balance_sheets", "cash_flow_statements"):
                    prog["demonstracoes"] += repo.upsert(conn, t, data[t])
                prog["dividendos"] += repo.upsert(conn, "dividends", data["dividends"])
                prog["indicadores"] += repo.upsert(conn, "calculated_metrics", data["calculated_metrics"])
            prog["tickers"] += 1
        except Exception as exc:
            logger.warning("renormalize %s: %s", tk, exc)
            prog["erros"] += 1
    logger.info("market/renormalize: %s", prog)
    return prog


def _latest_annual(conn, table: str, cols: str, tk: str):
    row = conn.execute(text(
        f"SELECT {cols} FROM market.{table} WHERE ticker=:t AND period='annual' "
        f"ORDER BY year DESC LIMIT 1"), {"t": tk}).fetchone()
    return row


def reprocess_metrics(tickers: list[str] | None = None, limit: int | None = None) -> dict:
    """
    Recalcula indicadores derivados a partir de market.* (SEM rede):
      • snapshot completo (period='ttm'): Margem_Liquida/Operacional, ROE, ROA,
        ROIC, Endividamento_Total, P/L, P/VP, EV_EBIT, P_FCO, DY, Payout;
      • DY anual (period='annual') por dividendos/preço de fim de ano.
    Roda sobre as empresas já em market.assets (cresce com o bootstrap).
    """
    import core.data_quality as dq
    from datetime import datetime, timezone
    from data_pipeline.market import metrics as mx
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

    ref = datetime.now(timezone.utc).date()
    for tk in tickers:
        try:
            with engine.begin() as conn:
                inc = _latest_annual(conn, "income_statements", "revenue, ebit, ebitda, net_income", tk)
                bal = _latest_annual(conn, "balance_sheets",
                                     "total_assets, equity, cash, gross_debt, net_debt, "
                                     "current_assets, current_liabilities", tk)
                cf = _latest_annual(conn, "cash_flow_statements", "operating_cash_flow", tk)
                mc = conn.execute(text(
                    "SELECT metric_value FROM market.calculated_metrics "
                    "WHERE ticker=:t AND metric_name='marketCap' ORDER BY year DESC LIMIT 1"),
                    {"t": tk}).scalar()
                eps = conn.execute(text(
                    "SELECT metric_value FROM market.calculated_metrics "
                    "WHERE ticker=:t AND metric_name='LPA' ORDER BY year DESC LIMIT 1"),
                    {"t": tk}).scalar()
                last_price = conn.execute(text(
                    "SELECT COALESCE(adjusted_close, close) FROM market.historical_prices "
                    "WHERE ticker=:t AND COALESCE(adjusted_close, close) IS NOT NULL "
                    "ORDER BY date DESC LIMIT 1"), {"t": tk}).scalar()
                divs = conn.execute(text(
                    "SELECT event_date, amount FROM market.dividends WHERE ticker=:t"),
                    {"t": tk}).fetchall()

                # dividendos POR AÇÃO nos últimos 366 dias (base p/ DY e Payout)
                div_ps_ttm = sum(float(a) for d, a in divs
                                 if d is not None and a is not None and 0 <= (ref - d).days <= 366)
                f = {
                    "revenue": inc[0] if inc else None, "ebit": inc[1] if inc else None,
                    "ebitda": inc[2] if inc else None, "net_income": inc[3] if inc else None,
                    "total_assets": bal[0] if bal else None, "equity": bal[1] if bal else None,
                    "cash": bal[2] if bal else None, "gross_debt": bal[3] if bal else None,
                    "net_debt": bal[4] if bal else None,
                    "current_assets": bal[5] if bal else None,
                    "current_liabilities": bal[6] if bal else None,
                    "fco": cf[0] if cf else None, "market_cap": mc,
                    "div_ttm": div_ps_ttm or None, "price": last_price, "eps": eps,
                }
                rows_out = mx.to_metric_rows(tk, mx.compute_snapshot(f))

                # DY anual (histórico)
                year_end, div_year = {}, {}
                for d, px in conn.execute(text(
                    "SELECT date, COALESCE(adjusted_close, close) FROM market.historical_prices "
                    "WHERE ticker=:t AND COALESCE(adjusted_close, close) IS NOT NULL ORDER BY date"),
                        {"t": tk}).fetchall():
                    if d is not None and px:
                        year_end[d.year] = float(px)
                for d, amt in divs:
                    if d is not None and amt:
                        div_year[d.year] = div_year.get(d.year, 0.0) + float(amt)
                for y, total in div_year.items():
                    px = year_end.get(y)
                    if px and px > 0 and dq.is_valid_value("DY", total / px):
                        rows_out.append({
                            "ticker": tk, "period": "annual", "year": y, "quarter": 0,
                            "metric_name": "DY", "metric_value": total / px,
                            "calculation_method": "dividends/year_end_price",
                            "source": "market.dividends+historical_prices", "confidence_score": 90.0,
                        })
                if rows_out:
                    prog["indicadores"] += repo.upsert(conn, "calculated_metrics", rows_out)
                    prog["tickers"] += 1
        except Exception as exc:
            logger.warning("reprocess %s: %s", tk, exc)
            prog["erros"] += 1
    logger.info("market/reprocess: %s", prog)
    return prog
