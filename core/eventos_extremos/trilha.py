"""A trilha de auditoria das transições de nível -- o "por quê" do Nível 3.

Por que este módulo existe
--------------------------
O Prompt 4 pede que *"todas as decisões automáticas sejam auditáveis"*. Metade
disso já existia: a decisão sobre **notícia** é persistida em
``noticias_avaliacoes.acao`` e ``.portoes``. A outra metade não existia.

``transicao.avaliar`` produz um :class:`~core.eventos_extremos.transicao.Veredito`
inteiro -- nível bruto, nível final, severidade, confiança, cobertura por classe
de evidência e a lista de :class:`~core.eventos_extremos.transicao.RegraAplicada`
com o motivo de cada teto e cada piso. Desse veredito, só o número do modo
chegava ao banco, via ``estado_coleta.definir_modo``. Tudo que **explica** o
número morria com o processo.

O efeito não é um erro: é a impossibilidade de responder "por que estamos no
Nível 3?" depois que o job termina. Um motor que decide e não guarda a
justificativa é auditável só enquanto o processo está vivo -- que é o mesmo que
não ser.

Onde a trilha mora, e o que isso custa
--------------------------------------
No **armazém local**, com ``exigir_local`` antes de qualquer ``INSERT``, pelo
mesmo motivo que o acervo de notícias: a instrução do usuário é literal e o
Supabase estava em 477 MB de 500 em 05/09/2026.

A consequência é declarada, e não escondida: a produção continua vendo apenas
``estado.modo``. A justificativa é consultável de onde o job roda, não da
Streamlit Cloud. O volume seria pequeno (~1 KB por ciclo), mas "pequeno" foi o
argumento que encheu o Supabase da primeira vez, e a folga de 23 MB não comporta
mais um "pequeno" indefinido.

Idempotência
------------
A chave natural é ``(ciclo_em, versao_metodologia)`` -- carimbo do ciclo, não
número de sequência. Sequência que reinicia já colidiu neste projeto e fez um
portão declarar "coberto" lendo procedência de outro payload. A versão entra na
chave pelo mesmo motivo que entra na das avaliações: um Nível 3 sob limiares
antigos e outro sob os novos não são o mesmo fato.

Avaliação sem ciclo (chamada manual) grava ``ciclo_em`` nulo e **não** deduplica
-- em Postgres, ``NULL`` não colide com ``NULL`` no índice único. Está certo:
duas apurações manuais são dois fatos, e não uma repetida.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading

from sqlalchemy import text

from core.destino_local import exigir_local
from core.noticias.destino import engine_acervo

logger = logging.getLogger(__name__)

#: Descrição usada nas mensagens de recusa de destino remoto.
O_QUE = "a trilha de transições de nível"

DDL_SQL = [
    """
    CREATE TABLE IF NOT EXISTS eventos_extremos_trilha (
        id                  BIGSERIAL PRIMARY KEY,
        ciclo_em            TIMESTAMPTZ,
        avaliado_em         TIMESTAMPTZ NOT NULL,
        versao_metodologia  TEXT NOT NULL,
        nivel_anterior      SMALLINT,
        nivel               SMALLINT NOT NULL,
        nivel_bruto         SMALLINT NOT NULL,
        teto_aplicado       SMALLINT,
        severidade          NUMERIC(6,4),
        confianca           NUMERIC(6,4),
        severidade_evento   NUMERIC(6,4),
        severidade_carteira NUMERIC(6,4),
        abrangencia         TEXT,
        evento_id           TEXT,
        notificar           BOOLEAN NOT NULL DEFAULT FALSE,
        cobertura           JSONB,
        regras              JSONB,
        limitacoes          JSONB
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ix_trilha_ciclo_versao
        ON eventos_extremos_trilha (ciclo_em, versao_metodologia)
     WHERE ciclo_em IS NOT NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_trilha_avaliado_em
        ON eventos_extremos_trilha (avaliado_em DESC)
    """,
]

_lock = threading.Lock()
_schema_pronto: set[object] = set()


def _chave_destino(conn) -> object:
    return id(getattr(conn, "engine", None) or conn)


def garantir_schema(conn) -> None:
    """Cria a tabela se faltar. Idempotente e não destrutivo."""
    chave = _chave_destino(conn)
    if chave in _schema_pronto:
        return
    with _lock:
        if chave in _schema_pronto:
            return
        for ddl in DDL_SQL:
            conn.execute(text(ddl))
        _schema_pronto.add(chave)


_UPSERT = text("""
    INSERT INTO eventos_extremos_trilha (
        ciclo_em, avaliado_em, versao_metodologia, nivel_anterior, nivel,
        nivel_bruto, teto_aplicado, severidade, confianca, severidade_evento,
        severidade_carteira, abrangencia, evento_id, notificar, cobertura,
        regras, limitacoes)
    VALUES (
        :ciclo_em, :avaliado_em, :versao_metodologia, :nivel_anterior, :nivel,
        :nivel_bruto, :teto_aplicado, :severidade, :confianca,
        :severidade_evento, :severidade_carteira, :abrangencia, :evento_id,
        :notificar, CAST(:cobertura AS JSONB), CAST(:regras AS JSONB),
        CAST(:limitacoes AS JSONB))
    ON CONFLICT (ciclo_em, versao_metodologia) WHERE ciclo_em IS NOT NULL
    DO UPDATE SET
        avaliado_em         = EXCLUDED.avaliado_em,
        nivel_anterior      = EXCLUDED.nivel_anterior,
        nivel               = EXCLUDED.nivel,
        nivel_bruto         = EXCLUDED.nivel_bruto,
        teto_aplicado       = EXCLUDED.teto_aplicado,
        severidade          = EXCLUDED.severidade,
        confianca           = EXCLUDED.confianca,
        severidade_evento   = EXCLUDED.severidade_evento,
        severidade_carteira = EXCLUDED.severidade_carteira,
        abrangencia         = EXCLUDED.abrangencia,
        evento_id           = EXCLUDED.evento_id,
        notificar           = EXCLUDED.notificar,
        cobertura           = EXCLUDED.cobertura,
        regras              = EXCLUDED.regras,
        limitacoes          = EXCLUDED.limitacoes
""")


def linha(veredito, *, ciclo_em: dt.datetime | None = None,
          agora: dt.datetime | None = None) -> dict:
    """Traduz o veredito para a linha da trilha.

    Cada regra é gravada com ``chave``, ``efeito``, ``motivo``, ``de`` e
    ``para`` -- e não só com o texto de ``RegraAplicada.descrever()``. Texto
    formatado responde "o que apareceu na tela"; os campos respondem "qual
    regra, com que efeito, movendo de quanto para quanto", que é a pergunta da
    auditoria.
    """
    estado = veredito.estado
    anterior = veredito.anterior
    regras = [{"chave": r.chave, "efeito": r.efeito, "motivo": r.motivo,
               "de": r.de, "para": r.para} for r in veredito.regras]
    return {
        "ciclo_em": ciclo_em,
        "avaliado_em": (estado.atualizado_em or agora
                        or dt.datetime.now(dt.timezone.utc)),
        "versao_metodologia": estado.versao_metodologia,
        "nivel_anterior": None if anterior is None else int(anterior.nivel),
        "nivel": int(veredito.nivel.codigo),
        "nivel_bruto": int(veredito.nivel_bruto),
        "teto_aplicado": int(veredito.teto_aplicado),
        "severidade": float(veredito.severidade),
        "confianca": float(veredito.confianca),
        "severidade_evento": veredito.severidade_evento,
        "severidade_carteira": veredito.severidade_carteira,
        "abrangencia": veredito.abrangencia,
        "evento_id": estado.evento_id,
        "notificar": bool(veredito.notificar),
        "cobertura": json.dumps(dict(veredito.cobertura or {}),
                                ensure_ascii=False),
        "regras": json.dumps(regras, ensure_ascii=False),
        "limitacoes": json.dumps(list(veredito.limitacoes or ()),
                                 ensure_ascii=False),
    }


def registrar(veredito, *, engine=None, ciclo_em: dt.datetime | None = None,
              agora: dt.datetime | None = None) -> dict:
    """Persiste a justificativa da transição. Nunca levanta.

    Sem armazém configurado devolve ``{"gravado": False, "motivo": ...}`` --
    ausência declarada, e não exceção: uma trilha indisponível não pode derrubar
    a coleta que ela documenta. Ela também não pode *calar*, então o caminho de
    falha registra em log e devolve o motivo a quem chamou.

    **Resolver o destino já é falível**, e a primeira versão deste módulo o
    fazia fora do ``try``: ``engine_acervo()`` lê ``settings``, e uma
    configuração incompleta subia ``AttributeError`` pelo job inteiro. A
    promessa "nunca levanta" só vale se ela cobrir a linha que descobre para
    onde escrever.
    """
    if veredito is None:
        return {"gravado": False, "motivo": "sem veredito para registrar"}

    try:
        motor = engine if engine is not None else engine_acervo()
    except Exception as exc:  # noqa: BLE001 - resolver o destino já é falível
        return {"gravado": False,
                "motivo": f"armazem local nao resolvido ({exc}): a "
                          f"justificativa da transicao nao foi persistida"}
    if motor is None:
        return {"gravado": False,
                "motivo": "armazem local nao configurado: a justificativa da "
                          "transicao nao foi persistida"}
    try:
        exigir_local(motor, o_que=O_QUE)
        with motor.begin() as conn:
            garantir_schema(conn)
            conn.execute(_UPSERT, linha(veredito, ciclo_em=ciclo_em,
                                        agora=agora))
    except Exception as exc:  # noqa: BLE001 - a coleta não cai por causa disto
        causa = str(exc).splitlines()[0][:160]
        logger.warning("Trilha de transicao nao gravada: %s", causa)
        return {"gravado": False, "motivo": f"trilha nao gravada ({causa})"}
    return {"gravado": True, "nivel": int(veredito.nivel.codigo),
            "regras": len(veredito.regras)}


_SELECT_ULTIMAS = text("""
    SELECT ciclo_em, avaliado_em, versao_metodologia, nivel_anterior, nivel,
           nivel_bruto, teto_aplicado, severidade, confianca, abrangencia,
           evento_id, notificar, cobertura, regras, limitacoes
      FROM eventos_extremos_trilha
     ORDER BY avaliado_em DESC
     LIMIT :limite
""")


def ultimas(limite: int = 20, *, engine=None
            ) -> tuple[tuple[dict, ...], tuple[str, ...]]:
    """As últimas transições, mais recente primeiro, e as limitações da leitura.

    Devolve ``((), (motivo,))`` quando não pôde ler -- nunca ``((), ())``, que
    quem lê entenderia como "nenhuma transição aconteceu". Trilha ilegível e
    trilha vazia são estados diferentes do mundo.
    """
    try:
        motor = engine if engine is not None else engine_acervo()
    except Exception as exc:  # noqa: BLE001
        return (), (f"armazem local nao resolvido ({exc}): a trilha de "
                    f"transicoes nao pode ser lida",)
    if motor is None:
        return (), ("armazem local nao configurado: a trilha de transicoes "
                    "nao pode ser lida",)
    try:
        with motor.begin() as conn:
            garantir_schema(conn)
            linhas = conn.execute(_SELECT_ULTIMAS,
                                  {"limite": int(limite)}).mappings().all()
    except Exception as exc:  # noqa: BLE001
        causa = str(exc).splitlines()[0][:160]
        logger.warning("Trilha de transicao nao lida: %s", causa)
        return (), (f"trilha de transicoes ilegivel ({causa})",)
    return tuple(dict(linha_lida) for linha_lida in linhas), ()
