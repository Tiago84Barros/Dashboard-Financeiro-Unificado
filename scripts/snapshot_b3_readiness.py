"""Gera snapshot auditavel da prontidao dos dados de Empresas B3.

``--warehouse``: grava no armazém local, onde a ingestão brapi roda desde o
caminho A (outubro/2026). ``build_data_manifest`` lê o universo inteiro; contra
o Supabase isso era egress diário. A vitrine recebe o snapshot pronto pelo
``publish_b3_brapi_from_local.py``.
"""
from __future__ import annotations

import argparse

from core.b3_validation import persist_readiness_snapshot
from data_pipeline.utils.db_utils import get_pipeline_engine


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--warehouse", action="store_true",
                   help="grava no armazém local (127.0.0.1:5433)")
    args = p.parse_args(argv)
    if args.warehouse:
        from scripts.publish_fii_selection_from_local import _warehouse_url
        from scripts.publish_us_snapshot import _engine
        engine = _engine(_warehouse_url())
    else:
        engine = get_pipeline_engine()
    if engine is None:
        raise RuntimeError("banco indisponivel")
    snapshot_hash = persist_readiness_snapshot(engine=engine)
    if not snapshot_hash:
        raise RuntimeError("snapshot de prontidao B3 nao foi persistido")
    print({"status": "ok", "artifact_hash": snapshot_hash})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
