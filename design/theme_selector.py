"""Preferência visual da sessão, sempre vinculada ao usuário autenticado."""
import streamlit as st

from core.user_context import require_user
from core.user_preferences import load_theme, save_theme

_CHOICE = "_app4_theme_choice"
_CACHE = "_app4_theme_preference"
_ERROR = "_app4_theme_error"


def current_theme() -> str:
    uid = require_user()
    cached = st.session_state.get(_CACHE)
    if not cached or cached[0] != uid:
        try:
            theme = load_theme()
        except Exception:
            st.warning("Não foi possível carregar seu tema. Usando dark temporariamente.")
            return "dark"
        st.session_state[_CACHE] = (uid, theme)
        st.session_state[_CHOICE] = theme
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
    if st.session_state.pop(_ERROR, False):
        st.error("Não foi possível salvar o tema. Sua preferência anterior foi mantida; tente novamente.")
