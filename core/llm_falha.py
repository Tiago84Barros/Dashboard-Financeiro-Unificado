"""Falha do provedor de LLM: mensagem amigável na tela, detalhe só no log.

Por que existe (auditoria app4, LLM-A11, 04/10/2026)
---------------------------------------------------
Os chats faziam ``resposta = f"Não foi possível consultar a LLM: {exc}"`` e
gravavam essa ``resposta`` no histórico (``save_chat_history``). Três defeitos:

* o texto de ``RuntimeError("Todos os provedores LLM falharam — ...")``
  concatena a mensagem crua de cada provedor (código HTTP, id de requisição,
  nome da organização), e ia para a tela e para o banco;
* o histórico salvo volta como contexto do turno seguinte: o modelo lia a
  própria falha como se fosse uma resposta sua;
* nenhum desses ``except`` registrava log, então a causa sumia junto.

Aqui o detalhe vai para ``logger.error`` com o traceback, e a tela recebe só
o motivo em categoria. A exceção de configuração que o próprio app escreve
(``Nenhum provedor LLM configurado``, ``Só há provedor LLM gratuito``) é
acionável pelo usuário e sai como está.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Mensagens do próprio app (core/llm_b3._chat_complete) que dizem ao usuário
#: o que configurar: não têm dado de terceiro, então vão à tela inteiras.
_DO_APP = ("Nenhum provedor LLM configurado", "Só há provedor LLM gratuito")


def _motivo(exc: BaseException) -> str:
    nome = type(exc).__name__.lower()
    texto = str(exc).lower()
    if "timeout" in nome or "timed out" in texto or "timeout" in texto:
        return "o provedor de IA demorou demais para responder"
    if any(m in texto for m in ("429", "rate limit", "quota", "insufficient",
                                "crédito", "credit", "billing")):
        return "o provedor de IA recusou por limite de uso ou falta de crédito"
    if any(m in texto for m in ("401", "403", "api key", "invalid_api_key",
                                "unauthorized", "permission")):
        return "o provedor de IA recusou a chave de acesso"
    if "connection" in nome or "connect" in texto or "network" in texto:
        return "não houve conexão com o provedor de IA"
    if "não interpretável" in texto or nome == "jsondecodeerror":
        # core/dossie_b3._parecer_llm_cached levanta ValueError("resposta não
        # interpretável") quando o modelo responde fora do esquema.
        return "a resposta da IA veio fora do formato esperado"
    return "o provedor de IA falhou"


def descrever_falha_llm(exc: BaseException) -> str:
    """O motivo da falha em categoria, sem ``str(exc)`` e sem log.

    Para quem precisa do motivo dentro de outro texto -- o ``resumo`` do
    fallback do relatório, o ``motivo`` do portão de seleção. Antes esses
    pontos gravavam ``str(exc)[:200]`` no relatório, que a tela mostra e que
    fica no cache de sessão (LLM-A11, sobra do 13b). Mensagem de configuração
    do próprio app (``_DO_APP``) sai inteira: é acionável e não tem dado de
    terceiro.
    """
    bruto = str(exc)
    if bruto.startswith(_DO_APP):
        return bruto
    return _motivo(exc)


def motivo_falha_llm(exc: BaseException, onde: str) -> str:
    """Registra ``exc`` no log (com traceback) e devolve o motivo em categoria."""
    logger.error("Falha da LLM em %s (%s)", onde, type(exc).__name__,
                 exc_info=(type(exc), exc, exc.__traceback__))
    return descrever_falha_llm(exc)


def mensagem_falha_llm(exc: BaseException, onde: str, *,
                       acao: str = "consultar a IA") -> str:
    """Registra ``exc`` no log e devolve o texto para a tela e o histórico.

    ``onde`` nomeia o chat no log ("chat do ativo", "dossiê da carteira").
    O texto devolvido nunca contém ``str(exc)``, salvo as mensagens de
    configuração do próprio app (``_DO_APP``).
    """
    motivo = motivo_falha_llm(exc, onde)
    if str(exc).startswith(_DO_APP):
        return f"Não foi possível {acao} agora: {motivo}"
    return (f"Não foi possível {acao} agora: {motivo}. Tente de novo em "
            "instantes; o detalhe técnico ficou no log do app.")
