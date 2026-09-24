"""Preferência visual da sessão, sempre vinculada ao usuário autenticado."""
import logging

import streamlit as st

from core.user_context import require_user
from core.user_preferences import load_theme, save_theme

logger = logging.getLogger(__name__)

_CHOICE = "_app4_theme_choice"
_CACHE = "_app4_theme_preference"
_ERROR = "_app4_theme_error"
_LOAD_ERROR = "_app4_theme_load_error"
_ULTIMO_OK = "_app4_theme_last_ok"


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
            ultimo = st.session_state.get(_ULTIMO_OK)
            return ultimo if ultimo in ("dark", "light") else "dark"
        st.session_state.pop(_LOAD_ERROR, None)
        st.session_state[_CACHE] = (uid, theme)
        st.session_state[_CHOICE] = theme
        st.session_state[_ULTIMO_OK] = theme
    return st.session_state[_CACHE][1]


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
