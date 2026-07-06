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
    elif source == "market":
        # apenas o que JÁ está carregado — universo do refresh diário/anual,
        # evita gastar cota com tickers ainda não bootstrapados.
        with engine.connect() as c:
            rows = c.execute(text(
                "SELECT ticker FROM market.assets ORDER BY ticker")).fetchall()
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
        # point-in-time (019): id do payload bruto vira proveniência das
        # linhas de demonstrações (raw_payload_id)
        pid = repo.save_raw_payload(conn, tk, "quote", quote, status="success")
        for _t_pit in ("income_statements", "balance_sheets", "cash_flow_statements"):
            for _r_pit in data.get(_t_pit) or []:
                _r_pit["raw_payload_id"] = pid
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
    repo.reset_db_cols_cache()  # migração pode ter sido aplicada com processo vivo
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

def enrich_setores() -> dict:
    """
    Resolve a taxonomia setorial de cada empresa em vocabulário B3 consistente,
    self-contained (busca o cad da CVM, aprende e aplica numa passada):
      1. cad_cia_aberta -> CD_CVM -> SETOR_ATIV (CVM, completo e atual);
      2. APRENDE o mapa CVM -> B3 (Setor/Subsetor/Segmento) das empresas que têm
         AMBAS (CVM + public.setores), POR NÍVEL com pureza (>=70%): setor CVM
         heterogêneo (ex.: "Transporte e Logística") só propaga o nível em que os
         pares concordam (evita rotular Rumo como "Linhas Aéreas");
      3. APLICA em market.companies p/ TODAS as empresas com setor CVM — mapeadas
         recebem o trio B3; sem mapa recebem o setor CVM cru (1 nível). Subsetor/
         Segmento são SEMPRE reescritos (apaga resíduo do brapi). Por empresa
         (codigo_cvm) => ON/PN/Unit herdam. public.setores continua autoritativo
         por ticker no load_setores.
    """
    import pandas as pd
    import core.cvm_cadastro as cad
    engine = _engine()
    prog = {"cad": 0, "mapa": 0, "empresas": 0, "mapeadas": 0, "cvm_raw": 0, "erros": 0}
    if engine is None:
        return {**prog, "erros": -1}
    raw = cad.fetch_cad()
    if not raw:
        return {**prog, "erros": -1}
    cd2cvm = {int(v["codigo_cvm"]): v["sector"]
              for v in cad.parse_cad(raw).values() if v.get("sector")}
    prog["cad"] = len(cd2cvm)

    with engine.connect() as conn:
        df = pd.read_sql_query(text("""
            SELECT DISTINCT c.codigo_cvm, a.ticker,
                   s."SETOR" AS b3_setor, s."SUBSETOR" AS b3_sub, s."SEGMENTO" AS b3_seg
            FROM market.companies c
            JOIN market.assets a ON a.company_id = c.id
            LEFT JOIN LATERAL (
                SELECT s2."SETOR", s2."SUBSETOR", s2."SEGMENTO" FROM public.setores s2
                WHERE s2."SETOR" IS NOT NULL
                  AND (UPPER(REPLACE(s2.ticker,'.SA',''))=a.ticker
                       OR LEFT(UPPER(REPLACE(s2.ticker,'.SA','')),4)=LEFT(a.ticker,4))
                ORDER BY (UPPER(REPLACE(s2.ticker,'.SA',''))=a.ticker) DESC LIMIT 1
            ) s ON TRUE
            WHERE c.codigo_cvm IS NOT NULL
        """), conn)
    df["cvm"] = df["codigo_cvm"].map(lambda x: cd2cvm.get(int(x)) if pd.notna(x) else None)

    THRESH = 0.70
    train = df[df["b3_setor"].notna() & df["cvm"].notna()]
    mapa: dict = {}
    for cvm, g in train.groupby("cvm"):
        n = len(g)
        sc = g["b3_setor"].value_counts()
        if sc.iloc[0] / n < THRESH:
            continue
        setor = sc.idxmax()
        gs = g[g["b3_setor"] == setor]
        sub = seg = ""
        subc = gs["b3_sub"].value_counts()
        if len(subc) and len(gs) >= 2 and subc.iloc[0] / len(gs) >= THRESH:
            sub = subc.idxmax()
            gsub = gs[gs["b3_sub"] == sub]
            segc = gsub["b3_seg"].value_counts()
            if len(segc) and len(gsub) >= 3 and segc.iloc[0] / len(gsub) >= THRESH:
                seg = segc.idxmax()
        mapa[str(cvm)] = (setor, sub or "", seg or "")
    prog["mapa"] = len(mapa)

    with engine.begin() as conn:
        for cd, cvm in cd2cvm.items():
            trio = mapa.get(str(cvm))
            if trio:
                setor, sub, seg = trio
                prog["mapeadas"] += 1
            else:
                setor, sub, seg = cvm, "", ""
                prog["cvm_raw"] += 1
            res = conn.execute(text(
                "UPDATE market.companies SET sector=:s, subsector=:su, segment=:se "
                "WHERE codigo_cvm=:c"),
                {"s": setor, "su": sub, "se": seg, "c": int(cd)})
            prog["empresas"] += res.rowcount or 0
    logger.info("market/enrich_setores: %s", prog)
    return prog


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
    repo.reset_db_cols_cache()  # migração pode ter sido aplicada com processo vivo
    with engine.connect() as conn:
        if not repo.schema_exists(conn):
            return {**prog, "erros": -1}
        cvm_map = repo.load_cvm_to_ticker(conn)
        # inclui o id do payload: proveniência das linhas re-normalizadas
        # (fix revisão 2026-07: sem isso o upsert sobrescrevia raw_payload_id
        # existente com NULL em todo backfill — o ON CONFLICT atualiza a coluna)
        q = ("SELECT DISTINCT ON (ticker) ticker, id, payload_json FROM market.brapi_raw_payloads "
             "WHERE endpoint='quote' AND request_status='success' AND payload_json IS NOT NULL")
        params: dict = {}
        if tickers:
            q += " AND ticker = ANY(:tks)"
            params["tks"] = [t.upper().replace(".SA", "") for t in tickers]
        q += " ORDER BY ticker, id DESC"
        rows = conn.execute(text(q), params).fetchall()
    if limit:
        rows = rows[:limit]

    for tk, pid, payload in rows:
        try:
            p = json.loads(payload) if isinstance(payload, str) else payload
            quote = (p.get("results") or [p])[0] if isinstance(p, dict) else None
            if not quote:
                continue
            data = nz.normalize_all(quote)
            for _t_pit in ("income_statements", "balance_sheets", "cash_flow_statements"):
                for _r_pit in data.get(_t_pit) or []:
                    _r_pit["raw_payload_id"] = pid
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
        ROIC, Endividamento_Total, Liquidez_Corrente, P/L, P/VP, EV_EBIT, P_FCO,
        DY, Payout;
      • histórico anual (period='annual', um conjunto por ano): fundamentais e DY
        exatos; valuation (P/L, P/VP, EV_EBIT, P_FCO, Payout) aproximado via ações
        atuais (confiança menor).
    Roda sobre as empresas já em market.assets (cresce com o bootstrap).
    """
    from datetime import datetime, timezone
    from data_pipeline.market import metrics as mx
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

    ref = datetime.now(timezone.utc).date()
    for tk in tickers:
        try:
            with engine.begin() as conn:
                inc = _latest_annual(conn, "income_statements",
                                     "revenue, ebit, ebitda, net_income, eps", tk)
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
                    "SELECT close FROM market.historical_prices "
                    "WHERE ticker=:t AND close IS NOT NULL "
                    "ORDER BY date DESC LIMIT 1"), {"t": tk}).scalar()
                # market cap: brapi quando disponível; senão deriva = preço × ações,
                # com ações = lucro anual / LPA anual (mesma empresa, ON/PN herdam).
                if (mc is None or float(mc) <= 0) and last_price and inc and inc[3] and inc[4]:
                    try:
                        shares_d = float(inc[3]) / float(inc[4])
                        if shares_d > 0:
                            mc = float(last_price) * shares_d
                    except (TypeError, ValueError, ZeroDivisionError):
                        pass
                divs = conn.execute(text(
                    "SELECT event_date, amount FROM market.dividends WHERE ticker=:t"),
                    {"t": tk}).fetchall()

                # dividendos POR AÇÃO nos últimos 366 dias (fallback p/ DY)
                div_ps_ttm = sum(float(a) for d, a in divs
                                 if d is not None and a is not None and 0 <= (ref - d).days <= 366)

                # ── Base do snapshot: TTM REAL (soma dos 4 últimos trimestres) ──
                # Metodologia de consenso (Fundamentus/SI): fluxos (receita/EBIT/
                # lucro/FCO) somam 4 trimestres; estoques (balanço) usam o trimestre
                # mais recente. Sem 4 trimestres completos → cai p/ o último anual.
                q_inc = conn.execute(text(
                    "SELECT year, quarter, revenue, ebit, ebitda, net_income "
                    "FROM market.income_statements "
                    "WHERE ticker=:t AND period='quarterly' ORDER BY year DESC, quarter DESC LIMIT 4"),
                    {"t": tk}).fetchall()
                q_cf = conn.execute(text(
                    "SELECT year, quarter, operating_cash_flow "
                    "FROM market.cash_flow_statements "
                    "WHERE ticker=:t AND period='quarterly' ORDER BY year DESC, quarter DESC LIMIT 4"),
                    {"t": tk}).fetchall()
                q_bal = conn.execute(text(
                    "SELECT year, quarter, total_assets, equity, cash, gross_debt, net_debt, "
                    "current_assets, current_liabilities FROM market.balance_sheets "
                    "WHERE ticker=:t AND period='quarterly' ORDER BY year DESC, quarter DESC LIMIT 1"),
                    {"t": tk}).fetchone()

                def _sum4(rows, i):
                    vals = [r[i] for r in rows if r[i] is not None]
                    return float(sum(vals)) if len(rows) == 4 and len(vals) == 4 else None

                def _quarter_keys(rows):
                    return [(int(r[0]), int(r[1])) for r in rows]

                def _four_consecutive(rows):
                    if len(rows) != 4:
                        return False
                    serial = [int(y) * 4 + int(q) for y, q in _quarter_keys(rows)]
                    return all(serial[i] - serial[i + 1] == 1 for i in range(3))

                inc_keys = _quarter_keys(q_inc)
                cf_keys = _quarter_keys(q_cf)
                aligned_balance = (
                    q_bal is not None and inc_keys
                    and (int(q_bal[0]), int(q_bal[1])) == inc_keys[0]
                )
                aligned_cf = cf_keys == inc_keys
                ttm_ni = _sum4(q_inc, 5) if _four_consecutive(q_inc) else None
                from_q = ttm_ni is not None and aligned_balance
                if from_q:
                    base_method = "ttm_4q"
                    f = {
                        "revenue": _sum4(q_inc, 2), "ebit": _sum4(q_inc, 3),
                        "ebitda": _sum4(q_inc, 4), "net_income": ttm_ni,
                        "total_assets": q_bal[2], "equity": q_bal[3], "cash": q_bal[4],
                        "gross_debt": q_bal[5], "net_debt": q_bal[6],
                        "current_assets": q_bal[7], "current_liabilities": q_bal[8],
                        "fco": _sum4(q_cf, 2) if aligned_cf else None, "market_cap": mc,
                        "div_ttm": div_ps_ttm or None, "price": last_price, "eps": eps,
                    }
                else:
                    base_method = "annual"
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
                snap = mx.compute_snapshot(f)

                # brapi spot: no caminho ANUAL sobrescreve (anual distorce por
                # não-recorrentes); no TTM real só PREENCHE o que faltar. DY sempre
                # do trailing da brapi (evita over-count da janela de dividendos).
                spot = {r[0]: float(r[1]) for r in conn.execute(text(
                    "SELECT metric_name, metric_value FROM market.calculated_metrics "
                    "WHERE ticker=:t AND period='spot'"), {"t": tk}).fetchall()}
                for k in ("P/L", "P/VP", "ROE", "ROA", "Margem_Liquida", "Margem_Operacional"):
                    v = spot.get(k)
                    if v is None or not dq.is_valid_value(k, v):
                        continue
                    if base_method == "annual" or k not in snap:
                        snap[k] = (round(v, 8), "brapi_trailing")
                dyv = spot.get("DY")
                if dyv is not None and dq.is_valid_value("DY", dyv):
                    snap["DY"] = (round(dyv, 8), "brapi_trailing")
                if "DY" in snap and "P/L" in snap:
                    pay = snap["DY"][0] * snap["P/L"][0]
                    if dq.is_valid_value("Payout", pay):
                        snap["Payout"] = (round(pay, 8), "DY*P/L")
                rows_out = mx.to_metric_rows(tk, snap)

                # ── Histórico anual (period='annual', um conjunto por ano) ──
                # Fundamentais (margens, ROE, ROA, ROIC, Endiv, Liquidez) e DY são
                # exatos das demonstrações/preço. Valuation (P/L, P/VP, EV_EBIT,
                # P_FCO, Payout): EXATO quando há LPA do ano (ações = NI/LPA, com
                # guarda); senão APROXIMADO via ações atuais → confiança menor.
                shares = (float(mc) / float(last_price)) if (mc and last_price) else None

                def _by_year(table: str, cols: str) -> dict:
                    return {int(r[0]): r[1:] for r in conn.execute(text(
                        f"SELECT year, {cols} FROM market.{table} "
                        f"WHERE ticker=:t AND period='annual'"), {"t": tk}).fetchall()}
                inc_y = _by_year("income_statements", "revenue, ebit, ebitda, net_income, eps")
                bal_y = _by_year("balance_sheets",
                                 "total_assets, equity, cash, gross_debt, net_debt, "
                                 "current_assets, current_liabilities")
                cf_y = _by_year("cash_flow_statements", "operating_cash_flow")

                year_end, div_year = {}, {}
                for d, px in conn.execute(text(
                    "SELECT date, close FROM market.historical_prices "
                    "WHERE ticker=:t AND close IS NOT NULL ORDER BY date"),
                        {"t": tk}).fetchall():
                    if d is not None and px:
                        year_end[d.year] = float(px)
                for d, amt in divs:
                    if d is not None and amt:
                        div_year[d.year] = div_year.get(d.year, 0.0) + float(amt)

                # Fix auditoria 2026-07 (ponto-no-tempo): itera apenas anos
                # com DEMONSTRAÇÃO publicada. Antes, set(div_year) criava
                # linha 'annual' para o ano corrente só com dividendos
                # parciais + preço de hoje (DY distorcido no histórico).
                for y in sorted(set(inc_y) | set(bal_y) | set(cf_y)):
                    i, b, cfy = inc_y.get(y), bal_y.get(y), cf_y.get(y)
                    px, dps = year_end.get(y), div_year.get(y)
                    ni = i[3] if i else None
                    eps_y = i[4] if i else None
                    # ações do ano: NI/LPA (exato) com guarda 0,2–5× das atuais;
                    # senão usa ações atuais (aproximação).
                    shares_y, exact = None, False
                    if eps_y and ni:
                        cand = float(ni) / float(eps_y)
                        if cand > 0 and (not shares or 0.2 <= cand / shares <= 5.0):
                            shares_y, exact = cand, True
                    eps_use = float(eps_y) if exact and eps_y else None
                    f_y = {
                        "revenue": i[0] if i else None, "ebit": i[1] if i else None,
                        "ebitda": i[2] if i else None, "net_income": ni,
                        "total_assets": b[0] if b else None, "equity": b[1] if b else None,
                        "cash": b[2] if b else None, "gross_debt": b[3] if b else None,
                        "net_debt": b[4] if b else None,
                        "current_assets": b[5] if b else None,
                        "current_liabilities": b[6] if b else None,
                        "fco": cfy[0] if cfy else None,
                        "market_cap": (px * shares_y) if (px and shares_y) else None,
                        "price": px, "eps": eps_use, "div_ttm": dps,
                    }
                    rows_out += mx.to_metric_rows(tk, mx.compute_snapshot(f_y),
                                                  period="annual", year=y,
                                                  low_conf=None if exact else mx.ANNUAL_APPROX)
                if rows_out:
                    repo.append_metric_vintages(conn, rows_out)
                    prog["indicadores"] += repo.upsert(conn, "calculated_metrics", rows_out)
                    prog["tickers"] += 1
        except Exception as exc:
            logger.warning("reprocess %s: %s", tk, exc)
            prog["erros"] += 1
    logger.info("market/reprocess: %s", prog)
    return prog
