"""
data_pipeline/jobs/update_cvm_fulltext.py
Extração de TEXTO COMPLETO dos documentos CVM/IPE — em gotejamento (drip).

Processa apenas os documentos descobertos por `update_cvm_ipe` que ainda têm só
o chunk de metadados (`extraction_version='ipe_meta_v1'`), em PEQUENAS porções
por execução, com throttling e disjuntor — para nunca tomar rate-limit.

Estratégia anti-bloqueio:
  • N docs por execução (CVM_FULLTEXT_MAX, default 12), sequencial (sem paralelismo);
  • atraso aleatório entre downloads (jitter);
  • retry com backoff exponencial; pausa longa em bloqueio (429/503);
  • DISJUNTOR: após K bloqueios consecutivos, encerra o run e retoma amanhã;
  • prioriza carteira + Fato Relevante/Resultados, mais antigos primeiro;
  • idempotente: sucesso → 'fulltext_v1'; sem texto → 'ipe_meta_v1_nofulltext'
    (não reprocessa em loop).

Controlado por env (ajustável sem código). Roda no mesmo cron diário.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from data_pipeline.quality import scheduler as _sched

logger = logging.getLogger(__name__)

JOB_NAME = "update_cvm_fulltext"
TABLE_NAME = "docs_corporativos, docs_corporativos_chunks"
SOURCE_NAME = "CVM ENET (texto completo)"

_PENDING_VERSION = "ipe_meta_v1"
_DONE_VERSION = "fulltext_v1"
_NOFULLTEXT_VERSION = "ipe_meta_v1_nofulltext"


def _enabled() -> bool:
    return os.getenv("CVM_FULLTEXT_ENABLE", "true").strip().lower() in ("1", "true", "yes", "sim")


def _cfg_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _select_pending(conn, limit: int):
    from sqlalchemy import text
    # Prioriza: carteira (b3_portfolio_model_items) → Fato Relevante/Resultados → mais antigos
    return conn.execute(text(f"""
        SELECT d.id, d.ticker, d.url, d.categoria, d.document_date
        FROM public.docs_corporativos d
        LEFT JOIN (SELECT DISTINCT ticker FROM public.b3_portfolio_model_items) c
               ON LEFT(UPPER(c.ticker), 4) = LEFT(UPPER(d.ticker), 4)
        WHERE d.extraction_version = :pend AND d.url IS NOT NULL
        ORDER BY
            (c.ticker IS NOT NULL) DESC,
            (LOWER(d.categoria) LIKE '%fato relevante%'
             OR LOWER(d.categoria) LIKE '%econômico%'
             OR LOWER(d.categoria) LIKE '%economico%') DESC,
            d.document_date ASC NULLS FIRST
        LIMIT :lim
    """), {"pend": _PENDING_VERSION, "lim": int(limit)}).fetchall()


def run() -> dict:
    result = {
        "status": "success", "table_name": TABLE_NAME, "source_name": SOURCE_NAME,
        "job_name": JOB_NAME, "records_inserted": 0, "records_updated": 0,
        "records_failed": 0, "error_message": None,
    }
    if not _enabled():
        result["status"] = "skipped"
        result["error_message"] = "CVM_FULLTEXT_ENABLE=false (extração desligada)."
        return result

    try:
        from sqlalchemy import text
        from data_pipeline.utils.db_utils import get_pipeline_engine
        import core.cvm_ipe as ipe
    except Exception as exc:
        result["status"] = "failed"
        result["error_message"] = f"import: {exc}"[:500]
        return result

    engine = get_pipeline_engine()
    if engine is None:
        result["status"] = "failed"
        result["error_message"] = "Banco não conectado"
        return result

    max_docs = _cfg_int("CVM_FULLTEXT_MAX", 12)
    delay = float(os.getenv("CVM_FULLTEXT_DELAY", "3.0"))
    max_blocks = _cfg_int("CVM_FULLTEXT_MAX_BLOCKS", 3)
    min_chars = _cfg_int("CVM_FULLTEXT_MIN_CHARS", 200)

    try:
        with engine.connect() as conn:
            pend = _select_pending(conn, max_docs)
    except Exception as exc:
        result["status"] = "failed"
        result["error_message"] = f"seleção: {exc}"[:500]
        return result

    if not pend:
        result["status"] = "skipped"
        result["error_message"] = "Nenhum documento pendente de texto completo."
        return result

    run_id = f"cvm_fulltext_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    extraidos = falhas = sem_texto = 0
    blocos_consecutivos = 0
    interrompido = False

    for i, row in enumerate(pend):
        doc_id, ticker, url, categoria, doc_date = row[0], row[1], row[2], row[3], row[4]
        try:
            content = _sched.with_backoff(
                lambda u=url: ipe.fetch_document(u),
                retries=3, base=float(os.getenv("CVM_FULLTEXT_BACKOFF", "4.0")),
                on_block=ipe.is_rate_limited,
            )
            blocos_consecutivos = 0
        except Exception as exc:
            if ipe.is_rate_limited(exc):
                blocos_consecutivos += 1
                logger.warning("fulltext: bloqueio %d/%d em %s", blocos_consecutivos, max_blocks, url)
                if blocos_consecutivos >= max_blocks:
                    interrompido = True  # disjuntor
                    break
            falhas += 1
            continue

        texto = ipe.extract_text(content)
        try:
            with engine.begin() as conn:
                if len(texto) < min_chars:
                    # sem texto útil → marca para não reprocessar em loop
                    conn.execute(text("""
                        UPDATE public.docs_corporativos
                        SET extraction_version = :nf WHERE id = :id
                    """), {"nf": _NOFULLTEXT_VERSION, "id": doc_id})
                    sem_texto += 1
                else:
                    chunks = ipe.chunk_text(texto)
                    # remove o chunk-resumo e grava os chunks reais
                    conn.execute(text(
                        "DELETE FROM public.docs_corporativos_chunks WHERE doc_id = :id"
                    ), {"id": doc_id})
                    for idx, ch in enumerate(chunks):
                        conn.execute(text("""
                            INSERT INTO public.docs_corporativos_chunks
                              (doc_id, ticker, chunk_index, chunk_text, chunk_hash,
                               categoria, document_date, chunking_version, ingestion_run_id)
                            VALUES (:doc_id, :tk, :ci, :ct, :chash, :cat, :dt, :ver, :rid)
                        """), {
                            "doc_id": doc_id, "tk": ticker, "ci": idx, "ct": ch,
                            "chash": ipe.sha256(doc_id, idx, ch),
                            "cat": (categoria or "")[:300], "dt": doc_date,
                            "ver": _DONE_VERSION, "rid": run_id,
                        })
                    # NÃO grava o texto no doc-pai: ele já está fatiado em
                    # docs_corporativos_chunks (o que o RAG lê). Duplicar em
                    # raw_text/texto só inflava o banco (raw_text é NOT NULL → '').
                    conn.execute(text("""
                        UPDATE public.docs_corporativos
                        SET raw_text = '', texto = '', content_hash = :chash,
                            extraction_version = :ver, chunking_version = :ver,
                            ingestion_run_id = :rid
                        WHERE id = :id
                    """), {
                        "chash": ipe.sha256(texto),
                        "ver": _DONE_VERSION, "rid": run_id, "id": doc_id,
                    })
                    extraidos += 1
        except Exception as exc:
            falhas += 1
            logger.warning("fulltext: gravação falhou doc %s: %s", doc_id, exc)

        if i < len(pend) - 1:
            _sched.sleep_jittered(base=delay)

    try:
        from core.rag_b3 import get_cobertura_docs
        get_cobertura_docs.clear()
    except Exception:
        pass

    result["records_updated"] = extraidos
    result["records_failed"] = falhas
    msg = f"{extraidos} extraídos, {sem_texto} sem texto, {falhas} falhas"
    if interrompido:
        result["status"] = "partial_success"
        msg += " — disjuntor acionado (bloqueio); retoma na próxima execução"
    elif falhas and not extraidos:
        result["status"] = "partial_success"
    result["error_message"] = msg if (interrompido or falhas or sem_texto) else None
    logger.info("update_cvm_fulltext: %s", msg)
    return result
