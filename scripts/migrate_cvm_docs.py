"""
scripts/migrate_cvm_docs.py
Migração física dos documentos CVM do banco do App1 (SUPABASE_DB_URL_B3)
para o banco unificado do App4 (SUPABASE_UNIFICADO_URL).

Uso:
    python scripts/migrate_cvm_docs.py [--tickers VALE3 PETR4] [--batch 500] [--dry-run]

Pré-requisitos:
  - SUPABASE_DB_URL_B3      : banco de origem (App1) — somente leitura
  - SUPABASE_UNIFICADO_URL  : banco de destino (App4) — leitura+escrita
  - Extensão pgvector ativa no banco de destino:
      CREATE EXTENSION IF NOT EXISTS vector;

O script é idempotente: usa ON CONFLICT DO NOTHING em todas as inserções.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# Garante que o diretório raiz do projeto está no path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

load_dotenv(_ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("migrate_cvm")

# ─────────────────────────────────────────────────────────────────────────────
# DDL do banco de destino
# ─────────────────────────────────────────────────────────────────────────────

_DDL_EXTENSION = "CREATE EXTENSION IF NOT EXISTS vector;"

_DDL_DOCS = """
CREATE TABLE IF NOT EXISTS public.docs_corporativos (
    id                  BIGSERIAL PRIMARY KEY,
    ticker              TEXT NOT NULL,
    titulo              TEXT,
    url                 TEXT,
    fonte               TEXT,
    tipo                TEXT,
    data                DATE,
    raw_text            TEXT,
    doc_hash            TEXT,
    texto_chars         INT,
    texto_qualidade     TEXT,
    ingestion_run_id    TEXT,
    extraction_version  TEXT,
    content_hash        TEXT,
    is_stub             BOOLEAN DEFAULT FALSE,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
"""

_DDL_DOCS_UNIQUE = """
CREATE UNIQUE INDEX IF NOT EXISTS uq_docs_corporativos_doc_hash
    ON public.docs_corporativos (doc_hash)
    WHERE doc_hash IS NOT NULL;
"""

_DDL_CHUNKS = """
CREATE TABLE IF NOT EXISTS public.docs_corporativos_chunks (
    id                  BIGSERIAL PRIMARY KEY,
    doc_id              BIGINT NOT NULL
        REFERENCES public.docs_corporativos(id) ON DELETE CASCADE,
    ticker              TEXT NOT NULL,
    chunk_index         INT NOT NULL,
    chunk_text          TEXT NOT NULL,
    chunk_hash          TEXT UNIQUE NOT NULL,
    document_date       DATE,
    categoria           TEXT,
    context_preview     TEXT,
    titulo              TEXT,
    fonte               TEXT,
    url                 TEXT,
    chunking_version    TEXT,
    extraction_version  TEXT,
    ingestion_run_id    TEXT,
    content_hash        TEXT,
    is_stub             BOOLEAN DEFAULT FALSE,
    embedding           vector(1536)
);
"""

_DDL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_docs_corporativos_ticker ON public.docs_corporativos (ticker, data DESC NULLS LAST);",
    "CREATE INDEX IF NOT EXISTS idx_docs_chunks_ticker ON public.docs_corporativos_chunks (ticker, document_date DESC NULLS LAST);",
    "CREATE INDEX IF NOT EXISTS idx_docs_chunks_doc_id ON public.docs_corporativos_chunks (doc_id, chunk_index ASC);",
    # HNSW index para busca vetorial (requer pgvector >= 0.5)
    """
    CREATE INDEX IF NOT EXISTS idx_docs_chunks_embedding_hnsw
        ON public.docs_corporativos_chunks
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64);
    """,
]


# ─────────────────────────────────────────────────────────────────────────────
# Engines
# ─────────────────────────────────────────────────────────────────────────────

def _make_engine(url: str, label: str):
    if not url:
        raise ValueError(f"URL do banco '{label}' não configurada.")
    kwargs = {
        "pool_pre_ping": True,
        "pool_size": 2,
        "max_overflow": 1,
        "connect_args": {"connect_timeout": 15, "sslmode": "require"},
    }
    logger.info("Conectando a %s…", label)
    return create_engine(url, **kwargs)


def _get_urls() -> tuple[str, str]:
    def _env(key: str) -> str:
        try:
            import streamlit as st
            v = st.secrets.get(key, "")
            if v:
                return str(v)
        except Exception:
            pass
        return os.getenv(key, "")

    src = _env("SUPABASE_DB_URL_B3") or _env("SUPABASE_DB_URL")
    dst = _env("SUPABASE_UNIFICADO_URL") or _env("DATABASE_URL")
    return src, dst


# ─────────────────────────────────────────────────────────────────────────────
# Setup DDL no destino
# ─────────────────────────────────────────────────────────────────────────────

def setup_destination(dst_engine) -> None:
    logger.info("Criando extensão e tabelas no banco de destino…")
    with dst_engine.begin() as conn:
        try:
            conn.execute(text(_DDL_EXTENSION))
        except Exception as e:
            logger.warning("pgvector extension: %s (pode já existir)", e)

        conn.execute(text(_DDL_DOCS))
        conn.execute(text(_DDL_DOCS_UNIQUE))
        conn.execute(text(_DDL_CHUNKS))

        for ddl in _DDL_INDEXES:
            try:
                conn.execute(text(ddl))
            except Exception as e:
                logger.warning("Index (pode já existir): %s", str(e)[:120])

    logger.info("Tabelas prontas no banco de destino.")


# ─────────────────────────────────────────────────────────────────────────────
# Migração de docs_corporativos
# ─────────────────────────────────────────────────────────────────────────────

def _get_src_doc_columns(src_conn) -> set[str]:
    rows = src_conn.execute(text("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'docs_corporativos'
    """)).fetchall()
    return {r[0].lower() for r in rows}


def migrate_docs(src_engine, dst_engine, tickers: list[str] | None, batch: int, dry_run: bool) -> dict:
    stats = {"docs_read": 0, "docs_inserted": 0, "docs_skipped": 0}

    ticker_filter = ""
    params: dict = {}
    if tickers:
        placeholders = ", ".join(f":tk{i}" for i in range(len(tickers)))
        ticker_filter = f"WHERE UPPER(ticker) IN ({placeholders})"
        params = {f"tk{i}": tk.upper() for i, tk in enumerate(tickers)}

    with src_engine.connect() as src_conn:
        src_cols = _get_src_doc_columns(src_conn)
        # Colunas garantidas
        select_cols = ["id", "ticker"]
        for c in ["titulo", "url", "fonte", "tipo", "data", "raw_text", "doc_hash",
                  "texto_chars", "texto_qualidade", "ingestion_run_id",
                  "extraction_version", "content_hash", "is_stub", "created_at"]:
            if c in src_cols:
                select_cols.append(c)

        total = src_conn.execute(
            text(f"SELECT COUNT(*) FROM public.docs_corporativos {ticker_filter}"),
            params,
        ).scalar() or 0
        logger.info("docs_corporativos: %d documentos na origem", total)

        offset = 0
        while True:
            rows = src_conn.execute(
                text(f"""
                    SELECT {', '.join(select_cols)}
                    FROM public.docs_corporativos
                    {ticker_filter}
                    ORDER BY id
                    LIMIT :lim OFFSET :off
                """),
                {**params, "lim": batch, "off": offset},
            ).mappings().fetchall()

            if not rows:
                break

            stats["docs_read"] += len(rows)

            if not dry_run:
                with dst_engine.begin() as dst_conn:
                    for row in rows:
                        d = dict(row)
                        d.pop("id")  # ID da origem — não usamos no destino
                        # Mapeia doc_hash como chave de upsert
                        doc_hash = d.get("doc_hash")
                        if doc_hash:
                            result = dst_conn.execute(
                                text("""
                                    INSERT INTO public.docs_corporativos
                                        (ticker, titulo, url, fonte, tipo, data, raw_text, doc_hash,
                                         texto_chars, texto_qualidade, ingestion_run_id,
                                         extraction_version, content_hash, is_stub)
                                    VALUES
                                        (:ticker, :titulo, :url, :fonte, :tipo, :data, :raw_text, :doc_hash,
                                         :texto_chars, :texto_qualidade, :ingestion_run_id,
                                         :extraction_version, :content_hash, :is_stub)
                                    ON CONFLICT (doc_hash) DO NOTHING
                                    RETURNING id
                                """),
                                {
                                    "ticker":             (d.get("ticker") or "").upper(),
                                    "titulo":             d.get("titulo"),
                                    "url":                d.get("url"),
                                    "fonte":              d.get("fonte"),
                                    "tipo":               d.get("tipo"),
                                    "data":               d.get("data"),
                                    "raw_text":           d.get("raw_text"),
                                    "doc_hash":           doc_hash,
                                    "texto_chars":        d.get("texto_chars"),
                                    "texto_qualidade":    d.get("texto_qualidade"),
                                    "ingestion_run_id":   d.get("ingestion_run_id"),
                                    "extraction_version": d.get("extraction_version"),
                                    "content_hash":       d.get("content_hash"),
                                    "is_stub":            bool(d.get("is_stub") or False),
                                },
                            )
                            if result.fetchone():
                                stats["docs_inserted"] += 1
                            else:
                                stats["docs_skipped"] += 1
                        else:
                            stats["docs_skipped"] += 1

            offset += batch
            pct = min(100, int(offset * 100 / max(total, 1)))
            logger.info("docs_corporativos: %d%% (%d/%d)", pct, min(offset, total), total)

    return stats


# ─────────────────────────────────────────────────────────────────────────────
# Migração de docs_corporativos_chunks (com mapeamento de IDs)
# ─────────────────────────────────────────────────────────────────────────────

def _get_src_chunk_columns(src_conn) -> set[str]:
    rows = src_conn.execute(text("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'docs_corporativos_chunks'
    """)).fetchall()
    return {r[0].lower() for r in rows}


def _build_id_map(src_engine, dst_engine, tickers: list[str] | None) -> dict[int, int]:
    """Constrói mapa {src_doc_id -> dst_doc_id} via doc_hash."""
    ticker_filter = ""
    params: dict = {}
    if tickers:
        placeholders = ", ".join(f":tk{i}" for i in range(len(tickers)))
        ticker_filter = f"AND UPPER(src.ticker) IN ({placeholders})"
        params = {f"tk{i}": tk.upper() for i, tk in enumerate(tickers)}

    id_map: dict[int, int] = {}
    with src_engine.connect() as src_conn, dst_engine.connect() as dst_conn:
        src_rows = src_conn.execute(
            text(f"SELECT id, doc_hash FROM public.docs_corporativos WHERE doc_hash IS NOT NULL {ticker_filter}"),
            params,
        ).fetchall()

        if not src_rows:
            return id_map

        hashes = [r[1] for r in src_rows if r[1]]
        src_hash_to_id = {r[1]: r[0] for r in src_rows if r[1]}

        # Busca IDs correspondentes no destino
        for i in range(0, len(hashes), 500):
            batch_hashes = hashes[i:i + 500]
            ph = ", ".join(f":h{j}" for j in range(len(batch_hashes)))
            dst_rows = dst_conn.execute(
                text(f"SELECT id, doc_hash FROM public.docs_corporativos WHERE doc_hash IN ({ph})"),
                {f"h{j}": h for j, h in enumerate(batch_hashes)},
            ).fetchall()
            for dst_id, doc_hash in dst_rows:
                src_id = src_hash_to_id.get(doc_hash)
                if src_id is not None:
                    id_map[src_id] = dst_id

    logger.info("ID map construído: %d documentos mapeados", len(id_map))
    return id_map


def migrate_chunks(
    src_engine,
    dst_engine,
    tickers: list[str] | None,
    batch: int,
    dry_run: bool,
    id_map: dict[int, int],
) -> dict:
    stats = {"chunks_read": 0, "chunks_inserted": 0, "chunks_skipped": 0}

    if not id_map:
        logger.warning("ID map vazio — nenhum documento foi migrado ou mapeado.")
        return stats

    ticker_filter = ""
    params: dict = {}
    if tickers:
        placeholders = ", ".join(f":tk{i}" for i in range(len(tickers)))
        ticker_filter = f"WHERE UPPER(c.ticker) IN ({placeholders})"
        params = {f"tk{i}": tk.upper() for i, tk in enumerate(tickers)}

    # Verifica se embedding existe na origem
    with src_engine.connect() as src_conn:
        src_chunk_cols = _get_src_chunk_columns(src_conn)
        has_embedding = "embedding" in src_chunk_cols

        total = src_conn.execute(
            text(f"SELECT COUNT(*) FROM public.docs_corporativos_chunks c {ticker_filter}"),
            params,
        ).scalar() or 0
        logger.info("docs_corporativos_chunks: %d chunks na origem%s",
                    total, " (com embedding)" if has_embedding else " (sem embedding)")

        offset = 0
        while True:
            embed_col = ", c.embedding::text AS embedding_raw" if has_embedding else ""
            rows = src_conn.execute(
                text(f"""
                    SELECT c.doc_id, c.ticker, c.chunk_index, c.chunk_text, c.chunk_hash,
                           c.document_date, c.categoria, c.context_preview,
                           c.titulo, c.fonte, c.url,
                           c.chunking_version, c.extraction_version,
                           c.ingestion_run_id, c.content_hash, c.is_stub
                           {embed_col}
                    FROM public.docs_corporativos_chunks c
                    {ticker_filter}
                    ORDER BY c.doc_id, c.chunk_index
                    LIMIT :lim OFFSET :off
                """),
                {**params, "lim": batch, "off": offset},
            ).mappings().fetchall()

            if not rows:
                break

            stats["chunks_read"] += len(rows)

            if not dry_run:
                with dst_engine.begin() as dst_conn:
                    for row in rows:
                        d = dict(row)
                        src_doc_id = d.get("doc_id")
                        dst_doc_id = id_map.get(src_doc_id)
                        if dst_doc_id is None:
                            stats["chunks_skipped"] += 1
                            continue

                        # Reconstrói vetor se disponível
                        emb_val: str | None = None
                        if has_embedding:
                            raw_emb = d.get("embedding_raw")
                            if raw_emb:
                                emb_val = raw_emb  # já é string no formato pgvector

                        insert_sql = """
                            INSERT INTO public.docs_corporativos_chunks
                                (doc_id, ticker, chunk_index, chunk_text, chunk_hash,
                                 document_date, categoria, context_preview, titulo, fonte, url,
                                 chunking_version, extraction_version, ingestion_run_id,
                                 content_hash, is_stub, embedding)
                            VALUES
                                (:doc_id, :ticker, :chunk_index, :chunk_text, :chunk_hash,
                                 :document_date, :categoria, :context_preview, :titulo, :fonte, :url,
                                 :chunking_version, :extraction_version, :ingestion_run_id,
                                 :content_hash, :is_stub,
                                 CASE WHEN :embedding IS NULL THEN NULL
                                      ELSE :embedding::vector END)
                            ON CONFLICT (chunk_hash) DO NOTHING
                        """
                        result = dst_conn.execute(text(insert_sql), {
                            "doc_id":            dst_doc_id,
                            "ticker":            (d.get("ticker") or "").upper(),
                            "chunk_index":       d.get("chunk_index"),
                            "chunk_text":        d.get("chunk_text") or "",
                            "chunk_hash":        d.get("chunk_hash") or "",
                            "document_date":     d.get("document_date"),
                            "categoria":         d.get("categoria"),
                            "context_preview":   d.get("context_preview"),
                            "titulo":            d.get("titulo"),
                            "fonte":             d.get("fonte"),
                            "url":               d.get("url"),
                            "chunking_version":  d.get("chunking_version"),
                            "extraction_version": d.get("extraction_version"),
                            "ingestion_run_id":  d.get("ingestion_run_id"),
                            "content_hash":      d.get("content_hash"),
                            "is_stub":           bool(d.get("is_stub") or False),
                            "embedding":         emb_val,
                        })
                        if result.rowcount > 0:
                            stats["chunks_inserted"] += 1
                        else:
                            stats["chunks_skipped"] += 1

            offset += batch
            pct = min(100, int(offset * 100 / max(total, 1)))
            logger.info("chunks: %d%% (%d/%d) ins=%d skip=%d",
                        pct, min(offset, total), total,
                        stats["chunks_inserted"], stats["chunks_skipped"])

    return stats


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Migra docs CVM do App1 para o App4 unificado")
    parser.add_argument("--tickers", nargs="*", help="Tickers a migrar (padrão: todos)")
    parser.add_argument("--batch", type=int, default=500, help="Tamanho do lote (padrão: 500)")
    parser.add_argument("--dry-run", action="store_true", help="Simula sem gravar no destino")
    parser.add_argument("--skip-docs", action="store_true", help="Pula migração de docs_corporativos")
    parser.add_argument("--skip-chunks", action="store_true", help="Pula migração de chunks")
    args = parser.parse_args()

    src_url, dst_url = _get_urls()

    if not src_url:
        logger.error("SUPABASE_DB_URL_B3 não configurada. Abortando.")
        sys.exit(1)
    if not dst_url:
        logger.error("SUPABASE_UNIFICADO_URL não configurada. Abortando.")
        sys.exit(1)
    if src_url == dst_url:
        logger.error("Origem e destino são o mesmo banco. Abortando.")
        sys.exit(1)

    tickers = [t.upper() for t in args.tickers] if args.tickers else None
    if tickers:
        logger.info("Filtrando tickers: %s", tickers)
    else:
        logger.info("Migrando TODOS os tickers")

    if args.dry_run:
        logger.info("*** DRY RUN — nenhuma gravação será feita ***")

    src_engine = _make_engine(src_url, "origem (App1)")
    dst_engine = _make_engine(dst_url, "destino (App4 unificado)")

    t0 = time.monotonic()

    # Setup DDL
    if not args.dry_run:
        setup_destination(dst_engine)

    # Migração docs
    doc_stats: dict = {"docs_read": 0, "docs_inserted": 0, "docs_skipped": 0}
    if not args.skip_docs:
        doc_stats = migrate_docs(src_engine, dst_engine, tickers, args.batch, args.dry_run)
        logger.info("docs_corporativos: lidos=%d ins=%d skip=%d",
                    doc_stats["docs_read"], doc_stats["docs_inserted"], doc_stats["docs_skipped"])

    # Mapeamento de IDs
    id_map: dict[int, int] = {}
    if not args.skip_chunks:
        if not args.dry_run:
            id_map = _build_id_map(src_engine, dst_engine, tickers)
        else:
            logger.info("DRY RUN: pulando construção do ID map")

    # Migração chunks
    chunk_stats: dict = {"chunks_read": 0, "chunks_inserted": 0, "chunks_skipped": 0}
    if not args.skip_chunks:
        chunk_stats = migrate_chunks(src_engine, dst_engine, tickers, args.batch, args.dry_run, id_map)
        logger.info("chunks: lidos=%d ins=%d skip=%d",
                    chunk_stats["chunks_read"], chunk_stats["chunks_inserted"], chunk_stats["chunks_skipped"])

    elapsed = time.monotonic() - t0
    logger.info(
        "Migração concluída em %.1fs | docs ins=%d | chunks ins=%d",
        elapsed, doc_stats["docs_inserted"], chunk_stats["chunks_inserted"],
    )


if __name__ == "__main__":
    main()
