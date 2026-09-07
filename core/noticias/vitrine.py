"""A vitrine do noticiário: a leitura corrente por ativo, e só ela.

O acervo mora no armazém local por aritmética, não por gosto: ~11 mil itens por
janela de 30 dias a ~2 KB dão ~22 MB **acumulando**, contra 23 MB de folga no
Supabase (477 de 500 medidos em 05/09/2026). Em pouco mais de três meses o
acervo sozinho estouraria o plano.

Só que a Streamlit Cloud não alcança o armazém local. Sem uma ponte, "ingerir só
no local" resolveria o espaço e desligaria o componente em produção — que é
exatamente o modo de falha que este projeto já pagou uma vez.

A ponte é esta vitrine, e ela é o oposto do acervo em três eixos:

=================  ===========================  =============================
                   acervo (local)               vitrine (Supabase)
=================  ===========================  =============================
grão               um item de notícia           um ativo
histórico          acumula, nunca apaga         **substituída** a cada publicação
texto              título, resumo, 2 URLs       título e veículo de 3 itens
tamanho            ~22 MB por janela, somando   ~1,5 MB, fixo
=================  ===========================  =============================

Três recusas que definem o formato
----------------------------------

1. **A vitrine é substituída inteira, sem filtro de versão.** Já houve neste
   projeto um ``DELETE`` escopado pela versão corrente da metodologia: ao subir
   a versão, as linhas antigas ficaram fora do alcance do próprio publicador e
   70% da vitrine dos EUA virou metodologia morta e imortal
   (``memoria: remocao-escopada-pelo-filtro-da-leitura``). Aqui o ``DELETE`` não
   tem ``WHERE``: a vitrine é uma foto, e foto não tem safra.

2. **Vitrine vazia não é a mesma coisa que vitrine que nunca existiu.** Por isso
   ``noticias_vitrine_meta`` existe, com uma linha só. Sem ela, o dia em que o
   publicador quebrasse seria indistinguível do dia em que nenhum ativo teve
   notícia: as duas telas mostrariam zero linhas.

3. **``valor`` nulo é "não medido", e permanece nulo.** O ativo que não alcançou
   o piso de itens entra na vitrine com ``valor = NULL`` e o motivo escrito ao
   lado, em vez de ficar de fora. Ficar de fora faria o consumidor não saber
   distinguir "não medimos este" de "este não existe"
   (``memoria: medicao-que-pune-a-evidencia``).

Quem lê esta vitrine tem de carimbá-la. ``gerada_em`` viaja na linha e na meta
justamente porque uma vitrine substituída não envelhece visivelmente: ela sempre
parece a leitura de agora. A regra de idade máxima mora em
:mod:`core.conjuntura.ponte`, onde a decisão de mover peso é tomada.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import text

logger = logging.getLogger(__name__)

#: Itens citados por ativo. Três é o que a tela e o prompt já exibem
#: (``para_llm`` corta em 3), e guardar mais seria carregar o Supabase com texto
#: que ninguém lê.
ITENS_POR_ATIVO = 3


class VitrineIlegivel(RuntimeError):
    """A vitrine não pôde ser lida — o que não é o mesmo que vitrine vazia."""


DDL_SQL = [
    """
    CREATE TABLE IF NOT EXISTS noticias_vitrine (
        simbolo             TEXT PRIMARY KEY,
        valor               NUMERIC(6,2),
        n_itens             INTEGER NOT NULL DEFAULT 0,
        motivo              TEXT NOT NULL DEFAULT '',
        itens               JSONB NOT NULL DEFAULT '[]'::jsonb,
        versao_metodologia  TEXT NOT NULL,
        janela_dias         INTEGER NOT NULL,
        gerada_em           TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS noticias_vitrine_meta (
        id                  SMALLINT PRIMARY KEY,
        gerada_em           TIMESTAMPTZ NOT NULL,
        janela_dias         INTEGER NOT NULL,
        versao_metodologia  TEXT NOT NULL,
        ativos              INTEGER NOT NULL,
        ativos_medidos      INTEGER NOT NULL,
        itens_no_acervo     INTEGER NOT NULL,
        origem              TEXT NOT NULL DEFAULT ''
    )
    """,
]

_INSERT_LINHA = text("""
    INSERT INTO noticias_vitrine (
        simbolo, valor, n_itens, motivo, itens, versao_metodologia,
        janela_dias, gerada_em
    ) VALUES (
        :simbolo, :valor, :n_itens, :motivo, CAST(:itens AS JSONB),
        :versao_metodologia, :janela_dias, :gerada_em
    )
""")

_UPSERT_META = text("""
    INSERT INTO noticias_vitrine_meta (
        id, gerada_em, janela_dias, versao_metodologia, ativos,
        ativos_medidos, itens_no_acervo, origem
    ) VALUES (
        1, :gerada_em, :janela_dias, :versao_metodologia, :ativos,
        :ativos_medidos, :itens_no_acervo, :origem
    )
    ON CONFLICT (id) DO UPDATE SET
        gerada_em = EXCLUDED.gerada_em,
        janela_dias = EXCLUDED.janela_dias,
        versao_metodologia = EXCLUDED.versao_metodologia,
        ativos = EXCLUDED.ativos,
        ativos_medidos = EXCLUDED.ativos_medidos,
        itens_no_acervo = EXCLUDED.itens_no_acervo,
        origem = EXCLUDED.origem
""")

_SELECT_LINHAS = text("""
    SELECT simbolo, valor, n_itens, motivo, itens, versao_metodologia,
           janela_dias, gerada_em
      FROM noticias_vitrine
     WHERE simbolo = ANY(:simbolos)
""")

_SELECT_META = text("""
    SELECT gerada_em, janela_dias, versao_metodologia, ativos,
           ativos_medidos, itens_no_acervo, origem
      FROM noticias_vitrine_meta
     WHERE id = 1
""")


def garantir_schema(conn) -> None:
    """Cria as duas tabelas se faltarem. Idempotente e não destrutivo.

    Chamada só pelo publicador. O leitor **não** a chama: criar tabela no
    caminho de leitura transforma "a vitrine nunca foi publicada" em "a vitrine
    está vazia", e as duas frases pedem providências opostas.
    """
    for ddl in DDL_SQL:
        conn.execute(text(ddl))


def linha_da_leitura(leitura, *, versao: str, janela_dias: int,
                     gerada_em: datetime) -> dict:
    """Achata uma ``LeituraNoticias`` numa linha. Testável sem banco.

    Aceita qualquer objeto com ``simbolo``, ``valor``, ``n_itens``, ``motivo`` e
    ``itens`` — a vitrine não importa :mod:`core.conjuntura.ponte`, que é quem a
    consome, para que a dependência aponte num sentido só.
    """
    itens = []
    for item in tuple(getattr(leitura, "itens", ()))[:ITENS_POR_ATIVO]:
        publicado = getattr(item, "publicado_em", None)
        itens.append({
            "titulo": str(getattr(item, "titulo", "") or ""),
            "veiculo": (str(item.veiculo) if getattr(item, "veiculo", None)
                        else None),
            "url": (str(item.url) if getattr(item, "url", None) else None),
            "publicado_em": (publicado.isoformat()
                             if isinstance(publicado, datetime) else None),
        })
    valor = getattr(leitura, "valor", None)
    return {
        "simbolo": str(leitura.simbolo).strip().upper(),
        "valor": (None if valor is None else round(float(valor), 2)),
        "n_itens": int(getattr(leitura, "n_itens", 0) or 0),
        "motivo": str(getattr(leitura, "motivo", "") or ""),
        "itens": json.dumps(itens, ensure_ascii=False),
        "versao_metodologia": versao,
        "janela_dias": int(janela_dias),
        "gerada_em": gerada_em,
    }


def publicar(engine, leituras, *, versao: str, janela_dias: int,
             itens_no_acervo: int = 0, origem: str = "armazém local",
             gerada_em: datetime | None = None) -> dict:
    """Substitui a vitrine inteira, numa transação só.

    **Esta é a única gravação remota do noticiário, e é deliberada.** O acervo é
    barrado por ``core.destino_local.exigir_local``; a vitrine não é, porque o
    destino dela *é* o banco que a produção alcança. O que a mantém pequena não
    é uma guarda, é a forma: uma linha por ativo, três itens, sem ``resumo``, e
    substituída em vez de acumulada.

    O ``DELETE`` não tem ``WHERE`` de propósito — ver o módulo.
    """
    momento = gerada_em or datetime.now(timezone.utc)
    linhas = [linha_da_leitura(lt, versao=versao, janela_dias=janela_dias,
                               gerada_em=momento)
              for lt in leituras]
    medidos = sum(1 for linha in linhas if linha["valor"] is not None)
    with engine.begin() as conn:
        garantir_schema(conn)
        conn.execute(text("DELETE FROM noticias_vitrine"))
        for linha in linhas:
            conn.execute(_INSERT_LINHA, linha)
        conn.execute(_UPSERT_META, {
            "gerada_em": momento, "janela_dias": int(janela_dias),
            "versao_metodologia": versao, "ativos": len(linhas),
            "ativos_medidos": medidos,
            "itens_no_acervo": int(itens_no_acervo), "origem": origem,
        })
    return {"publicado": True, "ativos": len(linhas), "ativos_medidos": medidos,
            "gerada_em": momento, "versao": versao, "janela_dias": janela_dias}


def ler(engine, simbolos) -> tuple[tuple[dict, ...], dict | None]:
    """Devolve ``(linhas, meta)``; levanta em falha, ``meta`` ``None`` se nunca publicada.

    Os dois valores de retorno respondem perguntas diferentes, e é por isso que
    são dois: as linhas dizem o que se sabe sobre estes ativos, a meta diz
    **quando** se soube. Vitrine substituída não envelhece na aparência — sem a
    meta, a leitura de três semanas atrás chega à tela com a mesma cara da de
    hoje.
    """
    alvos = [str(s).strip().upper() for s in simbolos if str(s).strip()]
    try:
        with engine.connect() as conn:
            meta_linha = conn.execute(_SELECT_META).mappings().first()
            linhas = (conn.execute(_SELECT_LINHAS, {"simbolos": alvos})
                      .mappings().all() if alvos else [])
    except Exception as exc:  # noqa: BLE001 - falha declarada, não vitrine vazia
        causa = str(exc).splitlines()[0].strip()
        logger.warning("vitrine de noticias ilegivel: %s", causa)
        raise VitrineIlegivel(causa) from exc
    return (tuple(dict(linha) for linha in linhas),
            dict(meta_linha) if meta_linha is not None else None)
