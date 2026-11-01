"""
core/medicao_rota.py — quanto tempo cada execução do app leva, e onde.

Por que existe: em 01/11/2026 o dono do app relatou que a troca de página
demora, e a primeira ideia foi trocar o Streamlit por outro frontend. Antes de
qualquer decisão desse tamanho, é preciso saber ONDE o tempo vai. Este módulo
divide cada execução do script em três fases e conta, em cada uma, quanto foi
consulta ao banco:

* ``preambulo`` — do topo do ``app.py`` até a rota: tema, autenticação, menu;
* ``import``    — importar o módulo da tela (só pesa na primeira visita);
* ``render``    — o ``render()`` da tela: consultas, cálculo e montagem.

O que sobra de ``render`` depois de tirar o banco é Python puro, rede fora do
banco (APIs de cotação, por exemplo) e a montagem dos elementos.

Limites, ditos para ninguém tirar do número mais do que ele mede:

* É tempo do SERVIDOR. A ida do resultado até o navegador e o desenho da tela
  ficam de fora.
* Consulta feita em outra thread (``ThreadPoolExecutor``) não é contada: o
  cronômetro vive no contexto da thread que roda o script.
* Consulta servida pelo ``st.cache_data`` não chega ao banco e, corretamente,
  não aparece -- é exatamente a economia que o cache existe para fazer.

Nada daqui guarda parâmetro de consulta, só o começo do texto SQL, que é
código deste repositório e não carrega dado de usuário.
"""
from __future__ import annotations

import contextvars
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, MutableMapping

FASES: tuple[str, ...] = ("preambulo", "import", "render")

# Tipos de execução, do ponto de vista de quem usa o app.
ABERTURA = "abertura"  # primeira execução da sessão
TROCA = "troca"  # a rota mudou desde a execução anterior
INTERACAO = "interacao"  # mesma rota: um clique, filtro ou botão

_TAMANHO_TRECHO_SQL = 90

_logger = logging.getLogger("app4.medicao")
# O Streamlit só configura os loggers "streamlit.*"; o raiz fica em WARNING e
# engoliria o INFO. Handler próprio, sem propagar, para a linha aparecer no log
# da nuvem sem precisar subir o nível para WARNING.
if not _logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s"))
    _logger.addHandler(_handler)
    _logger.setLevel(logging.INFO)
    _logger.propagate = False


@dataclass
class Fase:
    nome: str
    ms: float = 0.0
    sql_n: int = 0
    sql_ms: float = 0.0


@dataclass
class Cronometro:
    """Cronômetro de UMA execução do script."""

    inicio: float
    relogio: Callable[[], float] = time.perf_counter
    fases: dict[str, Fase] = field(default_factory=lambda: {n: Fase(n) for n in FASES})
    fase_atual: str | None = "preambulo"
    _marco: float = 0.0
    sql_mais_lenta_ms: float = 0.0
    sql_mais_lenta: str = ""

    def __post_init__(self) -> None:
        self._marco = self.inicio

    def entrar(self, fase: str) -> None:
        """Fecha a fase corrente e abre ``fase``."""
        if fase not in self.fases:
            raise ValueError(f"fase desconhecida: {fase}")
        self._fechar()
        self.fase_atual = fase

    def encerrar(self) -> None:
        self._fechar()
        self.fase_atual = None

    def _fechar(self) -> None:
        agora = self.relogio()
        if self.fase_atual is not None:
            self.fases[self.fase_atual].ms += (agora - self._marco) * 1000.0
        self._marco = agora

    def registrar_sql(self, ms: float, sql: str) -> None:
        # Consulta depois do encerramento (o próprio painel, por exemplo) não
        # pertence a fase nenhuma e não distorce o que já foi medido.
        if self.fase_atual is None:
            return
        fase = self.fases[self.fase_atual]
        fase.sql_n += 1
        fase.sql_ms += ms
        if ms > self.sql_mais_lenta_ms:
            self.sql_mais_lenta_ms = ms
            self.sql_mais_lenta = trecho_sql(sql)

    @property
    def total_ms(self) -> float:
        return sum(f.ms for f in self.fases.values())


def trecho_sql(sql: str) -> str:
    """Começo do SQL numa linha só: identifica a consulta sem despejar o texto."""
    uma_linha = " ".join(str(sql).split())
    if len(uma_linha) <= _TAMANHO_TRECHO_SQL:
        return uma_linha
    return uma_linha[: _TAMANHO_TRECHO_SQL - 1] + "…"


def classificar(rota: str, rota_anterior: str | None) -> str:
    if rota_anterior is None:
        return ABERTURA
    return INTERACAO if rota == rota_anterior else TROCA


def resumo(
    cron: Cronometro,
    *,
    rota: str,
    tipo: str,
    import_frio: bool,
    concluiu: bool,
) -> dict:
    """Uma linha plana, pronta para log, tabela e CSV."""
    f = cron.fases
    sql_ms = sum(x.sql_ms for x in f.values())
    return {
        "quando": time.strftime("%H:%M:%S"),
        "rota": rota,
        "tipo": tipo,
        "concluiu": concluiu,
        "total_ms": round(cron.total_ms),
        "preambulo_ms": round(f["preambulo"].ms),
        "import_ms": round(f["import"].ms),
        "import_frio": import_frio,
        "render_ms": round(f["render"].ms),
        "sql_n": sum(x.sql_n for x in f.values()),
        "sql_ms": round(sql_ms),
        "sql_preambulo_ms": round(f["preambulo"].sql_ms),
        "sql_render_ms": round(f["render"].sql_ms),
        "fora_do_banco_ms": round(cron.total_ms - sql_ms),
        "sql_mais_lenta_ms": round(cron.sql_mais_lenta_ms),
        "sql_mais_lenta": cron.sql_mais_lenta,
    }


def registrar_no_log(linha: dict) -> None:
    _logger.info("medicao_rota %s", json.dumps(linha, ensure_ascii=False))


# ── Histórico da sessão ──────────────────────────────────────────────────────
CHAVE_HISTORICO = "_medicao_rota_historico"
CHAVE_ULTIMA_ROTA = "_medicao_rota_ultima"
LIMITE_HISTORICO = 60


def anexar(historico: list[dict] | None, linha: dict, limite: int = LIMITE_HISTORICO) -> list[dict]:
    """Lista nova (o session_state não vê mutação in place), cortada no limite."""
    return [*(historico or []), linha][-limite:]


def fechar_execucao(
    cron: Cronometro,
    estado: MutableMapping | None,
    *,
    rota: str,
    import_frio: bool,
    concluiu: bool,
) -> dict:
    """Encerra o cronômetro, escreve o log e guarda a linha no histórico."""
    cron.encerrar()
    desativar()
    anterior = estado.get(CHAVE_ULTIMA_ROTA) if estado is not None else None
    linha = resumo(
        cron,
        rota=rota,
        tipo=classificar(rota, anterior),
        import_frio=import_frio,
        concluiu=concluiu,
    )
    registrar_no_log(linha)
    if estado is not None:
        estado[CHAVE_ULTIMA_ROTA] = rota
        estado[CHAVE_HISTORICO] = anexar(estado.get(CHAVE_HISTORICO), linha)
    return linha


def _mediana(valores: list[float]) -> float:
    ordenados = sorted(valores)
    n = len(ordenados)
    meio = n // 2
    return float(ordenados[meio]) if n % 2 else (ordenados[meio - 1] + ordenados[meio]) / 2.0


def agregar(historico: list[dict]) -> list[dict]:
    """Mediana por rota e tipo: a pergunta é "esta tela costuma demorar quanto?".

    Mediana e não média: uma execução que esperou o pooler acordar não deve
    pintar a tela inteira de lenta. O máximo vai junto para ninguém esconder
    o pior caso. Só execuções concluídas -- uma que parou no meio (``st.stop``,
    erro) não mede a tela.
    """
    grupos: dict[tuple[str, str], list[dict]] = {}
    for linha in historico:
        if linha.get("concluiu"):
            grupos.setdefault((linha["rota"], linha["tipo"]), []).append(linha)
    saida = []
    for (rota, tipo), linhas in grupos.items():
        saida.append({
            "rota": rota,
            "tipo": tipo,
            "execucoes": len(linhas),
            "total_mediana_ms": round(_mediana([x["total_ms"] for x in linhas])),
            "total_max_ms": max(x["total_ms"] for x in linhas),
            "sql_mediana_ms": round(_mediana([x["sql_ms"] for x in linhas])),
            "sql_n_mediana": round(_mediana([x["sql_n"] for x in linhas])),
            "fora_do_banco_mediana_ms": round(_mediana([x["fora_do_banco_ms"] for x in linhas])),
            "import_max_ms": max(x["import_ms"] for x in linhas),
        })
    saida.sort(key=lambda x: x["total_mediana_ms"], reverse=True)
    return saida


# ── Ligação com o SQLAlchemy ─────────────────────────────────────────────────
# ContextVar e não variável de módulo: o Streamlit roda cada sessão numa thread
# própria, e duas pessoas trocando de página ao mesmo tempo não podem somar uma
# a consulta da outra.
_ATUAL: contextvars.ContextVar[Cronometro | None] = contextvars.ContextVar(
    "app4_medicao_rota", default=None
)
_CHAVE_INICIO = "_app4_medicao_inicio"
_ouvintes_lock = threading.Lock()
_ouvintes_ligados = False


def ativar(cron: Cronometro) -> None:
    _instalar_ouvintes_sql()
    _ATUAL.set(cron)


def desativar() -> None:
    _ATUAL.set(None)


def atual() -> Cronometro | None:
    return _ATUAL.get()


def _antes(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
    if _ATUAL.get() is not None:
        conn.info.setdefault(_CHAVE_INICIO, []).append(time.perf_counter())


def _depois(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
    cron = _ATUAL.get()
    pilha = conn.info.get(_CHAVE_INICIO)
    if cron is None or not pilha:
        return
    cron.registrar_sql((time.perf_counter() - pilha.pop()) * 1000.0, statement)


def _erro(contexto) -> None:
    # Consulta que falha não chega ao ``after_cursor_execute``; sem isto o
    # início dela ficaria na pilha e a consulta seguinte herdaria o relógio.
    conn = getattr(contexto, "connection", None)
    pilha = conn.info.get(_CHAVE_INICIO) if conn is not None else None
    if pilha:
        pilha.pop()


def _instalar_ouvintes_sql() -> None:
    """Ouve TODA engine, inclusive as criadas depois, uma vez por processo."""
    global _ouvintes_ligados
    if _ouvintes_ligados:
        return
    with _ouvintes_lock:
        if _ouvintes_ligados:
            return
        from sqlalchemy import event
        from sqlalchemy.engine import Engine

        event.listen(Engine, "before_cursor_execute", _antes)
        event.listen(Engine, "after_cursor_execute", _depois)
        event.listen(Engine, "handle_error", _erro)
        _ouvintes_ligados = True
