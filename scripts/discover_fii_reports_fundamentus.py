"""Descobre relatórios Fundos.NET indexados pelo Fundamentus, sem baixar PDFs."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests
from sqlalchemy.exc import SQLAlchemyError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tickers", nargs="*", default=[])
    parser.add_argument("--limit", type=int, default=20,
                        help="Máximo de FIIs; o piloto é limitado a 20.")
    parser.add_argument("--max-links-per-ticker", type=int, default=300)
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--attempts", type=int, default=2)
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    parser.add_argument("--write", action="store_true",
                        help="Insere somente lacunas; sem esta opção é dry-run.")
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    from scripts.backfill_fii_documents_local import _configure_database
    _configure_database()
    from data_pipeline.market.fii_fundamentus import (
        discover_fundamentus_reports,
        normalize_ticker,
        persist_discovery,
        select_pilot_tickers,
    )
    from data_pipeline.utils.db_utils import get_pipeline_engine

    engine = get_pipeline_engine()
    if engine is None:
        print(json.dumps({"status": "blocked", "blocker": "database"}))
        return 2
    limit = max(1, min(int(args.limit), 20))
    requested = []
    for raw in args.tickers:
        for ticker in str(raw).split(","):
            if ticker.strip():
                requested.append(normalize_ticker(ticker))
    tickers = list(dict.fromkeys(requested))[:limit]
    if not tickers:
        tickers = select_pilot_tickers(engine=engine, limit=limit)

    report: dict = {
        "status": "completed", "mode": "write" if args.write else "dry_run",
        "source_role": "third_party_index_only", "canonical_source": "Fundos.NET",
        "tickers_requested": len(tickers), "tickers_completed": 0,
        "pages_failed": 0, "discovered": 0, "existing": 0,
        "new": 0, "inserted": 0, "rejected_links": 0, "tickers": {},
    }
    for index, ticker in enumerate(tickers):
        try:
            discovery = discover_fundamentus_reports(
                ticker,
                timeout=max(1, min(int(args.timeout), 60)),
                attempts=max(1, min(int(args.attempts), 3)),
                max_links=max(1, min(int(args.max_links_per_ticker), 1000)),
            )
            persisted = persist_discovery(discovery, engine=engine, write=bool(args.write))
            item = {
                **persisted, "rejected_links": discovery.rejected_links,
                "page_sha256": discovery.page_sha256,
            }
            report["tickers"][ticker] = item
            report["tickers_completed"] += 1
            for key in ("discovered", "existing", "new", "inserted"):
                report[key] += int(persisted[key])
            report["rejected_links"] += int(discovery.rejected_links)
        except (ValueError, RuntimeError, requests.RequestException, SQLAlchemyError) as exc:
            report["pages_failed"] += 1
            report["tickers"][ticker] = {
                "status": "failed", "error_type": type(exc).__name__,
                "error": str(exc)[:300],
            }
        if index + 1 < len(tickers):
            time.sleep(max(0.0, min(float(args.delay_seconds), 10.0)))
    if report["pages_failed"]:
        report["status"] = "warning" if report["tickers_completed"] else "failed"
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] in {"completed", "warning"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
