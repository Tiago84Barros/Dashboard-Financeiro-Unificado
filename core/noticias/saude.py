"""Saúde dos serviços de que a coleta depende. Sete verificações, sem rede.

Regra de ouro deste módulo: **nenhuma verificação chama uma API externa**. Um
health check que gasta cota transforma o painel de saúde num consumidor de
requisições -- abrir a tela cinco vezes gastaria um quinto da cota diária do
Alpha Vantage. O que se verifica aqui é configuração, carimbo e conectividade
com o próprio banco, que é barato e não tem cota.

``ok=None`` é desconhecido, e não é ``False``
---------------------------------------------
Um serviço que não pôde ser verificado não é um serviço com defeito. Marcar
desconhecido como falho encheria a tela de alarme falso; marcar como saudável
esconderia risco. As duas coisas erradas, e a mesma origem: tratar a ausência de
medição como medição. Por isso ``ok`` é ternário.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import text

from core.database import get_engine
from core.noticias import cadencia as cad
from core.noticias import estado_coleta as ec

logger = logging.getLogger(__name__)

SERVICO_BANCO = "banco"
SERVICO_PROVEDORES = "provedores"
SERVICO_AGENDADOR = "agendador"
SERVICO_WORKER = "worker"
SERVICO_CACHE = "cache"
SERVICO_PRECOS = "precos"
SERVICO_LLM = "llm"

#: Quantos ciclos previstos podem ser perdidos antes de o agendador ser dado
#: como parado. Um único atraso é ruído de fila do GitHub Actions -- o cron de
#: lá não é pontual e nunca prometeu ser.
CICLOS_PERDIDOS_PARA_ALARME = 3


@dataclass(frozen=True)
class Verificacao:
    servico: str
    ok: bool | None
    detalhe: str
    medido_em: datetime | None = None

    @property
    def rotulo(self) -> str:
        return {True: "no ar", False: "com falha",
                None: "não verificado"}[self.ok]

    def descrever(self) -> str:
        return f"{self.servico}: {self.rotulo} — {self.detalhe}"


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def checar_banco(*, engine=None) -> Verificacao:
    motor = engine if engine is not None else get_engine()
    if motor is None:
        return Verificacao(SERVICO_BANCO, None,
                           "sem DATABASE_URL: o motor roda em memória")
    try:
        with motor.connect() as conn:
            conn.execute(text("SELECT 1"))
        return Verificacao(SERVICO_BANCO, True, "conexão respondeu", _agora())
    except Exception as exc:
        return Verificacao(SERVICO_BANCO, False, str(exc)[:200], _agora())


def checar_provedores(*, config=None) -> Verificacao:
    """Configuração dos provedores. Não consulta nenhum deles."""
    from core.noticias.provedores import registro

    situacoes = registro.descrever(config=config)
    if not situacoes:
        return Verificacao(SERVICO_PROVEDORES, False,
                           "nenhum provedor configurado", _agora())
    disponiveis = [s.nome for s in situacoes if s.disponivel]
    faltando = [f"{s.nome} ({s.motivo})" for s in situacoes if not s.disponivel]
    if not disponiveis:
        return Verificacao(SERVICO_PROVEDORES, False,
                           "nenhum provedor utilizável: " + "; ".join(faltando),
                           _agora())
    detalhe = f"{len(disponiveis)} de {len(situacoes)} utilizáveis"
    if faltando:
        detalhe += " — sem: " + "; ".join(faltando)
    return Verificacao(SERVICO_PROVEDORES, True, detalhe, _agora())


def checar_agendador(*, engine=None, agora: datetime | None = None,
                     config=None) -> Verificacao:
    """O cron está disparando? Deduzido do carimbo, não de quem o dispara.

    Verificar "o workflow existe no repositório" responderia à pergunta errada:
    um workflow desabilitado, com cota de Actions estourada ou com o cron
    silenciosamente desligado pelo GitHub após 60 dias sem commit continua
    existindo no arquivo. Só o carimbo da última tentativa prova execução.
    """
    agora = agora or _agora()
    estado = ec.ler(engine=engine)
    if not estado.disponivel:
        return Verificacao(SERVICO_AGENDADOR, None,
                           "estado compartilhado indisponível: "
                           f"{estado.ultimo_erro or 'motivo não informado'}")
    if estado.ultima_tentativa is None:
        return Verificacao(SERVICO_AGENDADOR, False,
                           "nenhuma execução registrada até agora", agora)

    ritmo = cad.cadencia(estado.modo, config=config)
    atraso = (agora - estado.ultima_tentativa).total_seconds() / 60.0
    limite = ritmo.intervalo_min * CICLOS_PERDIDOS_PARA_ALARME
    if atraso > limite:
        return Verificacao(
            SERVICO_AGENDADOR, False,
            f"última tentativa há {atraso:.0f} min; o modo {ritmo.rotulo} "
            f"prevê uma a cada {ritmo.intervalo_min:.0f} min", agora)
    return Verificacao(SERVICO_AGENDADOR, True,
                       f"última tentativa há {atraso:.0f} min", agora)


def checar_worker(*, engine=None, agora: datetime | None = None) -> Verificacao:
    """Há ciclo preso? Um início sem conclusão é execução que morreu no meio."""
    agora = agora or _agora()
    ciclos = ec.ultimos_ciclos(3, engine=engine)
    if not ciclos:
        return Verificacao(SERVICO_WORKER, None, "nenhum ciclo registrado")

    ultimo = ciclos[0]
    if ultimo.get("concluido_em") is None:
        inicio = ec._utc(ultimo.get("iniciado_em")) or agora
        parado = (agora - inicio).total_seconds() / 60.0
        return Verificacao(SERVICO_WORKER, False,
                           f"ciclo iniciado há {parado:.0f} min sem conclusão",
                           agora)
    falhos = [c for c in ciclos
              if c.get("status") == cad.STATUS_INDISPONIVEL]
    if len(falhos) == len(ciclos):
        return Verificacao(SERVICO_WORKER, False,
                           f"os últimos {len(ciclos)} ciclos falharam", agora)
    return Verificacao(SERVICO_WORKER, True,
                       f"último ciclo: {ultimo.get('status')}", agora)


def checar_cache(*, cache=None) -> Verificacao:
    if cache is None:
        return Verificacao(SERVICO_CACHE, None,
                           "cache não instanciado nesta verificação")
    try:
        tamanho = len(getattr(cache, "_entradas", {}) or {})
    except Exception as exc:                            # pragma: no cover
        return Verificacao(SERVICO_CACHE, False, str(exc)[:200], _agora())
    return Verificacao(SERVICO_CACHE, True, f"{tamanho} entradas", _agora())


def checar_precos(*, engine=None) -> Verificacao:
    """O serviço de preços responde? Sem ele não há retorno anormal.

    Importa: a checagem existe porque *"falha no serviço de preços não deve
    permitir calcular impacto atual"*, e quem bloqueia precisa saber se falhou.
    """
    motor = engine if engine is not None else get_engine()
    if motor is None:
        return Verificacao(SERVICO_PRECOS, None, "sem banco: preços não lidos")
    try:
        with motor.connect() as conn:
            n = conn.execute(text(
                "SELECT COUNT(*) FROM historical_prices")).scalar()
        return Verificacao(SERVICO_PRECOS, True,
                           f"{int(n or 0):,} cotações no acervo".replace(",", "."),
                           _agora())
    except Exception as exc:
        return Verificacao(SERVICO_PRECOS, False, str(exc)[:200], _agora())


def checar_llm(*, config=None) -> Verificacao:
    """Só configuração. A LLM é opcional: sem ela o painel continua inteiro."""
    if config is None:
        from core.config import settings as config
    chave = getattr(config, "OPENAI_API_KEY", "") or ""
    if not chave:
        return Verificacao(SERVICO_LLM, None,
                           "sem chave: o painel publica o backend sem explicação",
                           _agora())
    return Verificacao(SERVICO_LLM, True, "chave configurada", _agora())


def checar_tudo(*, engine=None, cache=None, config=None,
                agora: datetime | None = None) -> tuple[Verificacao, ...]:
    """As sete verificações, na ordem em que uma falha explica a seguinte."""
    return (
        checar_banco(engine=engine),
        checar_provedores(config=config),
        checar_agendador(engine=engine, agora=agora, config=config),
        checar_worker(engine=engine, agora=agora),
        checar_cache(cache=cache),
        checar_precos(engine=engine),
        checar_llm(config=config),
    )


def resumo(verificacoes) -> dict:
    """Contagem por estado, para a tela e para o relatório de homologação."""
    verificacoes = tuple(verificacoes)
    return {
        "total": len(verificacoes),
        "ok": sum(1 for v in verificacoes if v.ok is True),
        "falha": sum(1 for v in verificacoes if v.ok is False),
        "desconhecido": sum(1 for v in verificacoes if v.ok is None),
        "falhando": tuple(v.servico for v in verificacoes if v.ok is False),
    }
