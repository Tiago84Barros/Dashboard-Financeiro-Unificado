"""Arquiva payloads BRAPI do Supabase no warehouse local, com validação por hash."""
from __future__ import annotations

import json
import os
import sys
import time
from itertools import islice
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import settings
from scripts.publish_fii_selection_from_local import _warehouse_url


def _engine(url: str, remote: bool = False):
    parsed = make_url(url)
    if parsed.drivername in {"postgresql", "postgres"}:
        parsed = parsed.set(drivername="postgresql+psycopg2")
    connect_args: dict = {"connect_timeout": 15}
    kwargs: dict = {"future": True, "connect_args": connect_args}
    if remote:
        parsed = parsed.update_query_dict({"sslmode": "require"})
        connect_args.update(
            options="-c statement_timeout=300000",
            keepalives=1, keepalives_idle=10, keepalives_interval=5,
            keepalives_count=3,
        )
        if os.getenv("SUPABASE_DB_HOSTADDR"):
            connect_args["hostaddr"] = os.environ["SUPABASE_DB_HOSTADDR"]
        kwargs["poolclass"] = NullPool
    else:
        kwargs["pool_pre_ping"] = True
    return create_engine(parsed, **kwargs)


def _chunks(values: list[int], size: int):
    iterator = iter(values)
    while chunk := list(islice(iterator, size)):
        yield chunk


def _json(value):
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def ids_sem_manifesto(ids_remotos, ids_no_manifesto) -> set:
    """Ids do remoto que o manifesto local nao cobre. Vazio = pode compactar.

    Cobertura e questao de CONJUNTO, nao de total. A checagem anterior comparava
    contagens (`manifest_rows != len(metadata)`) e por isso so passava na
    primeira execucao: o manifesto e chaveado por (archive_source, remote_id) e
    ACUMULA entre rodadas, enquanto a tabela remota e podada. Em 16/08/2026 ele
    tinha 26.481 linhas para 19.115 remotas -- 7.366 ids de julho que ja nao
    existem la -- e o portao acusava "manifesto incompleto" justamente quando
    havia dado a MAIS preservado, travando a compactacao para sempre.
    """
    return set(ids_remotos) - set(ids_no_manifesto)


def archive() -> dict:
    if not settings.db_url:
        raise RuntimeError("Supabase não configurado")
    remote = _engine(settings.db_url, remote=True)
    local = _engine(_warehouse_url())
    hash_sql = text("""
        SELECT DISTINCT content_sha256 FROM market.brapi_raw_payloads
        WHERE content_sha256 IS NOT NULL
    """)
    with local.connect() as conn:
        local_hashes = set(conn.execute(hash_sql).scalars())
    with remote.connect() as conn:
        metadata = [dict(row) for row in conn.execute(text("""
            SELECT id,ticker,endpoint,fetched_at,source,request_status,error_message,
                   request_fingerprint,content_sha256,http_status,
                   source_published_at,collected_at,revision_detected
            FROM market.brapi_raw_payloads ORDER BY id
        """)).mappings()]

    # Guarda a relação endpoint/coleta/hash separadamente do blob. Assim payloads
    # idênticos não precisam ser duplicados para preservar toda a rastreabilidade.
    manifest_ddl = text("""
        CREATE TABLE IF NOT EXISTS market.brapi_remote_archive_manifest (
            archive_source TEXT NOT NULL,
            remote_id BIGINT NOT NULL,
            ticker TEXT,
            endpoint TEXT NOT NULL,
            fetched_at TIMESTAMPTZ NOT NULL,
            source TEXT,
            request_status TEXT NOT NULL,
            error_message TEXT,
            request_fingerprint TEXT,
            content_sha256 TEXT,
            http_status INTEGER,
            source_published_at TIMESTAMPTZ,
            collected_at TIMESTAMPTZ,
            revision_detected BOOLEAN NOT NULL DEFAULT FALSE,
            archived_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (archive_source,remote_id)
        )
    """)
    manifest_insert = text("""
        INSERT INTO market.brapi_remote_archive_manifest (
            archive_source,remote_id,ticker,endpoint,fetched_at,source,
            request_status,error_message,request_fingerprint,content_sha256,
            http_status,source_published_at,collected_at,revision_detected
        ) VALUES (
            'supabase_pre_compaction_2026_07',:id,:ticker,:endpoint,:fetched_at,
            :source,:request_status,:error_message,:request_fingerprint,
            :content_sha256,:http_status,:source_published_at,:collected_at,
            :revision_detected
        ) ON CONFLICT (archive_source,remote_id) DO NOTHING
    """)
    with local.begin() as conn:
        conn.execute(manifest_ddl)
        for start in range(0, len(metadata), 1000):
            conn.execute(manifest_insert, metadata[start:start + 1000])

    first_id_by_missing_hash: dict[str, int] = {}
    for row in metadata:
        digest = row.get("content_sha256")
        if digest and digest not in local_hashes and digest not in first_id_by_missing_hash:
            first_id_by_missing_hash[str(digest)] = int(row["id"])
    missing_ids = list(first_id_by_missing_hash.values())
    select_rows = text("""
        SELECT id,ticker,endpoint,payload_json,fetched_at,source,request_status,
               error_message,request_fingerprint,request_params_json,
               response_headers_json,content_sha256,http_status,
               source_published_at,collected_at,revision_detected
        FROM market.brapi_raw_payloads WHERE id = ANY(:ids) ORDER BY id
    """)
    insert = text("""
        INSERT INTO market.brapi_raw_payloads (
            ticker,endpoint,payload_json,fetched_at,source,request_status,
            error_message,request_fingerprint,request_params_json,
            response_headers_json,content_sha256,http_status,
            source_published_at,collected_at,revision_detected
        )
        SELECT :ticker,:endpoint,CAST(:payload_json AS jsonb),:fetched_at,:source,
               :request_status,:error_message,:request_fingerprint,
               CAST(:request_params_json AS jsonb),
               CAST(:response_headers_json AS jsonb),:content_sha256,:http_status,
               :source_published_at,:collected_at,:revision_detected
        WHERE NOT EXISTS (
            SELECT 1 FROM market.brapi_raw_payloads p
            WHERE p.endpoint=:endpoint
              AND p.ticker IS NOT DISTINCT FROM :ticker
              AND p.fetched_at=:fetched_at
              AND p.request_status=:request_status
              AND p.content_sha256 IS NOT DISTINCT FROM :content_sha256
        )
        ON CONFLICT (endpoint,request_fingerprint,content_sha256)
        WHERE request_status='success' AND content_sha256 IS NOT NULL
        DO NOTHING
    """)
    read = inserted = 0
    for ids in _chunks(missing_ids, 5):
        rows = None
        for attempt in range(1, 6):
            try:
                with remote.connect() as conn:
                    rows = [dict(row) for row in conn.execute(select_rows, {"ids": ids}).mappings()]
                break
            except Exception:
                if attempt == 5:
                    raise
                time.sleep(2 * attempt)
        assert rows is not None
        prepared = []
        for row in rows:
            row.pop("id", None)
            for column in ("payload_json", "request_params_json", "response_headers_json"):
                row[column] = _json(row.get(column))
            prepared.append(row)
        with local.begin() as conn:
            result = conn.execute(insert, prepared)
            inserted += max(0, int(result.rowcount or 0))
        read += len(prepared)
        if read % 500 < len(prepared):
            print(f"arquivados {read}/{len(missing_ids)}", flush=True)

    with local.connect() as conn:
        final_local_hashes = set(conn.execute(hash_sql).scalars())
        manifest_ids = set(conn.execute(text("""
            SELECT remote_id FROM market.brapi_remote_archive_manifest
            WHERE archive_source='supabase_pre_compaction_2026_07'
        """)).scalars())
    remote.dispose()
    local.dispose()
    remote_hashes = {
        str(row["content_sha256"]) for row in metadata if row.get("content_sha256")
    }
    missing_after = len(remote_hashes - final_local_hashes)
    if missing_after:
        raise RuntimeError(f"arquivo local incompleto: {missing_after} hashes ausentes")
    sem_manifesto = ids_sem_manifesto((int(row["id"]) for row in metadata), manifest_ids)
    if sem_manifesto:
        raise RuntimeError(
            f"manifesto incompleto: {len(sem_manifesto)} ids remotos sem registro"
        )
    return {
        "remote_rows": len(metadata),
        "selected_for_archive": len(missing_ids),
        "unhashed_operational_rows": sum(
            1 for row in metadata if row.get("content_sha256") is None
        ),
        "read": read,
        "inserted": inserted,
        "manifest_rows": len(manifest_ids),
        "remote_ids_sem_manifesto": len(sem_manifesto),
        "remote_unique_hashes": len(remote_hashes),
        "local_unique_hashes": len(final_local_hashes),
        "remote_hashes_missing_local": missing_after,
    }


if __name__ == "__main__":
    print(json.dumps(archive(), ensure_ascii=False, sort_keys=True))
