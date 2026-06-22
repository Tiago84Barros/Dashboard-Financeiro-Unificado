"""
run_market_ingest.py
CLI da ingestão BRAPI Pro -> Supabase (schema market.*).

Comandos:
    python run_market_ingest.py bootstrap   # 16 anos de histórico (range=max)
    python run_market_ingest.py daily        # atualização leve diária
    python run_market_ingest.py annual       # refresh de demonstrações
    python run_market_ingest.py reprocess    # recalcula indicadores (sem rede)

Opções comuns:
    --tickers PETR4 VALE3   processa só estes
    --source setores|brapi  universo (default: setores)
    --limit N               limita a N empresas (teste)

Requer schema market.* aplicado (013_market_brapi_schema.sql) e BRAPI_TOKEN
(para o universo completo; sem token, só os 4 tickers gratuitos da brapi).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))
from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("market_ingest")


def main() -> int:
    p = argparse.ArgumentParser(description="Ingestão BRAPI Pro -> Supabase (market.*)")
    p.add_argument("command", choices=["validate", "bootstrap", "daily", "annual", "reprocess"])
    p.add_argument("--tickers", nargs="*", help="Tickers específicos")
    p.add_argument("--source", default="setores", choices=["setores", "brapi"])
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    from data_pipeline.market import ingest

    tickers = [t.upper().replace(".SA", "") for t in args.tickers] if args.tickers else None
    log.info("Comando=%s  source=%s  limit=%s  tickers=%s",
             args.command, args.source, args.limit, tickers or "(universo)")

    if args.command == "validate":
        rep = ingest.validate(tickers or ["PETR4", "ITUB4", "WEGE3"])
        log.info("BRAPI token: %s", rep.get("brapi_token"))
        log.info("schema market.*: %s | persistido: %s",
                 rep.get("schema_market"), rep.get("persistido"))
        for tk, e in rep.get("tickers", {}).items():
            if e.get("erro"):
                log.warning("  %s: ERRO %s", tk, e["erro"])
            else:
                b = e.get("blocos", {})
                log.info("  %s: perfil=%s cotação=%s histórico=%d DRE=%d BP=%d DFC=%d div=%d ind=%d %s",
                         tk, b.get("perfil"), b.get("cotacao"), b.get("historico", 0),
                         b.get("dre", 0), b.get("bp", 0), b.get("dfc", 0),
                         b.get("dividendos", 0), b.get("indicadores", 0),
                         (f"| faltando: {', '.join(e['faltando'])}" if e.get("faltando") else ""))
        if args.json:
            print(json.dumps(rep, indent=2, default=str))
        return 0

    if args.command == "bootstrap":
        prog = ingest.bootstrap(tickers, args.source, args.limit)
    elif args.command == "daily":
        prog = ingest.daily(tickers, args.source, args.limit)
    elif args.command == "annual":
        prog = ingest.annual(tickers, args.source, args.limit)
    else:
        prog = ingest.reprocess_metrics(tickers, args.limit)

    if prog.get("erros") == -1:
        log.error("Falha: schema market.* ausente ou banco não conectado.")
        return 1

    log.info("Progresso — empresas=%d preços=%d demonstrações=%d dividendos=%d "
             "indicadores=%d erros=%d (tickers=%d)",
             prog["empresas"], prog["precos"], prog["demonstracoes"], prog["dividendos"],
             prog["indicadores"], prog["erros"], prog["tickers"])
    if args.json:
        print(json.dumps(prog, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
