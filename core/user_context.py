"""Identidade por sessão; nunca altera o proprietário global do processo."""
from __future__ import annotations

import time
from functools import wraps

import streamlit as st
from streamlit.runtime.scriptrunner import get_script_run_ctx


def principal() -> dict:
    value = st.session_state.get("_app4_user", {})
    if not isinstance(value, dict) or value.get("expires_at", 0) <= time.time():
        return {}
    return value


def current_owner(legacy_owner: str = "") -> str:
    if get_script_run_ctx(suppress_warning=True) is not None:
        return str(principal().get("id", ""))
    # Jobs de manutenção não têm sessão de navegador.
    return legacy_owner


def require_user() -> str:
    user_id = str(principal().get("id", ""))
    if not user_id:
        raise PermissionError("Entre na sua conta para continuar.")
    return user_id


def web_owner() -> str | None:
    """None só em jobs/CLI; navegador sem login é sempre recusado."""
    if get_script_run_ctx(suppress_warning=True) is None:
        return None
    return require_user()


def is_admin() -> bool:
    from core.config import settings
    return bool(principal() and principal().get("id") == settings.ADMIN_USER_ID)


def require_admin() -> str:
    user_id = require_user()
    if not is_admin():
        raise PermissionError("Acesso exclusivo do administrador.")
    return user_id


def user_cache_data(**options):
    """Inclui dono e modo de dados na chave de todos os caches pessoais."""
    def decorate(function):
        @st.cache_data(**options)
        def cached(identity, function_key, mock_mode, args, kwargs):
            return function(*args, **kwargs)

        @wraps(function)
        def wrapped(*args, **kwargs):
            from core.config import settings
            return cached(settings.OWNER_USER_ID,
                          f"{function.__module__}.{function.__qualname__}",
                          settings.MOCK_MODE, args, kwargs)

        wrapped.clear = cached.clear
        return wrapped
    return decorate
