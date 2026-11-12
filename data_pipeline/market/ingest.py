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
from core.b3_troca_codigo import MOTIVO_TROCA, MOTIVO_TROCA_PENDENTE
from data_pipeline.market import normalize as nz
from data_pipeline.market import repository as repo
from data_pipeline.market.troca_codigo import carregar_trocas, e_troca
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


def _ensure_ticker_alias(conn) -> None:
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS market.ticker_alias (
            brapi_symbol TEXT PRIMARY KEY,
            b3_ticker    TEXT NOT NULL,
            codigo_cvm   INTEGER,
            motivo       TEXT,
            created_at   TIMESTAMPTZ DEFAULT NOW()
        )
    """))


def _reconcile_ticker(data: dict, tk: str) -> str | None:
    """
    A brapi devolve, para parte das empresas, um símbolo DIVERGENTE do ticker de
    negociação B3 requisitado — rebrand (Eletrobras ELET3 -> AXIA3, CCR CCRO3 ->
    MOTV3), troca de classe (Azul AZUL4 -> AZUL3) ou erro da própria brapi
    (Embraer EMBR3 -> EMBJ3). Como o app casa por ticker-B3 (public.setores),
    gravar sob o símbolo da brapi cria um asset ÓRFÃO invisível.

    Força o ticker de TODAS as linhas de fato para `tk` (o requisitado) e devolve
    o símbolo divergente (para registrar em market.ticker_alias) ou None.
    O company_id continua ancorado no CVM (cvm_map), então a identidade da
    empresa é preservada independentemente do símbolo.
    """
    devolvido = None
    ast = data.get("assets") or []
    if ast:
        sym = str(ast[0].get("ticker") or "").upper().replace(".SA", "")
        if sym and sym != tk:
            devolvido = sym
    for key in ("assets", "historical_prices", "income_statements",
                "balance_sheets", "cash_flow_statements", "dividends",
                "calculated_metrics"):
        for row in data.get(key) or []:
            if "ticker" in row:
                row["ticker"] = tk
    return devolvido


def _carregar_trocas(engine) -> dict[str, str]:
    # conexão própria: load_cvm_to_ticker engole erro de tabela ausente e pode
    # deixar a transação dele abortada
    with engine.connect() as conn:
        return carregar_trocas(conn)


def _pular_substituido(tk: str, trocas: dict[str, str] | None, prog: dict) -> bool:
    """Código já trocado não é mais ingerido: a brapi devolve o histórico dele
    sob o código atual, e regravá-lo recriaria o ativo duplicado."""
    if not trocas or tk not in trocas:
        return False
    logger.info("%s: código substituído por %s — fica de fora da ingestão", tk, trocas[tk])
    prog["codigos_substituidos"] = prog.get("codigos_substituidos", 0) + 1
    return True


def _troca_provada(conn, cvm_map, tk: str, alias_sym: str | None,
                   trocas: dict[str, str] | None) -> str | None:
    """O símbolo divergente é o código novo da MESMA companhia e classe?
    Então é troca de código, e o histórico passa a ser dele (não do antigo)."""
    if alias_sym and e_troca(conn, cvm_map, tk, alias_sym, trocas):
        return alias_sym
    return None


def _codigo_cvm(cvm_map: dict, tk: str, novo: str | None,
                trocas: dict[str, str] | None):
    """codigo_cvm do código que fica: o do atual, o do requisitado e, na falta
    dos dois, o de um código antigo trocado por ele. A referência da CVM às
    vezes só conhece o código antigo (ELET3); sem o elo da troca o atual
    ficaria sem companhia."""
    for t in (novo, tk):
        if t and cvm_map.get(t) is not None:
            return cvm_map[t]
    dono = novo or tk
    for antigo, atual in sorted((trocas or {}).items()):
        if atual == dono and cvm_map.get(antigo) is not None:
            return cvm_map[antigo]
    return None


def _company_id(conn, cod, ticker: str):
    """company_id pelo codigo_cvm; sem ele, o que o ativo já tem. O upsert de
    assets grava ``company_id = EXCLUDED.company_id``: um None aqui apagaria a
    identidade, e o ativo sairia do universo (load_setores exige company_id)."""
    cid = repo.company_id_by_codigo(conn, cod) if cod is not None else None
    if cid is None:
        cid = conn.execute(text("SELECT company_id FROM market.assets WHERE ticker = :t"),
                           {"t": ticker}).scalar()
    return cid


def _registrar_troca_pendente(conn, antigo: str, novo: str, cod, prog: dict) -> None:
    """Troca provada na ingestão fica PENDENTE, não migrada.

    Mover o histórico, descartar o que colide e desativar o código antigo é
    migração: exige ``scripts/unificar_trocas_de_codigo_b3.py --apply`` (com
    backup e, no Supabase, ``--confirmo-producao``), nunca a rotina agendada
    (AGENTS.md: confirmação humana). Aqui só fica o alias antigo -> atual com
    ``MOTIVO_TROCA_PENDENTE`` — a tela já deduplica e o dry-run lista o par —
    e o aviso com o comando. Alias do código antigo para outro destino, ou a
    troca já migrada (``MOTIVO_TROCA``), não é sobrescrito.
    """
    _ensure_ticker_alias(conn)
    conn.execute(text("""
        INSERT INTO market.ticker_alias (brapi_symbol, b3_ticker, codigo_cvm, motivo)
        VALUES (:a, :n, :c, :m)
        ON CONFLICT (brapi_symbol) DO UPDATE
            SET motivo = EXCLUDED.motivo,
                codigo_cvm = COALESCE(EXCLUDED.codigo_cvm, market.ticker_alias.codigo_cvm)
            WHERE market.ticker_alias.b3_ticker = EXCLUDED.b3_ticker
              AND market.ticker_alias.motivo IS DISTINCT FROM EXCLUDED.motivo
              AND market.ticker_alias.motivo IS DISTINCT FROM :t
    """), {"a": antigo, "n": novo, "c": cod, "m": MOTIVO_TROCA_PENDENTE, "t": MOTIVO_TROCA})
    prog["trocas_pendentes"] = prog.get("trocas_pendentes", 0) + 1
    logger.warning("%s -> %s: troca de código provada e NÃO migrada; revise com "
                   "scripts/unificar_trocas_de_codigo_b3.py --pares %s:%s e aplique "
                   "com --apply", antigo, novo, antigo, novo)


def ingest_ticker(engine, ticker: str, *, range_: str, full: bool,
                  cvm_map: dict[str, int], prog: dict,
                  interval: str = "1mo", trocas: dict[str, str] | None = None) -> None:
    """Busca 1 ticker na brapi e grava tudo em market.* (1 transação).

    ``interval`` é a granularidade das velas e precisa acompanhar ``range_``.
    O default da brapi é mensal: pedir range=1mo sem dizer o intervalo devolve
    as duas bordas do mês, não os pregões dele — foi assim que o refresh
    diário passou meses sem trazer preço novo.

    ``trocas`` ({antigo: atual}, de ``carregar_trocas``): o código antigo é
    pulado antes da rede. Quando a brapi devolve outro símbolo e ele é troca
    de código provada, grava sob o código atual e registra a troca como
    pendente — o histórico antigo só muda de dono pelo script, com aprovação.
    """
    tk = ticker.upper().replace(".SA", "")
    if _pular_substituido(tk, trocas, prog):
        return
    fetch = (lambda: brapi.fetch_quote_full(tk, range_=range_, interval=interval)) if full else \
            (lambda: brapi.fetch_quote(tk, range_=range_, interval=interval,
                                       dividends=True, fundamental=True))
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

    # O refresh diário vem sem ``defaultKeyStatistics``: a composição (ações
    # emitidas e os campos de k) sai do quote completo mais novo, como no
    # renormalize. Sem ela, o diário caía no marketCap da brapi e sobrescrevia
    # o valor corrigido pelo completo anterior.
    from data_pipeline.market import acoes as _ac
    composicao = None
    if not _ac.tem_composicao(quote):
        with engine.connect() as conn:
            composicao = _composicoes_mais_novas(conn, [tk]).get(tk)
    data = nz.normalize_all(quote, composicao)
    # Reconciliação: grava sob o ticker-B3 requisitado, não o símbolo divergente
    # que a brapi possa devolver (evita assets órfãos invisíveis no app).
    alias_sym = _reconcile_ticker(data, tk)
    with engine.begin() as conn:
        # point-in-time (019): id do payload bruto vira proveniência das
        # linhas de demonstrações (raw_payload_id)
        pid = repo.save_raw_payload(conn, tk, "quote", quote, status="success")
        novo = _troca_provada(conn, cvm_map, tk, alias_sym, trocas)
        if novo:
            _reconcile_ticker(data, novo)
        elif alias_sym:
            _ensure_ticker_alias(conn)
            conn.execute(text("""
                INSERT INTO market.ticker_alias (brapi_symbol, b3_ticker, codigo_cvm, motivo)
                VALUES (:s, :b, :c, 'auto-ingest: símbolo brapi divergente do ticker B3')
                ON CONFLICT (brapi_symbol) DO UPDATE
                    SET b3_ticker = EXCLUDED.b3_ticker, codigo_cvm = EXCLUDED.codigo_cvm
            """), {"s": alias_sym, "b": tk, "c": cvm_map.get(tk)})
        for _t_pit in ("income_statements", "balance_sheets", "cash_flow_statements"):
            for _r_pit in data.get(_t_pit) or []:
                _r_pit["raw_payload_id"] = pid
        # empresa (enriquece codigo_cvm pelo mapa CVM)
        comp = data["companies"]
        cod = _codigo_cvm(cvm_map, tk, novo, trocas)
        if comp and cod is not None:
            comp[0]["codigo_cvm"] = cod
            repo.upsert(conn, "companies", comp)
            prog["empresas"] += 1
        # asset (liga company_id se houver; sem código, mantém o que tinha)
        ast = data["assets"]
        if ast:
            ast[0]["company_id"] = _company_id(conn, cod, ast[0]["ticker"])
            repo.upsert(conn, "assets", ast)
        # fatos
        prog["precos"] += repo.upsert(conn, "historical_prices", data["historical_prices"])
        dem = 0
        for t in ("income_statements", "balance_sheets", "cash_flow_statements"):
            dem += repo.upsert(conn, t, data[t])
        prog["demonstracoes"] += dem
        prog["dividendos"] += repo.upsert(conn, "dividends", data["dividends"])
        prog["indicadores"] += _gravar_indicadores(conn, data, novo or tk, quote, composicao)
        if novo:
            _registrar_troca_pendente(conn, tk, novo, cod, prog)
        # qualidade: sinaliza ausências relevantes
        if full and dem == 0:
            repo.log_quality(conn, ticker=novo or tk, table_name="income_statements",
                             issue_type="missing", severity="warn",
                             new_value="sem demonstrações no payload")
    prog["tickers"] += 1


def _run(engine, tickers: list[str], *, range_: str, full: bool,
         batch_label: str, delay: float | None = None,
         interval: str = "1mo") -> dict:
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
    trocas = _carregar_trocas(engine)

    blocks = 0
    for i, tk in enumerate(tickers):
        try:
            ingest_ticker(engine, tk, range_=range_, full=full, cvm_map=cvm_map,
                          prog=prog, interval=interval, trocas=trocas)
            blocks = 0
        except Exception as exc:
            if brapi.is_rate_limited(exc):
                blocks += 1
                if blocks >= max_blocks:
                    # Cota esgotada não é aviso: de 30/10 a 07/11/2026 todo
                    # `quote` voltou 429, o lote parava no 3º ticker com
                    # tickers=0 e o comando saía 0 -- a rotina carimbava a
                    # cadeia em dia com o preço parado. Mesma regra do gêmeo
                    # de FIIs (D2): o coletado fica, o comando sai com erro.
                    prog["bloqueio_brapi"] = True
                    prog["nao_tentados"] = len(tickers) - i - 1
                    logger.error("%s: disjuntor (429) após %d bloqueios — encerrando run "
                                 "com %d ticker(s) gravados e %d não tentados.",
                                 batch_label, blocks, prog["tickers"],
                                 prog["nao_tentados"])
                    break
            else:
                logger.warning("%s: %s falhou: %s", batch_label, tk, exc)
        if i < len(tickers) - 1:
            sched.sleep_jittered(base=delay)
    # Integridade pós-run: ecos de dividendos (classe/fonte CSV da brapi) que
    # tenham escapado do dedup do normalizador viram warn no painel de
    # qualidade em vez de corromper silenciosamente somas 12m e DY (caso CEB).
    try:
        from data_pipeline.market import integrity
        eco = integrity.check_dividend_echoes(engine, tickers=tickers)
        if eco.get("linhas_eco"):
            prog["ecos_dividendos"] = eco["linhas_eco"]
    except Exception as exc:
        logger.warning("%s: checagem de ecos de dividendos falhou: %s", batch_label, exc)
    if prog.get("bloqueio_brapi"):
        prog["erros"] = -1  # exit 1 no runner; o que já foi coletado foi gravado acima
    logger.info("market/%s: %s", batch_label, prog)
    return prog


# ── Comandos ──────────────────────────────────────────────────────────────────

def bootstrap(tickers: list[str] | None = None, source: str = "setores",
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
    trocas = _carregar_trocas(engine)
    # código substituído não conta como pendente: nunca seria baixado
    universe = [t for t in universe if t not in trocas]
    pending = [t for t in universe if t not in done]
    pending.sort(key=lambda t: (t not in carteira, t))  # carteira primeiro
    lote = pending[:batch]

    delay = float(os.getenv("MARKET_DELAY", "1.5"))
    max_blocks = int(os.getenv("MARKET_MAX_BLOCKS", "3"))
    blocks = 0
    for i, tk in enumerate(lote):
        try:
            ingest_ticker(engine, tk, range_="max", full=True, cvm_map=cvm_map, prog=prog,
                          trocas=trocas)
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
    """Atualização leve: preços recentes + dividendos + indicadores spot.

    ``interval="1d"`` é o ponto todo desta função. Sem ele a brapi aplica o
    default mensal e devolve duas velas para o mês inteiro: o preço mais novo
    do universo travava na última borda de mês, e a idade mediana do preço
    ficava perto de 30 dias com a ingestão aparentemente saudável — nenhum
    erro, nenhum ticker perdido, só granularidade errada.
    """
    engine = _engine()
    if engine is None:
        return {**_new_progress(), "erros": -1}
    tks = tickers or _universe(engine, source, limit)
    return _run(engine, tks, range_="1mo", full=False, batch_label="daily",
                interval="1d")


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
    from datetime import datetime, timezone

    import core.cvm_cadastro as cad
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
    trocas = _carregar_trocas(engine) if schema_ok else {}
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
                ingest_ticker(engine, tk, range_="max", full=True, cvm_map=cvm_map, prog=prog,
                              trocas=trocas)
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


def _gravar_indicadores(conn, data, ticker, quote, composicao=None) -> int:
    """Grava as linhas de indicador (vintages e spot). O upsert não apaga, e
    as ``acoes.METRICAS_DE_COMPOSICAO`` só valem juntas, da mesma fonte.
    Quando o quote tem a composição (própria ou a do último completo) ou
    emite ações emitidas novas, e mesmo assim não emite uma delas (k não
    fecha), a linha antiga sai: o k velho não fica ao lado das ações novas,
    que o reprocess juntaria. O quote diário sem composição e sem ações não
    apaga nada: o k do último completo continua valendo."""
    from data_pipeline.market import acoes as _ac
    linhas = data["calculated_metrics"]
    repo.append_metric_vintages(conn, linhas)
    n = repo.upsert(conn, "calculated_metrics", linhas)
    acoes_novas = any(str(r.get("period")) == "spot"
                      and r.get("metric_name") == "Acoes_Emitidas" for r in linhas)
    if acoes_novas or _ac.composicao_conhecida(quote, composicao):
        repo.apagar_composicao_omitida(conn, ticker, linhas)
    return n


def _composicoes_mais_novas(conn, tickers=None) -> dict:
    """{ticker: defaultKeyStatistics} do quote completo mais novo, só com os
    campos de ``acoes.COMPOSICAO``. O quote mais novo pode ser o do refresh
    diário, que vem sem eles (``acoes.quote_com_composicao``)."""
    import json
    q = ("SELECT DISTINCT ON (ticker) ticker, jsonb_build_object("
         "'sharesOutstanding', payload_json->'defaultKeyStatistics'->'sharesOutstanding', "
         "'trailingEps', payload_json->'defaultKeyStatistics'->'trailingEps', "
         "'netIncomeToCommon', payload_json->'defaultKeyStatistics'->'netIncomeToCommon') "
         "FROM market.brapi_raw_payloads "
         "WHERE endpoint='quote' AND request_status='success' "
         "AND payload_json->'defaultKeyStatistics' ?& "
         "array['sharesOutstanding','trailingEps','netIncomeToCommon']")
    params: dict = {}
    if tickers:
        q += " AND ticker = ANY(:tks)"
        params["tks"] = list(tickers)
    # Payload arquivado ganha id novo: a ordem é a da coleta, id só desempata.
    q += " ORDER BY ticker, fetched_at DESC, id DESC"
    out = {}
    for tk, dks in conn.execute(text(q), params).fetchall():
        out[str(tk).upper()] = json.loads(dks) if isinstance(dks, str) else dks
    return out


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
        q += " ORDER BY ticker, fetched_at DESC, id DESC"
        rows = conn.execute(text(q), params).fetchall()
        composicoes = _composicoes_mais_novas(conn, params.get("tks"))
    trocas = _carregar_trocas(engine)
    if limit:
        rows = rows[:limit]

    for tk, pid, payload in rows:
        if _pular_substituido(tk, trocas, prog):
            continue
        try:
            p = json.loads(payload) if isinstance(payload, str) else payload
            quote = (p.get("results") or [p])[0] if isinstance(p, dict) else None
            if not quote:
                continue
            data = nz.normalize_all(quote, composicoes.get(tk))
            # Reconciliação: grava sob o ticker-B3 da coluna do payload (o
            # requisitado), não o símbolo divergente que a brapi possa devolver.
            # Sem isto, um backfill recria os assets órfãos que o ingest evita
            # (AXIA3 em vez de ELET3 etc. — ver market.ticker_alias, PR #45).
            alias_sym = _reconcile_ticker(data, tk)
            for _t_pit in ("income_statements", "balance_sheets", "cash_flow_statements"):
                for _r_pit in data.get(_t_pit) or []:
                    _r_pit["raw_payload_id"] = pid
            with engine.begin() as conn:
                novo = _troca_provada(conn, cvm_map, tk, alias_sym, trocas)
                cod = _codigo_cvm(cvm_map, tk, novo, trocas)
                if novo:
                    _reconcile_ticker(data, novo)
                elif alias_sym:
                    _ensure_ticker_alias(conn)
                    conn.execute(text("""
                        INSERT INTO market.ticker_alias (brapi_symbol, b3_ticker, codigo_cvm, motivo)
                        VALUES (:s, :b, :c, 'renormalize: símbolo brapi divergente do ticker B3')
                        ON CONFLICT (brapi_symbol) DO UPDATE
                            SET b3_ticker = EXCLUDED.b3_ticker, codigo_cvm = EXCLUDED.codigo_cvm
                    """), {"s": alias_sym, "b": tk, "c": cod})
                comp = data["companies"]
                if comp and cod is not None:
                    comp[0]["codigo_cvm"] = cod
                    repo.upsert(conn, "companies", comp)
                    prog["empresas"] += 1
                ast = data["assets"]
                if ast:
                    ast[0]["company_id"] = _company_id(conn, cod, ast[0]["ticker"])
                    repo.upsert(conn, "assets", ast)
                prog["precos"] += repo.upsert(conn, "historical_prices", data["historical_prices"])
                for t in ("income_statements", "balance_sheets", "cash_flow_statements"):
                    prog["demonstracoes"] += repo.upsert(conn, t, data[t])
                prog["dividendos"] += repo.upsert(conn, "dividends", data["dividends"])
                prog["indicadores"] += _gravar_indicadores(
                    conn, data, novo or tk, quote, composicoes.get(tk))
                if novo:
                    _registrar_troca_pendente(conn, tk, novo, cod, prog)
            prog["tickers"] += 1
        except Exception as exc:
            logger.warning("renormalize %s: %s", tk, exc)
            prog["erros"] += 1
    logger.info("market/renormalize: %s", prog)
    return prog


_TABELAS_DEMONSTRACAO = ("income_statements", "balance_sheets", "cash_flow_statements")

# Por dono, o payload mais novo QUE TRAZ demonstrações -- não o mais novo de
# todos, que é a cotação diária (só preço) e normaliza para zero linha. Ordena
# por fetched_at, e não por id: o arquivamento do Supabase dá id novo a payload
# antigo. O dono é o código atual (``:trocas`` = {antigo: atual}): o payload
# gravado sob ELET3 antes da troca ser descoberta é da AXIA3, e pode ser o
# único com as demonstrações. Só entra o dono cujas demonstrações ainda não
# saíram dele.
_SQL_PAYLOAD_DE_DEMONSTRACAO = """
    WITH ultimo AS (
        SELECT DISTINCT ON (dono) dono, ticker, id, payload_json
        FROM (SELECT COALESCE(CAST(:trocas AS jsonb) ->> ticker, ticker) AS dono,
                     ticker, id, fetched_at, payload_json
              FROM market.brapi_raw_payloads
              WHERE endpoint = 'quote' AND request_status = 'success'
                AND payload_json ?| array['incomeStatementHistoryQuarterly',
                                          'incomeStatementHistory']
                {filtro}) p
        ORDER BY dono, fetched_at DESC, id DESC
    )
    SELECT u.dono, u.ticker, u.id, u.payload_json FROM ultimo u
    {so_pendentes}
    ORDER BY u.dono
"""

# Pula o dono cujas demonstrações já saíram do payload mais novo. Com
# ``forcar`` o filtro cai: correção no normalizador não muda o payload, e sem
# isso nunca alcançaria a linha já gravada.
_SO_PENDENTES = """WHERE NOT EXISTS (
        SELECT 1 FROM market.income_statements s
        WHERE s.ticker = u.dono AND s.raw_payload_id = u.id)"""


def renormalize_demonstracoes(tickers: list[str] | None = None,
                              limit: int | None = None,
                              forcar: bool = False) -> dict:
    """Regrava as três demonstrações a partir do payload mais novo que as traz.

    Por que existe: o armazém recebe do Supabase, todo dia, os payloads da
    brapi (``archive_remote_brapi_raw``), mas só normaliza o que ele mesmo
    baixa -- e o ``annual`` local roda sobre ``public.setores``, quase só a
    classe ON. Em 04/10/2026 o armazém tinha o payload de 19/09 da BBDC4, com o
    2T26, e as demonstrações dela ainda saíam do payload de julho: 103 tickers
    (BBDC4, ITUB4, PETR4, CMIG4...) parados no 2026T1, e o vigia acusando o
    universo um trimestre atrás com o dado já no disco.

    Só as demonstrações: preço, dividendo e indicador do payload antigo
    regravariam por cima do diário mais novo. Os indicadores derivados saem de
    ``reprocess_metrics`` sobre ``prog["atualizados"]``. Sem rede.

    ``forcar`` regrava também o que já saiu do payload mais novo -- o caminho
    de uma correção do normalizador chegar às linhas existentes.

    Código trocado (ELET3 -> AXIA3): os payloads dos dois códigos concorrem
    pelo mesmo dono, o atual, e o escolhido grava sob ele com o id original
    -- ``unificar`` não move payload, e o da troca fica sob o código antigo.
    """
    import json
    engine = _engine()
    prog = {**_new_progress(), "atualizados": [], "payloads_de_codigo_antigo": 0}
    if engine is None:
        return {**prog, "erros": -1}
    repo.reset_db_cols_cache()
    trocas = _carregar_trocas(engine)
    params: dict = {"trocas": json.dumps(trocas)}
    filtro = ""
    if tickers:
        # pedir ELET3 ou AXIA3 é pedir o dono, com os payloads dos dois códigos
        donos = {trocas.get(t, t) for t in (x.upper().replace(".SA", "") for x in tickers)}
        filtro = "AND ticker = ANY(:tks)"
        params["tks"] = sorted(donos | {a for a, n in trocas.items() if n in donos})
    with engine.connect() as conn:
        if not repo.schema_exists(conn):
            return {**prog, "erros": -1}
        rows = conn.execute(text(_SQL_PAYLOAD_DE_DEMONSTRACAO.format(
            filtro=filtro, so_pendentes="" if forcar else _SO_PENDENTES)),
                            params).fetchall()
    if limit:
        rows = rows[:limit]

    for dono, tk, pid, payload in rows:
        try:
            p = json.loads(payload) if isinstance(payload, str) else payload
            quote = (p.get("results") or [p])[0] if isinstance(p, dict) else None
            if not quote:
                continue
            data = nz.normalize_all(quote)
            _reconcile_ticker(data, dono)
            if tk != dono:
                logger.info("%s: demonstrações do payload %s, gravado sob %s antes da troca",
                            dono, pid, tk)
                prog["payloads_de_codigo_antigo"] += 1
            n = 0
            with engine.begin() as conn:
                for t in _TABELAS_DEMONSTRACAO:
                    linhas = data.get(t) or []
                    for r in linhas:
                        r["raw_payload_id"] = pid
                    n += repo.upsert(conn, t, linhas)
            if n:
                prog["demonstracoes"] += n
                prog["atualizados"].append(dono)
            prog["tickers"] += 1
        except Exception as exc:
            logger.warning("renormalize_demonstracoes %s: %s", dono, exc)
            prog["erros"] += 1
    logger.info("market/renormalize_demonstracoes: tickers=%d atualizados=%d "
                "demonstracoes=%d erros=%d", prog["tickers"], len(prog["atualizados"]),
                prog["demonstracoes"], prog["erros"])
    return prog


def _latest_annual(conn, table: str, cols: str, tk: str):
    row = conn.execute(text(
        f"SELECT {cols} FROM market.{table} WHERE ticker=:t AND period='annual' "
        f"ORDER BY year DESC LIMIT 1"), {"t": tk}).fetchone()
    return row


def _valor_de_mercado_atual(*, preco, acoes, por_titulo, mc_spot, unit: bool,
                            lucro_anual, lpa_anual) -> float | None:
    """Valor de mercado do título para o snapshot de hoje.

    1. preço do armazém × ações emitidas ÷ ações por título (acoes.py);
    2. senão, o ``marketCap`` spot do normalizador;
    3. senão, só para ação, preço × lucro anual / LPA anual. Na unit esse
       reserva multiplica o preço da unit pelas ações emitidas e infla o valor
       k vezes (TAEE11 ×3, KLBN11 ×5), então a unit fica sem valor."""
    from data_pipeline.market import acoes as ac

    mc = ac.valor_de_mercado(preco, acoes, por_titulo)
    if mc is not None:
        return mc
    mc = ac._positivo(mc_spot)
    if mc is not None or unit:
        return mc
    p, lucro, lpa = ac._positivo(preco), ac._f(lucro_anual), ac._f(lpa_anual)
    if p is None or not lucro or not lpa:
        return None
    acoes_d = lucro / lpa
    return p * acoes_d if acoes_d > 0 else None


# Método que o normalizador gravava no ``marketCap`` spot antes do #710: o campo
# da brapi, sem ``Acoes_Emitidas`` nem ``Acoes_por_Titulo``. Desde o #710 o
# ``marketCap`` spot sai de ``acoes.valor_de_mercado_do_quote`` e leva
# ``preco_x_acoes_emitidas_por_titulo`` mesmo quando cai no campo da brapi.
METODO_SPOT_ANTERIOR_A_COMPOSICAO = "brapi_field"


def spot_anterior_a_composicao(*, unit: bool, por_titulo, metodo_market_cap) -> bool:
    """True quando a unit chega ao reprocess com as linhas spot de antes do #710.

    Nessas linhas falta k, e sem k a unit não tem ações do ano: o
    ``replace_metric_snapshot`` apagaria todo o valuation anual dela (em
    11/11/2026, TAEE11 perdeu 2010-2023). Não é a unit cuja composição a brapi
    não fecha -- essa já passou pelo normalizador novo e fica sem valor por
    decisão. É linha velha, que o ``renormalize --warehouse`` regrava a partir
    do quote arquivado. A ação não entra: sem k ela vale 1."""
    if not unit or por_titulo is not None:
        return False
    return str(metodo_market_cap or "") == METODO_SPOT_ANTERIOR_A_COMPOSICAO


# Origem do preço do ano. Não vai para o ``calculation_method``: mudar o método
# abre safra nova em ``calculated_metric_vintages`` para cada métrica, mesmo com
# o valor igual. A contagem sai em ``anos_preco_ajustado`` no retorno.
ORIGEM_PRECO_FITA = "fechamento COTAHIST sem ajuste"
ORIGEM_PRECO_AJUSTADO = "historical_prices ajustado"


def _preco_do_ano(*, sem_ajuste, ajustado) -> tuple[float | None, float | None, str | None]:
    """(preço do ano, fator_base, origem) para os múltiplos anuais.

    Ações do ano (lucro ÷ LPA), LPA e provento por ação (`market.dividends`)
    estão na base DA ÉPOCA; o preço que se multiplica por elas tem de estar
    também. O último pregão do ano na fita (sem ajuste) está; o
    `historical_prices.close` está retroajustado até hoje. Com os dois, a
    razão entre eles é o fator acumulado dos eventos posteriores
    (``fator_base`` de `acoes.acoes_e_lpa_do_ano`).

    Sem a fita (ano antes de 2010, tabela ausente, ticker sem pregão no ano)
    fica o preço ajustado, que é o que o reprocess usava antes: correto onde
    não houve evento depois do ano, e marcado na origem para a auditoria."""
    if sem_ajuste:
        fator = (sem_ajuste / ajustado) if ajustado else None
        return float(sem_ajuste), fator, ORIGEM_PRECO_FITA
    if ajustado:
        return float(ajustado), None, ORIGEM_PRECO_AJUSTADO
    return None, None, None


FONTE_PROVENTOS_FOTO = "foto mais nova da brapi + market.dividends deduplicado"
FONTE_PROVENTOS_BANCO = "market.dividends deduplicado (sem market.brapi_raw_payloads)"


def _eventos_de_renda(conn) -> tuple[dict[str, list[tuple]], str]:
    """Proventos de renda por ticker, ``[(event_date, ex_date, amount), ...]``.

    ``market.dividends`` cru não serve: o upsert não apaga, e cada revisão da
    brapi deixa a linha velha ao lado da nova. A SBSP3 tem dois JCP de 1,276 em
    2023 (data-ex 28/04 e 12/05) e a soma crua dobrava o DY. A leitura é a de
    `core.proventos_b3.eventos_do_armazem`, a mesma do valuation histórico
    publicado. Banco sem as fotos (`market.brapi_raw_payloads`) cai na regra de
    leitura de `market.dividends` (`renda_por_evento`) e a fonte fica nomeada."""
    import pandas as pd

    from core.dividend_types import renda_por_evento, sql_proventos_de_renda
    from core.proventos_b3 import eventos_do_armazem

    tem_fotos = conn.execute(text(
        "SELECT to_regclass('market.brapi_raw_payloads') IS NOT NULL")).scalar()
    if tem_fotos:
        ev, fonte = eventos_do_armazem(conn), FONTE_PROVENTOS_FOTO
    else:
        logger.warning("reprocess: market.brapi_raw_payloads ausente; proventos "
                       "pela regra de leitura de market.dividends")
        ev = renda_por_evento(pd.read_sql_query(
            text(sql_proventos_de_renda("TRUE")), conn))
        fonte = FONTE_PROVENTOS_BANCO

    def _dia(v):
        return None if v is None or pd.isna(v) else pd.Timestamp(v).date()

    out: dict[str, list[tuple]] = {}
    for tk, d, ex, a in ev[["ticker", "event_date", "ex_date", "amount"]].itertuples(
            index=False, name=None):
        out.setdefault(str(tk).strip().upper(), []).append((_dia(d), _dia(ex), a))
    return out, fonte


def reprocess_metrics(tickers: list[str] | None = None, limit: int | None = None) -> dict:
    """
    Recalcula indicadores derivados a partir de market.* (SEM rede):
      • snapshot completo (period='ttm'): Margem_Liquida/Operacional, ROE, ROA,
        ROIC, Endividamento_Total, Liquidez_Corrente, P/L, P/VP, EV_EBIT, P_FCO,
        DY, Payout;
      • histórico anual (period='annual', um conjunto por ano): fundamentais
        exatos das demonstrações; valuation (P/L, P/VP, EV_EBIT, P_FCO), DY e
        Payout com o fechamento do ano na base da época (`_preco_do_ano`) e as
        ações do ano = lucro ÷ LPA (`acoes.acoes_e_lpa_do_ano`). Ano sem LPA, ou
        reprovado na guarda, fica SEM valuation: não há reserva por ações atuais
        desde jun/2026.
    Roda sobre as empresas já em market.assets (cresce com o bootstrap).
    """
    from datetime import datetime, timezone

    import core.data_quality as dq
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

    from data_pipeline.market import acoes_cotacao as _ac_cot
    with engine.connect() as _c:
        # tabela ausente é nomeada no log (ler_slot), não vira "sem preço"
        slot_existe = _ac_cot.tabela_existe(_c)
        if not slot_existe:
            logger.warning("reprocess: %s ausente (migration %s); preço = última barra",
                           _ac_cot.TABELA, _ac_cot.MIGRATION)
        fita_existe = _ac_cot.fita_existe(_c)
        if not fita_existe:
            logger.warning("reprocess: %s ausente; múltiplos anuais com o preço "
                           "ajustado (%s)", _ac_cot.FITA, ORIGEM_PRECO_AJUSTADO)
        eventos_renda, prog["fonte_proventos"] = _eventos_de_renda(_c)
    # Cadeias de sucessão de ticker (ELET6 → AXIA6): a fita troca de código, as
    # demonstrações não. Conexão própria: uma falha aqui aborta a transação, e
    # o resto do cabeçalho não pode morrer com ela.
    cadeias_tk: dict[str, list[str]] = {}
    if fita_existe:
        try:
            from data_pipeline.market import b3_sucessao as _suc
            with engine.connect() as _c:
                cadeias_tk = _suc.cadeias(_suc.carregar_sucessoes(_c))
        except Exception as exc:  # sem sucessão, cada código só com a sua fita
            logger.warning("reprocess: sucessão de tickers indisponível (%s: %s); "
                           "fita só pelo próprio código", type(exc).__name__, exc)
    prog["tickers_com_sucessao"] = len(cadeias_tk)
    prog["anos_preco_ajustado"] = 0
    prog["anos_lpa_base_de_hoje"] = 0
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
                # Linhas spot do normalizador (o quote mais novo da brapi).
                linhas_spot = conn.execute(text(
                    "SELECT metric_name, metric_value, calculation_method "
                    "FROM market.calculated_metrics "
                    "WHERE ticker=:t AND period='spot'"), {"t": tk}).fetchall()
                spot = {r[0]: float(r[1]) for r in linhas_spot}
                metodo_mc = next((r[2] for r in linhas_spot if r[0] == "marketCap"), None)
                eps = spot.get("LPA")
                # Cotação vigente: a mais nova entre a última barra e o fechamento
                # do COTAHIST (market.b3_cotacao_atual, migration 089).
                last_price, preco_brapi = _ac_cot.preco_atual(conn, tk, existe=slot_existe)
                # P/L, P/VP e DY do spot foram calculados sobre a cotação da
                # brapi: com o fechamento do COTAHIST vencendo, reescalam.
                spot = _ac_cot.cp.rebasear_spot(spot, last_price, preco_brapi)
                from data_pipeline.market import acoes as _ac
                unit = _ac.exige_k(conn.execute(text(
                    "SELECT asset_type FROM market.assets WHERE ticker=:t"),
                    {"t": tk}).scalar(), tk)
                # Ações por título: 1 na ação; k na unit, se o normalizador o
                # derivou; None na unit sem k (acoes.py).
                por_titulo = spot.get("Acoes_por_Titulo") or (None if unit else 1.0)
                if spot_anterior_a_composicao(unit=unit, por_titulo=por_titulo,
                                              metodo_market_cap=metodo_mc):
                    # Pular em vez de recalcular: nada é apagado nem gravado, e
                    # o ticker sai nomeado no resultado.
                    logger.warning(
                        "reprocess %s: unit com linhas spot de antes do #710 (sem k); "
                        "pulada para não apagar o valuation anual. Rode "
                        "`run_market_ingest.py renormalize --warehouse` e repita.", tk)
                    prog.setdefault("units_spot_sem_composicao", []).append(tk)
                    continue
                mc = _valor_de_mercado_atual(
                    preco=last_price, acoes=spot.get("Acoes_Emitidas"),
                    por_titulo=por_titulo, mc_spot=spot.get("marketCap"), unit=unit,
                    lucro_anual=inc[3] if inc else None,
                    lpa_anual=inc[4] if inc else None)
                # (event_date, ex_date, amount) já deduplicados: a mesma regra
                # do valuation histórico publicado (`_eventos_de_renda`).
                ev_tk = eventos_renda.get(tk, [])
                divs = [(d, a) for d, _ex, a in ev_tk]

                # dividendos POR AÇÃO nos últimos 366 dias (fallback p/ DY)
                div_ps_ttm = sum(float(a) for d, a in divs
                                 if d is not None and a is not None and 0 <= (ref - d).days <= 366)

                # Payout: separar "não distribuiu" de "não sabemos".
                # `div_ps_ttm or None` convertia 0.0 em None, então empresa que
                # retém todo o lucro ficava SEM Payout em vez de Payout = 0 —
                # e o piso de qualidade não podia aprová-la nem reprová-la.
                # Medido em 30/07/2026: 165 dos 170 tickers sem Payout caíam
                # aqui. Mas zero só é medição quando há EVIDÊNCIA de que a
                # ingestão de dividendos funciona para o ticker: 110 têm
                # histórico e nada em 366 dias (zero real); 55 nunca tiveram
                # nenhum evento, e aí é indecidível entre "não paga" e "não
                # ingerido" — gravar 0 nesses seria inventar valor favorável.
                tem_historico_dividendo = any(
                    d is not None and a is not None for d, a in divs)
                div_ttm_val = (div_ps_ttm if (div_ps_ttm or tem_historico_dividendo)
                               else None)

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
                        # Sem 4 trimestres de CF alinhados, o FCO ANUAL é melhor
                        # que nada: descartá-lo deixava 96 tickers com FCO no
                        # balanço e nenhum P_FCO (93 deles com preço disponível).
                        # É base mista (fluxo anual sobre market cap de hoje), e
                        # por isso o método fica marcado abaixo.
                        "fco": (_sum4(q_cf, 2) if aligned_cf
                                else (cf[0] if cf else None)),
                        "market_cap": mc,
                        "div_ttm": div_ttm_val, "price": last_price, "eps": eps,
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
                        "div_ttm": div_ttm_val, "price": last_price, "eps": eps,
                    }
                snap = mx.compute_snapshot(f)
                # Procedência do FCO misto fica no método (auditoria), não na
                # confiança: o leitor filtra confidence_score >= 80, então
                # rebaixar aqui apagaria a métrica que acabamos de recuperar.
                if from_q and not aligned_cf and "P_FCO" in snap:
                    _val, _met = snap["P_FCO"]
                    snap["P_FCO"] = (_val, _met + " (fco anual sobre base ttm)")

                # brapi spot: no caminho ANUAL sobrescreve (anual distorce por
                # não-recorrentes); no TTM real só PREENCHE o que faltar. DY sempre
                # do trailing da brapi (evita over-count da janela de dividendos).
                for k in ("P/L", "P/VP", "ROE", "ROA", "Margem_Liquida", "Margem_Operacional"):
                    v = spot.get(k)
                    if v is None or not dq.is_valid_value(k, v):
                        continue
                    if base_method == "annual" or k not in snap:
                        snap[k] = (round(v, 8), "brapi_trailing")
                # DY trailing da brapi, EXCETO quando excede muito a soma
                # própria 12m: em multi-classe (CEBR5/6, BRSR5/6...) o consenso
                # da brapi soma as classes e infla ~2x; a soma própria (pós
                # dedup por classe no normalizador) é o valor defensável. No
                # sentido inverso (própria >> brapi) mantém a brapi — é o caso
                # do over-count por janela (ex.: SAPR3 cluster de JCP).
                dyv = spot.get("DY")
                own_dy = snap.get("DY", (None,))[0]
                if dyv is not None and dq.is_valid_value("DY", dyv) and \
                        not (own_dy and dyv > own_dy * 1.5):
                    snap["DY"] = (round(dyv, 8), "brapi_trailing")
                if "DY" in snap and "P/L" in snap:
                    pay = snap["DY"][0] * snap["P/L"][0]
                    if dq.is_valid_value("Payout", pay):
                        snap["Payout"] = (round(pay, 8), "DY*P/L")
                rows_out = mx.to_metric_rows(tk, snap)

                # ── Histórico anual (period='annual', um conjunto por ano) ──
                # Fundamentais (margens, ROE, ROA, ROIC, Endiv, Liquidez) são
                # exatos das demonstrações. Valuation (P/L, P/VP, EV_EBIT, P_FCO,
                # Payout) só existe com LPA do ano (ações = NI/LPA, com guarda);
                # DY e valuation usam o preço do ano na base da época
                # (`_preco_do_ano`).
                shares = (float(mc) / float(last_price)) if (mc and last_price) else None
                sem_ajuste_y = (_ac_cot.fechamentos_de_fim_de_ano(
                    conn, tk, cadeias_tk.get(str(tk).upper())) if fita_existe else {})

                def _by_year(table: str, cols: str) -> dict:
                    return {int(r[0]): r[1:] for r in conn.execute(text(
                        f"SELECT year, {cols} FROM market.{table} "
                        f"WHERE ticker=:t AND period='annual'"), {"t": tk}).fetchall()}
                inc_y = _by_year("income_statements", "revenue, ebit, ebitda, net_income, eps")
                bal_y = _by_year("balance_sheets",
                                 "total_assets, equity, cash, gross_debt, net_debt, "
                                 "current_assets, current_liabilities")
                cf_y = _by_year("cash_flow_statements", "operating_cash_flow")

                # Último close ajustado > 0 do ano, a mesma leitura do valuation
                # histórico publicado (`acoes_cotacao.ajustados_de_fim_de_ano`).
                year_end = _ac_cot.ajustados_de_fim_de_ano(conn, tk)
                div_year = {}
                # Ano do provento = ano da data-ex, como no valuation histórico
                # publicado (`publish_valuation_historico.coletar_b3`): é a data
                # que dá direito, e a soma por ela fica igual nos dois caminhos.
                for _d, ex, amt in ev_tk:
                    if ex is not None and amt:
                        div_year[ex.year] = div_year.get(ex.year, 0.0) + float(amt)

                # Fix auditoria 2026-07 (ponto-no-tempo): itera apenas anos
                # com DEMONSTRAÇÃO publicada. Antes, set(div_year) criava
                # linha 'annual' para o ano corrente só com dividendos
                # parciais + preço de hoje (DY distorcido no histórico).
                for y in sorted(set(inc_y) | set(bal_y) | set(cf_y)):
                    i, b, cfy = inc_y.get(y), bal_y.get(y), cf_y.get(y)
                    px, fator_base, origem_px = _preco_do_ano(
                        sem_ajuste=sem_ajuste_y.get(y), ajustado=year_end.get(y))
                    dps = div_year.get(y)
                    # Mesmo princípio do snapshot: ano sem dividendo é payout 0,
                    # não payout desconhecido — mas só quando o ticker tem algum
                    # histórico, provando que a ingestão de dividendos funciona
                    # para ele. A brapi devolve o histórico inteiro num payload,
                    # então lacuna de UM ano dentro de um histórico existente é
                    # ano sem distribuição, não ano não coletado.
                    if dps is None and div_year:
                        dps = 0.0
                    ni = i[3] if i else None
                    eps_y = i[4] if i else None
                    # Ações do ano = NI/LPA, época até o teto (`acoes.acoes_e_lpa_do_ano`,
                    # a mesma conta do valuation histórico publicado); acima do
                    # teto e sem resgate pela base de hoje = sem valuation no ano.
                    base_y = _ac.acoes_e_lpa_do_ano(
                        lucro=ni, lpa=eps_y, por_titulo=por_titulo, acoes_atuais=shares,
                        fator_base=fator_base)
                    shares_y, eps_use = (base_y[0], base_y[1]) if base_y else (None, None)
                    if base_y and base_y[2] == _ac.BASE_DE_HOJE:
                        prog["anos_lpa_base_de_hoje"] += 1
                    exact = shares_y is not None
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
                    if origem_px == ORIGEM_PRECO_AJUSTADO:
                        prog["anos_preco_ajustado"] += 1
                    rows_out += mx.to_metric_rows(tk, mx.compute_snapshot(f_y),
                                                  period="annual", year=y,
                                                  low_conf=None if exact else mx.ANNUAL_APPROX)
                if rows_out:
                    repo.append_metric_vintages(conn, rows_out)
                    removed = repo.replace_metric_snapshot(conn, tk, rows_out)
                    if removed:
                        logger.info("reprocess %s: %s métricas obsoletas removidas", tk, removed)
                    prog["indicadores"] += len(rows_out)
                    prog["tickers"] += 1
        except Exception as exc:
            logger.warning("reprocess %s: %s", tk, exc)
            prog["erros"] += 1
    logger.info("market/reprocess: %s", prog)
    return prog
