"""
run_us_ingest.py
CLI da ingestão Empresas Americanas → warehouse local market_us.*.

FONTE PADRÃO: SEC EDGAR (fundamentos, domínio público — exige SEC_USER_AGENT no
.env com nome e e-mail) + yfinance (preços). FMP é opcional via
US_FUNDAMENTALS_SOURCE=fmp, apenas com licença compatível com armazenamento local.

Comandos:
    python run_us_ingest.py init-schema  --warehouse            # aplica migration 040
    python run_us_ingest.py test         --warehouse            # testa chave + engine
    python run_us_ingest.py universe     --warehouse            # lista/seeda o universo
    python run_us_ingest.py estimate     --tickers AAPL MSFT    # dry-run (chamadas/disco)
    python run_us_ingest.py bootstrap    --warehouse --limit 50 # carga histórica
    python run_us_ingest.py daily        --warehouse            # só preços/dividendos
    python run_us_ingest.py fundamentals --warehouse --tickers AAPL
    python run_us_ingest.py resume       --warehouse            # retoma do checkpoint
    python run_us_ingest.py validate     --warehouse            # auditoria de qualidade

Opções: --tickers, --exchanges, --limit, --years, --budget, --dry-run, --offline,
--warehouse (usa 127.0.0.1:5433 lendo warehouse/.env), --json.

A CHAVE (FMP_API_KEY) só é usada aqui/na ingestão — NUNCA na interface. A view lê
apenas o warehouse local e funciona offline após a carga.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from urllib.parse import quote

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(_ROOT / ".env")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("us_ingest_cli")


def _point_to_warehouse() -> bool:
    """Aponta a engine para o Postgres local lendo warehouse/.env (sem expor senha)."""
    from dotenv import dotenv_values
    env_paths = [_ROOT / "warehouse" / ".env"]
    env_paths.extend(_ROOT.glob(".claude/worktrees/*/warehouse/.env"))
    warehouse_file = next((p for p in env_paths if p.exists()), None)
    password = str((dotenv_values(warehouse_file) if warehouse_file else {}).get(
        "WAREHOUSE_PASSWORD") or "").strip()
    if not password:
        log.error("nenhum warehouse/.env com WAREHOUSE_PASSWORD encontrado")
        return False
    os.environ["SUPABASE_UNIFICADO_URL"] = (
        "postgresql://postgres:" + quote(password, safe="") + "@127.0.0.1:5433/postgres")
    return True


def _is_local_target() -> bool:
    url = (os.getenv("SUPABASE_UNIFICADO_URL") or os.getenv("DATABASE_URL") or "").lower()
    return "127.0.0.1" in url or "localhost" in url


def main() -> int:
    p = argparse.ArgumentParser(description="Ingestão FMP → market_us.* (warehouse local)")
    p.add_argument("command", choices=[
        "init-schema", "test", "universe", "estimate", "bootstrap", "daily",
        "fundamentals", "resume", "validate", "score-history", "backtest",
        "snapshot", "prices"])
    p.add_argument("--tickers", nargs="*", help="símbolos específicos")
    p.add_argument("--exchanges", nargs="*", default=None, help="NYSE NASDAQ AMEX")
    p.add_argument("--limit", type=int, default=None, help="limita o universo/lote")
    p.add_argument("--years", type=int, default=20, help="anos de histórico anual")
    p.add_argument("--budget", type=int, default=None, help="teto de chamadas na execução")
    p.add_argument("--start-year", type=int, default=None, help="score-history: ano inicial")
    p.add_argument("--end-year", type=int, default=None, help="score-history: ano final")
    p.add_argument("--top-n", type=int, default=20, help="backtest: nº de ativos por período")
    p.add_argument("--no-prices", action="store_true",
                   help="bootstrap: só fundamentos (EDGAR); pula o yfinance (rápido)")
    p.add_argument("--dry-run", action="store_true", help="não grava; só estima/planeja")
    p.add_argument("--offline", action="store_true", help="proíbe qualquer chamada de rede")
    p.add_argument("--warehouse", action="store_true",
                   help="usa o Postgres local em 127.0.0.1:5433 (warehouse/.env)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    if args.warehouse and not _point_to_warehouse():
        return 1

    # Proteção: ingestão pesada NUNCA deve escrever no Supabase remoto.
    if args.command in {"bootstrap", "daily", "fundamentals", "universe", "resume",
                        "score-history", "snapshot"} \
            and not _is_local_target() and not args.dry_run:
        log.error("Comando %s exige --warehouse (destino local). "
                  "Ingestão pesada não pode ir para o Supabase.", args.command)
        return 2

    from core.config import settings
    from data_pipeline.us import ingest

    def out(payload: dict) -> int:
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, default=str))
        else:
            for k, v in payload.items():
                log.info("%s = %s", k, v)
        return 0 if payload.get("ok", True) else 1

    # ── comandos sem rede ─────────────────────────────────────────────────────
    if args.command == "estimate":
        n = len(args.tickers) if args.tickers else (args.limit or 0)
        return out({"ok": True, **ingest.estimate(n)})

    if args.command == "init-schema":
        if args.dry_run:
            return out({"ok": True, "action": "dry-run",
                        "migrations": [p.name for p in ingest.schema_files()]})
        return out({"ok": True, "applied": ingest.apply_schema()})

    if args.command == "test":
        from core.database import test_connection
        return out({"ok": True, "source": settings.us_source,
                    "sec_user_agent_ok": settings.has_sec_user_agent,
                    "has_fmp_key": settings.has_fmp,
                    "ingest_ready": settings.us_ingest_ready,
                    "engine_local": _is_local_target(),
                    "db_connected": test_connection()})

    if args.command == "snapshot":
        # sem rede: constrói a vitrine (company_snapshots) no warehouse a partir
        # do que já foi ingerido. Publicação p/ Supabase = scripts/publish_us_snapshot.py
        from core.database import get_engine
        from data_pipeline.us import snapshot as snap
        if args.dry_run:
            return out({"ok": True, "action": "dry-run: vitrine não construída"})
        return out(snap.build_snapshot(get_engine(), limit_companies=args.limit or 800))

    if args.command == "score-history":
        # sem rede: recomputa scores PIT a partir do que já está no warehouse
        from core.database import get_engine
        from core.us_methodology import US_FUNDAMENTAL_SCORE_VERSION
        from data_pipeline.us import scoring_history as sh
        end = args.end_year or 2025
        start = args.start_year or (end - 10)
        dates = sh.annual_asof_dates(start, end)
        if args.dry_run:
            return out({"ok": True, "dates": len(dates), "action": "dry-run"})
        eng = get_engine()
        # deriva o fechamento mensal (o backtest lê de prices_monthly, que a
        # ingestão não popula) antes de computar os vintages PIT
        monthly = sh.derive_prices_monthly(eng)
        res = sh.compute_score_history(eng, dates,
                                       score_version=US_FUNDAMENTAL_SCORE_VERSION)
        res["prices_monthly_rows"] = monthly.get("rows")
        return out(res)

    if args.command == "backtest":
        from core.database import get_engine
        from core.us_read import load_score_panel
        import core.us_backtest as bt
        panel = load_score_panel()
        if panel is None or panel.empty:
            return out({"ok": False, "reason": "sem histórico de scores — rode score-history"})
        res = bt.walk_forward(panel, top_n=args.top_n, periods_per_year=1)  # painel anual
        # resumo enxuto p/ o terminal
        return out({"ok": res.get("ok"), "n_periods": res.get("n_periods"),
                    "rank_ic_mean": res.get("rank_ic", {}).get("mean"),
                    "rank_ic_tstat": res.get("rank_ic", {}).get("t_stat"),
                    "hit_rate": res.get("rank_ic", {}).get("hit_rate"),
                    "ann_return": res.get("portfolio", {}).get("ann_return"),
                    "excess_vs_ew": res.get("excess_ann_vs_ew"),
                    "sharpe": res.get("portfolio", {}).get("sharpe")})

    # ── comandos que podem tocar a rede ───────────────────────────────────────
    if args.offline:
        log.error("--offline: comando %s precisaria da rede; nada a fazer.", args.command)
        return out({"ok": False, "reason": "offline"})
    if not settings.us_ingest_ready and args.command in {"universe", "bootstrap",
                                                          "daily", "fundamentals",
                                                          "resume", "prices"}:
        if settings.us_source == "edgar":
            log.error("SEC_USER_AGENT ausente — a SEC exige identificação "
                      "('Seu Nome seu@email.com') no .env.")
            return out({"ok": False, "reason": "sem SEC_USER_AGENT"})
        log.error("FMP_API_KEY ausente — configure a chave para ingerir dados novos.")
        return out({"ok": False, "reason": "sem FMP_API_KEY"})

    from core.database import get_engine
    engine = get_engine()
    provider = ingest.make_provider(budget_limit=args.budget)

    if args.command == "universe":
        return out({"ok": True, **ingest.ingest_universe(
            provider, engine, exchanges=args.exchanges, limit=args.limit)})

    if args.command in {"bootstrap", "fundamentals", "resume"}:
        symbols = args.tickers
        if not symbols:
            # do universo já seedado no warehouse
            from sqlalchemy import text
            with engine.connect() as conn:
                q = "SELECT symbol FROM market_us.assets WHERE security_type='common'"
                if args.limit:
                    q += f" ORDER BY symbol LIMIT {int(args.limit)}"
                symbols = [r[0] for r in conn.execute(text(q)).fetchall()]
        return out({"ok": True, **ingest.ingest_symbols(
            provider, engine, symbols, years=args.years,
            resume=(args.command == "resume"), with_prices=not args.no_prices)})

    if args.command == "prices":
        # passagem incremental de preços: só quem tem fundamento e ainda não tem preço
        symbols = args.tickers
        if not symbols:
            from sqlalchemy import text
            with engine.connect() as conn:
                q = ("SELECT DISTINCT i.symbol FROM market_us.income_statements i "
                     "WHERE NOT EXISTS (SELECT 1 FROM market_us.prices_daily p "
                     "WHERE p.symbol=i.symbol) ORDER BY i.symbol")
                if args.limit:
                    q += f" LIMIT {int(args.limit)}"
                symbols = [r[0] for r in conn.execute(text(q)).fetchall()]
        return out({"ok": True, **ingest.ingest_prices_only(provider, engine, symbols)})

    if args.command == "daily":
        symbols = args.tickers or []
        n = 0
        for sym in symbols:
            r = ingest.ingest_symbol(provider, engine, sym, years=1, with_prices=True)
            n += r.get("rows", 0)
        return out({"ok": True, "symbols": len(symbols), "rows": n})

    if args.command == "validate":
        from data_pipeline.us import quality
        return out({"ok": True, **quality.run_audit(engine)})

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
