"""
core/rag_b3.py
Módulo RAG para análise B3 — recupera chunks de documentos CVM (IPE/ENET).

Estratégia adaptativa:
  - Se embeddings existem no banco → busca semântica multi-tópica (pgvector)
  - Se embeddings ausentes        → busca temporal diversificada (padrão atual)

A busca temporal seleciona os chunks mais recentes diversificando por tipo de
documento (fato relevante, resultado, ata, etc.), que é o comportamento útil
enquanto o pipeline de embeddings não foi executado.
"""
from __future__ import annotations

import logging
import os
import re
from collections import defaultdict
from typing import Any

import streamlit as st
from sqlalchemy import text

logger = logging.getLogger(__name__)

_EMBED_MODEL = "text-embedding-3-small"
_EMBED_DIMS  = 1536

_TOPICS_DEFAULT = [
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

# tipos CVM priorizados na seleção temporal
_TIPOS_PRIO = [
    "resultado", "ata", "fato relevante", "comunicado", "aviso", "ipe",
    "press release", "relatório", "guidance", "dividendo",
]


# ─────────────────────────────────────────────────────────────────────────────
# Limpeza de rodapé/boilerplate e relevância do chunk
#
# Documentos CVM/IPE trazem rodapé de RI (site, e-mail, telefone, endereço) e um
# bloco jurídico de "safe harbor" ("este documento pode conter previsões…").
# Esse texto não tem valor analítico e, sem filtro, ocupa o orçamento do prompt e
# afoga o conteúdo factual. Aqui removemos o rodapé cosmético e descartamos os
# chunks dominados pelo disclaimer (sem nenhum sinal factual: número, R$/US$, verbo
# de ação). Ver core/rag_b3.py — usado em _search_temporal e _search_semantic.
# ─────────────────────────────────────────────────────────────────────────────

_FOOTER_PATTERNS = [
    re.compile(r"www\.[\w./-]+", re.I),
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+", re.I),                 # e-mails de RI
    re.compile(r"tel\.?\s*:?\s*\+?55[\s\d()/–-]+", re.I),
    re.compile(r"0800[\s\d-]+", re.I),
    re.compile(r"para mais informa[çc][õo]es\s*:?", re.I),
    re.compile(r"rela[çc][õo]es com investidores", re.I),
    re.compile(r"\bp[úu]blic[ao]\b", re.I),                        # marca-d'água "PÚBLICA"
    # Endereço (Av./Rua/Praça … CEP … cidade, UF), CNPJ e NIRE — rodapé societário.
    re.compile(r"(av\.?|avenida|rua|pra[çc]a)\s+[\w\s.,ºª°–\-]+?\d{5}\s*-?\s*\d{3}[\w\s.,ºª°–\-]*?,\s*[A-Z]{2}\b", re.I),
    re.compile(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}", re.I),          # CNPJ
    re.compile(r"\bnire\s*[\d.\-]+", re.I),
    re.compile(r"\bcnpj\b\.?:?", re.I),
]

# Vocabulário do disclaimer jurídico (boilerplate "forward-looking").
_BOILER_MARKERS = (
    "refletem apenas expectativas", "riscos ou incertezas", "não deve se basear",
    "nao deve se basear", "lei de valores mobiliários", "lei de negociaç",
    "pode conter previsões", "pode conter previsoes", "forward-looking",
    "não devem ser interpretad", "nao devem ser interpretad", "tais previsões",
    "seção 27a", "secao 27a", "seção 21e", "secao 21e",
)

# Sinais de conteúdo factual/analítico. NÃO inclua "resultado": casa com
# "os RESULTADOs futuros das operações" do próprio disclaimer (falso positivo).
_SUBSTANCE_MARKERS = (
    "informa que", "informa sobre", "comunica", "aprovou", "aprova ", "revisa",
    "revisou", "pagamento", "dividendos", "juros sobre capital", "aquisição",
    "aquisicao", "assinou", "celebrou", "decisão", "decisao", "guidance", "capex",
    "produção", "producao", "emissão", "emissao", "debêntures", "debentures",
    "bilhões", "bilhoes", "milhões", "milhoes", "lucro líquido", "receita líquida",
    "ebitda",
)
_NUM_RE = re.compile(r"(r\$|us\$|\d+[.,]?\d*\s*%|\d+[.,]?\d*\s*(bilh|milh|boed|bpd|mil))", re.I)


def _ticker_root(tk: str) -> str:
    """
    Raiz do emissor (4 letras, sem o dígito de classe). PETR3/PETR4/PETR11 → PETR.

    Documentos CVM/IPE são arquivados por empresa (codigo_cvm), mas no banco ficam
    sob UMA classe de ação (ex.: PETR3). A carteira pode ter outra classe da MESMA
    empresa (ex.: PETR4). Casar pela raiz garante que qualquer classe encontre os
    documentos do emissor. Na B3 o prefixo de 4 letras identifica o emissor, então
    não há colisão entre empresas distintas.
    """
    return re.sub(r"\d+$", "", str(tk or "").strip().upper())[:4]


def _clean_chunk_text(texto: str) -> str:
    """Remove o rodapé cosmético (site, e-mail, telefone, endereço, marca PÚBLICA)."""
    t = texto or ""
    for pat in _FOOTER_PATTERNS:
        t = pat.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def _chunk_is_relevant(texto_limpo: str) -> bool:
    """
    Mantém o chunk se ele carrega sinal factual; descarta os dominados pelo
    disclaimer jurídico ou pelo rodapé. Critério:
      - tem marcador de substância/número → mantém;
      - tem vocabulário de disclaimer → descarta;
      - sem substância e curto (< 140 chars) → descarta (resíduo de rodapé);
      - caso contrário (texto narrativo razoável) → mantém.
    """
    if len(texto_limpo) < 60:
        return False
    low = texto_limpo.lower()
    if any(m in low for m in _SUBSTANCE_MARKERS) or _NUM_RE.search(low):
        return True
    if any(m in low for m in _BOILER_MARKERS):
        return False
    return len(texto_limpo) >= 140


# ─────────────────────────────────────────────────────────────────────────────
# Engine
# ─────────────────────────────────────────────────────────────────────────────

def _get_b3_engine():
    try:
        from core.b3_db import _engine
        return _engine()
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Cobertura (quantos chunks por ticker)
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=600, show_spinner=False)
def get_cobertura_docs(tickers: tuple[str, ...]) -> dict[str, int]:
    """Retorna {ticker: n_chunks}. Zero = sem documentos CVM."""
    if not tickers:
        return {}
    engine = _get_b3_engine()
    if engine is None:
        return {tk: 0 for tk in tickers}
    try:
        with engine.connect() as conn:
            exists = conn.execute(text("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'public'
                    AND table_name = 'docs_corporativos_chunks'
                )
            """)).scalar()
            if not exists:
                return {tk: 0 for tk in tickers}

            # Conta por raiz do emissor (PETR3/PETR4 → PETR) para que qualquer
            # classe enxergue os documentos da empresa.
            roots = {tk: _ticker_root(tk) for tk in tickers}
            uniq = sorted(set(roots.values()))
            ph = ", ".join(f":r{i}" for i in range(len(uniq)))
            params = {f"r{i}": r for i, r in enumerate(uniq)}
            rows = conn.execute(
                text(f"""
                    SELECT LEFT(UPPER(ticker), 4) AS root, COUNT(*) AS n
                    FROM public.docs_corporativos_chunks
                    WHERE LEFT(UPPER(ticker), 4) IN ({ph})
                    GROUP BY LEFT(UPPER(ticker), 4)
                """),
                params,
            ).fetchall()
            by_root = {row[0]: int(row[1]) for row in rows}
            return {tk: by_root.get(roots[tk], 0) for tk in tickers}
    except Exception as exc:
        logger.warning("RAG: get_cobertura_docs falhou: %s", exc)
        return {tk: 0 for tk in tickers}


# ─────────────────────────────────────────────────────────────────────────────
# Verifica se embeddings existem no banco para o ticker
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=1800, show_spinner=False)
def _has_embeddings(ticker: str) -> bool:
    engine = _get_b3_engine()
    if engine is None:
        return False
    try:
        with engine.connect() as conn:
            n = conn.execute(text("""
                SELECT COUNT(*) FROM public.docs_corporativos_chunks
                WHERE LEFT(UPPER(ticker), 4) = :root AND embedding IS NOT NULL
                LIMIT 1
            """), {"root": _ticker_root(ticker)}).scalar()
            return int(n or 0) > 0
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# OpenAI (só usado quando embeddings existem)
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
        logger.warning("RAG: embedding falhou: %s", exc)
        return None


def _to_pgvector_literal(emb: list[float]) -> str:
    return "[" + ",".join(f"{float(x):.10f}" for x in emb) + "]"


# ─────────────────────────────────────────────────────────────────────────────
# Busca temporal diversificada (modo principal enquanto não há embeddings)
# ─────────────────────────────────────────────────────────────────────────────

def _search_temporal(
    conn: Any,
    ticker: str,
    top_k: int,
    months_back: int,
) -> list[dict]:
    """
    Retorna chunks recentes diversificados por tipo de documento.
    Prioriza: resultados > fatos relevantes > atas > comunicados > outros.
    """
    where_date = ""
    if months_back > 0:
        # Qualifica c.document_date — ambas as tabelas têm a coluna (ambígua sem prefixo).
        where_date = """
            AND (
                c.document_date IS NULL
                OR c.document_date >= (CURRENT_DATE - (:months_back || ' months')::interval)
            )
        """

    # Título mora só na tabela-pai (docs_corporativos); categoria/data existem em
    # ambas. Antes a query usava c.titulo (coluna inexistente) e falhava silenciosa
    # → retornava 0 chunks para TODOS os tickers. Agora pega tipo/categoria/título
    # do documento-pai e a data do chunk com fallback no pai.
    sql = f"""
        SELECT
            c.chunk_text,
            COALESCE(c.document_date, d.document_date, d.data)::text   AS data_doc,
            COALESCE(NULLIF(d.tipo, ''), NULLIF(d.categoria, ''),
                     NULLIF(c.categoria, ''), '')                      AS tipo_doc,
            COALESCE(d.titulo, '')                                     AS titulo,
            c.chunk_index
        FROM public.docs_corporativos_chunks c
        JOIN public.docs_corporativos d ON d.id = c.doc_id
        WHERE LEFT(UPPER(c.ticker), 4) = :root
          {where_date}
        ORDER BY
            COALESCE(c.document_date, d.document_date, d.data) DESC NULLS LAST,
            c.chunk_index ASC
        LIMIT :lim
    """
    params: dict = {"root": _ticker_root(ticker), "lim": top_k * 4}  # busca mais p/ filtrar+diversificar
    if months_back > 0:
        params["months_back"] = months_back

    try:
        rows = conn.execute(text(sql), params).fetchall()
    except Exception as exc:
        logger.warning("RAG: busca temporal falhou para %s: %s", ticker, exc)
        return []

    # Diversifica por tipo: max ~15 chunks por tipo para cobrir mais documentos.
    # Limpa rodapé e descarta chunks dominados pelo disclaimer (sem sinal factual).
    by_tipo: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        texto = _clean_chunk_text(r[0] or "")
        if not _chunk_is_relevant(texto):
            continue
        tipo = (r[2] or "outros").lower()
        by_tipo[tipo].append({
            "chunk_text": texto,
            "data_doc":   r[1],
            "tipo_doc":   r[2],
            "titulo":     r[3],
            "dist":       None,
        })

    # Intercala por tipo para máxima diversidade
    result: list[dict] = []
    max_per_tipo = max(1, top_k // max(len(by_tipo), 1))
    # Primeiro passa: tipos prioritários
    for prio in _TIPOS_PRIO:
        for tipo, chunks in by_tipo.items():
            if prio in tipo and chunks:
                take = chunks[:max_per_tipo]
                result.extend(take)
                by_tipo[tipo] = chunks[max_per_tipo:]
                if len(result) >= top_k:
                    break
        if len(result) >= top_k:
            break
    # Segunda passa: tipos restantes
    for chunks in by_tipo.values():
        for ch in chunks:
            if len(result) >= top_k:
                break
            result.append(ch)

    return result[:top_k]


# ─────────────────────────────────────────────────────────────────────────────
# Busca semântica (quando embeddings existem)
# ─────────────────────────────────────────────────────────────────────────────

def _search_semantic(
    conn: Any,
    ticker: str,
    emb_literal: str,
    lim: int,
    months_back: int,
) -> list[dict]:
    where_date = ""
    if months_back > 0:
        # Qualifica c.document_date — coluna existe em ambas as tabelas (ambígua sem prefixo).
        where_date = """
            AND (
                c.document_date IS NULL
                OR c.document_date >= (CURRENT_DATE - (:months_back || ' months')::interval)
            )
        """
    # Mesma correção do título da busca temporal (c.titulo não existe).
    sql = f"""
        SELECT
            c.chunk_text,
            COALESCE(c.document_date, d.document_date, d.data)::text   AS data_doc,
            COALESCE(NULLIF(d.tipo, ''), NULLIF(d.categoria, ''),
                     NULLIF(c.categoria, ''), '')                      AS tipo_doc,
            COALESCE(d.titulo, '')                                     AS titulo,
            (c.embedding <-> (:emb)::vector)                           AS dist
        FROM public.docs_corporativos_chunks c
        JOIN public.docs_corporativos d ON d.id = c.doc_id
        WHERE LEFT(UPPER(c.ticker), 4) = :root
          AND c.embedding IS NOT NULL
          {where_date}
        ORDER BY (c.embedding <-> (:emb)::vector) ASC
        LIMIT :lim
    """
    params: dict = {"emb": emb_literal, "root": _ticker_root(ticker), "lim": lim}
    if months_back > 0:
        params["months_back"] = months_back
    try:
        rows = conn.execute(text(sql), params).fetchall()
    except Exception as exc:
        logger.debug("RAG: busca semântica falhou: %s", exc)
        return []
    out: list[dict] = []
    for r in rows:
        texto = _clean_chunk_text(r[0] or "")
        if not _chunk_is_relevant(texto):
            continue
        out.append({
            "chunk_text": texto,
            "data_doc":   r[1],
            "tipo_doc":   r[2],
            "titulo":     r[3],
            "dist":       float(r[4]) if r[4] is not None else None,
        })
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Recuperação principal
# ─────────────────────────────────────────────────────────────────────────────

def retrieve_chunks(
    ticker: str,
    top_k_total: int = 60,
    per_topic_k: int = 10,
    months_back: int = 24,
    topics: list[str] | None = None,
) -> tuple[list[dict], dict]:
    """
    Recupera chunks relevantes para o ticker.
    Retorna (chunks, stats).
    """
    tk = ticker.strip().upper()
    engine = _get_b3_engine()
    stats: dict = {"ticker": tk, "mode": "none", "total_hits": 0, "months_back": months_back}

    if engine is None:
        return [], stats

    try:
        with engine.connect() as conn:
            # Verifica tabela
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

            use_semantic = _has_embeddings(tk)

            if use_semantic:
                topics = topics or _TOPICS_DEFAULT
                hits: list[dict] = []
                for topic in topics:
                    query = f"{tk}. {topic}."
                    emb = _embed_query(query)
                    if emb is None:
                        use_semantic = False
                        break
                    rows = _search_semantic(conn, tk, _to_pgvector_literal(emb),
                                            per_topic_k, months_back)
                    for r in rows:
                        r["topic"] = topic
                    hits.extend(rows)

                if use_semantic and hits:
                    # dedup + ordena por distância
                    seen: set[str] = set()
                    dedup: list[dict] = []
                    for h in hits:
                        key = (h.get("chunk_text") or "")[:200]
                        if key not in seen:
                            seen.add(key)
                            dedup.append(h)
                    dedup.sort(key=lambda x: float(x.get("dist") or 1e9))
                    final = dedup[:top_k_total]
                    stats["mode"] = "semantic"
                    stats["total_hits"] = len(final)
                    return final, stats

            # Temporal (padrão quando sem embeddings, ou fallback)
            final = _search_temporal(conn, tk, top_k_total, months_back)
            stats["mode"] = "temporal"
            stats["total_hits"] = len(final)
            return final, stats

    except Exception as exc:
        logger.warning("RAG: retrieve_chunks falhou para %s: %s", tk, exc)
        return [], stats


# ─────────────────────────────────────────────────────────────────────────────
# Formatação do contexto para o prompt LLM
# ─────────────────────────────────────────────────────────────────────────────

def format_rag_context(chunks: list[dict], max_chars: int = 8000) -> str:
    """
    Formata chunks em string de contexto para injeção no prompt.

    A seleção respeita o ranking de entrada (mais recentes/relevantes primeiro)
    para caber no orçamento, mas a APRESENTAÇÃO é cronológica (mais antigo →
    mais novo). Assim a LLM lê os acontecimentos em ordem temporal e consegue
    construir a evolução dos fatos e a tendência da empresa.
    """
    if not chunks:
        return "  Nenhum documento CVM disponível para este ativo."

    selecionados: list[dict] = []
    total = 0
    for ch in chunks:
        data   = ch.get("data_doc") or "—"
        tipo   = ch.get("tipo_doc") or "Documento"
        titulo = ch.get("titulo") or ""
        texto  = (ch.get("chunk_text") or "").strip()
        if not texto:
            continue
        header = f"[{data} | {tipo}" + (f" | {titulo[:60]}" if titulo else "") + "]"
        entry  = f"{header}\n{texto}"
        if total + len(entry) > max_chars:
            restante = max_chars - total - len(header) - 5
            if restante > 100:
                entry = f"{header}\n{texto[:restante]}…"
            else:
                break
        selecionados.append({"data": data, "entry": entry})
        total += len(entry)
        if total >= max_chars:
            break

    if not selecionados:
        return "  Documentos disponíveis mas sem texto."

    # Ordena cronologicamente para leitura em linha do tempo. Datas vazias ("—")
    # vão para o fim (ordenam como string alta).
    selecionados.sort(key=lambda x: (x["data"] or "9999"))
    return "\n\n---\n\n".join(s["entry"] for s in selecionados)
