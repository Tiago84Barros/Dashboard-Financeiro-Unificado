"""
data_pipeline/jobs/update_cvm.py
Sincroniza documentos CVM (fatos relevantes, resultados) do App1 para o App4.

Requer SOURCE_DB_APP1 ou SUPABASE_DB_URL_B3 no .env.
Faz UPSERT incremental em docs_corporativos e docs_corporativos_chunks.
"""
from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)

TABLE_NAME  = "docs_corporativos_chunks"
SOURCE_NAME = "CVM / IPE"
JOB_NAME    = "update_cvm"

_BATCH = 500


def run() -> dict:
    """Copia documentos CVM novos do App1 para o App4 (incremental)."""
    result = {
        "status":           "success",
        "table_name":       TABLE_NAME,
        "source_name":      SOURCE_NAME,
        "job_name":         JOB_NAME,
        "records_inserted": 0,
        "records_updated":  0,
        "records_failed":   0,
        "error_message":    None,
    }

    source_url = (
        os.getenv("SOURCE_DB_APP1", "").strip()
        or os.getenv("SUPABASE_DB_URL_B3", "").strip()
    )

    if not source_url:
        logger.info("update_cvm: SOURCE_DB_APP1 não configurado — pulando")
        result["status"] = "skipped"
        result["error_message"] = "SOURCE_DB_APP1/SUPABASE_DB_URL_B3 não configurado"
        return result

    from data_pipeline.utils.db_utils import get_pipeline_engine
    from sqlalchemy import create_engine, text

    dst_engine = get_pipeline_engine()
    if dst_engine is None:
        result["status"] = "failed"
        result["error_message"] = "Banco destino não conectado"
        return result

    try:
        kwargs = {"pool_pre_ping": True, "pool_size": 1, "max_overflow": 1,
                  "connect_args": {"connect_timeout": 15, "sslmode": "require"}}
        if source_url.startswith("sqlite"):
            kwargs = {"pool_pre_ping": True}
        src_engine = create_engine(source_url, **kwargs)
    except Exception as exc:
        result["status"] = "failed"
        result["error_message"] = f"Falha ao conectar na origem: {exc}"
        return result

    try:
        # Determina o maior id já importado (para sincronização incremental)
        with dst_engine.connect() as conn:
            last_id = conn.execute(
                text("SELECT COALESCE(MAX(id), 0) FROM docs_corporativos")
            ).scalar() or 0

        total_docs = 0
        total_chunks = 0
        offset = 0

        while True:
            with src_engine.connect() as src_conn:
                docs = src_conn.execute(text("""
                    SELECT id, ticker, titulo, url, fonte, tipo, data,
                           raw_text, doc_hash, texto_chars, texto_qualidade, created_at
                    FROM   docs_corporativos
                    WHERE  id > :last_id
                    ORDER  BY id
                    LIMIT  :lim
                """), {"last_id": last_id, "lim": _BATCH}).fetchall()

            if not docs:
                break

            doc_ids = [r.id for r in docs]
            new_last = doc_ids[-1]

            # Copia os documentos
            doc_records = [dict(r._mapping) for r in docs]
            with dst_engine.begin() as dst_conn:
                for rec in doc_records:
                    dst_conn.execute(text("""
                        INSERT INTO docs_corporativos
                            (id, ticker, titulo, url, fonte, tipo, data,
                             raw_text, doc_hash, texto_chars, texto_qualidade, created_at)
                        VALUES
                            (:id, :ticker, :titulo, :url, :fonte, :tipo, :data,
                             :raw_text, :doc_hash, :texto_chars, :texto_qualidade, :created_at)
                        ON CONFLICT (id) DO NOTHING
                    """), rec)

            # Copia os chunks correspondentes
            with src_engine.connect() as src_conn:
                chunks = src_conn.execute(text("""
                    SELECT id, doc_id, ticker, chunk_index, chunk_text, tokens
                    FROM   docs_corporativos_chunks
                    WHERE  doc_id = ANY(:ids)
                    ORDER  BY doc_id, chunk_index
                """), {"ids": doc_ids}).fetchall()

            if chunks:
                chunk_records = [dict(r._mapping) for r in chunks]
                with dst_engine.begin() as dst_conn:
                    for rec in chunk_records:
                        dst_conn.execute(text("""
                            INSERT INTO docs_corporativos_chunks
                                (id, doc_id, ticker, chunk_index, chunk_text, tokens)
                            VALUES
                                (:id, :doc_id, :ticker, :chunk_index, :chunk_text, :tokens)
                            ON CONFLICT (id) DO NOTHING
                        """), rec)

            total_docs   += len(docs)
            total_chunks += len(chunks)
            last_id = new_last

            if len(docs) < _BATCH:
                break

            time.sleep(0.1)

        result["records_inserted"] = total_docs + total_chunks
        logger.info(
            "update_cvm: %d docs + %d chunks importados",
            total_docs, total_chunks,
        )

        if total_docs == 0:
            result["status"] = "skipped"

    except Exception as exc:
        logger.exception("update_cvm falhou")
        result["status"] = "failed"
        result["error_message"] = str(exc)

    return result
