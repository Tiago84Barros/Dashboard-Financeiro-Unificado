"""Carga histórica macro isolada; escreve somente no PostgreSQL Docker local."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from dotenv import load_dotenv

from core.macro_data.backfill import parse_backfill_period

_ROOT = Path(__file__).resolve().parent
load_dotenv(_ROOT / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Carga histórica macro no PostgreSQL Docker local"
    )
    parser.add_argument("--from", dest="start", help="Data inicial AAAA-MM-DD")
    parser.add_argument("--to", dest="end", help="Data final AAAA-MM-DD")
    parser.add_argument(
        "--provider", action="append", dest="providers", help="Fonte habilitada (repetível)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Exibir prontidão sem consultar APIs ou banco"
    )
    parser.add_argument(
        "--fred-vintages",
        action="store_true",
        help="Incluir vintages FRED; desligado por padrão para preservar a cota da API",
    )
    parser.add_argument(
        "--max-fred-vintages",
        type=int,
        default=12,
        help="Máximo de vintages recentes por série FRED (padrão: 12)",
    )
    args = parser.parse_args()
    try:
        start, end = parse_backfill_period(args.start, args.end)
    except ValueError as exc:
        parser.error(str(exc))
    if args.dry_run:
        from core.config import settings
        from core.macro_data.readiness import backfill_readiness

        rows = [row.__dict__ for row in backfill_readiness(settings)]
        print(json.dumps({"start": str(start), "end": str(end), "providers": rows}))
        return 0
    from data_pipeline.jobs.update_macro_international import run

    if args.max_fred_vintages < 0:
        parser.error("--max-fred-vintages não pode ser negativo")
    result = run(
        start=start,
        end=end,
        provider_names=set(args.providers or []) or None,
        fred_vintages=args.fred_vintages,
        max_fred_vintages=args.max_fred_vintages,
    )
    logging.getLogger(__name__).info(
        "macro backfill %s..%s status=%s inserted=%s failed=%s",
        start,
        end,
        result["status"],
        result["records_inserted"],
        result["records_failed"],
    )
    print(json.dumps(result, default=str))
    return 0 if result["status"] in {"success", "partial_success", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
