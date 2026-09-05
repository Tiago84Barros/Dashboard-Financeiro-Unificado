"""Persistência das notícias e das avaliações, em duas tabelas separadas.

A separação repete a de ``modelos.py`` e não é estética:

``noticias_itens``       -- o fato observado. Não muda depois de gravado.
``noticias_avaliacoes``  -- o que o APP4 concluiu, **carimbado com a versão da
                            metodologia**. Muda quando a metodologia muda.

Já houve neste projeto o modo de falha oposto -- subir a versão do motor sem
reconstruir a safra correspondente, e o painel esvaziar em silêncio. Aqui a
versão viaja na chave: uma avaliação da versão 1.0.0 e outra da 1.1.0 coexistem
para a mesma notícia, e quem lê escolhe. Trocar ``VERSAO_METODOLOGIA`` sem
reprocessar não apaga nada; apenas passa a haver notícia sem avaliação na versão
nova, o que é visível em vez de silencioso.

Persistir é opcional. Sem ``DATABASE_URL`` o motor funciona inteiro em memória
-- é assim que ele roda nos testes e é assim que roda antes de qualquer
migração ser aplicada.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from core.database import get_engine
from core.destino_local import exigir_local
from core.noticias.coleta import ResultadoColeta
from core.noticias.destino import O_QUE, engine_acervo
from core.noticias.modelos import NoticiaAvaliada

logger = logging.getLogger(__name__)

#: Versão do conjunto relevância + impacto + portões. Subir isto significa que
#: as avaliações antigas não são comparáveis com as novas -- e por isso elas
#: convivem, em vez de uma sobrescrever a outra.
VERSAO_METODOLOGIA = "1.0.0"


class AcervoIlegivel(RuntimeError):
    """A leitura do acervo falhou -- o que não é o mesmo que acervo vazio.

    Devolver tupla vazia aqui publicaria "nada relevante aconteceu" toda vez
    que o banco caísse ou a tabela faltasse. Quem chama tem de poder dizer na
    tela qual dos dois é.
    """

DDL_SQL = [
    """
    CREATE TABLE IF NOT EXISTS noticias_itens (
        id_dedup            TEXT PRIMARY KEY,
        hash_conteudo       TEXT NOT NULL,
        simhash             NUMERIC(20,0),
        titulo              TEXT NOT NULL,
        resumo              TEXT,
        url                 TEXT,
        url_canonica        TEXT,
        dominio             TEXT,
        veiculo             TEXT,
        classe_fonte        TEXT,
        confiabilidade_fonte NUMERIC(4,3),
        autor               TEXT,
        publicado_em        TIMESTAMPTZ,
        coletado_em         TIMESTAMPTZ NOT NULL,
        provedor            TEXT NOT NULL,
        idioma              TEXT,
        pais                TEXT,
        entidades           JSONB NOT NULL DEFAULT '{}'::jsonb,
        tipo_evento         TEXT NOT NULL,
        evento_id           TEXT,
        sentimento_api      NUMERIC(5,4),
        sentimento_app4     NUMERIC(5,4),
        rotulo_sentimento   TEXT,
        metodo_sentimento   TEXT,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS noticias_avaliacoes (
        id_dedup                TEXT NOT NULL,
        versao_metodologia      TEXT NOT NULL,
        nota                    NUMERIC(5,2) NOT NULL,
        faixa                   TEXT NOT NULL,
        cobertura               NUMERIC(5,4) NOT NULL,
        componentes             JSONB NOT NULL DEFAULT '{}'::jsonb,
        pesos                   JSONB NOT NULL DEFAULT '{}'::jsonb,
        direcao                 TEXT,
        probabilidade           NUMERIC(5,4),
        variacao_min            NUMERIC(8,3),
        variacao_max            NUMERIC(8,3),
        horizonte               TEXT,
        confianca               NUMERIC(5,4),
        n_observacoes           INTEGER,
        estado_verificacao      TEXT NOT NULL,
        n_fontes_independentes  INTEGER NOT NULL DEFAULT 1,
        confirmado_por_primaria BOOLEAN NOT NULL DEFAULT FALSE,
        limitacoes              JSONB NOT NULL DEFAULT '[]'::jsonb,
        avaliado_em             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (id_dedup, versao_metodologia)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_noticias_itens_publicado
    ON noticias_itens (publicado_em DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_noticias_itens_evento
    ON noticias_itens (evento_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_noticias_avaliacoes_nota
    ON noticias_avaliacoes (versao_metodologia, nota DESC)
    """,
]

_schema_pronto = False
_lock = threading.Lock()

_UPSERT_ITEM = text("""
    INSERT INTO noticias_itens (
        id_dedup, hash_conteudo, simhash, titulo, resumo, url, url_canonica,
        dominio, veiculo, classe_fonte, confiabilidade_fonte, autor,
        publicado_em, coletado_em, provedor, idioma, pais, entidades,
        tipo_evento, evento_id, sentimento_api, sentimento_app4,
        rotulo_sentimento, metodo_sentimento
    ) VALUES (
        :id_dedup, :hash_conteudo, :simhash, :titulo, :resumo, :url,
        :url_canonica, :dominio, :veiculo, :classe_fonte,
        :confiabilidade_fonte, :autor, :publicado_em, :coletado_em, :provedor,
        :idioma, :pais, CAST(:entidades AS JSONB), :tipo_evento, :evento_id,
        :sentimento_api, :sentimento_app4, :rotulo_sentimento,
        :metodo_sentimento
    )
    ON CONFLICT (id_dedup) DO UPDATE SET
        evento_id = EXCLUDED.evento_id,
        entidades = EXCLUDED.entidades,
        tipo_evento = EXCLUDED.tipo_evento,
        sentimento_app4 = EXCLUDED.sentimento_app4,
        metodo_sentimento = EXCLUDED.metodo_sentimento
""")

_UPSERT_AVALIACAO = text("""
    INSERT INTO noticias_avaliacoes (
        id_dedup, versao_metodologia, nota, faixa, cobertura, componentes,
        pesos, direcao, probabilidade, variacao_min, variacao_max, horizonte,
        confianca, n_observacoes, estado_verificacao, n_fontes_independentes,
        confirmado_por_primaria, limitacoes
    ) VALUES (
        :id_dedup, :versao_metodologia, :nota, :faixa, :cobertura,
        CAST(:componentes AS JSONB), CAST(:pesos AS JSONB), :direcao,
        :probabilidade, :variacao_min, :variacao_max, :horizonte, :confianca,
        :n_observacoes, :estado_verificacao, :n_fontes_independentes,
        :confirmado_por_primaria, CAST(:limitacoes AS JSONB)
    )
    ON CONFLICT (id_dedup, versao_metodologia) DO UPDATE SET
        nota = EXCLUDED.nota,
        faixa = EXCLUDED.faixa,
        cobertura = EXCLUDED.cobertura,
        componentes = EXCLUDED.componentes,
        pesos = EXCLUDED.pesos,
        direcao = EXCLUDED.direcao,
        probabilidade = EXCLUDED.probabilidade,
        variacao_min = EXCLUDED.variacao_min,
        variacao_max = EXCLUDED.variacao_max,
        horizonte = EXCLUDED.horizonte,
        confianca = EXCLUDED.confianca,
        n_observacoes = EXCLUDED.n_observacoes,
        estado_verificacao = EXCLUDED.estado_verificacao,
        n_fontes_independentes = EXCLUDED.n_fontes_independentes,
        confirmado_por_primaria = EXCLUDED.confirmado_por_primaria,
        limitacoes = EXCLUDED.limitacoes,
        avaliado_em = NOW()
""")


def garantir_schema(conn) -> None:
    """Cria as tabelas se faltarem. Idempotente e não destrutivo."""
    global _schema_pronto
    if _schema_pronto:
        return
    with _lock:
        if _schema_pronto:
            return
        for ddl in DDL_SQL:
            conn.execute(text(ddl))
        _schema_pronto = True


def linha_item(avaliada: NoticiaAvaliada, evento_id: str | None = None) -> dict:
    """Monta a linha do fato observado. Testável sem banco."""
    n = avaliada.noticia
    fonte = n.fonte
    ent = n.entidades
    return {
        "id_dedup": n.id_dedup,
        "hash_conteudo": n.hash_conteudo,
        "simhash": n.simhash,
        "titulo": n.titulo,
        "resumo": n.resumo,
        "url": n.url,
        "url_canonica": n.url_canonica,
        "dominio": fonte.dominio if fonte else None,
        "veiculo": fonte.veiculo if fonte else None,
        "classe_fonte": fonte.classe if fonte else None,
        "confiabilidade_fonte": fonte.confiabilidade if fonte else None,
        "autor": n.autor,
        "publicado_em": n.publicado_em,
        "coletado_em": n.coletado_em,
        "provedor": n.provedor,
        "idioma": n.idioma,
        "pais": n.pais,
        "entidades": json.dumps({
            "tickers": list(ent.tickers), "empresas": list(ent.empresas),
            "setores": list(ent.setores), "paises": list(ent.paises),
            "moedas": list(ent.moedas), "ativos": list(ent.ativos),
        }, ensure_ascii=False),
        "tipo_evento": n.tipo_evento,
        "evento_id": evento_id or n.evento_id,
        "sentimento_api": n.sentimento.valor_api,
        "sentimento_app4": n.sentimento.valor_app4,
        "rotulo_sentimento": n.sentimento.rotulo_api,
        "metodo_sentimento": n.sentimento.metodo_app4,
    }


def linha_avaliacao(avaliada: NoticiaAvaliada,
                    versao: str = VERSAO_METODOLOGIA) -> dict:
    """Monta a linha da conclusão. ``None`` permanece ``None`` em toda coluna."""
    rel = avaliada.relevancia
    imp = avaliada.impacto
    return {
        "id_dedup": avaliada.noticia.id_dedup,
        "versao_metodologia": versao,
        "nota": rel.nota,
        "faixa": rel.faixa,
        "cobertura": rel.cobertura,
        "componentes": json.dumps(rel.componentes, ensure_ascii=False),
        "pesos": json.dumps(rel.pesos, ensure_ascii=False),
        "direcao": imp.direcao,
        "probabilidade": imp.probabilidade,
        "variacao_min": imp.faixa.minimo if imp.faixa else None,
        "variacao_max": imp.faixa.maximo if imp.faixa else None,
        "horizonte": imp.horizonte,
        "confianca": imp.confianca,
        "n_observacoes": imp.n_observacoes,
        "estado_verificacao": avaliada.estado_verificacao,
        "n_fontes_independentes": avaliada.n_fontes_independentes,
        "confirmado_por_primaria": avaliada.confirmado_por_primaria,
        "limitacoes": json.dumps(
            list(rel.limitacoes) + list(imp.limitacoes), ensure_ascii=False),
    }


def gravar(resultado: ResultadoColeta, *, engine=None,
           versao: str = VERSAO_METODOLOGIA) -> dict:
    """Grava itens e avaliações. Sem banco configurado, não faz nada e diz isso.

    Idempotente: rodar duas vezes a mesma coleta não duplica linha nenhuma --
    a chave é o ``id_dedup``, que sai da URL canônica.

    **O destino é o armazém local, e não é preferência: é aritmética.** São
    ~22 MB por janela de 30 dias, acumulando, contra 71 MB de folga no
    Supabase. Por isso ``exigir_local`` roda antes de qualquer ``INSERT`` --
    um ``engine=`` distraído não pode ser suficiente para encher o banco de que
    a produção depende. Para a produção vai a vitrine, não o acervo.
    """
    motor = engine if engine is not None else engine_acervo()
    if motor is None:
        logger.info("Sem acervo local configurado: coleta mantida em memoria")
        return {"gravado": False, "motivo": "sem banco configurado",
                "itens": 0, "avaliacoes": 0}
    exigir_local(motor, o_que=O_QUE)

    # Mapa notícia -> evento, para a linha do fato registrar a que evento ela
    # pertence sem que o agrupamento precise ser refeito na leitura.
    evento_de: dict[str, str] = {}
    for evento in resultado.eventos:
        for noticia in evento.noticias:
            evento_de[noticia.id_dedup] = evento.id

    itens = 0
    avaliacoes = 0
    with motor.begin() as conn:
        garantir_schema(conn)
        for avaliada in resultado.avaliadas:
            conn.execute(_UPSERT_ITEM, linha_item(
                avaliada, evento_de.get(avaliada.noticia.id_dedup)))
            itens += 1
            conn.execute(_UPSERT_AVALIACAO, linha_avaliacao(avaliada, versao))
            avaliacoes += 1

    return {"gravado": True, "itens": itens, "avaliacoes": avaliacoes,
            "versao": versao}


_SELECT_RECENTES = text("""
    SELECT i.id_dedup, i.titulo, i.resumo, i.url, i.url_canonica, i.dominio,
           i.veiculo, i.confiabilidade_fonte, i.publicado_em, i.coletado_em,
           i.provedor, i.entidades, i.tipo_evento, i.evento_id,
           i.rotulo_sentimento,
           a.nota, a.faixa, a.cobertura, a.direcao, a.probabilidade,
           a.variacao_min, a.variacao_max, a.horizonte, a.confianca,
           a.estado_verificacao, a.n_fontes_independentes,
           a.confirmado_por_primaria, a.limitacoes, a.avaliado_em
      FROM noticias_itens i
      JOIN noticias_avaliacoes a
        ON a.id_dedup = i.id_dedup AND a.versao_metodologia = :versao
     WHERE COALESCE(i.publicado_em, i.coletado_em) >= :corte
     ORDER BY COALESCE(i.publicado_em, i.coletado_em) DESC
     LIMIT :limite
""")


def ler_recentes(limite: int = 50, *, dias: float = 7.0, engine=None,
                 versao: str = VERSAO_METODOLOGIA) -> tuple[dict, ...]:
    """Acervo recente já avaliado, para a tela abrir sem ter coletado nada.

    Existe porque a coleta e a exibição são processos diferentes. O job do cron
    grava; a sessão do Streamlit nasce depois e não presenciou nada. Sem esta
    leitura a tela diria "nenhuma coleta nesta sessão" com o acervo cheio --
    apresentando trabalho feito como trabalho ausente.

    O ``JOIN`` é pela versão de metodologia, e é restritivo de propósito: item
    avaliado sob outra versão não é comparável com estes e some da lista em vez
    de entrar sem nota. Subir ``VERSAO_METODOLOGIA`` sem reavaliar o acervo
    esvazia a tela, e isso é visível -- o contrário seria silencioso.
    """
    motor = engine if engine is not None else engine_acervo() or get_engine()
    if motor is None:
        return ()
    corte = datetime.now(timezone.utc) - timedelta(days=float(dias))
    try:
        # Sem ``garantir_schema``: ler não cria tabela. Criar no caminho de
        # leitura fazia duas coisas erradas de uma vez -- gastava espaço do
        # Supabase numa consulta, e transformava "as tabelas não existem" em
        # "não há notícias", que é o mesmo texto de um acervo legitimamente
        # vazio.
        with motor.connect() as conn:
            linhas = conn.execute(_SELECT_RECENTES, {
                "versao": versao, "corte": corte,
                "limite": int(limite)}).mappings().all()
    except Exception as exc:  # noqa: BLE001 - vira falha declarada, não vazio
        logger.warning("Acervo de noticias ilegivel: %s", exc)
        raise AcervoIlegivel(str(exc).splitlines()[0].strip()) from exc
    return tuple(dict(linha) for linha in linhas)
