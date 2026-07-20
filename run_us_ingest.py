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
    import subprocess
    from dotenv import dotenv_values
    env_paths = [_ROOT / "warehouse" / ".env"]
    env_paths.extend(_ROOT.glob(".claude/worktrees/*/warehouse/.env"))
    warehouse_file = next((p for p in env_paths if p.exists()), None)
    password = ""
    # O volume pode ter sido recriado com senha diferente do .env. Quando o
    # contêiner está ativo, sua variável é a fonte de verdade; capture_output
    # impede que a credencial apareça no terminal/log.
    try:
        proc = subprocess.run(
            ["docker", "compose", "exec", "-T", "warehouse", "printenv",
             "POSTGRES_PASSWORD"], cwd=str(_ROOT / "warehouse"),
            capture_output=True, text=True, timeout=10, check=False)
        if proc.returncode == 0:
            password = (proc.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        pass
    if not password:
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
        "snapshot", "prices", "enrich"])
    p.add_argument("--tickers", nargs="*", help="símbolos específicos")
    p.add_argument("--exchanges", nargs="*", default=None, help="NYSE NASDAQ AMEX")
    p.add_argument("--limit", type=int, default=None, help="limita o universo/lote")
    p.add_argument("--years", type=int, default=20, help="anos de histórico anual")
    p.add_argument("--budget", type=int, default=None, help="teto de chamadas na execução")
    p.add_argument("--start-year", type=int, default=None, help="score-history: ano inicial")
    p.add_argument("--end-year", type=int, default=None, help="score-history: ano final")
    p.add_argument("--top-n", type=int, default=20, help="backtest: nº de ativos por período")
    p.add_argument("--transaction-cost-bps", type=float, default=10.0,
                   help="backtest: corretagem/fees em pontos-base por turnover")
    p.add_argument("--slippage-bps", type=float, default=5.0,
                   help="backtest: slippage em pontos-base por turnover")
    p.add_argument("--bootstrap-samples", type=int, default=2000,
                   help="backtest: reamostragens do intervalo de confiança")
    p.add_argument("--no-prices", action="store_true",
                   help="bootstrap: só fundamentos (EDGAR); pula o yfinance (rápido)")
    p.add_argument("--shard", default=None,
                   help="bootstrap: processa só a fatia N/M do universo (ex.: 0/6). "
                        "Rode M processos em paralelo p/ acelerar (SEC ~10 req/s).")
    p.add_argument("--workers", type=int, default=1,
                   help="fundamentos: concorrência local compartilhando o limite SEC (máx. recomendado 6)")
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
                        "score-history", "snapshot", "enrich"} \
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
        return out(snap.build_snapshot(get_engine(), limit_companies=args.limit))

    if args.command == "enrich":
        from core.database import get_engine
        from data_pipeline.us.enrichment import enrich_warehouse
        if args.dry_run:
            return out({"ok": True, "action": "dry-run: enriquecimento não executado"})
        return out({"ok": True, **enrich_warehouse(get_engine())})

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
                                       score_version=US_FUNDAMENTAL_SCORE_VERSION,
                                       limit_companies=args.limit)
        res["prices_monthly_rows"] = monthly.get("rows")
        return out(res)

    if args.command == "backtest":
        from core.database import get_engine
        from core.us_read import load_score_panel
        import core.us_backtest as bt
        from core.us_methodology import US_FUNDAMENTAL_SCORE_VERSION
        panel = load_score_panel(score_version=US_FUNDAMENTAL_SCORE_VERSION)
        if panel is None or panel.empty:
            return out({"ok": False, "reason": "sem histórico de scores — rode score-history"})
        res = bt.walk_forward(
            panel, top_n=args.top_n, periods_per_year=1,
            transaction_cost_bps=args.transaction_cost_bps,
            slippage_bps=args.slippage_bps,
            bootstrap_samples=args.bootstrap_samples)
        if res.get("ok"):
            import datetime as _dt
            from sqlalchemy import text
            run_key = f"wf-{res['start_date']}-{res['end_date']}-top{args.top_n}"
            with get_engine().begin() as conn:
                conn.execute(text("""
                    INSERT INTO market_us.backtest_results
                      (run_key,score_version,strategy,params,start_date,end_date,n_periods,
                       rank_ic_mean,rank_ic_tstat,rank_ic_pvalue,hit_rate,ann_return,
                       gross_ann_return,excess_ew,volatility,sharpe,sortino,calmar,
                       max_drawdown,turnover,transaction_cost_bps,benchmark_name,
                       validation_status,bootstrap_json,equity_curve)
                    VALUES
                      (:run_key,:version,'top_n',CAST(:params AS JSONB),:start,:end,:n,
                       :ic,:tstat,:pvalue,:hit,:ann,:gross,:excess,:vol,:sharpe,:sortino,
                       :calmar,:mdd,:turnover,:cost,'equal_weight_universe','diagnostic',
                       CAST(:bootstrap AS JSONB),CAST(:curve AS JSONB))
                    ON CONFLICT (run_key,score_version,strategy) DO UPDATE SET
                       params=EXCLUDED.params,n_periods=EXCLUDED.n_periods,
                       rank_ic_mean=EXCLUDED.rank_ic_mean,rank_ic_tstat=EXCLUDED.rank_ic_tstat,
                       rank_ic_pvalue=EXCLUDED.rank_ic_pvalue,hit_rate=EXCLUDED.hit_rate,
                       ann_return=EXCLUDED.ann_return,gross_ann_return=EXCLUDED.gross_ann_return,
                       excess_ew=EXCLUDED.excess_ew,volatility=EXCLUDED.volatility,
                       sharpe=EXCLUDED.sharpe,sortino=EXCLUDED.sortino,calmar=EXCLUDED.calmar,
                       max_drawdown=EXCLUDED.max_drawdown,turnover=EXCLUDED.turnover,
                       transaction_cost_bps=EXCLUDED.transaction_cost_bps,
                       bootstrap_json=EXCLUDED.bootstrap_json,equity_curve=EXCLUDED.equity_curve,
                       created_at=NOW()
                """), {
                    "run_key": run_key, "version": US_FUNDAMENTAL_SCORE_VERSION,
                    "params": json.dumps({"top_n": args.top_n, "weighting": "score",
                                          "slippage_bps": args.slippage_bps}),
                    "start": res["start_date"], "end": res["end_date"], "n": res["n_periods"],
                    "ic": res["rank_ic"].get("mean"), "tstat": res["rank_ic"].get("t_stat"),
                    "pvalue": res["rank_ic"].get("p_value"), "hit": res["rank_ic"].get("hit_rate"),
                    "ann": res["portfolio"].get("ann_return"),
                    "gross": res["portfolio_gross"].get("ann_return"),
                    "excess": res.get("excess_ann_vs_ew"), "vol": res["portfolio"].get("volatility"),
                    "sharpe": res["portfolio"].get("sharpe"), "sortino": res["portfolio"].get("sortino"),
                    "calmar": res["portfolio"].get("calmar"), "mdd": res["portfolio"].get("max_drawdown"),
                    "turnover": res.get("avg_turnover"),
                    "cost": args.transaction_cost_bps + args.slippage_bps,
                    "bootstrap": json.dumps(res.get("bootstrap_excess")),
                    "curve": json.dumps(res.get("equity_curve")),
                })
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
            # Uma representação por companhia. Evita repetir o mesmo CIK para
            # classes de ações diferentes e ignora ativos sem identidade
            # analisável. No incremental, prioriza apenas quem ainda não possui
            # demonstrações trimestrais válidas.
            from sqlalchemy import text
            with engine.connect() as conn:
                query_params = {}
                if args.command == "bootstrap":
                    from data_pipeline.us.edgar_facts import PARSER_VERSION
                    query_params["parser_version"] = PARSER_VERSION
                    missing_quarterly = (
                        "AND NOT EXISTS ("
                        "SELECT 1 FROM market_us.income_statements s "
                        "WHERE s.company_id=a.company_id "
                        "AND s.source_version=:parser_version "
                        "UNION ALL SELECT 1 FROM market_us.balance_sheets s "
                        "WHERE s.company_id=a.company_id "
                        "AND s.source_version=:parser_version "
                        "UNION ALL SELECT 1 FROM market_us.cash_flow_statements s "
                        "WHERE s.company_id=a.company_id "
                        "AND s.source_version=:parser_version) "
                    )
                else:
                    missing_quarterly = (
                        "AND NOT EXISTS ("
                        "SELECT 1 FROM market_us.income_statements i "
                        "WHERE i.company_id=a.company_id AND i.period='quarterly') "
                    )
                q = (
                    "SELECT symbol FROM ("
                    "SELECT DISTINCT ON (a.company_id) a.symbol, "
                    "(SELECT max(i.updated_at) FROM market_us.income_statements i "
                    " WHERE i.company_id=a.company_id) AS last_statement_update "
                    "FROM market_us.assets a "
                    "WHERE a.company_id IS NOT NULL "
                    "AND a.analysis_status IN ('eligible','pending') "
                    "AND a.security_type IN ('common','reit') "
                    f"{missing_quarterly}"
                    "ORDER BY a.company_id, a.is_active DESC, a.symbol"
                    ") eligible_companies "
                    "ORDER BY last_statement_update NULLS FIRST, symbol"
                )
                if args.limit:
                    q += f" LIMIT {int(args.limit)}"
                symbols = [r[0] for r in conn.execute(
                    text(q), query_params).fetchall()]
        run_key = "bootstrap"
        if args.shard:                       # fatia N/M (round-robin) p/ paralelismo
            n, m = (int(x) for x in args.shard.split("/"))
            symbols = symbols[n::m]
            run_key = f"sweep-{n}of{m}"
            log.info("shard %d/%d: %d símbolos", n, m, len(symbols))
        return out({"ok": True, **ingest.ingest_symbols(
            provider, engine, symbols, years=args.years, run_key=run_key,
            resume=(args.command == "resume"), with_prices=not args.no_prices,
            workers=max(1, min(int(args.workers), 8)))})

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
