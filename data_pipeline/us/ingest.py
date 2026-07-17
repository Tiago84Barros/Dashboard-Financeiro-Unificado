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
from pathlib import Path
from typing import Iterable, Optional

from sqlalchemy import text

from data_pipeline.us import identity, normalize, repository as repo
from data_pipeline.us.providers import (
    Budget, FmpProvider, ProviderError, build_default_provider, estimate_calls,
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
        result["reason"] = "perfil vazio"
        return result

    # divergência símbolo solicitado vs retornado → rejeita (não grava sob ticker errado)
    div = identity.detect_symbol_divergence(sym, profile_raw.get("symbol"))
    prof = normalize.map_profile(profile_raw)

    income = provider.get_income_statements(sym, "annual", years)
    balance = provider.get_balance_sheets(sym, "annual", years)
    cashflow = provider.get_cash_flow_statements(sym, "annual", years)

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
            inc_rows, bal_rows, cfw_rows = income, balance, cashflow
        else:
            inc_rows = [normalize.map_income_statement(r) for r in income]
            bal_rows = [normalize.map_balance_sheet(r) for r in balance]
            cfw_rows = [normalize.map_cash_flow(r) for r in cashflow]
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


def ingest_symbols(provider: FmpProvider, engine, symbols: Iterable[str], *,
                   run_key="bootstrap", years=20, resume=True) -> dict:
    """Percorre símbolos com checkpoint/retomada. Retoma do cursor se resume=True."""
    symbols = [identity.normalize_symbol(s) for s in symbols if s]
    with engine.begin() as conn:
        run_id = repo.start_run(conn, run_key, "profiles", {"years": years})
        open_run = repo.get_open_run(conn, run_key, "profiles")
    start_idx = 0
    if resume and open_run and open_run.get("cursor") in symbols:
        start_idx = symbols.index(open_run["cursor"]) + 1
    ok = err = 0
    for sym in symbols[start_idx:]:
        try:
            r = ingest_symbol(provider, engine, sym, years=years, run_id=run_id)
            ok += 1 if r.get("ok") else 0
            err += 0 if r.get("ok") else 1
        except Exception as exc:  # noqa: BLE001
            err += 1
            with engine.begin() as conn:
                repo.log_error(conn, run_id, symbol=sym, domain="profiles",
                               error_type="unexpected", message=str(exc))
        with engine.begin() as conn:
            repo.checkpoint_run(conn, run_id, cursor=sym, calls=provider.calls_made)
    with engine.begin() as conn:
        repo.finish_run(conn, run_id)
    return {"processed": len(symbols[start_idx:]), "ok": ok, "errors": err,
            "calls": provider.calls_made}


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
