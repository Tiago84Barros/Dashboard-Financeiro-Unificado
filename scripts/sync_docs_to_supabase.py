"""
Sincroniza o corpus RAG (docs_corporativos + chunks) do banco de STAGING local
para o Supabase (produção), publicando SÓ o subconjunto curado — política:

  • universo inteiro (todas as empresas com texto extraído);
  • por empresa: até --per-ticker docs, priorizando alto sinal (Fato Relevante/
    Resultados), com mix balanceado entre categorias e recência dentro de cada;
  • apenas documentos de TEXTO COMPLETO (não os stubs de metadados);
  • SEM embeddings (o Supabase serve em modo temporal — economiza o índice HNSW).

Assim o processamento pesado fica local (sem limite) e o Supabase recebe só o que
o app consulta. Substituição limpa: apaga o corpus de docs no destino (o FK
chunks→docs é ON DELETE CASCADE) e reinsere o curado com ids novos.

Fontes de conexão (env):
  STAGING_DB_URL   — banco local de staging (origem). Obrigatório para --apply.
  SUPABASE_DB_URL  — Supabase (destino).

Uso:
  python scripts/sync_docs_to_supabase.py                       # dry-run (conta o que subiria)
  python scripts/sync_docs_to_supabase.py --apply               # publica no Supabase
  python scripts/sync_docs_to_supabase.py --tickers PETR,VALE   # testa um subconjunto
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()  # carrega .env (SUPABASE_DB_URL); não sobrescreve vars já no shell

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logger = logging.getLogger("sync_docs_to_supabase")

_FULLTEXT_VERSIONS = ("fulltext_v1", "ipe_pdf_text_v1")

# Prioridade de sinal para a curadoria (igual à recuperação do RAG).
_BUCKET_ORDER = ["resultado", "fato", "comunicado", "provento",
                 "assembleia", "capital", "critico", "outro"]


def _bucket(tipo: str, titulo: str) -> str:
    s = f"{tipo} {titulo}".lower()
    if "fato relevante" in s:
        return "fato"
    if "dados econ" in s or "resultado" in s or "release" in s or "itr" in s or "dfp" in s:
        return "resultado"
    if "comunicado ao mercado" in s:
        return "comunicado"
    if "aviso aos acionistas" in s or "provento" in s:
        return "provento"
    if "assembleia" in s:
        return "assembleia"
    if "oferta" in s or "deb" in s:
        return "capital"
    if "recupera" in s or "opa" in s:
        return "critico"
    return "outro"


def _select_curated(src_eng, per_ticker: int, tickers: list[str] | None) -> list[int]:
    """Ids de docs a publicar: por raiz do emissor, round-robin entre categorias."""
    where_tk = ""
    params: dict = {}
    if tickers:
        where_tk = "AND LEFT(UPPER(ticker), 4) = ANY(:roots)"
        params["roots"] = [t.strip().upper()[:4] for t in tickers]
    ph = ", ".join(f"'{v}'" for v in _FULLTEXT_VERSIONS)
    with src_eng.connect() as c:
        rows = c.execute(text(f"""
            SELECT id, LEFT(UPPER(ticker), 4) AS raiz,
                   COALESCE(tipo, '') tipo, COALESCE(titulo, '') titulo,
                   COALESCE(document_date, data) dd
            FROM public.docs_corporativos
            WHERE extraction_version IN ({ph}) {where_tk}
        """), params).fetchall()

    by_root: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for did, raiz, tipo, titulo, dd in rows:
        by_root[raiz][_bucket(tipo, titulo)].append((dd or date.min, did))

    selected: list[int] = []
    for buckets in by_root.values():
        for lst in buckets.values():
            lst.sort(reverse=True)  # mais recentes primeiro
        picked, rank = [], 0
        while len(picked) < per_ticker:
            progressed = False
            for b in _BUCKET_ORDER:
                lst = buckets.get(b)
                if lst and rank < len(lst):
                    picked.append(lst[rank][1])
                    progressed = True
                    if len(picked) >= per_ticker:
                        break
            if not progressed:
                break
            rank += 1
        selected.extend(picked)
    return selected


def _dry_run_report(src_eng, doc_ids: list[int]) -> None:
    if not doc_ids:
        logger.info("Nenhum documento de texto completo encontrado no staging.")
        return
    with src_eng.connect() as c:
        n_emp = c.execute(text(
            "SELECT COUNT(DISTINCT LEFT(UPPER(ticker),4)) FROM public.docs_corporativos "
            "WHERE id = ANY(:ids)"), {"ids": doc_ids}).scalar()
        nchunks, chars = c.execute(text(
            "SELECT COUNT(*), COALESCE(SUM(LENGTH(chunk_text)),0) "
            "FROM public.docs_corporativos_chunks WHERE doc_id = ANY(:ids)"),
            {"ids": doc_ids}).fetchone()
    logger.info("CURADORIA (dry-run):")
    logger.info("  documentos a publicar : %d  (%d empresas)", len(doc_ids), n_emp)
    logger.info("  chunks a publicar     : %d", nchunks)
    logger.info("  texto (sem embeddings): ~%.1f MB", (chars or 0) / 1_048_576)
    logger.info("Use --apply para publicar no Supabase.")


def _apply(src_eng, dst_eng, doc_ids: list[int], tickers: list[str] | None) -> None:
    from psycopg2.extras import execute_values
    ids = tuple(doc_ids)

    # 1) origem: lê docs + chunks selecionados (sem embeddings)
    with src_eng.connect() as c:
        docs = c.execute(text("""
            SELECT id, ticker, data, fonte, tipo, titulo, url, lang, doc_hash,
                   codigo_cvm, categoria, document_date, content_hash,
                   ingestion_run_id, extraction_version
            FROM public.docs_corporativos WHERE id = ANY(:ids)
        """), {"ids": list(ids)}).fetchall()
        chunks = c.execute(text("""
            SELECT doc_id, ticker, chunk_index, chunk_text, chunk_hash, categoria,
                   document_date, chunking_version, ingestion_run_id
            FROM public.docs_corporativos_chunks WHERE doc_id = ANY(:ids)
            ORDER BY doc_id, chunk_index
        """), {"ids": list(ids)}).fetchall()
    chunks_by_doc: dict[int, list] = defaultdict(list)
    for ch in chunks:
        chunks_by_doc[ch[0]].append(ch)

    raw = dst_eng.raw_connection()
    try:
        cur = raw.cursor()
        # 2) destino: apaga o corpus alvo (cascata remove os chunks)
        if tickers:
            roots = [t.strip().upper()[:4] for t in tickers]
            cur.execute("DELETE FROM public.docs_corporativos WHERE LEFT(UPPER(ticker),4) = ANY(%s)", (roots,))
        else:
            cur.execute("TRUNCATE public.docs_corporativos_chunks, public.docs_corporativos RESTART IDENTITY")
        raw.commit()

        # 3) insere docs (id novo) + chunks, em lotes; raw_text vazio, sem embedding
        ins_docs = ins_chunks = 0
        B = 400
        for i in range(0, len(docs), B):
            batch = docs[i:i + B]
            rows = [(d[1], d[2], d[3], d[4], d[5], d[6], "", d[7], d[8], d[9], d[10],
                     d[11], d[12], d[13], d[14]) for d in batch]
            got = execute_values(cur, """
                INSERT INTO public.docs_corporativos
                  (ticker, data, fonte, tipo, titulo, url, raw_text, lang, doc_hash,
                   codigo_cvm, categoria, document_date, content_hash,
                   ingestion_run_id, extraction_version)
                VALUES %s RETURNING id
            """, rows, fetch=True)
            ins_docs += len(got)
            # got está na ordem de inserção → alinha com batch (source id → novo id)
            id_map = {batch[j][0]: got[j][0] for j in range(len(batch))}
            crows = []
            for src_id, new_id in id_map.items():
                for ch in chunks_by_doc.get(src_id, []):
                    crows.append((new_id, ch[1], ch[2], ch[3], ch[4], ch[5], ch[6], ch[7], ch[8]))
            if crows:
                execute_values(cur, """
                    INSERT INTO public.docs_corporativos_chunks
                      (doc_id, ticker, chunk_index, chunk_text, chunk_hash, categoria,
                       document_date, chunking_version, ingestion_run_id)
                    VALUES %s ON CONFLICT (chunk_hash) DO NOTHING
                """, crows)
                ins_chunks += len(crows)
            raw.commit()
            logger.info("lote %d: +%d docs (acum %d)", i // B + 1, len(got), ins_docs)
        cur.close()
    finally:
        raw.close()
    logger.info("PUBLICADO no Supabase: %d docs + %d chunks", ins_docs, ins_chunks)


def main() -> int:
    ap = argparse.ArgumentParser(description="Sync curado do corpus RAG local → Supabase.")
    ap.add_argument("--per-ticker", type=int, default=25, help="Docs por empresa (default 25).")
    ap.add_argument("--tickers", default="", help="Raízes p/ testar (ex.: PETR,VALE). Vazio = universo.")
    ap.add_argument("--apply", action="store_true", help="Publica no Supabase (senão dry-run).")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    tickers = [t for t in args.tickers.split(",") if t.strip()] or None
    src_url = os.getenv("STAGING_DB_URL")
    dst_url = os.getenv("SUPABASE_DB_URL_B3") or os.getenv("SUPABASE_DB_URL")

    if not args.apply:
        # dry-run: se não houver staging, usa o próprio Supabase como origem só p/
        # validar a lógica de curadoria (não escreve nada).
        src_url = src_url or dst_url
    if not src_url:
        logger.error("Defina STAGING_DB_URL (origem).")
        return 1
    if args.apply and not os.getenv("STAGING_DB_URL"):
        logger.error("--apply exige STAGING_DB_URL (origem local).")
        return 1
    if not dst_url:
        logger.error("Defina SUPABASE_DB_URL (destino).")
        return 1

    src_eng = create_engine(src_url, connect_args={"connect_timeout": 20})
    doc_ids = _select_curated(src_eng, args.per_ticker, tickers)

    if not args.apply:
        _dry_run_report(src_eng, doc_ids)
        return 0

    _ssl = {} if ("localhost" in dst_url or "127.0.0.1" in dst_url) else {"sslmode": "require"}
    dst_eng = create_engine(dst_url, connect_args={"connect_timeout": 20, **_ssl})
    _apply(src_eng, dst_eng, doc_ids, tickers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
