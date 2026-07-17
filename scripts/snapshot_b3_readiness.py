"""Gera snapshot auditavel da prontidao dos dados de Empresas B3."""
from __future__ import annotations

from data_pipeline.utils.db_utils import get_pipeline_engine
from core.b3_validation import persist_readiness_snapshot


def main() -> int:
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
