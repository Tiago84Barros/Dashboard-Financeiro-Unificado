"""Aplica, de forma idempotente, as migrations necessárias à metodologia FII v6.

**Estas migrations são do ARMAZÉM LOCAL, não do Supabase.** Das nove, quatro
criam tabelas de trabalho do pipeline -- `fii_cvm_archive_loads`,
`fii_cri_archive_loads`, os checkpoints de parser, o índice de
`fii_document_versions` -- que a arquitetura local-first deliberadamente tirou
do Supabase. No armazém as nove já existem e este script é um no-op; no Supabase
ele quebra em `relation "market.fii_source_releases" does not exist`, e o
sucesso seria pior que a falha: repovoaria o Supabase com as tabelas que a
migração local-first esvaziou.

Foi rodá-lo contra o Supabase que derrubou o job de FIIs do `market-refresh.yml`
em dez execuções diárias seguidas, sempre no mesmo ponto. Por isso o
``--warehouse``: quem chama declara o destino em vez de herdar o que estiver no
`.env`.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from data_pipeline.utils.db_utils import get_pipeline_engine

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MIGRATIONS = (
    ("033_fii_pit_validation_and_calibration.sql", "market.fii_pit_score_snapshots"),
    ("034_fii_v6_covering_indexes.sql", "market.idx_fii_validation_methodology"),
    ("035_fii_b3_archive_checkpoints.sql", "market.fii_b3_archive_loads"),
    ("036_fii_cvm_archive_checkpoints.sql", "market.fii_cvm_archive_loads"),
    ("037_fii_b3_parser_checkpoints.sql", "market.idx_fii_b3_archive_parser_status"),
    ("038_fii_cri_archive_checkpoints.sql", "market.fii_cri_archive_loads"),
    ("039_fii_selection_inputs_snapshot.sql", "market.fii_selection_inputs"),
    ("041_fii_evidence_review_and_rls.sql", "market.fii_schema_migrations"),
    ("042_fii_document_source_hash_storage.sql", "market.idx_fii_document_versions_source_hash"),
)


def apply() -> dict[str, list[str]]:
    engine = get_pipeline_engine()
    if engine is None:
        raise RuntimeError("banco indisponível")
    report: dict[str, list[str]] = {"applied": [], "skipped": []}
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(hashtext(%s))", ("fii-schema-v6",))
            # O warehouse local não possui os papéis criados pelo Supabase.
            # Papéis NOLOGIN permitem validar DDL/RLS sem conceder acesso.
            from core.config import settings
            if "127.0.0.1" in settings.db_url or "localhost" in settings.db_url:
                cursor.execute("""
                    DO $$ BEGIN
                        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon')
                        THEN CREATE ROLE anon NOLOGIN; END IF;
                        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated')
                        THEN CREATE ROLE authenticated NOLOGIN; END IF;
                    END $$
                """)
            for filename, marker in MIGRATIONS:
                cursor.execute("SELECT to_regclass(%s)", (marker,))
                if cursor.fetchone()[0] is not None:
                    report["skipped"].append(filename)
                    continue
                sql = (ROOT / "supabase_unificado" / "schema" / filename).read_text(
                    encoding="utf-8")
                cursor.execute(sql)
                raw.commit()
                report["applied"].append(filename)
            cursor.execute("SELECT pg_advisory_unlock(hashtext(%s))", ("fii-schema-v6",))
            raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()
    return report


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--warehouse", action="store_true",
                   help="Aplica no armazém local (127.0.0.1:5433), que é o destino "
                        "correto destas migrations.")
    args = p.parse_args(argv)
    if args.warehouse:
        from run_market_ingest import _point_to_warehouse
        if not _point_to_warehouse():
            print("armazém local indisponível")
            return 1
    print(apply())
    return 0


if __name__ == "__main__":
    sys.exit(main())
