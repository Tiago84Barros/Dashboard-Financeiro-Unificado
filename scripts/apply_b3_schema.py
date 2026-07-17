"""Aplica o schema auditavel de Empresas B3 de forma idempotente."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_pipeline.utils.db_utils import get_pipeline_engine

MIGRATIONS = (("043_b3_validation_and_pit_audit.sql", "market.b3_validation_runs"),)


def apply() -> dict[str, list[str]]:
    engine = get_pipeline_engine()
    if engine is None:
        raise RuntimeError("banco indisponivel")
    report: dict[str, list[str]] = {"applied": [], "skipped": []}
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(hashtext(%s))", ("b3-audit-schema",))
            for filename, marker in MIGRATIONS:
                cursor.execute("SELECT to_regclass(%s)", (marker,))
                if cursor.fetchone()[0] is not None:
                    report["skipped"].append(filename)
                    continue
                sql = (ROOT / "supabase_unificado" / "schema" / filename).read_text(encoding="utf-8")
                cursor.execute(sql)
                raw.commit()
                report["applied"].append(filename)
            cursor.execute("SELECT pg_advisory_unlock(hashtext(%s))", ("b3-audit-schema",))
            raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()
    return report


if __name__ == "__main__":
    print(apply())
