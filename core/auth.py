"""
core/auth.py
Autenticação simples por senha para o app Streamlit.

Comportamento:
  - APP_PASSWORD ausente ou vazia → acesso liberado (dev local sem senha)
  - APP_PASSWORD definida         → exige senha antes de renderizar qualquer página
  - Aceita texto simples OU hash SHA-256 pré-computado (mais seguro no secrets.toml)

Uso em app.py:
    from core.auth import verificar_autenticacao
    verificar_autenticacao()   # Para a execução via st.stop() se não autenticado

Gerar hash seguro para o secrets.toml:
    python -c "import hashlib; print(hashlib.sha256(b'suasenha').hexdigest())"
"""
from __future__ import annotations

import hashlib

import streamlit as st

from core.config import settings


# ── Pública ───────────────────────────────────────────────────────────────────

def verificar_autenticacao() -> None:
    """
    Verifica se o usuário está autenticado.
    Para a execução com st.stop() se a senha estiver configurada e não foi informada.

    Fluxo:
      1. APP_PASSWORD vazio  → retorna (modo dev local, acesso liberado)
      2. Já autenticado      → retorna
      3. Caso contrário      → exibe tela de login e chama st.stop()
    """
    if not settings.APP_PASSWORD:
        return  # Sem senha configurada — acesso liberado

    if st.session_state.get("_auth_ok", False):
        return  # Sessão já autenticada

    _renderizar_login()
    st.stop()


def encerrar_sessao() -> None:
    """Encerra a sessão autenticada e recarrega o app."""
    st.session_state.pop("_auth_ok", None)
    st.rerun()


def esta_autenticado() -> bool:
    """Retorna True se autenticado ou se não há senha configurada."""
    return not settings.APP_PASSWORD or st.session_state.get("_auth_ok", False)


# ── Interno ───────────────────────────────────────────────────────────────────

def _hash_sha256(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _senha_correta(entrada: str) -> bool:
    """Aceita texto simples ou hash SHA-256 armazenado no secrets."""
    conf = settings.APP_PASSWORD
    return entrada == conf or _hash_sha256(entrada) == conf


def _renderizar_login() -> None:
    """Renderiza o formulário de login centralizado."""
    _, col, _ = st.columns([1, 2, 1])
    with col:
        st.markdown("<br><br><br>", unsafe_allow_html=True)
        st.markdown(
            '<h2 style="text-align:center;margin-bottom:2px">📊 Dashboard Financeiro</h2>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<p style="text-align:center;color:#718096;margin-top:0">Acesso restrito</p>',
            unsafe_allow_html=True,
        )
        st.markdown("<br>", unsafe_allow_html=True)

        senha = st.text_input(
            "Senha de acesso",
            type="password",
            key="_auth_senha_input",
            placeholder="Digite sua senha",
        )

        if st.button("Entrar", use_container_width=True, type="primary"):
            if not senha:
                st.warning("Informe a senha.")
            elif _senha_correta(senha):
                st.session_state["_auth_ok"] = True
                st.rerun()
            else:
                st.error("Senha incorreta.")
