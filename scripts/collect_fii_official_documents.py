"""Coleta fontes oficiais cadastradas e, opcionalmente, processa PDFs pendentes."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-id", type=int)
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--process-limit", type=int, default=0)
    parser.add_argument("--ticker", action="append", default=[])
    parser.add_argument("--recent-months", type=int, default=24)
    parser.add_argument(
        "--source-hash-only", action="store_true",
        help="Preserva hash/URL sem reter uma cópia local do PDF.",
    )
    parser.add_argument("--min-free-gb", type=float, default=5.0)
    parser.add_argument("--download-timeout", type=int, default=60)
    parser.add_argument("--download-attempts", type=int, default=3)
    parser.add_argument("--host-failure-threshold", type=int, default=3)
    parser.add_argument("--host-cooldown-minutes", type=int, default=30)
    parser.add_argument("--max-documents-per-host", type=int, default=3)
    args = parser.parse_args()

    from data_pipeline.market.fii_documents import process_pending_documents
    from data_pipeline.market.fii_ri_documents import (
        collect_document_source,
        collect_due_document_sources,
    )

    if args.source_id:
        collection = collect_document_source(args.source_id)
    else:
        collection = collect_due_document_sources(limit=args.limit)
    report: dict = {"collection": collection}
    if args.process_limit > 0:
        report["processing"] = process_pending_documents(
            limit=args.process_limit,
            tickers=args.ticker,
            recent_months=max(args.recent_months, 0),
            min_free_bytes=max(int(args.min_free_gb * 1024**3), 0),
            retain_binary=not args.source_hash_only,
            download_timeout=max(args.download_timeout, 5),
            download_attempts=max(args.download_attempts, 1),
            host_failure_threshold=max(args.host_failure_threshold, 1),
            host_cooldown_minutes=max(args.host_cooldown_minutes, 1),
            max_documents_per_host=max(args.max_documents_per_host, 1),
        )
    print(json.dumps(report, ensure_ascii=False, default=str, sort_keys=True))
    failed = (
        collection.get("status") in {"failed", "partial", "blocked"}
        or (report.get("processing") or {}).get("failed", 0) > 0
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
