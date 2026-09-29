"""Publica o corpus RAG (CVM/ENET) do armazem local em Parquet particionado.

POR QUE ISTO EXISTE
-------------------
`public.docs_corporativos_chunks` ocupava 162 MB no Supabase -- 32% de um banco
com teto de 500 MB -- para guardar 93.498 chunks de texto que o app so LE. A
coluna `embedding` esta 100% nula: medido em 25/08/2026, zero vetores em 93.498
linhas, entao `core/rag_b3.py` sempre caiu no caminho de busca temporal, que e
filtro por ticker + ordenacao por data. Nenhuma capacidade de banco relacional
estava em uso ali; so se pagava o preco de armazenamento transacional
(MVCC, WAL, backup continuo, indices) por um arquivo.

Em Parquet+zstd o mesmo corpus cabe em ~28 MB, e DuckDB executa exatamente as
mesmas consultas por cima dos arquivos.

A FONTE DE VERDADE E O ARMAZEM LOCAL
------------------------------------
Verificado antes de escrever este script: as duas copias sao bit-identicas --
93.498 chunks, 93.498 hashes distintos, e md5 do agregado ordenado de
`chunk_hash` igual (`3134197f...`) nos dois bancos. O Supabase e copia, nao
original. Por isso publicar daqui e seguro, e por isso o manifesto abaixo grava
a MESMA assinatura: quem consumir o Parquet consegue provar que tem o corpus
inteiro, sem depender de contagem (contagem igual nao e corpus igual).

DESNORMALIZACAO DELIBERADA
--------------------------
As consultas de `rag_b3` sempre fazem o mesmo JOIN (chunks -> docs) e usam so
um punhado de colunas do pai. Achatar aqui elimina o join do caminho de leitura
e custa quase nada em disco: `tipo_doc` e `titulo` se repetem muito por
documento, e o dictionary encoding do Parquet colapsa a repeticao.

Mais importante: dois construtos das consultas eram especificos de Postgres --
o operador de regex `~` e `(:n || ' months')::interval`. Ambos viravam divida
de portabilidade. O regex de ancora classifica o DOCUMENTO, nao a consulta,
entao ele e resolvido aqui e vira a coluna booleana `eh_ancora`; o corte de
data vira parametro calculado em Python. O SQL de leitura fica identico nos
dois motores, sem tradutor de dialeto no meio.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.publish_fii_selection_from_local import _warehouse_url  # noqa: E402

DESTINO = ROOT / "data" / "public" / "rag"

# Mesma expressao que vivia embutida em _search_anchor_recent. Mantida literal
# de proposito: se ela mudar la, o teste de paridade acusa a divergencia.
_RE_ANCORA = re.compile(
    r"fato relevante|econ[oô]mico-financ|guidance|capex|produ[çc][aã]o|"
    r"dividend|provento|resultado")

# Stub de metadados do backfill IPE: tem titulo e data, nao tem numero. A ancora
# precisa exclui-lo para nao gastar o LIMIT de recencia com casca.
_EXTRACAO_STUB = "ipe_meta_v1"

_SQL = """
SELECT
    c.ticker,
    LEFT(UPPER(c.ticker), 4)                                    AS root,
    c.doc_id,
    c.chunk_index,
    c.chunk_text,
    c.chunk_hash,
    COALESCE(c.document_date, d.document_date, d.data)::date    AS data_doc,
    COALESCE(NULLIF(d.tipo, ''), NULLIF(d.categoria, ''),
             NULLIF(c.categoria, ''), '')                       AS tipo_doc,
    COALESCE(d.titulo, '')                                      AS titulo,
    COALESCE(d.extraction_version, '')                          AS extracao_doc
FROM public.docs_corporativos_chunks c
JOIN public.docs_corporativos d ON d.id = c.doc_id
ORDER BY c.doc_id, c.chunk_index
"""

_SQL_ASSINATURA = """
SELECT count(*) AS n,
       count(DISTINCT chunk_hash) AS distintos,
       md5(string_agg(chunk_hash, chr(10) ORDER BY chunk_hash)) AS assinatura
FROM public.docs_corporativos_chunks
"""


def _engine(url: str):
    return create_engine(url.replace("postgresql://", "postgresql+psycopg2://"),
                         future=True)


def _marca_ancora(df: pd.DataFrame) -> pd.Series:
    """Reproduz o predicado de ancora que estava no SQL, sobre o mesmo campo
    concatenado (tipo + categoria + titulo) e em minusculas."""
    alvo = (df["tipo_doc"].fillna("") + " " + df["titulo"].fillna("")).str.lower()
    return alvo.str.contains(_RE_ANCORA, regex=True, na=False)


def _manifesto_atual(destino: Path) -> dict | None:
    try:
        return json.loads((destino / "manifesto.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def origem_inalterada(anterior: dict | None, assinatura: dict, destino: Path) -> bool:
    """A origem e a mesma da ultima publicacao que conferiu.

    Reescrever nesse caso so geraria commit sem conteudo novo na rotina
    semanal. Sem particao em disco, publica de qualquer jeito.
    """
    return bool(anterior and anterior.get("confere")
                and anterior.get("assinatura_origem") == assinatura["assinatura"]
                and anterior.get("linhas_origem") == int(assinatura["n"])
                and list(destino.glob("chunks_*.parquet")))


def gravar_particoes(df: pd.DataFrame, destino: Path) -> list[dict]:
    """Grava uma particao por letra do root e ano do documento.

    A letra e o que a consulta filtra (`root`). O ano existe por causa do git:
    a rotina republica toda semana e commita o que mudou, e documento novo e
    quase sempre do ano corrente -- so letra fazia toda particao mudar toda
    semana, ~46 MB de historico por republicacao. Com a ordem fixa do SELECT
    (doc_id, chunk_index), particao sem dado novo sai byte a byte igual e o git
    nao a ve como mudada. Particao que deixou de existir e apagada.
    """
    destino.mkdir(parents=True, exist_ok=True)
    for antigo in destino.glob("*.parquet"):
        antigo.unlink()

    ano = pd.to_datetime(df["data_doc"], errors="coerce").dt.year
    rotulo = (df["root"].str[0].fillna("_").str.upper() + "_"
              + ano.map(lambda a: "sem-data" if pd.isna(a) else str(int(a))))
    partes = []
    for prefixo, bloco in df.groupby(rotulo, sort=True):
        caminho = destino / f"chunks_{prefixo}.parquet"
        tabela = pa.Table.from_pandas(bloco, preserve_index=False)
        pq.write_table(tabela, caminho, compression="zstd", compression_level=9)
        partes.append({"arquivo": caminho.name, "linhas": len(bloco),
                       "bytes": caminho.stat().st_size})
    return partes


def publicar(destino: Path = DESTINO) -> dict:
    eng = _engine(_warehouse_url())
    with eng.connect() as conn:
        # PORTAO: este publicador nao carrega vetores. Hoje isso e inofensivo --
        # medido em 25/08/2026, `embedding` esta 100% nula e `rag_b3` sempre usa
        # a busca temporal. Mas se alguem rodar `gerar_embeddings_chunks.py` e
        # depois republicar, o Parquet ficaria sem os vetores e a busca
        # semantica seria desligada EM SILENCIO: sem erro, sem log, so respostas
        # piores. Falhar alto aqui e o que impede isso.
        com_vetor = conn.execute(text(
            "SELECT count(*) FROM public.docs_corporativos_chunks "
            "WHERE embedding IS NOT NULL")).scalar_one()
        if com_vetor:
            raise RuntimeError(
                f"{com_vetor} chunks tem embedding e este publicador nao os "
                "carrega - publicar agora desligaria a busca semantica sem "
                "aviso. Estenda o Parquet para incluir o vetor antes de seguir.")
        assinatura = dict(conn.execute(text(_SQL_ASSINATURA)).mappings().one())
        anterior = _manifesto_atual(destino)
        if origem_inalterada(anterior, assinatura, destino):
            eng.dispose()
            return {**anterior, "inalterado": True}
        df = pd.read_sql(text(_SQL), conn)
    eng.dispose()

    if df.empty:
        raise RuntimeError("corpus local vazio - nada a publicar")

    df["eh_ancora"] = _marca_ancora(df)
    df["eh_stub"] = df["extracao_doc"].fillna("") == _EXTRACAO_STUB
    df = df.drop(columns=["extracao_doc"])
    # date32, nao timestamp. No Postgres a consulta faz `::text` sobre um DATE e
    # entrega 'YYYY-MM-DD'; se aqui virasse timestamp, o mesmo cast entregaria
    # 'YYYY-MM-DD 00:00:00' e o texto exibido ao usuario divergiria do banco.
    df["data_doc"] = pd.to_datetime(df["data_doc"], errors="coerce").dt.date

    # Assinatura propria do Parquet, calculada do MESMO jeito que a do Postgres
    # (md5 sobre os chunk_hash ordenados, separados por \n). Igualdade aqui
    # prova que o arquivo carrega o corpus inteiro, chunk a chunk. Conferida
    # ANTES de apagar as particoes: se divergir, o corpus publicado fica como
    # estava, coerente com o manifesto que o descreve.
    assinatura_parquet = hashlib.md5(
        "\n".join(sorted(df["chunk_hash"].astype(str))).encode()
    ).hexdigest()
    if not (assinatura_parquet == assinatura["assinatura"]
            and len(df) == int(assinatura["n"])):
        raise RuntimeError(
            "corpus lido diverge da origem: "
            f"{len(df)} linhas/{assinatura_parquet} vs "
            f"{assinatura['n']}/{assinatura['assinatura']}")

    partes = gravar_particoes(df, destino)
    manifesto = {
        "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "linhas": int(len(df)),
        "chunks_distintos": int(df["chunk_hash"].nunique()),
        "roots": int(df["root"].nunique()),
        "ancoras": int(df["eh_ancora"].sum()),
        "stubs": int(df["eh_stub"].sum()),
        "bytes": sum(p["bytes"] for p in partes),
        "particoes": partes,
        "assinatura_chunk_hash": assinatura_parquet,
        "assinatura_origem": assinatura["assinatura"],
        "linhas_origem": int(assinatura["n"]),
        "confere": True,
    }
    (destino / "manifesto.json").write_text(
        json.dumps(manifesto, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifesto


if __name__ == "__main__":
    m = publicar()
    print(json.dumps({k: v for k, v in m.items() if k != "particoes"},
                     ensure_ascii=False, indent=2))
    print(f"\n{m['bytes'] / 1048576:.1f} MB em {len(m['particoes'])} particoes")
