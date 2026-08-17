"""Compacta no Supabase o cache BRAPI bruto já arquivado no warehouse local."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import settings
from scripts.archive_remote_brapi_raw import ids_sem_manifesto
from scripts.publish_fii_selection_from_local import _warehouse_url


COMPACTION_SQL = """
DROP TABLE market.brapi_raw_payloads CASCADE;
CREATE TABLE market.brapi_raw_payloads (
    id BIGSERIAL PRIMARY KEY,
    ticker TEXT,
    endpoint TEXT NOT NULL,
    payload_json JSONB,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source TEXT NOT NULL DEFAULT 'brapi.dev',
    request_status TEXT NOT NULL DEFAULT 'success'
        CHECK (request_status IN ('success','failed','rate_limited','empty')),
    error_message TEXT,
    request_fingerprint TEXT,
    request_params_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    response_headers_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    content_sha256 TEXT,
    http_status INTEGER,
    source_published_at TIMESTAMPTZ,
    collected_at TIMESTAMPTZ,
    supersedes_id BIGINT REFERENCES market.brapi_raw_payloads(id) ON DELETE SET NULL,
    revision_detected BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX idx_brapi_raw_ticker ON market.brapi_raw_payloads (ticker);
CREATE INDEX idx_brapi_raw_endpoint ON market.brapi_raw_payloads (endpoint, fetched_at DESC);
CREATE INDEX idx_brapi_raw_fetched ON market.brapi_raw_payloads (fetched_at DESC);
CREATE INDEX idx_brapi_raw_request ON market.brapi_raw_payloads
    (endpoint, request_fingerprint, collected_at DESC);
CREATE INDEX idx_brapi_raw_payloads_supersedes ON market.brapi_raw_payloads (supersedes_id)
    WHERE supersedes_id IS NOT NULL;
CREATE UNIQUE INDEX uq_brapi_raw_success_content ON market.brapi_raw_payloads
    (endpoint, request_fingerprint, content_sha256)
    WHERE request_status='success' AND content_sha256 IS NOT NULL;
ALTER TABLE market.brapi_raw_payloads ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE market.brapi_raw_payloads FROM PUBLIC, anon, authenticated;
"""


def _engine(url: str, remote: bool):
    parsed = make_url(url)
    if parsed.drivername in {"postgresql", "postgres"}:
        parsed = parsed.set(drivername="postgresql+psycopg2")
    kwargs: dict = {"future": True, "connect_args": {"connect_timeout": 15}}
    if remote:
        parsed = parsed.update_query_dict({"sslmode": "require"})
        kwargs["connect_args"].update(options="-c statement_timeout=300000")
        if os.getenv("SUPABASE_DB_HOSTADDR"):
            kwargs["connect_args"]["hostaddr"] = os.environ["SUPABASE_DB_HOSTADDR"]
        kwargs["poolclass"] = NullPool
    return create_engine(parsed, **kwargs)


def audit() -> dict:
    remote = _engine(settings.db_url, True)
    local = _engine(_warehouse_url(), False)
    query = text("""
        SELECT DISTINCT content_sha256 FROM market.brapi_raw_payloads
        WHERE content_sha256 IS NOT NULL
    """)
    with remote.connect() as conn:
        remote_keys = set(conn.execute(query).scalars())
        remote_ids = set(conn.execute(
            text("SELECT id FROM market.brapi_raw_payloads")).scalars())
        before = dict(conn.execute(text("""
            SELECT count(*) rows,pg_total_relation_size('market.brapi_raw_payloads') bytes,
                   pg_database_size(current_database()) database_bytes
            FROM market.brapi_raw_payloads
        """)).mappings().one())
    with local.connect() as conn:
        local_keys = set(conn.execute(query).scalars())
        manifest_ids = set(conn.execute(text("""
            SELECT remote_id FROM market.brapi_remote_archive_manifest
            WHERE archive_source='supabase_pre_compaction_2026_07'
        """)).scalars())
    remote.dispose()
    local.dispose()
    return {
        **before,
        "remote_unique_hashes": len(remote_keys),
        "local_unique_hashes": len(local_keys),
        "remote_hashes_missing_local": len(remote_keys - local_keys),
        "local_manifest_rows": len(manifest_ids),
        # Cobertura por conjunto: o manifesto acumula entre rodadas e guarda ids
        # que a tabela remota ja podou, entao comparar totais acusaria falta onde
        # ha sobra. Ver ids_sem_manifesto em archive_remote_brapi_raw.
        "remote_ids_sem_manifesto": len(ids_sem_manifesto(remote_ids, manifest_ids)),
    }


def compact() -> dict:
    before = audit()
    if before["remote_hashes_missing_local"]:
        raise RuntimeError("compactação bloqueada: payloads remotos ausentes no local")
    if before["remote_ids_sem_manifesto"]:
        raise RuntimeError("compactação bloqueada: manifesto remoto incompleto")
    engine = _engine(settings.db_url, True)
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL statement_timeout = '300s'"))
        conn.exec_driver_sql(COMPACTION_SQL)
    with engine.connect() as conn:
        after = dict(conn.execute(text("""
            SELECT count(*) rows,pg_total_relation_size('market.brapi_raw_payloads') bytes,
                   pg_database_size(current_database()) database_bytes
            FROM market.brapi_raw_payloads
        """)).mappings().one())
    engine.dispose()
    return {"before": before, "after": after,
            "freed_bytes": int(before["database_bytes"]) - int(after["database_bytes"])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = compact() if args.apply else {"dry_run": True, "audit": audit()}
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
