"""Memória privada na preferência existente do usuário (sem novo schema).

O lock da linha preserva preferências e conversas de outras abas. O histórico
compartilhado da versão local nunca é atribuído a uma pessoa automaticamente.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import text

from core.user_accounts import _engine, _extra, locked_preferences, write_preferences
from core.user_context import require_user

FIELD = "app4_private_chats_v2"
RETENTION_SECONDS = 30 * 86400
MAX_CONVERSATIONS = 30


def load(conversation: str) -> list:
    uid = require_user()
    with _engine().connect() as conn:
        value = conn.execute(text("SELECT extra_settings FROM user_settings WHERE user_id = :uid"),
                             {"uid": uid}).scalar()
    chat = _extra(value).get(FIELD, {}).get(conversation, {})
    if not isinstance(chat, dict) or chat.get("updated", 0) < time.time() - RETENTION_SECONDS:
        return []
    return chat.get("messages", [])


def save(conversation: str, messages: list) -> None:
    uid = require_user()
    now = time.time()
    with _engine().begin() as conn:
        extra = locked_preferences(conn, uid)
        chats = {key: item for key, item in extra.get(FIELD, {}).items()
                 if isinstance(item, dict) and item.get("updated", 0) >= now - RETENTION_SECONDS}
        chats[conversation] = {"messages": messages, "updated": now}
        extra[FIELD] = dict(sorted(chats.items(), key=lambda pair: pair[1]["updated"], reverse=True)[:MAX_CONVERSATIONS])
        write_preferences(conn, uid, extra)


def clear(conversation: str) -> None:
    uid = require_user()
    with _engine().begin() as conn:
        extra = locked_preferences(conn, uid)
        extra.get(FIELD, {}).pop(conversation, None)
        write_preferences(conn, uid, extra)


# -- limpeza por seção --------------------------------------------------------

@dataclass(frozen=True)
class Secao:
    """Uma seção do app que conversa com a LLM.

    ``prefixo`` é o que sobrevive ao hash em ``conversation_key``: a chave é
    ``f"{prefixo}:{sha256(assinatura)[:24]}"``, então o prefixo é a única parte
    legível — e a única por onde dá para apagar uma seção inteira.

    ``chaves_de_sessao`` são PREFIXOS de chave do ``st.session_state``, porque
    apagar só o banco deixaria a tela aberta ainda exibindo o histórico velho —
    e, pior, regravando-o na próxima mensagem.
    """

    rotulo: str
    prefixo: str
    chaves_de_sessao: tuple[str, ...]


#: Fonte única. Um teste deriva os prefixos e as chaves de sessão das chamadas
#: reais no código-fonte: lista escrita à mão envelhece calada quando alguém
#: adiciona um chat, e a tela seguiria oferecendo "limpar tudo" sem limpar.
SECOES: tuple[Secao, ...] = (
    Secao("Controle Financeiro", "controle_financeiro", ("cf_chat_history",)),
    Secao("Cartão de Crédito", "cartao_credito", ("cc_chat_history",)),
    Secao("Análise de Portfólio B3", "apb3", ("apb3_chat_history",)),
    Secao("Análise de Portfólio EUA", "apus", ("apus_chat_history",)),
    Secao("Seleção de FIIs", "fii_portfolio", ("fii_chat_history",)),
    Secao("Portfólio Global", "portfolio_global", ("portfolio_global_chat_historico",)),
    Secao("Ativo individual (B3, EUA e FIIs)", "chat_ativo", ("chat_ativo_",)),
    Secao("Carteira por classe", "chat_carteira", ("chat_carteira_",)),
)


def secao_por_prefixo(prefixo: str) -> Secao | None:
    return next((s for s in SECOES if s.prefixo == prefixo), None)


def _pertence(chave: str, prefixo: str) -> bool:
    """``apb3`` não pode pegar ``apus``, e ``chat_ativo`` não pode pegar
    ``chat_carteira``: o separador entra na comparação."""
    return chave.startswith(f"{prefixo}:")


def contagens() -> dict[str, int]:
    """Quantas conversas gravadas cada seção tem hoje, numa leitura só.

    A tela precisa disto para não oferecer um botão que não apaga nada — e para
    dizer o número ANTES do clique, já que a ação é irreversível. Uma consulta
    por seção seriam oito idas ao banco para desenhar um selectbox.
    """
    uid = require_user()
    with _engine().connect() as conn:
        value = conn.execute(
            text("SELECT extra_settings FROM user_settings WHERE user_id = :uid"),
            {"uid": uid}).scalar()
    chaves = list(_extra(value).get(FIELD, {}))
    return {s.prefixo: sum(1 for c in chaves if _pertence(c, s.prefixo))
            for s in SECOES}


def contar(prefixo: str) -> int:
    return contagens().get(prefixo, 0)


def clear_prefixos(prefixos) -> int:
    """Apaga TODAS as conversas das seções e devolve quantas apagou.

    ``clear`` apaga uma chave exata, e uma seção tem N conversas — uma por
    assinatura de contexto (conjunto de tickers, mês selecionado, cenário).
    Apagar só a chave corrente limparia a conversa aberta e deixaria as outras
    de pé, com toda a cara de ter funcionado.

    Várias seções saem numa transação só: "Todas" que apaga em oito escritas
    pode terminar pela metade e deixar o usuário sem saber o que sobrou.
    """
    alvo = tuple(prefixos)
    uid = require_user()
    with _engine().begin() as conn:
        extra = locked_preferences(conn, uid)
        chats = extra.get(FIELD, {})
        mortas = [chave for chave in chats
                  if any(_pertence(chave, p) for p in alvo)]
        for chave in mortas:
            chats.pop(chave, None)
        extra[FIELD] = chats
        write_preferences(conn, uid, extra)
    return len(mortas)


def clear_prefixo(prefixo: str) -> int:
    return clear_prefixos((prefixo,))


def limpar_sessao(secao: Secao, session_state) -> int:
    """Tira da sessão o que a seção deixou em memória. Devolve quantas chaves."""
    alvos = [chave for chave in list(session_state)
             if any(str(chave).startswith(p) for p in secao.chaves_de_sessao)
             or any(str(chave).startswith(f"{marca}{p}")
                    for p in secao.chaves_de_sessao
                    for marca in ("_chat_memory_loaded_for:", "_chat_visible_start:",
                                  "_chat_memory_read_failed:"))]
    for chave in alvos:
        session_state.pop(chave, None)
    return len(alvos)
