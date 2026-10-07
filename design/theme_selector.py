"""Preferência visual da sessão, sempre vinculada ao usuário autenticado."""
import logging

import streamlit as st

from core.user_context import principal, require_user
from core.user_preferences import load_theme, save_theme

logger = logging.getLogger(__name__)

_CHOICE = "_app4_theme_choice"
_CACHE = "_app4_theme_preference"
_ERROR = "_app4_theme_error"
_LOAD_ERROR = "_app4_theme_load_error"
_ULTIMO_OK = "_app4_theme_last_ok"
_COOKIE = "app4_tema"

# Último tema lido com sucesso de cada conta, no processo -- e não na
# sessão. Uma sessão nova (reconexão do navegador, aba reaberta, app que
# voltou a aceitar conexão) começa sem `st.session_state`: a primeira
# leitura é obrigatória, e quando ela falha não há último tema de sessão
# para segurar a preferência -- o app repintava de escuro quem escolheu
# claro. A falha é mais provável justamente no meio de uma importação
# longa em Configurações, que ocupa a única conexão do pool.
_LEMBRADO: dict[str, str] = {}


def _lembrar(uid: str, theme: str) -> None:
    if theme in ("dark", "light"):
        _LEMBRADO[uid] = theme


def tema_do_navegador() -> str | None:
    """Último tema que ESTE navegador pintou, lido do cookie da sessão HTTP.

    ``_LEMBRADO`` é do processo e ``st.session_state`` é da sessão -- e os dois
    só respondem quando há conta identificada. O cookie é do navegador e
    sobrevive ao que derruba a sessão (expiração das 12 h, reconexão do
    websocket, aba reaberta): é o único lugar de onde uma execução sem conta
    consegue saber que a pessoa estava no claro.
    """
    try:
        valor = st.context.cookies.get(_COOKIE)
    except Exception:
        return None
    return valor if valor in ("dark", "light") else None


def lembrar_no_navegador(theme: str) -> None:
    """Grava o tema no cookie do navegador, SEMPRE e na mesma posição.

    Emitido em toda execução e nos dois temas, de propósito. Um elemento que
    só aparecesse num deles mudaria a contagem de elementos acima da página
    conforme o tema -- e é exatamente isso que devolve o ``st.tabs`` de
    Configurações para a primeira aba (ver :func:`current_theme`).

    O cookie é escrito no documento PAI: ``components.html`` roda num iframe,
    e o cookie do iframe não é o da aplicação. Guarda só ``dark``/``light``,
    nada que identifique a conta.
    """
    marcado = theme if theme in ("dark", "light") else "dark"
    try:
        from streamlit.components.v1 import html as _html
    except Exception:
        return
    try:
        _html(
            "<script>try{window.parent.document.cookie="
            f'"{_COOKIE}={marcado}; path=/; max-age=31536000; samesite=lax";'
            "}catch(e){}</script>",
            height=0,
        )
    except Exception:
        logger.debug("não foi possível lembrar o tema no navegador", exc_info=True)


def current_theme() -> str:
    """Resolve o tema da conta **sem desenhar nada na tela**.

    Esta função roda no topo de ``app.py``, acima de todo o conteúdo da
    página. Desenhar aqui custava caro de um jeito nada óbvio: um ``st.warning``
    que aparece só nas execuções em que a leitura falha insere um elemento
    ACIMA do ``st.tabs`` de Configurações, e o Streamlit devolve a seleção para
    a primeira aba quando o grupo de abas muda de posição.

    Medido em 22/09/2026 num app isolado, com as abas de Configurações e o
    mesmo ``file_uploader``: rerun de botão preserva a aba, adicionar arquivo
    ao uploader preserva a aba, elementos novos DENTRO da aba preservam a aba.
    Um elemento a mais acima das abas devolve para a primeira, sempre.

    Era um defeito só, com dois sintomas que pareciam dois: subir um arquivo em
    "Atualização de dados" jogava a pessoa de volta em "Geral" **e** repintava o
    app de escuro -- as duas coisas na execução em que esta leitura falhou.

    O aviso passou a morar em :func:`render_theme_selector`, que já está dentro
    da aba Geral: lá ele é conteúdo da aba, não deslocamento da página.
    """
    uid = require_user()
    cached = st.session_state.get(_CACHE)
    if not cached or cached[0] != uid:
        try:
            theme = load_theme()
        except Exception:
            # O log é o que faltava para diagnosticar: até aqui a exceção era
            # engolida inteira e a única pista que sobrava era o app escuro.
            logger.exception("falha ao ler o tema da conta")
            st.session_state[_LOAD_ERROR] = True
            # Falha de leitura não é troca de preferência. Enquanto o cache da
            # sessão não existe (a primeira leitura falhou), toda execução
            # relê, e cair para "dark" em cada falha repintava o app inteiro no
            # meio do trabalho -- foi o que acontecia ao clicar num botão de
            # atualização em Configurações, que ocupa a única conexão do pool.
            ultimo = (
                st.session_state.get(_ULTIMO_OK)
                or _LEMBRADO.get(uid)
                or tema_do_navegador()
            )
            return ultimo if ultimo in ("dark", "light") else "dark"
        st.session_state.pop(_LOAD_ERROR, None)
        st.session_state[_CACHE] = (uid, theme)
        st.session_state[_CHOICE] = theme
        st.session_state[_ULTIMO_OK] = theme
        _lembrar(uid, theme)
    return st.session_state[_CACHE][1]


def tema_para_pintar() -> str:
    """Tema a aplicar ANTES do portão de autenticação, e que nunca levanta.

    ``aplicar_tema`` morava depois de ``verificar_autenticacao()``, e toda
    execução interrompida pelo portão desenhava sem CSS nenhum -- com o tema
    base do ``config.toml``, que é escuro. Era o que acontecia ao importar os
    Dados Históricos B3: a importação longa ocupa a única conexão do pool, a
    validação da sessão na execução seguinte não consegue conexão e o app
    aparecia escuro, sem autorização de ninguém.

    Sem conta na sessão sobrou o caminho que escurecia a tela ao anexar o PDF
    da Nomad: ``principal()`` devolve ``{}`` assim que a sessão expira (12 h,
    nunca renovadas) e numa sessão nova do Streamlit -- reconexão do websocket
    durante uma execução longa, por exemplo -- o ``session_state`` nasce vazio.
    Devolver ``dark`` nesse ponto repintava de escuro quem tinha escolhido
    claro. O cookie do navegador é o único lembrete que atravessa a perda da
    sessão, e ele guarda só ``dark``/``light``.
    """
    uid = str(principal().get("id", ""))
    if not uid:
        return tema_do_navegador() or "dark"
    try:
        return current_theme()
    except Exception:
        logger.exception("falha ao resolver o tema antes do portão")
        return _LEMBRADO.get(uid) or tema_do_navegador() or "dark"


def _persist_choice() -> None:
    """Grava a escolha no callback do widget, e não depois de lê-lo.

    Comparar o retorno do ``selectbox`` com a preferência em cache e gravar na
    diferença parecia equivalente, mas ``st.rerun()`` reexecuta o script com o
    valor anterior ainda no widget: a segunda troca de tema da sessão era
    revertida e gravada de volta, deixando a pessoa presa no tema escolhido da
    primeira vez. No callback, o Streamlit já entregou o valor novo e o rerun
    seguinte lê o tema correto -- sem ``st.rerun()`` explícito.
    """
    uid = require_user()
    choice = st.session_state.get(_CHOICE)
    cached = st.session_state.get(_CACHE)
    anterior = cached[1] if cached and cached[0] == uid else None
    if choice not in ("dark", "light") or choice == anterior:
        return
    try:
        save_theme(choice)
    except Exception:
        # A preferência anterior é a verdade até o banco confirmar a nova.
        st.session_state[_CHOICE] = anterior or "dark"
        st.session_state[_ERROR] = True
    else:
        st.session_state[_CACHE] = (uid, choice)
        st.session_state[_ULTIMO_OK] = choice
        _lembrar(uid, choice)


def render_theme_selector() -> None:
    theme = current_theme()
    # Sem leitura válida, não sobrescreve uma preferência ainda desconhecida.
    cached = st.session_state.get(_CACHE)
    ready = bool(cached and cached[0] == require_user())
    st.session_state.setdefault(_CHOICE, theme)
    st.selectbox(
        "Tema da minha conta", ("dark", "light"),
        format_func=lambda value: "🌙 Dark (escuro)" if value == "dark" else "☀️ Light (claro)",
        key=_CHOICE, disabled=not ready, on_change=_persist_choice,
        help="Preferência salva apenas para sua conta, e aplicada na hora.",
    )
    if st.session_state.get(_LOAD_ERROR):
        # Não é `pop`: enquanto a leitura falhar, o seletor fica desabilitado e
        # a pessoa precisa continuar vendo por quê. `current_theme` limpa a
        # marca na primeira leitura que der certo.
        atual = st.session_state.get(_ULTIMO_OK)
        mantido = (
            "o app manteve o último tema que conseguiu ler"
            if atual in ("dark", "light")
            else "o app está usando dark temporariamente"
        )
        st.warning(
            "Não foi possível carregar o tema da sua conta. "
            f"{mantido[0].upper()}{mantido[1:]} e sua preferência não foi "
            "alterada — tente novamente em instantes."
        )
    if st.session_state.pop(_ERROR, False):
        st.error("Não foi possível salvar o tema. Sua preferência anterior foi mantida; tente novamente.")
