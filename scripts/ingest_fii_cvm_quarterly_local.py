"""Reprocessa informes trimestrais CVM no warehouse local com parser atual."""
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
    parser.add_argument("--years", type=int, default=11)
    parser.add_argument(
        "--skip-postprocess",
        action="store_true",
        help=(
            "não grava auditoria, gate bloqueado ou score snapshot; "
            "use antes de uma validação PIT controlada"
        ),
    )
    args = parser.parse_args()

    from core.config import settings
    from core.database import get_engine, get_session_factory
    from data_pipeline.market.fii_cvm_structured import ingest_cvm_structured
    from scripts.publish_fii_selection_from_local import _warehouse_url

    local_url = _warehouse_url()
    settings.SUPABASE_UNIFICADO_URL = local_url
    settings.DATABASE_URL = local_url
    settings.SUPABASE_DB_URL = local_url
    get_engine.clear()
    get_session_factory.clear()
    result = ingest_cvm_structured(
        years=max(int(args.years), 1),
        kinds=("quarterly",),
        run_postprocess=not args.skip_postprocess,
    )
    summary = {
        "status": result.get("status"),
        "archives": result.get("archives"),
        "skipped_archives": result.get("skipped_archives"),
        "revisions": result.get("revisions"),
        "observations": result.get("observations"),
        "exposures": result.get("exposures"),
        "errors": result.get("errors") or [],
        "by_kind": result.get("by_kind") or {},
        "snapshot": result.get("snapshot") or {},
        "postprocess": result.get("postprocess"),
    }
    print(json.dumps(summary, ensure_ascii=False, default=str, sort_keys=True))
    return 0 if result.get("status") in {"completed", "partial"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
