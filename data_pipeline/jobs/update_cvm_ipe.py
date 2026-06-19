"""
data_pipeline/jobs/update_cvm_ipe.py
Coletor NATIVO de documentos CVM/IPE para o app4 (substitui o sync do app1).

Fluxo:
  1. Monta o mapa codigo_cvm → ticker a partir de docs_corporativos existentes
     (mantém só empresas do nosso universo).
  2. Baixa o(s) CSV(s) anual(is) do IPE em CVM Dados Abertos (fonte primária).
  3. Filtra documentos novos (categorias relevantes, não cancelados, URL inédita).
  4. Insere metadados em docs_corporativos + um chunk-resumo em
     docs_corporativos_chunks (deixa o doc visível ao RAG mesmo sem full-text).

Incremental e seguro: limite de inserts por execução, falhas isoladas por doc,
nunca derruba o pipeline. (Extração de texto completo dos PDFs fica como
melhoria futura — controlável por env, hoje grava o chunk-resumo de metadados.)
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

JOB_NAME = "update_cvm_ipe"
TABLE_NAME = "docs_corporativos, docs_corporativos_chunks"
SOURCE_NAME = "CVM Dados Abertos (IPE)"

_MAX_INSERT = 400  # teto de docs novos por execução


def _codigo_to_ticker(conn) -> dict[int, str]:
    from sqlalchemy import text
    rows = conn.execute(text("""
        SELECT DISTINCT codigo_cvm, ticker FROM public.docs_corporativos
        WHERE codigo_cvm IS NOT NULL AND ticker IS NOT NULL
    """)).fetchall()
    out: dict[int, str] = {}
    for cod, tk in rows:
        try:
            out[int(cod)] = str(tk).upper().replace(".SA", "")
        except Exception:
            continue
    return out


def _existing_urls(conn, tickers: set[str]) -> set[str]:
    from sqlalchemy import text
    if not tickers:
        return set()
    rows = conn.execute(text(
        "SELECT url FROM public.docs_corporativos WHERE url IS NOT NULL"
    )).fetchall()
    return {str(r[0]) for r in rows if r[0]}


def run() -> dict:
    result = {
        "status": "success", "table_name": TABLE_NAME, "source_name": SOURCE_NAME,
        "job_name": JOB_NAME, "records_inserted": 0, "records_updated": 0,
        "records_failed": 0, "error_message": None,
    }
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

    # Anos a coletar (CVM publica por ano). Default: ano corrente (+ anterior em jan).
    now_year = datetime.now(timezone.utc).year
    years_env = os.getenv("CVM_IPE_YEARS", "").strip()
    if years_env:
        years = [int(y) for y in years_env.split(",") if y.strip().isdigit()]
    else:
        years = [now_year]
        if datetime.now(timezone.utc).month <= 2:
            years.append(now_year - 1)

    try:
        with engine.connect() as conn:
            cod_map = _codigo_to_ticker(conn)
            existing = _existing_urls(conn, set(cod_map.values()))
    except Exception as exc:
        result["status"] = "failed"
        result["error_message"] = f"leitura inicial: {exc}"[:500]
        return result

    if not cod_map:
        result["status"] = "skipped"
        result["error_message"] = "Sem mapa codigo_cvm→ticker (docs_corporativos vazia)."
        return result

    # Coleta + filtra
    novos: list[dict] = []
    for y in years:
        content = ipe.fetch_ipe_csv(y)
        if not content:
            result["records_failed"] += 1
            continue
        rows = ipe.parse_ipe_csv(content)
        for d in ipe.filter_docs(rows, cod_map):
            if d["url"] in existing:
                continue
            existing.add(d["url"])
            novos.append(d)

    if not novos:
        result["status"] = "skipped" if result["records_failed"] == 0 else "partial_success"
        result["error_message"] = "Nenhum documento novo encontrado." if result["records_failed"] == 0 \
            else "Falha ao baixar CSV de algum ano."
        return result

    novos = sorted(novos, key=lambda d: (d.get("data_entrega") or d.get("data_referencia") or datetime.min.date()))
    novos = novos[-_MAX_INSERT:]  # mantém os mais recentes se exceder o teto

    run_id = f"cvm_ipe_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    inserted_docs = inserted_chunks = 0
    try:
        with engine.begin() as conn:
            for d in novos:
                try:
                    meta = ipe.metadata_text(d)
                    dt = d.get("data_entrega") or d.get("data_referencia")
                    dhash = ipe.sha256(d["ticker"], d["url"], dt)
                    doc_id = conn.execute(text("""
                        INSERT INTO public.docs_corporativos
                          (ticker, data, fonte, tipo, titulo, url, raw_text, lang,
                           doc_hash, codigo_cvm, categoria, document_date,
                           content_hash, ingestion_run_id, extraction_version)
                        VALUES
                          (:tk, :dt, :fonte, :tipo, :titulo, :url, :raw, 'pt',
                           :dhash, :cod, :cat, :dt, :chash, :rid, 'ipe_meta_v1')
                        ON CONFLICT DO NOTHING
                        RETURNING id
                    """), {
                        "tk": d["ticker"], "dt": dt, "fonte": ipe.SOURCE_NAME,
                        "tipo": (d.get("tipo") or d.get("categoria") or "Documento")[:300],
                        "titulo": (d.get("assunto") or d.get("categoria") or "")[:500],
                        "url": d["url"], "raw": meta, "dhash": dhash,
                        "cod": d.get("codigo_cvm"), "cat": (d.get("categoria") or "")[:300],
                        "chash": ipe.sha256(meta), "rid": run_id,
                    }).scalar()
                    if doc_id is None:
                        continue
                    inserted_docs += 1
                    # chunk-resumo (RAG-visível)
                    conn.execute(text("""
                        INSERT INTO public.docs_corporativos_chunks
                          (doc_id, ticker, chunk_index, chunk_text, chunk_hash,
                           categoria, document_date, chunking_version, ingestion_run_id)
                        VALUES
                          (:doc_id, :tk, 0, :ct, :chash, :cat, :dt, 'ipe_meta_v1', :rid)
                    """), {
                        "doc_id": doc_id, "tk": d["ticker"], "ct": meta,
                        "chash": ipe.sha256("chunk", meta), "cat": (d.get("categoria") or "")[:300],
                        "dt": dt, "rid": run_id,
                    })
                    inserted_chunks += 1
                except Exception as exc:
                    result["records_failed"] += 1
                    logger.warning("update_cvm_ipe: doc falhou (%s): %s", d.get("url"), exc)
        # invalida cobertura RAG em cache
        try:
            from core.rag_b3 import get_cobertura_docs
            get_cobertura_docs.clear()
        except Exception:
            pass
    except Exception as exc:
        result["status"] = "failed"
        result["error_message"] = str(exc)[:500]
        return result

    result["records_inserted"] = inserted_docs + inserted_chunks
    if result["records_failed"] and inserted_docs:
        result["status"] = "partial_success"
    logger.info("update_cvm_ipe: %d docs + %d chunks novos", inserted_docs, inserted_chunks)
    return result
