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
    """
    Mapa codigo_cvm → ticker do UNIVERSO da B3.

    Fonte primária: registro oficial `cvm_to_ticker` cruzado com `setores`
    (universo do app). Antes este mapa era derivado de
    `docs_corporativos.codigo_cvm`, mas essa coluna está 100% NULL — o mapa saía
    vazio e o coletor pulava TODA execução (status="skipped"), nunca ingerindo
    nada para empresas ainda sem documento (problema de bootstrap ovo-e-galinha).
    O registro cobre ~263/266 tickers do universo, incluindo os que nunca foram
    coletados. Complementa com qualquer codigo_cvm já presente em
    docs_corporativos (retrocompatibilidade).
    """
    from sqlalchemy import text
    out: dict[int, str] = {}

    def _add(cod, tk, *, overwrite: bool) -> None:
        try:
            ci = int(cod)
        except (TypeError, ValueError):
            return
        tk = str(tk or "").upper().replace(".SA", "").strip()
        if not tk:
            return
        if overwrite or ci not in out:
            out[ci] = tk

    # 1) Registro oficial ∩ universo (cobre empresas sem nenhum documento ainda).
    try:
        rows = conn.execute(text('''
            SELECT DISTINCT c."CVM" AS cod, UPPER(s.ticker) AS ticker
            FROM public.cvm_to_ticker c
            JOIN public.setores s ON UPPER(s.ticker) = UPPER(c."Ticker")
            WHERE c."CVM" IS NOT NULL
        ''')).fetchall()
        for cod, tk in rows:
            _add(cod, tk, overwrite=True)
    except Exception as exc:
        logger.warning("update_cvm_ipe: cvm_to_ticker indisponível (%s)", exc)

    # 2) Complemento: codigo_cvm já registrado em docs_corporativos (se houver).
    try:
        rows = conn.execute(text("""
            SELECT DISTINCT codigo_cvm, ticker FROM public.docs_corporativos
            WHERE codigo_cvm IS NOT NULL AND ticker IS NOT NULL
        """)).fetchall()
        for cod, tk in rows:
            _add(cod, tk, overwrite=False)
    except Exception:
        pass

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
                    # chunk-resumo (RAG-visível). chunk_hash inclui a URL (única por
                    # doc) p/ não colidir quando dois documentos têm metadados idênticos
                    # — colisão antes derrubava a transação inteira. ON CONFLICT reforça.
                    conn.execute(text("""
                        INSERT INTO public.docs_corporativos_chunks
                          (doc_id, ticker, chunk_index, chunk_text, chunk_hash,
                           categoria, document_date, chunking_version, ingestion_run_id)
                        VALUES
                          (:doc_id, :tk, 0, :ct, :chash, :cat, :dt, 'ipe_meta_v1', :rid)
                        ON CONFLICT (chunk_hash) DO NOTHING
                    """), {
                        "doc_id": doc_id, "tk": d["ticker"], "ct": meta,
                        "chash": ipe.sha256("chunk", d["url"], meta),
                        "cat": (d.get("categoria") or "")[:300],
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
