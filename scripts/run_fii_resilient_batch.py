"""Executa um lote local de FIIs com fallback mensal estruturado da CVM."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--process-limit", type=int, default=10)
    parser.add_argument("--recent-months", type=int, default=24)
    parser.add_argument("--min-free-gb", type=float, default=2.5)
    parser.add_argument("--max-batch-mb", type=int, default=150)
    parser.add_argument("--max-document-mb", type=int, default=30)
    parser.add_argument("--download-timeout", type=int, default=15)
    parser.add_argument("--download-attempts", type=int, default=2)
    parser.add_argument("--host-failure-threshold", type=int, default=2)
    parser.add_argument("--host-cooldown-minutes", type=int, default=60)
    parser.add_argument("--max-documents-per-host", type=int, default=2)
    parser.add_argument("--force-structured", action="store_true")
    parser.add_argument("--skip-documents", action="store_true")
    args = parser.parse_args()

    minimum_free = max(int(args.min_free_gb * 1024**3), 0)
    free = shutil.disk_usage(ROOT).free
    if free < minimum_free:
        print(json.dumps({
            "status": "blocked", "blocker": "minimum_free_space",
            "free_bytes": free, "minimum_free_bytes": minimum_free,
        }, sort_keys=True))
        return 2

    from scripts.backfill_fii_documents_local import _configure_database
    _configure_database()
    from sqlalchemy import text
    from data_pipeline.market.fii_resilient_fallback import run_resilient_fallback
    from data_pipeline.utils.db_utils import get_pipeline_engine

    engine = get_pipeline_engine()
    if engine is None:
        print(json.dumps({"status": "blocked", "blocker": "database"}))
        return 2
    lock = engine.connect()
    acquired = bool(lock.execute(text(
        "SELECT pg_try_advisory_lock(hashtext('fii_resilient_batch'))"
    )).scalar())
    if not acquired:
        lock.close()
        print(json.dumps({"status": "blocked", "blocker": "worker_active"}))
        return 2
    try:
        report = run_resilient_fallback(
            force_structured=bool(args.force_structured),
            process_documents=not args.skip_documents,
            structured_years=1,
            cooldown_minutes=max(int(args.host_cooldown_minutes), 1),
            document_options={
                "limit": max(int(args.process_limit), 1),
                "recent_months": max(int(args.recent_months), 0),
                "max_batch_bytes": max(int(args.max_batch_mb), 1) * 1024**2,
                "max_document_bytes": max(int(args.max_document_mb), 1) * 1024**2,
                "min_free_bytes": minimum_free,
                "retain_binary": False,
                "download_timeout": max(int(args.download_timeout), 5),
                "download_attempts": max(int(args.download_attempts), 1),
                "host_failure_threshold": max(int(args.host_failure_threshold), 1),
                "host_cooldown_minutes": max(int(args.host_cooldown_minutes), 1),
                "max_documents_per_host": max(int(args.max_documents_per_host), 1),
            },
        )
    finally:
        lock.execute(text(
            "SELECT pg_advisory_unlock(hashtext('fii_resilient_batch'))"
        ))
        lock.close()
    print(json.dumps(report, ensure_ascii=False, default=str, sort_keys=True))
    return 0 if report.get("status") in {"completed", "warning"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
