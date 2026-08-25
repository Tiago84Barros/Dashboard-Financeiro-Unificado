"""Armazenamento do corpus RAG: Parquet (DuckDB) com Postgres como reserva.

O corpus e READ-ONLY em producao -- 93.498 chunks que o app so consulta -- e
ocupava 162 MB dos 500 MB do Supabase. Aqui ele passa a ser lido de Parquet
(~25 MB), publicado por `scripts/publish_rag_corpus_parquet.py`.

DESENHO
-------
As duas consultas analiticas (temporal e ancora) sao escritas UMA VEZ, contra
um formato achatado. Cada backend so precisa saber produzir esse formato:

  * Parquet   -> `read_parquet(...)`, ja achatado no publish;
  * Postgres  -> a subconsulta `_FONTE_PG`, que faz o mesmo JOIN e as mesmas
                 derivacoes que o publicador faz.

Isso e o oposto de manter duas versoes da consulta. As derivacoes que dependiam
de dialeto -- o regex `~` da ancora e `(:n || ' months')::interval` -- sairam do
caminho de leitura: a ancora virou coluna booleana calculada no publish, e o
corte de data virou parametro `date` calculado em Python. O que resta e SQL
comum aos dois motores, e `tests/test_rag_store_paridade.py` compara os dois
lado a lado com dado real para provar que continuam concordando.

A reserva em Postgres nao e decorativa: e ela que permite a comparacao, e ela
que segura o app se o Parquet nao tiver sido publicado num deploy.
"""
from __future__ import annotations

import functools
import json
import logging
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_RAIZ = Path(__file__).resolve().parents[1]
DIR_PARQUET = _RAIZ / "data" / "public" / "rag"
_GLOB = "chunks_*.parquet"

# Teto de chunks por documento na recuperacao. Sem ele um unico arquivo longo
# (o ITR da WEGE3 tem 46 chunks, a transcricao 74) consome o LIMIT inteiro com
# os documentos mais recentes, e o Release de Resultados nunca chega ao
# formatador -- que so pode escolher entre o que foi recuperado.
_MAX_CHUNKS_POR_DOC = 8

# Formato achatado, na ordem que as consultas assumem. O publicador produz
# exatamente estas colunas; _FONTE_PG as reconstroi a partir das duas tabelas.
_COLUNAS = ("ticker", "root", "doc_id", "chunk_index", "chunk_text",
            "chunk_hash", "data_doc", "tipo_doc", "titulo", "eh_ancora",
            "eh_stub")

# Reproduz, em Postgres, o que o publish grava no Parquet. Unico ponto do
# modulo que conhece o schema relacional original.
_FONTE_PG = """(
    SELECT
        c.ticker,
        LEFT(UPPER(c.ticker), 4)                                 AS root,
        c.doc_id,
        c.chunk_index,
        c.chunk_text,
        c.chunk_hash,
        COALESCE(c.document_date, d.document_date, d.data)::date AS data_doc,
        COALESCE(NULLIF(d.tipo, ''), NULLIF(d.categoria, ''),
                 NULLIF(c.categoria, ''), '')                    AS tipo_doc,
        COALESCE(d.titulo, '')                                   AS titulo,
        (LOWER(COALESCE(NULLIF(d.tipo, ''), NULLIF(d.categoria, ''),
                        NULLIF(c.categoria, ''), '') || ' '
               || COALESCE(d.titulo, ''))
         ~ 'fato relevante|econ[oô]mico-financ|guidance|capex|produ[çc][aã]o|dividend|provento|resultado'
        )                                                        AS eh_ancora,
        (COALESCE(d.extraction_version, '') = 'ipe_meta_v1')     AS eh_stub
    FROM public.docs_corporativos_chunks c
    JOIN public.docs_corporativos d ON d.id = c.doc_id
)"""

# ---------------------------------------------------------------- consultas --
# `$corte` nulo desliga o filtro de data. Passar a data pronta, em vez de montar
# um intervalo dentro do SQL, e o que torna estas consultas portateis.
#
# `doc_id` no fim do ORDER BY nao e enfeite. O SQL original ordenava so por
# (data, chunk_index), o que e ordenacao PARCIAL: dois documentos do mesmo dia
# empatam no mesmo chunk_index e o desempate fica a cargo do motor. Medido com
# corpus real -- PETR4, WEGE3 e ITUB4 devolviam o mesmo CONJUNTO de chunks em
# ordens diferentes no Postgres e no DuckDB. Como existe LIMIT, um empate na
# fronteira do corte nao troca so a ordem: troca QUAIS chunks chegam ao
# contexto do LLM. Mesmo ticker, mesmo dia, evidencia diferente. O defeito era
# anterior a esta migracao; a paridade entre os dois motores foi o que o
# revelou.

_Q_TEMPORAL = """
SELECT chunk_text, CAST(data_doc AS VARCHAR) AS data_doc, tipo_doc, titulo,
       chunk_index, doc_id
FROM (
    SELECT chunk_text, data_doc, tipo_doc, titulo, chunk_index, doc_id,
           ROW_NUMBER() OVER (PARTITION BY doc_id ORDER BY chunk_index ASC) AS _rn
    FROM {fonte}
    WHERE root = $root
      AND (data_doc IS NULL OR $corte IS NULL OR data_doc >= $corte)
) s
WHERE _rn <= {teto}
ORDER BY data_doc DESC NULLS LAST, chunk_index ASC, doc_id ASC
LIMIT $lim
"""

_Q_ANCORA = """
SELECT chunk_text, CAST(data_doc AS VARCHAR) AS data_doc, tipo_doc, titulo,
       doc_id
FROM {fonte}
WHERE root = $root
  AND eh_ancora AND NOT eh_stub
  AND (data_doc IS NULL OR $corte IS NULL OR data_doc >= $corte)
ORDER BY data_doc DESC NULLS LAST, chunk_index ASC, doc_id ASC
LIMIT $lim
"""

_Q_COBERTURA = """
SELECT root, COUNT(*) AS n
FROM {fonte}
WHERE root = ANY($roots)
GROUP BY root
"""


def _para_sqlalchemy(sql: str) -> str:
    """`$nome` -> `:nome`. DuckDB e SQLAlchemy divergem so na marcacao do
    parametro nomeado; traduzir aqui e mais barato que manter dois SQLs."""
    return re.sub(r"\$([a-z_]+)", r":\1", sql)


def corte_de_data(meses: int | None) -> date | None:
    """Converte "ultimos N meses" na data-limite. Fora do SQL de proposito:
    `(:n || ' months')::interval` so existe no Postgres."""
    if not meses or meses <= 0:
        return None
    return date.today() - timedelta(days=int(meses) * 30)


# ------------------------------------------------------------------ backend --

@functools.lru_cache(maxsize=1)
def manifesto() -> dict | None:
    """Manifesto do Parquet publicado, ou None se nao houver corpus em disco."""
    caminho = DIR_PARQUET / "manifesto.json"
    if not caminho.is_file():
        return None
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("RAG: manifesto ilegivel (%s) - usando Postgres", exc)
        return None
    if not dados.get("confere"):
        # Publicacao que nao bateu com a origem nao serve como fonte: cair para
        # o Postgres e melhor que responder com corpus parcial em silencio.
        logger.warning("RAG: manifesto marca divergencia - usando Postgres")
        return None
    if not list(DIR_PARQUET.glob(_GLOB)):
        logger.warning("RAG: manifesto sem particoes - usando Postgres")
        return None
    return dados


def usando_parquet() -> bool:
    return manifesto() is not None


@functools.lru_cache(maxsize=1)
def _duck():
    import duckdb
    con = duckdb.connect(":memory:")
    con.execute("SET TimeZone='UTC'")
    return con


def _fonte_parquet() -> str:
    caminho = str((DIR_PARQUET / _GLOB).as_posix()).replace("'", "''")
    return f"read_parquet('{caminho}')"


def _executar(sql_template: str, params: dict, conn: Any = None) -> list[tuple]:
    """Roda a consulta no backend disponivel e devolve linhas cruas.

    `conn` e a conexao SQLAlchemy da reserva; so e usada quando nao ha Parquet
    publicado (ou quando o chamador forca o Postgres para comparar).
    """
    if conn is None:
        sql = sql_template.format(fonte=_fonte_parquet(),
                                  teto=_MAX_CHUNKS_POR_DOC)
        return _duck().execute(sql, params).fetchall()
    from sqlalchemy import text
    sql = _para_sqlalchemy(
        sql_template.format(fonte=_FONTE_PG, teto=_MAX_CHUNKS_POR_DOC))
    return [tuple(r) for r in conn.execute(text(sql), params).fetchall()]


# ----------------------------------------------------------------- consultas --

def busca_temporal(root: str, limite: int, meses: int | None = None,
                   conn: Any = None) -> list[tuple]:
    """Chunks recentes, no maximo 8 por documento.

    Devolve (chunk_text, data_doc, tipo_doc, titulo, chunk_index, doc_id).
    """
    return _executar(_Q_TEMPORAL,
                     {"root": root.upper()[:4], "lim": int(limite),
                      "corte": corte_de_data(meses)}, conn)


def busca_ancora(root: str, limite: int, meses: int | None = None,
                 conn: Any = None) -> list[tuple]:
    """Chunks de documentos com sinal factual (resultado, fato relevante,
    dividendo, guidance...). Devolve (chunk_text, data_doc, tipo_doc, titulo,
    doc_id)."""
    return _executar(_Q_ANCORA,
                     {"root": root.upper()[:4], "lim": int(limite),
                      "corte": corte_de_data(meses)}, conn)


def cobertura(roots: tuple[str, ...], conn: Any = None) -> dict[str, int]:
    """{root: n_chunks}. Root ausente do retorno = sem documentos CVM."""
    alvo = sorted({r.upper()[:4] for r in roots if r})
    if not alvo:
        return {}
    linhas = _executar(_Q_COBERTURA, {"roots": alvo}, conn)
    return {str(r[0]): int(r[1]) for r in linhas}
