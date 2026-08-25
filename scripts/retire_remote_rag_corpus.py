"""Aposenta o corpus RAG no Supabase depois que o Parquet passou a servi-lo.

O QUE ESTE SCRIPT FAZ, E O QUE ELE SE RECUSA A FAZER
----------------------------------------------------
`public.docs_corporativos_chunks` ocupa 162 MB dos 500 MB do Supabase para
servir consultas que hoje saem de `data/public/rag/*.parquet` (24,9 MB). Este
script derruba a tabela remota -- **so com `--apply` e so depois de PROVAR que
o Parquet carrega o mesmo corpus, chunk a chunk**.

Sem `--apply` ele nao escreve nada: audita e imprime o veredito. Essa e a
posicao padrao de proposito. Apagar 93.498 linhas com base em "acho que ja
migrou" e o tipo de acao que nao tem desfazer.

TRES PORTOES, TODOS ELIMINATORIOS
---------------------------------
1. O manifesto do Parquet existe, marca `confere` e tem particoes em disco.
2. A assinatura do Parquet (md5 dos `chunk_hash` ordenados) e IGUAL a do
   Supabase. Contagem igual nao basta: duas tabelas com 93.498 linhas podem
   ter conteudo diferente. A assinatura fecha essa brecha.
3. Nenhum chunk remoto tem `embedding`. O Parquet nao carrega vetores; se
   houvesse algum, derrubar a tabela apagaria trabalho que o arquivo nao
   substitui.

`public.docs_corporativos` (4,4 MB, a tabela-pai) NAO e tocada. Ela e barata,
guarda o cadastro dos documentos e serve a ingestao. O ganho esta nos chunks.

O ARMAZEM LOCAL CONTINUA SENDO A ORIGEM
---------------------------------------
A tabela remota e copia: o corpus e gerado e mantido no armazem local
(`dfu_warehouse`), de onde o Parquet e publicado. Derrubar a copia remota nao
perde historico -- e reversivel por `scripts/sync_docs_to_supabase.py`, que foi
o que a criou.

USO
    python -m scripts.retire_remote_rag_corpus           # audita, nao escreve
    python -m scripts.retire_remote_rag_corpus --apply   # exige autorizacao humana
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import rag_store  # noqa: E402
from core.database import get_engine  # noqa: E402

_SQL_ASSINATURA = """
SELECT count(*) AS n,
       md5(string_agg(chunk_hash, chr(10) ORDER BY chunk_hash)) AS assinatura,
       count(*) FILTER (WHERE embedding IS NOT NULL) AS com_vetor,
       pg_total_relation_size('public.docs_corporativos_chunks') AS bytes
FROM public.docs_corporativos_chunks
"""


def auditar() -> dict:
    """Coleta a evidencia dos tres portoes. Nao escreve nada."""
    m = rag_store.manifesto()
    laudo: dict = {
        "parquet_publicado": m is not None,
        "parquet_linhas": (m or {}).get("linhas"),
        "parquet_assinatura": (m or {}).get("assinatura_chunk_hash"),
        "parquet_bytes": (m or {}).get("bytes"),
    }
    with get_engine().connect() as conn:
        remoto = dict(conn.execute(text(_SQL_ASSINATURA)).mappings().one())
    laudo.update(remoto_linhas=int(remoto["n"] or 0),
                 remoto_assinatura=remoto["assinatura"],
                 remoto_com_vetor=int(remoto["com_vetor"] or 0),
                 remoto_bytes=int(remoto["bytes"] or 0))

    bloqueios: list[str] = []
    if not laudo["parquet_publicado"]:
        bloqueios.append("Parquet nao publicado (ou manifesto marca divergencia)")
    elif laudo["parquet_assinatura"] != laudo["remoto_assinatura"]:
        bloqueios.append(
            f"assinatura difere: parquet {laudo['parquet_assinatura']} vs "
            f"remoto {laudo['remoto_assinatura']}")
    elif laudo["parquet_linhas"] != laudo["remoto_linhas"]:
        bloqueios.append(f"linhas diferem: {laudo['parquet_linhas']} vs "
                         f"{laudo['remoto_linhas']}")
    if laudo["remoto_com_vetor"]:
        bloqueios.append(
            f"{laudo['remoto_com_vetor']} chunks tem embedding e o Parquet nao "
            "os carrega - derrubar apagaria trabalho sem substituto")
    laudo["bloqueios"] = bloqueios
    laudo["liberado"] = not bloqueios
    return laudo


def aplicar() -> dict:
    laudo = auditar()
    if not laudo["liberado"]:
        raise RuntimeError("aposentadoria bloqueada: " + "; ".join(laudo["bloqueios"]))
    with get_engine().begin() as conn:
        conn.execute(text("DROP TABLE public.docs_corporativos_chunks CASCADE"))
    laudo["aplicado"] = True
    return laudo


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="derruba a tabela remota (exige autorizacao humana)")
    args = ap.parse_args()
    resultado = aplicar() if args.apply else auditar()
    print(json.dumps(resultado, ensure_ascii=False, indent=2, default=str))
    if not args.apply and resultado["liberado"]:
        print(f"\nLiberado: derrubar libera "
              f"{resultado['remoto_bytes'] / 1048576:.1f} MB no Supabase.")
