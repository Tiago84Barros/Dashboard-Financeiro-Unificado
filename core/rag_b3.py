"""
core/rag_b3.py
Módulo RAG para análise B3 — recupera chunks de documentos CVM (IPE/ENET)
do banco App1 (SUPABASE_DB_URL_B3) e os injeta no contexto do LLM.

Fluxo:
  1. Gera embedding da query via OpenAI text-embedding-3-small (1536 dims)
  2. Busca chunks semanticamente próximos em docs_corporativos_chunks
     usando operador pgvector <-> (distância L2 / coseno)
  3. Retorna lista de chunks + string de contexto formatada para o prompt

Fallback gracioso:
  - Se pgvector não disponível ou embedding ausente → retorna chunks mais
    recentes por data (ordenação temporal)
  - Se OpenAI indisponível → retorna string vazia (análise sem RAG)
"""
from __future__ import annotations

import logging
import os
from typing import Any

import numpy as np
import streamlit as st
from sqlalchemy import text

logger = logging.getLogger(__name__)

_EMBED_MODEL     = "text-embedding-3-small"
_EMBED_DIMS      = 1536
_TOPICS_DEFAULT  = [
    "resultados financeiros e guidance",
    "dividendos e payout",
    "dívida e endividamento",
    "capex e investimentos",
    "riscos e contingências",
    "governança corporativa",
    "eficiência operacional e margens",
    "M&A e reestruturação",
    "estratégia e perspectivas",
]


# ─────────────────────────────────────────────────────────────────────────────
# Cliente OpenAI (reutiliza o mesmo de llm_b3 se já inicializado)
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_resource
def _get_openai_client():
    try:
        from openai import OpenAI
        from core.config import settings
        key = getattr(settings, "OPENAI_API_KEY", None) or os.environ.get("OPENAI_API_KEY", "")
        if not key:
            return None
        return OpenAI(api_key=key, timeout=60)
    except Exception as exc:
        logger.warning("RAG: OpenAI client falhou: %s", exc)
        return None


def _embed_query(query: str) -> list[float] | None:
    client = _get_openai_client()
    if client is None:
        return None
    try:
        resp = client.embeddings.create(model=_EMBED_MODEL, input=query)
        return resp.data[0].embedding
    except Exception as exc:
        logger.warning("RAG: embedding falhou para query '%s…': %s", query[:40], exc)
        return None


def _to_pgvector_literal(emb: list[float]) -> str:
    return "[" + ",".join(f"{float(x):.10f}" for x in emb) + "]"


# ─────────────────────────────────────────────────────────────────────────────
# Engine do banco B3 (App1)
# ─────────────────────────────────────────────────────────────────────────────

def _get_b3_engine():
    """Reutiliza o engine de b3_db — mesma conexão SUPABASE_DB_URL_B3."""
    try:
        from core.b3_db import _engine
        return _engine()
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Verificação de cobertura
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=600, show_spinner=False)
def get_cobertura_docs(tickers: tuple[str, ...]) -> dict[str, int]:
    """
    Retorna {ticker: n_chunks} para cada ticker.
    Resultado zero = sem documentos CVM no banco.
    """
    if not tickers:
        return {}
    engine = _get_b3_engine()
    if engine is None:
        return {tk: 0 for tk in tickers}
    try:
        with engine.connect() as conn:
            # Verifica se a tabela existe
            exists = conn.execute(text("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'public'
                    AND table_name = 'docs_corporativos_chunks'
                )
            """)).scalar()
            if not exists:
                return {tk: 0 for tk in tickers}

            placeholders = ", ".join(f":tk{i}" for i in range(len(tickers)))
            params = {f"tk{i}": tk.upper() for i, tk in enumerate(tickers)}
            rows = conn.execute(
                text(f"""
                    SELECT UPPER(ticker), COUNT(*) AS n
                    FROM public.docs_corporativos_chunks
                    WHERE UPPER(ticker) IN ({placeholders})
                    GROUP BY UPPER(ticker)
                """),
                params,
            ).fetchall()
            result = {tk: 0 for tk in tickers}
            for row in rows:
                result[row[0]] = int(row[1])
            return result
    except Exception as exc:
        logger.warning("RAG: get_cobertura_docs falhou: %s", exc)
        return {tk: 0 for tk in tickers}


# ─────────────────────────────────────────────────────────────────────────────
# Busca semântica
# ─────────────────────────────────────────────────────────────────────────────

def _search_chunks_semantic(
    conn: Any,
    ticker: str,
    emb_literal: str,
    lim: int,
    months_back: int,
) -> list[dict]:
    """Busca vetorial com pgvector. Retorna [] se operador não disponível."""
    where_date = ""
    if months_back > 0:
        where_date = """
            AND (
                COALESCE(d.data::text,'') ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
                AND to_date(substr(d.data::text,1,10),'YYYY-MM-DD')
                    >= (CURRENT_DATE - (:months_back || ' months')::interval)
            )
        """
    sql = f"""
        SELECT
            c.chunk_text,
            COALESCE(d.data::text, '')         AS data_doc,
            COALESCE(d.tipo, '')               AS tipo_doc,
            COALESCE(c.titulo, d.titulo, '')   AS titulo,
            (c.embedding <-> (:emb)::vector)   AS dist
        FROM public.docs_corporativos_chunks c
        JOIN public.docs_corporativos d ON d.id = c.doc_id
        WHERE UPPER(c.ticker) = UPPER(:ticker)
          AND c.embedding IS NOT NULL
          {where_date}
        ORDER BY (c.embedding <-> (:emb)::vector) ASC,
                 COALESCE(d.data::text,'') DESC
        LIMIT :lim
    """
    params: dict = {"emb": emb_literal, "ticker": ticker, "lim": lim}
    if months_back > 0:
        params["months_back"] = months_back
    try:
        rows = conn.execute(text(sql), params).fetchall()
        return [
            {
                "chunk_text": r[0] or "",
                "data_doc":   r[1],
                "tipo_doc":   r[2],
                "titulo":     r[3],
                "dist":       float(r[4]) if r[4] is not None else None,
            }
            for r in rows
        ]
    except Exception as exc:
        logger.debug("RAG: busca semântica falhou (%s) — tentando fallback", exc)
        return []


def _search_chunks_temporal(
    conn: Any,
    ticker: str,
    lim: int,
    months_back: int,
) -> list[dict]:
    """Fallback: retorna chunks mais recentes (sem usar embedding)."""
    where_date = ""
    if months_back > 0:
        where_date = """
            AND (
                COALESCE(d.data::text,'') ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
                AND to_date(substr(d.data::text,1,10),'YYYY-MM-DD')
                    >= (CURRENT_DATE - (:months_back || ' months')::interval)
            )
        """
    sql = f"""
        SELECT
            c.chunk_text,
            COALESCE(d.data::text, '')         AS data_doc,
            COALESCE(d.tipo, '')               AS tipo_doc,
            COALESCE(c.titulo, d.titulo, '')   AS titulo
        FROM public.docs_corporativos_chunks c
        JOIN public.docs_corporativos d ON d.id = c.doc_id
        WHERE UPPER(c.ticker) = UPPER(:ticker)
          {where_date}
        ORDER BY COALESCE(d.data::text,'') DESC, c.chunk_index ASC
        LIMIT :lim
    """
    params: dict = {"ticker": ticker, "lim": lim}
    if months_back > 0:
        params["months_back"] = months_back
    try:
        rows = conn.execute(text(sql), params).fetchall()
        return [
            {
                "chunk_text": r[0] or "",
                "data_doc":   r[1],
                "tipo_doc":   r[2],
                "titulo":     r[3],
                "dist":       None,
            }
            for r in rows
        ]
    except Exception as exc:
        logger.warning("RAG: fallback temporal também falhou: %s", exc)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Recuperação multi-tópico
# ─────────────────────────────────────────────────────────────────────────────

def retrieve_chunks(
    ticker: str,
    top_k_total: int = 60,
    per_topic_k: int = 10,
    months_back: int = 36,
    topics: list[str] | None = None,
) -> tuple[list[dict], dict]:
    """
    Recupera chunks relevantes para o ticker via busca semântica multi-tópico.
    Retorna (chunks, stats).
    """
    tk = ticker.strip().upper()
    topics = topics or _TOPICS_DEFAULT
    engine = _get_b3_engine()

    stats: dict = {
        "ticker": tk,
        "mode": "none",
        "total_hits": 0,
        "months_back": months_back,
    }

    if engine is None:
        return [], stats

    client = _get_openai_client()
    hits_all: list[dict] = []

    try:
        with engine.connect() as conn:
            # Verifica se a tabela existe
            exists = conn.execute(text("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'public'
                    AND table_name = 'docs_corporativos_chunks'
                )
            """)).scalar()
            if not exists:
                stats["mode"] = "no_table"
                return [], stats

            if client is not None:
                # Busca semântica por tópico
                for topic in topics:
                    query = (
                        f"{tk}. {topic}. "
                        f"Resultados financeiros, números, guidance, dívidas, "
                        f"dividendos, governança, estratégia, riscos."
                    )
                    emb = _embed_query(query)
                    if emb is None:
                        continue
                    emb_lit = _to_pgvector_literal(emb)
                    rows = _search_chunks_semantic(conn, tk, emb_lit, per_topic_k, months_back)
                    for r in rows:
                        r["topic"] = topic
                    hits_all.extend(rows)
                stats["mode"] = "semantic"
            else:
                # Fallback temporal
                hits_all = _search_chunks_temporal(conn, tk, top_k_total, months_back)
                stats["mode"] = "temporal_fallback"

    except Exception as exc:
        logger.warning("RAG: retrieve_chunks falhou para %s: %s", tk, exc)
        return [], stats

    # Deduplicação por chunk_text
    seen: set[str] = set()
    dedup: list[dict] = []
    for h in hits_all:
        key = h.get("chunk_text", "")[:200]
        if key in seen:
            continue
        seen.add(key)
        dedup.append(h)

    # Ordenação por distância (se semântico) ou por data (fallback)
    if stats["mode"] == "semantic":
        dedup.sort(key=lambda x: (
            float(x["dist"]) if x.get("dist") is not None else 1e9
        ))

    final = dedup[:top_k_total]
    stats["total_hits"] = len(final)
    return final, stats


# ─────────────────────────────────────────────────────────────────────────────
# Formatação do contexto para o prompt LLM
# ─────────────────────────────────────────────────────────────────────────────

def format_rag_context(chunks: list[dict], max_chars: int = 8000) -> str:
    """
    Formata os chunks em string de contexto para injeção no prompt.
    Inclui data e tipo do documento como metadados.
    """
    if not chunks:
        return "  Nenhum documento CVM disponível para este ativo."

    lines: list[str] = []
    total = 0
    for ch in chunks:
        data    = ch.get("data_doc") or "—"
        tipo    = ch.get("tipo_doc") or "Documento"
        titulo  = ch.get("titulo") or ""
        texto   = (ch.get("chunk_text") or "").strip()
        if not texto:
            continue
        header = f"[{data} | {tipo}" + (f" | {titulo[:60]}" if titulo else "") + "]"
        entry  = f"{header}\n{texto}"
        if total + len(entry) > max_chars:
            # Trunca o último chunk para não exceder o limite
            restante = max_chars - total - len(header) - 5
            if restante > 100:
                entry = f"{header}\n{texto[:restante]}…"
            else:
                break
        lines.append(entry)
        total += len(entry)
        if total >= max_chars:
            break

    return "\n\n---\n\n".join(lines) if lines else "  Documentos disponíveis mas sem texto."
