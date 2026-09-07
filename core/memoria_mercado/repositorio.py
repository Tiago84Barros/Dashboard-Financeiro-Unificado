"""Persistência da Memória de Mercado -- **no armazém local, nunca no Supabase**.

A instrução do usuário nesta entrega é literal: *"Se for necessário ingerir
informações, salve-as no banco de dados local e nunca no Supabase, ele já está
quase no limite."* O Supabase estava em 425 MB de 500 MB em 01/09/2026. Este
módulo mede dezenas de janelas por evento, por horizonte, por versão de
metodologia -- é exatamente o tipo de tabela que enche o plano gratuito e derruba
o app publicado junto.

A regra virou código, não comentário
------------------------------------
:func:`gravar` chama :func:`exigir_local` antes de qualquer ``INSERT``, e
:func:`exigir_local` levanta :class:`DestinoRemotoRecusado` quando o host da
engine não é local. Uma regra que existe só na documentação é uma regra que
alguém quebra na sexta-feira à tarde.

E a convenção do repositório continua valendo: ``core/`` **não abre** o armazém
local por conta própria. A engine chega por parâmetro; quem a constrói é
``scripts/construir_memoria_mercado.py``, que é onde ``_warehouse_url()`` mora.
Este módulo sabe recusar um destino errado, não sabe escolher o certo.

Versão na chave
---------------
Mesma disciplina de :mod:`core.noticias.armazenamento`: a versão da metodologia
entra na chave primária, e safras de versões diferentes coexistem. Já custou
caro aqui subir uma versão sem reconstruir a safra e ver o painel esvaziar em
silêncio -- ``memoria: versao-de-metodologia-sem-safra``. E o inverso também:
apagar com um ``DELETE`` filtrado pela versão corrente deixa a safra antiga fora
de alcance para sempre -- ``memoria: remocao-escopada-pelo-filtro-da-leitura``.
Por isso :func:`limpar_tipo` apaga por ``(tipo_evento)``, sem filtrar versão.
"""
from __future__ import annotations

import json
import logging
import threading

from sqlalchemy import text

# Reexportados de proposito: quem escreve
# ``except repositorio.DestinoRemotoRecusado`` nao deveria precisar saber que a
# implementacao se mudou de casa. E a classe e a mesma, entao o ``except``
# continua pegando exatamente o que sempre pegou.
from core.destino_local import (  # noqa: F401
    FRAGMENTOS_REMOTOS,
    HOSTS_LOCAIS,
    DestinoRemotoRecusado,
    e_local,
    url_da_engine,
)
from core.destino_local import exigir_local as _exigir_local
from core.memoria_mercado import MEMORIA_MERCADO_VERSAO
from core.memoria_mercado.retornos import EventoMedido

logger = logging.getLogger(__name__)

ESQUEMA = "memoria_mercado"



DDL_SQL = [
    f"CREATE SCHEMA IF NOT EXISTS {ESQUEMA}",
    f"""
    CREATE TABLE IF NOT EXISTS {ESQUEMA}.eventos_medidos (
        versao_metodologia  TEXT NOT NULL,
        chave               TEXT NOT NULL,
        simbolo             TEXT NOT NULL,
        tipo_evento         TEXT NOT NULL,
        data_evento         DATE NOT NULL,
        data_pregao_zero    DATE,
        setor               TEXT,
        benchmark           TEXT,
        benchmark_sintetico BOOLEAN NOT NULL DEFAULT FALSE,
        modelo_anormal      TEXT,
        beta                NUMERIC(10,6),
        volatilidade_pre    NUMERIC(12,6),
        volatilidade_pos    NUMERIC(12,6),
        razao_volatilidade  NUMERIC(12,6),
        volume_medio_pre    NUMERIC(20,4),
        volume_medio_pos    NUMERIC(20,4),
        razao_volume        NUMERIC(12,6),
        drawdown            NUMERIC(12,6),
        pregoes_ate_o_pior  INTEGER,
        pregoes_ate_recuperar INTEGER,
        recuperacao_observada BOOLEAN,
        persistencia        TEXT,
        deriva_pre_evento   NUMERIC(12,6),
        janelas             JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        limitacoes          JSONB NOT NULL DEFAULT '[]'::jsonb,
        atualizado_em       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (versao_metodologia, chave)
    )
    """,
    f"""
    CREATE INDEX IF NOT EXISTS ix_mm_eventos_tipo
        ON {ESQUEMA}.eventos_medidos (tipo_evento, data_evento)
    """,
    f"""
    CREATE INDEX IF NOT EXISTS ix_mm_eventos_simbolo
        ON {ESQUEMA}.eventos_medidos (simbolo, data_evento)
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {ESQUEMA}.cenarios (
        versao_metodologia  TEXT NOT NULL,
        chave               TEXT NOT NULL,
        dimensoes           JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        atualizado_em       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (versao_metodologia, chave)
    )
    """,
    # ── Migracao 06/09/2026: a identidade do fato deixa de ser a `chave` ─────
    #
    # `chave` e um TEXTO COMPOSTO PELO CHAMADOR. Enquanto o UNIQUE recaia so
    # sobre ela, trocar o formato da chave re-admitia o acervo inteiro: em
    # 06/09/2026 o mesmo resultado anual da BBDC4 entrou duas vezes, como
    # `cvm:BBDC4:2010` e como `resultado_anual:BBDC4:2011-01-31`. Nenhum erro,
    # 8.923 linhas para 4.463 eventos, e a base historica com n dobrado --
    # `memoria: procedencia-na-chave-aceita-o-fato-de-novo`.
    #
    # A identidade real do evento medido e (versao, tipo, simbolo, data). Dois
    # eventos do mesmo tipo, no mesmo ativo, no mesmo dia sao o mesmo evento --
    # se a fonte devolver dois, o segundo e duplicata, nao observacao nova.
    #
    # O bloco so age quando o indice ainda nao existe, e nesse caso limpa as
    # duplicatas mantendo a linha mais recente. Depois disso e no-op.
    f"""
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_indexes
                        WHERE schemaname = '{ESQUEMA}'
                          AND indexname = 'ux_mm_eventos_fato') THEN
            DELETE FROM {ESQUEMA}.eventos_medidos a
             USING {ESQUEMA}.eventos_medidos b
             WHERE a.versao_metodologia = b.versao_metodologia
               AND a.tipo_evento = b.tipo_evento
               AND a.simbolo = b.simbolo
               AND a.data_evento = b.data_evento
               AND (a.atualizado_em, a.chave) < (b.atualizado_em, b.chave);
            CREATE UNIQUE INDEX ux_mm_eventos_fato
                ON {ESQUEMA}.eventos_medidos
                   (versao_metodologia, tipo_evento, simbolo, data_evento);
            -- Cenario cuja chave nao aponta mais para nenhum evento e lixo de
            -- geracao anterior: manteria dimensoes de um fato ja substituido.
            DELETE FROM {ESQUEMA}.cenarios c
             WHERE NOT EXISTS (SELECT 1 FROM {ESQUEMA}.eventos_medidos e
                                WHERE e.versao_metodologia = c.versao_metodologia
                                  AND e.chave = c.chave);
        END IF;
    END $$;
    """,
]

_schema_pronto = False
_lock = threading.Lock()


def exigir_local(engine) -> None:
    """Levanta :class:`DestinoRemotoRecusado` se o destino não for local.

    A decisão em si mora em :mod:`core.destino_local`. Este invólucro existe só
    para preservar a assinatura de uma linha que os chamadores já usam e para
    dizer *o quê* está sendo recusado, que é o que falta num log de
    "destino recusado" sem contexto.
    """
    _exigir_local(engine, o_que="a Memoria de Mercado")


def garantir_schema(conn) -> None:
    """Cria schema, tabelas e índices se faltarem. Idempotente."""
    global _schema_pronto
    if _schema_pronto:
        return
    with _lock:
        if _schema_pronto:
            return
        for ddl in DDL_SQL:
            conn.execute(text(ddl))
        _schema_pronto = True


def linha_evento(evento: EventoMedido, *,
                 versao: str = MEMORIA_MERCADO_VERSAO) -> dict:
    """Monta a linha do evento medido. Pura -- testável sem banco.

    As janelas viram JSON com o horizonte como chave em texto, porque JSON não
    tem chave inteira. Ler de volta exige ``int(k)``, e é por isso que
    :func:`carregar_eventos` faz a conversão num lugar só.
    """
    janelas = {
        str(h): {
            "retorno_ativo": j.retorno_ativo,
            "retorno_benchmark": j.retorno_benchmark,
            "retorno_setorial": j.retorno_setorial,
            "retorno_anormal": j.retorno_anormal,
            "retorno_anormal_setorial": j.retorno_anormal_setorial,
            "modelo_anormal": j.modelo_anormal,
            "densidade": j.densidade,
            "motivo_ausencia": j.motivo_ausencia,
        }
        for h, j in sorted(evento.janelas.items())
    }
    # O modelo de retorno anormal e por janela, nao por evento: `medir_evento`
    # pode degradar de mercado para diferenca simples em horizontes diferentes.
    # A coluna guarda o conjunto observado, ordenado, para a leitura nao
    # depender da ordem de iteracao -- `memoria: determinismo-carteira-b3`.
    modelos = sorted({j.modelo_anormal for j in evento.janelas.values()
                      if j.modelo_anormal})
    return {
        "versao_metodologia": versao,
        "chave": evento.chave,
        "simbolo": evento.simbolo,
        "tipo_evento": evento.tipo_evento,
        "data_evento": evento.data_evento,
        "data_pregao_zero": evento.data_pregao_zero,
        "setor": evento.setor,
        "benchmark": evento.benchmark,
        "benchmark_sintetico": bool(evento.benchmark_sintetico),
        "modelo_anormal": (",".join(modelos) if modelos else None),
        "beta": (evento.beta.beta if evento.beta is not None else None),
        "volatilidade_pre": evento.volatilidade_pre,
        "volatilidade_pos": evento.volatilidade_pos,
        "razao_volatilidade": evento.razao_volatilidade,
        "volume_medio_pre": evento.volume_medio_pre,
        "volume_medio_pos": evento.volume_medio_pos,
        "razao_volume": evento.razao_volume,
        "drawdown": evento.drawdown,
        "pregoes_ate_o_pior": evento.pregoes_ate_o_pior,
        "pregoes_ate_recuperar": evento.pregoes_ate_recuperar,
        "recuperacao_observada": evento.recuperacao_observada,
        "persistencia": evento.persistencia,
        "deriva_pre_evento": evento.deriva_pre_evento,
        "janelas": json.dumps(janelas),
        "limitacoes": json.dumps(list(evento.limitacoes)),
    }


_UPSERT = f"""
INSERT INTO {ESQUEMA}.eventos_medidos (
    versao_metodologia, chave, simbolo, tipo_evento, data_evento,
    data_pregao_zero, setor, benchmark, benchmark_sintetico, modelo_anormal,
    beta, volatilidade_pre, volatilidade_pos, razao_volatilidade,
    volume_medio_pre, volume_medio_pos, razao_volume, drawdown,
    pregoes_ate_o_pior, pregoes_ate_recuperar, recuperacao_observada,
    persistencia, deriva_pre_evento, janelas, limitacoes, atualizado_em
) VALUES (
    :versao_metodologia, :chave, :simbolo, :tipo_evento, :data_evento,
    :data_pregao_zero, :setor, :benchmark, :benchmark_sintetico,
    :modelo_anormal, :beta, :volatilidade_pre, :volatilidade_pos,
    :razao_volatilidade, :volume_medio_pre, :volume_medio_pos, :razao_volume,
    :drawdown, :pregoes_ate_o_pior, :pregoes_ate_recuperar,
    :recuperacao_observada, :persistencia, :deriva_pre_evento,
    CAST(:janelas AS JSONB), CAST(:limitacoes AS JSONB), NOW()
)
-- O alvo do conflito e a identidade do FATO, nao a chave de texto: ver a
-- migracao `ux_mm_eventos_fato` em DDL_SQL. `chave` entra no UPDATE porque o
-- formato novo deve substituir o antigo na linha que sobreviveu.
ON CONFLICT (versao_metodologia, tipo_evento, simbolo, data_evento)
DO UPDATE SET
    chave = EXCLUDED.chave,
    data_pregao_zero = EXCLUDED.data_pregao_zero,
    setor = EXCLUDED.setor,
    benchmark = EXCLUDED.benchmark,
    benchmark_sintetico = EXCLUDED.benchmark_sintetico,
    modelo_anormal = EXCLUDED.modelo_anormal,
    beta = EXCLUDED.beta,
    volatilidade_pre = EXCLUDED.volatilidade_pre,
    volatilidade_pos = EXCLUDED.volatilidade_pos,
    razao_volatilidade = EXCLUDED.razao_volatilidade,
    volume_medio_pre = EXCLUDED.volume_medio_pre,
    volume_medio_pos = EXCLUDED.volume_medio_pos,
    razao_volume = EXCLUDED.razao_volume,
    drawdown = EXCLUDED.drawdown,
    pregoes_ate_o_pior = EXCLUDED.pregoes_ate_o_pior,
    pregoes_ate_recuperar = EXCLUDED.pregoes_ate_recuperar,
    recuperacao_observada = EXCLUDED.recuperacao_observada,
    persistencia = EXCLUDED.persistencia,
    deriva_pre_evento = EXCLUDED.deriva_pre_evento,
    janelas = EXCLUDED.janelas,
    limitacoes = EXCLUDED.limitacoes,
    atualizado_em = NOW()
"""


def gravar(eventos, engine, *, versao: str = MEMORIA_MERCADO_VERSAO,
           cenarios: dict | None = None) -> dict:
    """Grava os eventos medidos no armazém local. Recusa destino remoto.

    A engine é **obrigatória e explícita**. Não há ``get_engine()`` de reserva
    aqui de propósito: a engine padrão do repositório aponta para o Supabase, e
    um parâmetro opcional com esse default transformaria o esquecimento de um
    argumento em gravação no lugar proibido.
    """
    exigir_local(engine)
    linhas = [linha_evento(e, versao=versao) for e in (eventos or ())]
    if not linhas:
        logger.info("memoria de mercado: nenhum evento medido para gravar")
        return {"gravado": False, "motivo": "nenhum evento medido", "linhas": 0}

    cenarios = dict(cenarios or {})
    with engine.begin() as conn:
        garantir_schema(conn)
        conn.execute(text(_UPSERT), linhas)
        if cenarios:
            conn.execute(text(f"""
                INSERT INTO {ESQUEMA}.cenarios
                    (versao_metodologia, chave, dimensoes, atualizado_em)
                VALUES (:versao_metodologia, :chave, CAST(:dimensoes AS JSONB), NOW())
                ON CONFLICT (versao_metodologia, chave) DO UPDATE SET
                    dimensoes = EXCLUDED.dimensoes,
                    atualizado_em = NOW()
            """), [{"versao_metodologia": versao, "chave": k,
                    "dimensoes": json.dumps(v)} for k, v in sorted(cenarios.items())])

        # Invariante, nao limpeza pontual: `cenarios` e chaveado pela `chave`
        # do evento, e a `chave` e texto composto pelo chamador. Quando o
        # formato muda, o upsert de eventos atualiza a linha do fato e a
        # entrada antiga de cenario fica orfa -- carregando as dimensoes de um
        # fato que ja foi substituido. Rodar isto a cada gravacao e barato e
        # impede que a orfandade acumule em silencio.
        orfaos = conn.execute(text(f"""
            DELETE FROM {ESQUEMA}.cenarios c
             WHERE c.versao_metodologia = :versao
               AND NOT EXISTS (SELECT 1 FROM {ESQUEMA}.eventos_medidos e
                                WHERE e.versao_metodologia = c.versao_metodologia
                                  AND e.chave = c.chave)
        """), {"versao": versao}).rowcount or 0
    if orfaos:
        logger.info("memoria de mercado: %d cenarios orfaos removidos", orfaos)

    # `linhas` conta FATOS distintos, nao linhas enviadas. As duas divergem
    # quando a fonte devolve o mesmo (tipo, simbolo, data) mais de uma vez --
    # duas DFP entregues no mesmo dia pelo mesmo emissor, por exemplo. Relatar
    # o tamanho do lote diria "4.463 gravados" onde existem 4.460, e a
    # diferenca so apareceria para quem fosse contar no banco.
    fatos = {(x["tipo_evento"], x["simbolo"], x["data_evento"]) for x in linhas}
    logger.info("memoria de mercado: %d eventos gravados no armazem local "
                "(versao %s; %d linhas enviadas)",
                len(fatos), versao, len(linhas))
    return {"gravado": True, "linhas": len(fatos),
            "linhas_enviadas": len(linhas),
            "fatos_repetidos_no_lote": len(linhas) - len(fatos),
            "cenarios": len(cenarios), "versao": versao,
            "destino": url_da_engine(engine)}


def carregar_eventos(engine, *, tipo_evento: str | None = None,
                     simbolo: str | None = None,
                     versao: str = MEMORIA_MERCADO_VERSAO) -> list[dict]:
    """Lê as linhas gravadas. Devolve dicionários crus, não ``EventoMedido``.

    A reconstrução do dataclass fica com quem chama porque a leitura tem um uso
    legítimo que não precisa dela: conferir cobertura e frescor da base sem
    reidratar objeto nenhum.
    """
    exigir_local(engine)
    condicoes = ["versao_metodologia = :versao"]
    params: dict = {"versao": versao}
    if tipo_evento:
        condicoes.append("tipo_evento = :tipo_evento")
        params["tipo_evento"] = tipo_evento
    if simbolo:
        condicoes.append("simbolo = :simbolo")
        params["simbolo"] = simbolo

    sql = (f"SELECT * FROM {ESQUEMA}.eventos_medidos "
           f"WHERE {' AND '.join(condicoes)} "
           "ORDER BY data_evento, chave")
    with engine.begin() as conn:
        garantir_schema(conn)
        linhas = [dict(r) for r in conn.execute(text(sql), params).mappings()]

    for linha in linhas:
        janelas = linha.get("janelas") or {}
        if isinstance(janelas, str):
            janelas = json.loads(janelas)
        linha["janelas"] = {int(k): v for k, v in janelas.items()}
        lim = linha.get("limitacoes") or []
        linha["limitacoes"] = tuple(json.loads(lim) if isinstance(lim, str) else lim)
    return linhas


def limpar_tipo(engine, tipo_evento: str) -> int:
    """Apaga todas as safras de um tipo de evento, de **todas** as versões.

    Filtrar por versão aqui deixaria as safras antigas fora do alcance de
    qualquer reprocessamento futuro -- o defeito de ``memoria:
    remocao-escopada-pelo-filtro-da-leitura``, que já deixou 70% de uma vitrine
    presa em metodologia morta.
    """
    exigir_local(engine)
    with engine.begin() as conn:
        garantir_schema(conn)
        resultado = conn.execute(
            text(f"DELETE FROM {ESQUEMA}.eventos_medidos "
                 "WHERE tipo_evento = :tipo"), {"tipo": tipo_evento})
    apagadas = resultado.rowcount or 0
    logger.info("memoria de mercado: %d linhas removidas do tipo %s "
                "(todas as versoes)", apagadas, tipo_evento)
    return apagadas
