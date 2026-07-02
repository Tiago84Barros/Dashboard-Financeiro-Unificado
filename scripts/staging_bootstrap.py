"""
Prepara o banco de STAGING local para a ingestão de documentos CVM/IPE:
  1. cria a extensão pgvector e o schema market no banco local;
  2. cria as tabelas docs_corporativos + docs_corporativos_chunks (vazias);
  3. copia as tabelas de REFERÊNCIA do Supabase → local (universo + taxonomia),
     que os coletores precisam para resolver tickers/setores/carteira.

Depois disto, aponte os coletores para o local (SUPABASE_DB_URL_B3=<local>) e rode
backfill_cvm_ipe / update_cvm_fulltext / enrich_setores_cvm normalmente.

Env:
  STAGING_DB_URL   — banco local (destino do bootstrap). Ex.:
                     postgresql://postgres:postgres@localhost:5433/staging
  SUPABASE_DB_URL  — Supabase (origem das tabelas de referência).

Uso:
  python scripts/staging_bootstrap.py
"""
from __future__ import annotations

import logging
import os
import sys

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()  # carrega .env (SUPABASE_DB_URL); não sobrescreve vars já no shell

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("staging_bootstrap")

# Tabelas de referência copiadas do Supabase (pequenas). (schema, tabela)
_REF_TABLES = [
    ("public", "setores"),
    ("public", "cvm_to_ticker"),
    ("public", "b3_portfolio_model_items"),
    ("market", "assets"),
    ("market", "companies"),
]

_DDL = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS market;

CREATE TABLE IF NOT EXISTS public.docs_corporativos (
    id                 BIGSERIAL PRIMARY KEY,
    ticker             text,
    data               date,
    fonte              text,
    tipo               text,
    titulo             text,
    url                text,
    raw_text           text NOT NULL DEFAULT '',
    lang               text,
    doc_hash           text,
    created_at         timestamptz DEFAULT now(),
    codigo_cvm         integer,
    categoria          text,
    score_estrategico  numeric,
    nivel              integer,
    url_hash           text,
    texto              text,
    document_date      date,
    content_hash       text,
    ingestion_run_id   text,
    chunking_version   text,
    extraction_version text
);

CREATE TABLE IF NOT EXISTS public.docs_corporativos_chunks (
    id                 BIGSERIAL PRIMARY KEY,
    doc_id             bigint REFERENCES public.docs_corporativos(id) ON DELETE CASCADE,
    ticker             text,
    chunk_index        integer,
    chunk_text         text,
    chunk_hash         text,
    created_at         timestamptz DEFAULT now(),
    embedding          vector(1536),
    categoria          text,
    document_date      date,
    chunking_version   text,
    extraction_version text,
    ingestion_run_id   text,
    CONSTRAINT docs_chunks_uq_hash UNIQUE (chunk_hash)
);
CREATE INDEX IF NOT EXISTS idx_chunks_ticker_date
    ON public.docs_corporativos_chunks (ticker, document_date);
"""


def main() -> int:
    src_url = os.getenv("SUPABASE_DB_URL_B3") or os.getenv("SUPABASE_DB_URL")
    dst_url = os.getenv("STAGING_DB_URL")
    if not dst_url:
        logger.error("Defina STAGING_DB_URL (banco local). Ex.: "
                     "postgresql://postgres:postgres@localhost:5433/staging")
        return 1
    if not src_url:
        logger.error("Defina SUPABASE_DB_URL (origem das referências).")
        return 1
    if "localhost" not in dst_url and "127.0.0.1" not in dst_url:
        logger.error("STAGING_DB_URL não parece local (%s). Abortando por segurança.", dst_url)
        return 1

    src = create_engine(src_url, connect_args={"connect_timeout": 20, "sslmode": "require"})
    dst = create_engine(dst_url, connect_args={"connect_timeout": 20})

    logger.info("1) criando schema/tabelas no staging local…")
    with dst.begin() as conn:
        for stmt in [s.strip() for s in _DDL.split(";") if s.strip()]:
            conn.execute(text(stmt))
    logger.info("   ok (pgvector, market, docs_corporativos + chunks).")

    logger.info("2) copiando tabelas de referência do Supabase…")
    import json
    for schema, tbl in _REF_TABLES:
        try:
            df = pd.read_sql_query(text(f'SELECT * FROM {schema}."{tbl}"'), src)
        except Exception as exc:
            logger.warning("   %s.%s: pulada (%s)", schema, tbl, str(exc)[:80])
            continue
        # Colunas JSON/JSONB voltam como dict/list — psycopg2 não adapta; vira texto.
        for col in df.columns:
            if df[col].dtype == object:
                df[col] = df[col].map(
                    lambda v: json.dumps(v, ensure_ascii=False)
                    if isinstance(v, (dict, list)) else v)
        if schema == "market":
            with dst.begin() as conn:
                conn.execute(text("CREATE SCHEMA IF NOT EXISTS market"))
        df.to_sql(tbl, dst, schema=schema, if_exists="replace", index=False,
                  method="multi", chunksize=500)
        logger.info("   %s.%s: %d linhas", schema, tbl, len(df))

    logger.info("Bootstrap concluído. Aponte os coletores para o staging:")
    logger.info("   SUPABASE_DB_URL_B3=%s", dst_url)
    logger.info("   python scripts/backfill_cvm_ipe.py --years 2023,2024,2025,2026 --apply")
    logger.info("   python scripts/enrich_setores_cvm.py --apply")
    logger.info("   (loop) python -c \"import data_pipeline.jobs.update_cvm_fulltext as j; "
                "print(j.run())\"  # CVM_FULLTEXT_MAX alto")
    return 0


if __name__ == "__main__":
    sys.exit(main())
