"""
run_data_updates.py
CLI para executar o pipeline de atualização de dados de forma headless.

Uso:
    python run_data_updates.py --all
    python run_data_updates.py --source update_bcb
    python run_data_updates.py --source update_b3_quotes --force
    python run_data_updates.py --status
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Garante que o diretório raiz está no path
_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_data_updates")


def cmd_run(args: argparse.Namespace) -> int:
    from data_pipeline.orchestrator import run_updates

    group = args.source if args.source else "all"
    logger.info("Iniciando pipeline — grupo=%s  force=%s", group, args.force)

    resultado = run_updates(update_group=group, force=args.force)

    status = resultado.get("status", "?")
    total  = resultado.get("total_jobs", 0)
    ok     = resultado.get("success_count", 0)
    falhou = resultado.get("failed_count", 0)
    skip   = resultado.get("skipped_count", 0)

    logger.info(
        "Pipeline concluído — status=%s  jobs=%d  ok=%d  falha=%d  pulados=%d",
        status, total, ok, falhou, skip,
    )

    for r in resultado.get("results", []):
        s = r.get("status", "?")
        logger.info(
            "  [%s] %s — inseridos=%d  falhas=%d  tempo=%.1fs  %s",
            s,
            r.get("source_name", r.get("job_name", "?")),
            r.get("records_inserted", 0),
            r.get("records_failed", 0),
            r.get("execution_time_seconds", 0.0),
            f"ERRO: {r['error_message']}" if r.get("error_message") else "",
        )

    if args.json:
        print(json.dumps(resultado, indent=2, default=str))

    return 0 if status in ("success", "partial_success", "skipped") else 1


def cmd_status(args: argparse.Namespace) -> int:
    from data_pipeline.orchestrator import get_update_status
    from data_pipeline.utils.date_utils import fmt_datetime_br

    status_list = get_update_status()

    if not status_list:
        logger.warning("Nenhuma fonte registrada. Execute --all primeiro.")
        return 0

    col_w = 35
    print(f"\n{'Fonte':<{col_w}} {'Status':<16} {'Última OK':<18} {'Próxima':<18}")
    print("-" * (col_w + 56))
    for s in status_list:
        nome   = (s.get("source_name") or "?")[:col_w - 1]
        fresh  = s.get("freshness_status", "?")
        ultima = fmt_datetime_br(s.get("last_success_at")) if s.get("last_success_at") else "—"
        proxima = fmt_datetime_br(s.get("next_expected_update")) if s.get("next_expected_update") else "—"
        print(f"{nome:<{col_w}} {fresh:<16} {ultima:<18} {proxima:<18}")

    if args.json:
        print(json.dumps(status_list, indent=2, default=str))

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pipeline de atualização de dados — Dashboard Financeiro Unificado"
    )
    parser.add_argument("--all",    action="store_true", help="Atualizar todas as fontes ativas")
    parser.add_argument("--source", metavar="JOB_NAME",  help="Executar apenas um job específico")
    parser.add_argument("--force",  action="store_true", help="Ignorar controle de frequência")
    parser.add_argument("--status", action="store_true", help="Mostrar status das fontes sem executar")
    parser.add_argument("--json",   action="store_true", help="Exibir saída em JSON")

    args = parser.parse_args()

    if args.status:
        return cmd_status(args)

    if not args.all and not args.source:
        parser.print_help()
        return 1

    return cmd_run(args)


if __name__ == "__main__":
    raise SystemExit(main())
