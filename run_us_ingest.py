"""
run_us_ingest.py
CLI da ingestão Empresas Americanas (FMP → warehouse local market_us.*).

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
        "fundamentals", "resume", "validate"])
    p.add_argument("--tickers", nargs="*", help="símbolos específicos")
    p.add_argument("--exchanges", nargs="*", default=None, help="NYSE NASDAQ AMEX")
    p.add_argument("--limit", type=int, default=None, help="limita o universo/lote")
    p.add_argument("--years", type=int, default=20, help="anos de histórico anual")
    p.add_argument("--budget", type=int, default=None, help="teto de chamadas na execução")
    p.add_argument("--dry-run", action="store_true", help="não grava; só estima/planeja")
    p.add_argument("--offline", action="store_true", help="proíbe qualquer chamada de rede")
    p.add_argument("--warehouse", action="store_true",
                   help="usa o Postgres local em 127.0.0.1:5433 (warehouse/.env)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    if args.warehouse and not _point_to_warehouse():
        return 1

    # Proteção: ingestão pesada NUNCA deve escrever no Supabase remoto.
    if args.command in {"bootstrap", "daily", "fundamentals", "universe", "resume"} \
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
            return out({"ok": True, "action": "dry-run: schema não aplicado"})
        ingest.apply_schema()
        return out({"ok": True, "action": "schema market_us aplicado (040)"})

    if args.command == "test":
        from core.database import test_connection
        return out({"ok": True, "has_fmp_key": settings.has_fmp,
                    "engine_local": _is_local_target(),
                    "db_connected": test_connection()})

    # ── comandos que podem tocar a rede ───────────────────────────────────────
    if args.offline:
        log.error("--offline: comando %s precisaria da rede; nada a fazer.", args.command)
        return out({"ok": False, "reason": "offline"})
    if not settings.has_fmp and args.command in {"universe", "bootstrap", "daily",
                                                 "fundamentals", "resume"}:
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
            resume=(args.command == "resume"))})

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
