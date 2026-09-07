"""
data_pipeline/us/ingest.py
Orquestrador da ingestão Empresas Americanas (FMP → market_us.*).

Desenho:
  - Por DOMÍNIO (universe, profiles, prices, statements, metrics, dividends,
    splits), reiniciável e incremental via ingestion_runs.cursor.
  - Falha de um símbolo é registrada (ingestion_errors) e NÃO aborta o lote nem
    corrompe dados válidos (transação por símbolo).
  - Nada de fallback silencioso: sem chave/rede a busca de dados novos falha
    explicitamente; a interface segue lendo o warehouse local.

Este módulo é usado pela CLI (run_us_ingest.py). A view não o importa.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Mapping, Optional

from sqlalchemy import text

from data_pipeline.us import identity, normalize
from data_pipeline.us import repository as repo
from data_pipeline.us.providers import (
    FmpProvider,
    ProviderError,
    build_default_provider,
    estimate_calls,
)


class CompositeProvider:
    """EDGAR (fundamentos) + yfinance (preços) atrás de uma fachada única.

    `pre_normalized=True` sinaliza que as demonstrações já vêm no formato do
    schema market_us (edgar_facts) e não passam pelos mapeadores da FMP.
    """
    pre_normalized = True
    source_name = "edgar+yfinance"

    def __init__(self, fundamentals, market):
        self._f = fundamentals
        self._m = market

    # fundamentos → EDGAR
    def get_universe(self, exchanges):
        return self._f.get_universe(exchanges)

    def get_profile(self, symbol):
        return self._f.get_profile(symbol)

    def set_cik_hints(self, hints):
        setter = getattr(self._f, "set_cik_hints", None)
        if setter is not None:
            setter(hints)

    def get_income_statements(self, *a, **k):
        return self._f.get_income_statements(*a, **k)

    def get_balance_sheets(self, *a, **k):
        return self._f.get_balance_sheets(*a, **k)

    def get_cash_flow_statements(self, *a, **k):
        return self._f.get_cash_flow_statements(*a, **k)

    def get_key_metrics(self, *a, **k):
        return self._f.get_key_metrics(*a, **k)

    # mercado → yfinance
    def get_prices_daily(self, *a, **k):
        return self._m.get_prices_daily(*a, **k)

    def get_dividends(self, *a, **k):
        return self._m.get_dividends(*a, **k)

    def get_splits(self, *a, **k):
        return self._m.get_splits(*a, **k)

    @property
    def calls_made(self) -> int:
        return getattr(self._f, "calls_made", 0) + getattr(self._m, "calls_made", 0)

logger = logging.getLogger("us_ingest")

_SCHEMA_DIR = Path(__file__).resolve().parents[2] / "supabase_unificado" / "schema"
# Todas as migrations do namespace market_us, aplicadas em ordem (idempotentes):
# 040 base, 041 portfólio/backtest, 042 fora da curva, 043 retained_earnings.
_SCHEMA_GLOB = "0*_market_us*.sql"

DEFAULT_EXCHANGES = ["NYSE", "NASDAQ", "AMEX"]


# ── Schema ────────────────────────────────────────────────────────────────────
def schema_files() -> list[Path]:
    """Migrations market_us em ordem lexicográfica (040, 041, 042, 043...)."""
    return sorted(_SCHEMA_DIR.glob(_SCHEMA_GLOB))


def apply_schema(engine=None) -> list[str]:
    """Aplica TODAS as migrations market_us (idempotentes) no warehouse."""
    from core.database import get_engine
    engine = engine or get_engine()
    if engine is None:
        raise RuntimeError("engine indisponível para aplicar o schema market_us.")
    applied = []
    for path in schema_files():
        with engine.begin() as conn:
            conn.execute(text(path.read_text(encoding="utf-8")))
        applied.append(path.name)
        logger.info("schema aplicado: %s", path.name)
    return applied


# ── Universo ──────────────────────────────────────────────────────────────────
def ingest_universe(provider: FmpProvider, engine, *, exchanges=None,
                    run_key="bootstrap", limit: Optional[int] = None) -> dict:
    exchanges = exchanges or DEFAULT_EXCHANGES
    rows = provider.get_universe(exchanges)
    if limit:
        rows = rows[:limit]
    written = 0
    with engine.begin() as conn:
        run_id = repo.start_run(conn, run_key, "universe", {"exchanges": exchanges})
        for r in rows:
            sym = identity.normalize_symbol(r.get("symbol"))
            if not sym:
                continue
            exch = str(r.get("exchangeShortName") or r.get("exchange") or "NASDAQ").upper()
            sec = "etf" if r.get("type") == "etf" else "common"
            try:
                conn.execute(text(repo.build_upsert(
                    "assets", ["symbol", "exchange", "security_type"],
                    conflict=["symbol", "exchange"], update=[])),
                    {"symbol": sym, "exchange": exch, "security_type": sec})
                written += 1
            except Exception as exc:  # noqa: BLE001
                repo.log_error(conn, run_id, symbol=sym, domain="universe",
                               error_type="db", message=str(exc))
        repo.checkpoint_run(conn, run_id, cursor=None, calls=provider.calls_made,
                            rows=written)
        repo.finish_run(conn, run_id)
    return {"symbols": len(rows), "written": written, "calls": provider.calls_made}


# ── Um símbolo (perfil + fatos) ───────────────────────────────────────────────
def ingest_symbol(provider: FmpProvider, engine, symbol: str, *,
                  years: int = 20, run_id: Optional[int] = None,
                  with_prices: bool = True) -> dict:
    """Ingesta perfil, demonstrações, métricas, preços, dividendos e splits de um
    símbolo. Transação por símbolo: falha aqui não afeta outros.
    """
    sym = identity.normalize_symbol(symbol)
    result = {"symbol": sym, "ok": False, "reason": None}
    profile_raw = provider.get_profile(sym)
    if not profile_raw:
        # A-146: este ramo somava ao contador `errors` e nao deixava rastro.
        # A varredura fechou com "errors = 23" e so 2 estavam em
        # ingestion_errors; os outros 21 eram exatamente estes, invisiveis.
        with engine.begin() as conn:
            repo.log_error(conn, run_id, symbol=sym, domain="profiles",
                           error_type="empty_profile",
                           message="perfil vazio (ticker sem CIK resolvido na SEC)")
        result["reason"] = "perfil vazio"
        return result

    # divergência símbolo solicitado vs retornado → rejeita (não grava sob ticker errado)
    div = identity.detect_symbol_divergence(sym, profile_raw.get("symbol"))
    prof = normalize.map_profile(profile_raw)

    income = provider.get_income_statements(sym, "annual", years)
    balance = provider.get_balance_sheets(sym, "annual", years)
    cashflow = provider.get_cash_flow_statements(sym, "annual", years)
    quarterly_limit = max(8, years * 4)
    income_q = provider.get_income_statements(sym, "quarterly", quarterly_limit)
    balance_q = provider.get_balance_sheets(sym, "quarterly", quarterly_limit)
    cashflow_q = provider.get_cash_flow_statements(sym, "quarterly", quarterly_limit)

    # Sem NENHUMA demonstração o CIK é uma casca (ex.: holding nova de
    # reestruturação, sem histórico). Não cria empresa-fantasma — registra e pula.
    if not income and not balance and not cashflow:
        with engine.begin() as conn:
            repo.log_error(conn, run_id, symbol=sym, domain="fundamentals",
                           error_type="empty_facts",
                           message="CIK sem fatos XBRL (possível holding sem histórico)")
        result["reason"] = "sem demonstrações (CIK vazio)"
        return result

    with engine.begin() as conn:
        if div is not None:
            repo.log_error(conn, run_id, symbol=sym, domain="profiles",
                           error_type="symbol_mismatch",
                           message=f"retornou {div['returned']}")
            result["reason"] = "symbol_mismatch"
            return result
        company_id = repo.upsert_company(conn, prof)
        repo.upsert_asset(conn, company_id, prof)
        n = 0
        # EDGAR (edgar_facts) já entrega linhas no formato do schema; FMP passa
        # pelos mapeadores de normalização.
        if getattr(provider, "pre_normalized", False):
            inc_rows, bal_rows, cfw_rows = income + income_q, balance + balance_q, cashflow + cashflow_q
        else:
            inc_rows = [normalize.map_income_statement(r) for r in income + income_q]
            bal_rows = [normalize.map_balance_sheet(r) for r in balance + balance_q]
            cfw_rows = [normalize.map_cash_flow(r) for r in cashflow + cashflow_q]
        n += repo.upsert_statements(conn, "income_statements", company_id, sym, inc_rows)
        n += repo.upsert_statements(conn, "balance_sheets", company_id, sym, bal_rows)
        n += repo.upsert_statements(conn, "cash_flow_statements", company_id, sym, cfw_rows)
        if with_prices:
            try:
                n += repo.upsert_prices_daily(conn, sym, provider.get_prices_daily(sym))
                n += repo.upsert_dividends(conn, sym, provider.get_dividends(sym))
                n += repo.upsert_splits(conn, sym, provider.get_splits(sym))
            except ProviderError as exc:
                repo.log_error(conn, run_id, symbol=sym, domain="prices",
                               error_type="provider", message=str(exc))
        result.update(ok=True, rows=n, company_id=company_id, is_reit=prof["is_reit"])
    return result


def load_cik_hints(provider, engine) -> int:
    """Alimenta o provider com o ticker->CIK que o armazem ja conhece (A-146).

    Nao substitui a SEC: e o ultimo recurso para o ticker que sumiu de
    `company_tickers.json`. Sem isso a empresa fica congelada num parser antigo
    sem parar de ser elegivel -- pior que sair do universo, porque ninguem ve.
    """
    setter = getattr(provider, "set_cik_hints", None)
    if setter is None or engine is None:
        return 0
    try:
        with engine.begin() as conn:
            hints = {r[0]: r[1] for r in conn.execute(text(f"""
                SELECT a.symbol, c.cik
                FROM {repo.SCHEMA}.assets a
                JOIN {repo.SCHEMA}.companies c ON c.id = a.company_id
                WHERE c.cik IS NOT NULL"""))}
    except Exception as exc:  # noqa: BLE001
        logger.warning("cik_hints indisponivel: %s", exc)
        return 0
    setter(hints)
    return len(hints)


def ingest_symbols(provider: FmpProvider, engine, symbols: Iterable[str], *,
                   run_key="bootstrap", years=20, resume=True,
                   with_prices=True, workers: int = 1) -> dict:
    """Percorre símbolos com checkpoint/retomada. Retoma do cursor se resume=True.

    with_prices=False: só fundamentos (EDGAR, rápido) — para varrer o mercado
    todo sem o gargalo do yfinance; os preços entram numa passagem incremental.
    """
    symbols = [identity.normalize_symbol(s) for s in symbols if s]
    load_cik_hints(provider, engine)
    with engine.begin() as conn:
        open_run = repo.get_open_run(conn, run_key, "profiles")
        run_id = repo.start_run(conn, run_key, "profiles", {"years": years,
                                                            "workers": workers})
    start_idx = 0
    if resume and open_run and open_run.get("cursor") in symbols:
        start_idx = symbols.index(open_run["cursor"]) + 1
    pending = symbols[start_idx:]
    ok = err = rows_written = 0

    def process(sym):
        try:
            return sym, ingest_symbol(provider, engine, sym, years=years, run_id=run_id,
                                      with_prices=with_prices), None
        except Exception as exc:  # noqa: BLE001
            with engine.begin() as conn:
                repo.log_error(conn, run_id, symbol=sym, domain="profiles",
                               error_type="unexpected", message=str(exc))
            return sym, None, exc

    if workers > 1:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=int(workers)) as pool:
            futures = [pool.submit(process, sym) for sym in pending]
            results = (f.result() for f in as_completed(futures))
            for sym, result, exc in results:
                success = bool(result and result.get("ok"))
                ok += int(success)
                err += int(not success)
                rows_written += int((result or {}).get("rows") or 0)
    else:
        for sym in pending:
            _, result, exc = process(sym)
            success = bool(result and result.get("ok"))
            ok += int(success)
            err += int(not success)
            rows_written += int((result or {}).get("rows") or 0)
            with engine.begin() as conn:
                repo.checkpoint_run(conn, run_id, cursor=sym)
    with engine.begin() as conn:
        repo.checkpoint_run(conn, run_id, cursor=None, calls=provider.calls_made,
                            rows=rows_written)
        repo.finish_run(conn, run_id)
    return {"processed": len(pending), "ok": ok, "errors": err,
            "calls": provider.calls_made, "rows": rows_written, "workers": int(workers)}


OVERLAP_DIAS = 7


def _janela(desde, sym, *, overlap_dias: int = OVERLAP_DIAS):
    """Data inicial da descida para ``sym``, ou ``None`` para histórico inteiro.

    O recuo de ``overlap_dias`` sobre a última data guardada existe porque o
    Yahoo corrige barra recente (volume consolidado, fechamento revisado) depois
    de publicá-la. Pedir a partir do dia seguinte gravaria a primeira versão e
    nunca mais voltaria nela; o upsert é por (symbol, date), então reescrever a
    semana é barato e idempotente.
    """
    if not desde:
        return None
    ultima = desde.get(sym)
    if ultima is None:
        return None
    return (ultima - timedelta(days=max(0, int(overlap_dias)))).isoformat()


def ingest_prices_only(provider, engine, symbols: Iterable[str], *,
                       desde: Mapping[str, date] | None = None) -> dict:
    """Passagem incremental de PREÇOS (yfinance) para símbolos já com fundamentos.

    Desacopla o gargalo do yfinance da carga de fundamentos (EDGAR). Uma falha de
    preço não afeta os outros; nada de fundamentos é tocado aqui.

    ``desde`` mapeia símbolo → última data já guardada e transforma a passagem em
    incremental de verdade. Sem ele, cada execução rebaixava ``period="max"`` e
    reescrevia o histórico inteiro: medido em 07/09/2026, ~13 s por símbolo
    contra ~0,4 s da janela, ou seja quatro horas para atualizar 1.100 empresas.
    Uma rotina diária que leva quatro horas não termina — e foi exatamente assim
    que a vitrine ficou parada em 20/08 e a Criação de Portfólio bloqueou o
    universo inteiro por negociabilidade não verificada.

    Split dentro da janela devolve o símbolo ao histórico completo: o Yahoo
    retroajusta a série toda depois de um desdobramento, e uma janela de dias
    deixaria as barras antigas com o preço da era anterior — o defeito que este
    projeto já conhece como "preço bilionário é retroajuste, não lixo".
    """
    symbols = [identity.normalize_symbol(s) for s in symbols if s]
    ok = err = rows = 0
    completos = 0
    for sym in symbols:
        try:
            start = _janela(desde, sym)
            splits = provider.get_splits(sym, start, None) if start else None
            if start and splits:
                # Retroajuste: a janela não corrige as barras anteriores.
                logger.info("prices %s: split na janela, refazendo histórico", sym)
                start, splits, completos = None, None, completos + 1
            with engine.begin() as conn:
                n = repo.upsert_prices_daily(
                    conn, sym, provider.get_prices_daily(sym, start, None))
                if n:
                    n += repo.upsert_dividends(
                        conn, sym, provider.get_dividends(sym, start, None))
                    if splits is None:
                        splits = provider.get_splits(sym, start, None)
                    n += repo.upsert_splits(conn, sym, splits)
            rows += n
            ok += 1 if n else 0
            err += 0 if n else 1
        except Exception as exc:  # noqa: BLE001
            err += 1
            logger.warning("prices %s falhou: %s", sym, exc)
    return {"processed": len(symbols), "with_prices": ok, "empty": err,
            "rows": rows, "historico_refeito": completos}


# ── Estimativa (dry-run) ──────────────────────────────────────────────────────
def estimate(n_symbols: int, *, with_prices: bool = True) -> dict:
    per = 8 if with_prices else 4
    est = estimate_calls(n_symbols, per)
    # espaço em disco: heurística ~ preços dominam (~200 KB/símbolo em 20 anos)
    est["estimated_disk_mb"] = round(n_symbols * 0.35, 1)
    return est


def make_provider(budget_limit: Optional[int] = None, source: str | None = None):
    """Provider conforme a fonte configurada.

    'edgar' (padrão): SEC EDGAR p/ fundamentos + yfinance p/ preços (composto).
    'fmp': FmpProvider (só com licença compatível com armazenamento local).
    """
    from core.config import settings
    src = (source or settings.us_source).lower()
    if src == "fmp":
        return build_default_provider(budget_limit=budget_limit)
    from data_pipeline.us.edgar import build_edgar_provider
    from data_pipeline.us.prices_yf import YFinanceProvider
    return CompositeProvider(build_edgar_provider(budget_limit=budget_limit),
                             YFinanceProvider())
