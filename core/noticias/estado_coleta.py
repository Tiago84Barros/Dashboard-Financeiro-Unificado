"""Estado da coleta compartilhado entre processos -- no banco, não no disco.

Por que este módulo existe
--------------------------
``frescor_noticias.RegistroColeta`` guarda o carimbo da última coleta num JSON
local, e o docstring dele acerta o requisito: *"o job agendado, o script manual
e a sessão do Streamlit são processos diferentes que precisam enxergar o mesmo
carimbo"*. O meio é que não sobrevive a este deploy.

O APP4 roda em três lugares que **não compartilham disco**:

* Streamlit Cloud -- container efêmero, recriado a cada deploy;
* GitHub Actions -- runner descartado ao fim de cada execução;
* a máquina do desenvolvedor.

Três processos, três arquivos disjuntos. Na prática o job agendado nunca vê a
própria execução anterior (``precisa_coletar`` responde sempre "sim" e o freio
de cadência não freia nada), e a tela nunca vê a coleta do job. O que os três
alcançam é o Supabase. É onde o estado passa a morar.

O arquivo continua existindo e continua sendo escrito. Ele vira o degrau de
desenvolvimento: sem ``DATABASE_URL``, o motor inteiro funciona em memória e em
disco, como sempre funcionou nos testes.

Tudo em UTC
-----------
Carimbo é gravado e comparado em UTC, sem exceção. ``NOTICIAS_TIMEZONE`` só
existe para a apresentação. Converter na gravação já produziu neste projeto
série que muda de dia conforme o horário de verão de quem gravou.

Uma execução por vez
--------------------
``travar`` usa *advisory lock* do Postgres. Duas execuções simultâneas do mesmo
job -- o cron atrasado encontrando o disparo manual, por exemplo -- gastariam
cota em dobro e gravariam o mesmo fato duas vezes. A segunda sai declarando que
saiu, e isso vira um ciclo com status ``ignorado``, não um silêncio.
"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from core.database import get_engine
from core.noticias import cadencia as cad

logger = logging.getLogger(__name__)

CHAVE_GLOBAL = "global"
PREFIXO_PROVEDOR = "provedor:"

#: Nome do advisory lock. Fixo: dois nomes diferentes para o mesmo job seriam
#: dois locks, e nenhum dos dois protegeria nada.
LOCK_COLETA = "noticias_coleta"

DDL_SQL = [
    """
    CREATE TABLE IF NOT EXISTS noticias_coleta_estado (
        chave              TEXT PRIMARY KEY,
        modo               TEXT,
        status             TEXT,
        ultima_tentativa   TIMESTAMPTZ,
        ultimo_sucesso     TIMESTAMPTZ,
        proximo_ciclo_em   TIMESTAMPTZ,
        itens              INTEGER NOT NULL DEFAULT 0,
        ultimo_erro        TEXT,
        atualizado_em      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS noticias_coleta_ciclos (
        id                 BIGSERIAL PRIMARY KEY,
        modo               TEXT NOT NULL,
        origem             TEXT NOT NULL,
        forcado            BOOLEAN NOT NULL DEFAULT FALSE,
        iniciado_em        TIMESTAMPTZ NOT NULL,
        concluido_em       TIMESTAMPTZ,
        duracao_s          NUMERIC(10,3),
        status             TEXT NOT NULL,
        proximo_ciclo_em   TIMESTAMPTZ,
        provedores_ok      JSONB NOT NULL DEFAULT '[]'::jsonb,
        provedores_falha   JSONB NOT NULL DEFAULT '[]'::jsonb,
        coletadas          INTEGER NOT NULL DEFAULT 0,
        novas              INTEGER NOT NULL DEFAULT 0,
        duplicadas         INTEGER NOT NULL DEFAULT 0,
        eventos            INTEGER NOT NULL DEFAULT 0,
        erros              JSONB NOT NULL DEFAULT '[]'::jsonb,
        limitacoes         JSONB NOT NULL DEFAULT '[]'::jsonb
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_noticias_ciclos_inicio
        ON noticias_coleta_ciclos (iniciado_em DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS noticias_consumo_provedor (
        provedor       TEXT PRIMARY KEY,
        marcas         JSONB NOT NULL DEFAULT '[]'::jsonb,
        atualizado_em  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
]

_schema_pronto = False


@contextmanager
def _acervo(engine=None):
    """Cede a engine do acervo local -- **não** a do Supabase.

    O estado da coleta, os ciclos e o consumo por provedor moram no banco da
    produção; ``noticias_itens`` mora no armazém local, por aritmética de espaço
    (ver :mod:`core.noticias.destino`). Este módulo fala com os dois, e resolver
    os dois com ``get_engine`` fazia as consultas do acervo procurarem a tabela
    no único banco onde ela nunca vai existir.

    A engine do acervo não é cacheada -- quem a cria, descarta. Por isso o
    descarte mora aqui, e não em cada chamador.
    """
    if engine is not None:      # injetada (testes): quem passou é o dono
        yield engine
        return
    from core.noticias.destino import engine_acervo

    motor = engine_acervo()
    try:
        yield motor
    finally:
        if motor is not None:
            motor.dispose()


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _utc(valor) -> datetime | None:
    if valor is None:
        return None
    if isinstance(valor, str):
        try:
            valor = datetime.fromisoformat(valor)
        except ValueError:
            return None
    if not isinstance(valor, datetime):
        return None
    return (valor.replace(tzinfo=timezone.utc) if valor.tzinfo is None
            else valor.astimezone(timezone.utc))


def garantir_schema(conn) -> None:
    """Cria as tabelas de estado se faltarem. Idempotente."""
    global _schema_pronto
    if _schema_pronto:
        return
    for ddl in DDL_SQL:
        conn.execute(text(ddl))
    _schema_pronto = True


@contextmanager
def travar(engine=None, *, nome: str = LOCK_COLETA):
    """Exclusividade entre processos. Cede ``False`` quando já há execução.

    Sem banco não há como coordenar processos, e o módulo diz isso cedendo
    ``True``: em desenvolvimento existe um processo só, e travar seria fingir
    uma garantia que o ambiente não dá.
    """
    motor = engine if engine is not None else get_engine()
    if motor is None:
        yield True
        return

    conn = motor.connect()
    obtido = False
    try:
        obtido = bool(conn.execute(
            text("SELECT pg_try_advisory_lock(hashtextextended(:n, 0))"),
            {"n": nome}).scalar())
        yield obtido
    finally:
        try:
            if obtido:
                conn.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:n, 0))"),
                    {"n": nome})
                conn.commit()
        except Exception as exc:                       # pragma: no cover
            logger.warning("Falha ao soltar o lock de coleta: %s", exc)
        conn.close()


@dataclass
class Ciclo:
    """Uma execução do coletor, do início ao registro.

    Nasce no início e é preenchido conforme a execução anda. Um ciclo que
    morreu no meio ainda é gravado, com o erro -- silêncio de job é
    indistinguível de job que nunca rodou, e essa confusão já custou dias
    neste projeto.
    """

    modo: str
    origem: str = "job"
    forcado: bool = False
    iniciado_em: datetime = field(default_factory=_agora)
    concluido_em: datetime | None = None
    status: str = cad.STATUS_INDISPONIVEL
    proximo_ciclo_em: datetime | None = None
    provedores_ok: tuple[str, ...] = ()
    provedores_falha: tuple[str, ...] = ()
    coletadas: int = 0
    novas: int = 0
    duplicadas: int = 0
    eventos: int = 0
    erros: tuple[str, ...] = ()
    limitacoes: tuple[str, ...] = ()

    @property
    def duracao_s(self) -> float | None:
        if self.concluido_em is None:
            return None
        return (self.concluido_em - self.iniciado_em).total_seconds()

    def como_dict(self) -> dict:
        return {
            "modo": self.modo,
            "origem": self.origem,
            "forcado": self.forcado,
            "iniciado_em": self.iniciado_em.isoformat(),
            "concluido_em": (self.concluido_em.isoformat()
                             if self.concluido_em else None),
            "duracao_s": self.duracao_s,
            "status": self.status,
            "proximo_ciclo_em": (self.proximo_ciclo_em.isoformat()
                                 if self.proximo_ciclo_em else None),
            "provedores_ok": list(self.provedores_ok),
            "provedores_falha": list(self.provedores_falha),
            "coletadas": self.coletadas,
            "novas": self.novas,
            "duplicadas": self.duplicadas,
            "eventos": self.eventos,
            "erros": list(self.erros),
            "limitacoes": list(self.limitacoes),
        }


_INSERT_CICLO = text("""
    INSERT INTO noticias_coleta_ciclos (
        modo, origem, forcado, iniciado_em, concluido_em, duracao_s, status,
        proximo_ciclo_em, provedores_ok, provedores_falha, coletadas, novas,
        duplicadas, eventos, erros, limitacoes)
    VALUES (
        :modo, :origem, :forcado, :iniciado_em, :concluido_em, :duracao_s,
        :status, :proximo_ciclo_em, CAST(:provedores_ok AS jsonb),
        CAST(:provedores_falha AS jsonb), :coletadas, :novas, :duplicadas,
        :eventos, CAST(:erros AS jsonb), CAST(:limitacoes AS jsonb))
    RETURNING id
""")

_UPSERT_ESTADO = text("""
    INSERT INTO noticias_coleta_estado (
        chave, modo, status, ultima_tentativa, ultimo_sucesso,
        proximo_ciclo_em, itens, ultimo_erro, atualizado_em)
    VALUES (:chave, :modo, :status, :ultima_tentativa, :ultimo_sucesso,
            :proximo_ciclo_em, :itens, :ultimo_erro, NOW())
    ON CONFLICT (chave) DO UPDATE SET
        modo = EXCLUDED.modo,
        status = EXCLUDED.status,
        ultima_tentativa = EXCLUDED.ultima_tentativa,
        -- Falha NUNCA avança o último sucesso. É a linha que impede o painel
        -- de dizer "atualizado agora" depois de uma coleta que não trouxe nada.
        ultimo_sucesso = COALESCE(EXCLUDED.ultimo_sucesso,
                                  noticias_coleta_estado.ultimo_sucesso),
        proximo_ciclo_em = EXCLUDED.proximo_ciclo_em,
        itens = EXCLUDED.itens,
        ultimo_erro = EXCLUDED.ultimo_erro,
        atualizado_em = NOW()
""")


def registrar(ciclo: Ciclo, *, engine=None,
              sucesso_em: datetime | None = None) -> dict:
    """Grava o ciclo e atualiza o ponteiro global. Sem banco, não faz nada."""
    motor = engine if engine is not None else get_engine()
    if motor is None:
        logger.info("Sem DATABASE_URL: ciclo de coleta nao persistido")
        return {"gravado": False, "motivo": "sem banco configurado"}

    dados = ciclo.como_dict()
    for chave in ("provedores_ok", "provedores_falha", "erros", "limitacoes"):
        dados[chave] = json.dumps(dados[chave], ensure_ascii=False)

    with motor.begin() as conn:
        garantir_schema(conn)
        ciclo_id = conn.execute(_INSERT_CICLO, dados).scalar()
        conn.execute(_UPSERT_ESTADO, {
            "chave": CHAVE_GLOBAL,
            "modo": ciclo.modo,
            "status": ciclo.status,
            "ultima_tentativa": ciclo.iniciado_em,
            "ultimo_sucesso": sucesso_em,
            "proximo_ciclo_em": ciclo.proximo_ciclo_em,
            "itens": ciclo.coletadas,
            "ultimo_erro": (ciclo.erros[0][:500] if ciclo.erros else None),
        })
        for provedor in ciclo.provedores_ok:
            conn.execute(_UPSERT_ESTADO, {
                "chave": f"{PREFIXO_PROVEDOR}{provedor}",
                "modo": ciclo.modo, "status": cad.STATUS_ATUALIZADO,
                "ultima_tentativa": ciclo.iniciado_em,
                "ultimo_sucesso": sucesso_em or ciclo.concluido_em,
                "proximo_ciclo_em": ciclo.proximo_ciclo_em,
                "itens": ciclo.coletadas, "ultimo_erro": None})
        for provedor in ciclo.provedores_falha:
            conn.execute(_UPSERT_ESTADO, {
                "chave": f"{PREFIXO_PROVEDOR}{provedor}",
                "modo": ciclo.modo, "status": cad.STATUS_INDISPONIVEL,
                "ultima_tentativa": ciclo.iniciado_em,
                "ultimo_sucesso": None,
                "proximo_ciclo_em": ciclo.proximo_ciclo_em,
                "itens": 0,
                "ultimo_erro": next((e for e in ciclo.erros if provedor in e),
                                    "falha nao detalhada")[:500]})

    return {"gravado": True, "ciclo_id": ciclo_id}


_ATUALIZAR_MODO = text("""
    INSERT INTO noticias_coleta_estado (chave, modo, status, atualizado_em)
    VALUES (:chave, :modo, :status, NOW())
    ON CONFLICT (chave) DO UPDATE SET modo = EXCLUDED.modo, atualizado_em = NOW()
""")


def definir_modo(nivel: int | None, *, engine=None,
                 chave: str = CHAVE_GLOBAL) -> dict:
    """Registra o modo que o nível de crise impõe ao próximo ciclo.

    Quem chama é o motor de eventos extremos, e só ele: o modo é consequência
    do nível avaliado, e deixar a coleta escolher o próprio ritmo criaria um
    segundo juiz de crise (ver o docstring de ``cadencia``). Concretamente, a
    chamada vem de ``update_noticias`` via
    :func:`core.eventos_extremos.da_coleta.nivel_para_cadencia` -- o job carrega
    a evidência, mas quem decide o nível continua sendo ``transicao``.

    Por dois anos esta função não teve chamador nenhum, e o efeito não era um
    erro: era silêncio. O banco guardava ``normal`` desde sempre e a coleta
    seguia em ritmo de dia calmo justamente no dia em que ele deixasse de ser.

    Escreve apenas ``modo``. Nenhum carimbo é tocado -- mudar de modo não é
    coletar, e avançar ``ultima_tentativa`` aqui faria o próximo ciclo pular.
    """
    modo = cad.modo_para_nivel(nivel)
    motor = engine if engine is not None else get_engine()
    if motor is None:
        return {"gravado": False, "modo": modo, "motivo": "sem banco"}
    try:
        with motor.begin() as conn:
            garantir_schema(conn)
            conn.execute(_ATUALIZAR_MODO, {
                "chave": chave, "modo": modo,
                "status": cad.STATUS_INDISPONIVEL})
    except Exception as exc:  # noqa: BLE001
        logger.warning("Modo de coleta nao gravado: %s", exc)
        return {"gravado": False, "modo": modo, "motivo": str(exc)[:200]}
    return {"gravado": True, "modo": modo}



class ConsumoBanco:
    """Consumo de cota por provedor, no banco em vez de no disco do runner.

    Sem isto o freio de requisições não freia em produção. ``Orcamento`` guarda
    as marcas de chamada num JSON sob ``local_staging/``, e esse arquivo é
    estado de máquina: o runner do GitHub Actions nasce com disco vazio a cada
    execução. Com o cron de meia em meia hora, cada execução se veria com as 25
    chamadas diárias do Alpha Vantage inteiras -- 48 orçamentos completos por
    dia contra um teto de um. O teto só passa a existir quando o contador mora
    onde os três processos o alcançam.

    Concorrência é do lock da coleta: quem escreve aqui está sob
    ``travar()``. Uma leitura-modificação-escrita sem esse lock poderia perder
    marcas, e perder marca é afrouxar o teto -- por isso o objeto não é para uso
    fora do ciclo.
    """

    def __init__(self, engine=None):
        self._engine = engine

    def _motor(self):
        return self._engine if self._engine is not None else get_engine()

    def disponivel(self) -> bool:
        """Há banco para compartilhar o contador? Sem ele, o arquivo é melhor
        que nada -- e melhor que um armazém que não guarda."""
        return self._motor() is not None

    def carregar(self) -> dict | None:
        """Marcas por provedor. ``None`` quando não deu para ler.

        ``None`` e ``{}`` são coisas diferentes: dicionário vazio afirma que
        ninguém consumiu nada, e afirmar isso sem ter lido liberaria a cota
        inteira. Quem recebe ``None`` mantém o que já tinha.
        """
        motor = self._motor()
        if motor is None:
            return None
        try:
            with motor.begin() as conn:
                garantir_schema(conn)
                linhas = conn.execute(text(
                    "SELECT provedor, marcas FROM noticias_consumo_provedor"
                )).fetchall()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Consumo de cota ilegivel (%s)", exc)
            return None
        saida: dict[str, list[float]] = {}
        for linha in linhas:
            marcas = linha[1]
            if isinstance(marcas, str):
                try:
                    marcas = json.loads(marcas)
                except ValueError:
                    marcas = []
            saida[str(linha[0])] = [float(m) for m in (marcas or [])
                                    if isinstance(m, (int, float))]
        return saida

    def salvar(self, registros: dict) -> None:
        motor = self._motor()
        if motor is None:
            return
        try:
            with motor.begin() as conn:
                garantir_schema(conn)
                for provedor, marcas in (registros or {}).items():
                    conn.execute(_UPSERT_CONSUMO, {
                        "provedor": str(provedor),
                        "marcas": json.dumps([float(m) for m in marcas]),
                    })
        except Exception as exc:  # noqa: BLE001
            # Não derruba a coleta: o orçamento em memória continua valendo
            # dentro desta execução. O que se perde é a memória entre execuções,
            # e isso vira limitação declarada pelo job.
            logger.warning("Consumo de cota nao gravado (%s)", exc)


_UPSERT_CONSUMO = text("""
    INSERT INTO noticias_consumo_provedor (provedor, marcas, atualizado_em)
    VALUES (:provedor, CAST(:marcas AS jsonb), NOW())
    ON CONFLICT (provedor) DO UPDATE SET
        marcas = EXCLUDED.marcas, atualizado_em = NOW()
""")


@dataclass(frozen=True)
class EstadoGlobal:
    """O que a tela e o próximo ciclo precisam saber, já em UTC."""

    modo: str = cad.MODO_NORMAL
    status: str = cad.STATUS_INDISPONIVEL
    ultima_tentativa: datetime | None = None
    ultimo_sucesso: datetime | None = None
    proximo_ciclo_em: datetime | None = None
    itens: int = 0
    ultimo_erro: str | None = None
    disponivel: bool = True

    @property
    def nunca_coletou(self) -> bool:
        return self.ultimo_sucesso is None

    def idade_min(self, *, agora: datetime | None = None) -> float | None:
        """Idade do último sucesso. ``None`` quando nunca houve sucesso --
        nunca ``0``: não saber há quanto tempo não é o mesmo que ser agora."""
        if self.ultimo_sucesso is None:
            return None
        agora = agora or _agora()
        return (agora - self.ultimo_sucesso).total_seconds() / 60.0


def ler(*, engine=None, chave: str = CHAVE_GLOBAL) -> EstadoGlobal:
    """Lê o estado compartilhado. Banco fora do ar devolve estado indisponível.

    Não levanta: a tela não pode ficar em branco porque o Supabase engasgou, e
    o job não pode abortar antes de tentar coletar. O que ela **não** faz é
    inventar um carimbo -- ``disponivel=False`` diz que o estado é desconhecido,
    e quem lê distingue isso de "nunca coletou".
    """
    motor = engine if engine is not None else get_engine()
    if motor is None:
        return EstadoGlobal(disponivel=False,
                            ultimo_erro="sem banco configurado")
    try:
        with motor.begin() as conn:
            garantir_schema(conn)
            linha = conn.execute(text("""
                SELECT modo, status, ultima_tentativa, ultimo_sucesso,
                       proximo_ciclo_em, itens, ultimo_erro
                  FROM noticias_coleta_estado WHERE chave = :c
            """), {"c": chave}).mappings().first()
    except Exception as exc:
        logger.warning("Estado de coleta ilegivel: %s", exc)
        return EstadoGlobal(disponivel=False, ultimo_erro=str(exc)[:500])

    if linha is None:
        return EstadoGlobal(status=cad.STATUS_INDISPONIVEL)
    return EstadoGlobal(
        modo=linha["modo"] or cad.MODO_NORMAL,
        status=linha["status"] or cad.STATUS_INDISPONIVEL,
        ultima_tentativa=_utc(linha["ultima_tentativa"]),
        ultimo_sucesso=_utc(linha["ultimo_sucesso"]),
        proximo_ciclo_em=_utc(linha["proximo_ciclo_em"]),
        itens=int(linha["itens"] or 0),
        ultimo_erro=linha["ultimo_erro"],
    )


def ultimos_ciclos(limite: int = 10, *, engine=None) -> tuple[dict, ...]:
    """Histórico recente, para a tela de saúde e para a homologação."""
    motor = engine if engine is not None else get_engine()
    if motor is None:
        return ()
    try:
        with motor.begin() as conn:
            garantir_schema(conn)
            linhas = conn.execute(text("""
                SELECT modo, origem, forcado, iniciado_em, concluido_em,
                       duracao_s, status, coletadas, novas, duplicadas,
                       eventos, provedores_ok, provedores_falha, erros
                  FROM noticias_coleta_ciclos
                 ORDER BY iniciado_em DESC LIMIT :n
            """), {"n": int(limite)}).mappings().all()
    except Exception as exc:
        logger.warning("Historico de ciclos ilegivel: %s", exc)
        return ()
    return tuple(dict(linha) for linha in linhas)


def contar_novas(ids: list[str], *, engine=None) -> int | None:
    """Quantas das notícias trazidas ainda não estavam no acervo.

    ``None`` quando não dá para saber -- sem banco, ou com a consulta falhando.
    Devolver ``len(ids)`` nesse caso apresentaria toda coleta como se fosse
    inteiramente nova, que é o número mais bonito e o mais errado.
    """
    if not ids:
        return 0
    # ``noticias_itens`` mora no armazém local; o estado da coleta e os ciclos
    # moram no Supabase. Resolver os dois com ``get_engine`` fazia esta consulta
    # procurar o acervo no banco que nunca vai tê-lo, e a resposta era sempre
    # ``None`` -- ausência de medição com cara de banco fora do ar.
    with _acervo(engine) as motor:
        if motor is None:
            return None
        try:
            with motor.begin() as conn:
                existentes = conn.execute(text("""
                    SELECT COUNT(*) FROM noticias_itens
                     WHERE id_dedup = ANY(:ids)
                """), {"ids": list(ids)}).scalar() or 0
        except Exception as exc:
            logger.warning("Contagem de novas indisponivel: %s", exc)
            return None
    return max(0, len(ids) - int(existentes))


def expurgar(*, dias: int | None = None, engine=None,
             engine_acervo=None) -> dict:
    """Apaga acervo e ciclos além da retenção. É o freio do crescimento.

    Notícia sai por **data de publicação**, não por data de coleta: uma matéria
    de 2019 recoletada hoje não vira acervo recente. O ciclo sai por data de
    execução, que é o que ele mede.

    São dois bancos e duas transações, e isso não é detalhe de implementação.
    Os ciclos moram no Supabase; o acervo, no armazém local. Enquanto os dois
    ``DELETE`` compartilhavam uma transação só, o do acervo falhava (tabela
    inexistente naquele host), a exceção era engolida com ``itens = 0`` e o
    freio do crescimento ficava desligado sem nunca reclamar -- exatamente o
    modo de falha que o cálculo de espaço do acervo pressupõe que não existe.

    ``itens`` e ``ciclos`` vêm ``None`` quando o expurgo correspondente não
    pôde ser feito. ``0`` significa "varri e não havia o que apagar", que é uma
    afirmação diferente.
    """
    if dias is None:
        from core.config import settings
        dias = settings.noticias_retencao_dias
    corte = _agora() - timedelta(days=int(dias))

    ciclos: int | None = None
    motivos: list[str] = []
    motor = engine if engine is not None else get_engine()
    if motor is None:
        motivos.append("ciclos: sem banco configurado")
    else:
        try:
            with motor.begin() as conn:
                garantir_schema(conn)
                ciclos = conn.execute(text(
                    "DELETE FROM noticias_coleta_ciclos WHERE iniciado_em < :c"
                ), {"c": corte}).rowcount
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ciclos nao expurgados: %s", exc)
            motivos.append(f"ciclos: {str(exc).splitlines()[0][:200]}")

    itens: int | None = None
    with _acervo(engine_acervo) as motor_acervo:
        if motor_acervo is None:
            motivos.append("itens: acervo local nao configurado")
        else:
            try:
                with motor_acervo.begin() as conn:
                    itens = conn.execute(text(
                        "DELETE FROM noticias_itens WHERE publicado_em < :c"
                    ), {"c": corte}).rowcount
            except Exception as exc:  # noqa: BLE001
                logger.warning("Acervo nao expurgado: %s", exc)
                motivos.append(f"itens: {str(exc).splitlines()[0][:200]}")

    return {"expurgado": not motivos, "corte": corte.isoformat(),
            "ciclos": (None if ciclos is None else int(ciclos)),
            "itens": (None if itens is None else int(itens)),
            "motivo": "; ".join(motivos)}
