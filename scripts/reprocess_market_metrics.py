"""Reprocessa métricas market em lotes, com progresso e retomada simples."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from core.database import get_engine
from data_pipeline.market.ingest import reprocess_metrics


def canonical_tickers() -> list[str]:
    engine = get_engine()
    if engine is None:
        return []
    with engine.connect() as conn:
        return [row[0] for row in conn.execute(text("""
            SELECT DISTINCT a.ticker
            FROM market.assets a
            JOIN market.companies c ON c.id=a.company_id
            WHERE a.asset_type IN ('stock','unit')
              AND a.is_active IS TRUE
            ORDER BY a.ticker
        """)).fetchall()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    tickers = canonical_tickers()
    if args.limit:
        tickers = tickers[: args.limit]
    total = len(tickers)
    aggregate = {"tickers": 0, "indicadores": 0, "erros": 0}
    started = time.time()
    for start in range(0, total, max(args.batch_size, 1)):
        batch = tickers[start:start + args.batch_size]
        result = reprocess_metrics(tickers=batch)
        for key in aggregate:
            aggregate[key] += int(result.get(key, 0) or 0)
        print(json.dumps({
            "processed": min(start + len(batch), total),
            "total": total,
            "elapsed_s": round(time.time() - started, 1),
            **aggregate,
        }), flush=True)
    return 0 if aggregate["erros"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
