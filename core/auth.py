"""
core/auth.py
Autenticação individual no Streamlit, com perfis persistidos no banco.
O administrador original mantém seu UUID e pode usar APP_PASSWORD durante a
transição. Novas contas usam e-mail e senha scrypt. Ausência de senha global
nunca libera dados financeiros anonimamente.

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
from core.user_context import principal

# ── Pública ───────────────────────────────────────────────────────────────────

def verificar_autenticacao() -> None:
    """
    Verifica se o usuário está autenticado.
    Para a execução com st.stop() se a senha estiver configurada e não foi informada.

    Valida conta ativa, versão da credencial e prazo antes de liberar as rotas.
    """
    from core.user_accounts import session_valid
    user = principal()
    if user:
        try:
            if session_valid(user):
                return
        except Exception:
            st.error("Não foi possível validar sua sessão. Tente novamente.")
            st.stop()
        st.session_state.clear()

    _renderizar_login()
    st.stop()


def encerrar_sessao() -> None:
    """Encerra a sessão autenticada e recarrega o app."""
    st.session_state.clear()
    st.rerun()


def esta_autenticado() -> bool:
    """Retorna True somente para uma identidade individual válida na sessão."""
    return bool(principal())


# ── Interno ───────────────────────────────────────────────────────────────────

def _hash_sha256(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _senha_correta(entrada: str) -> bool:
    """Aceita texto simples ou hash SHA-256 armazenado no secrets."""
    conf = settings.APP_PASSWORD
    import hmac
    import re
    if re.fullmatch(r"[a-fA-F0-9]{64}", conf):
        return hmac.compare_digest(_hash_sha256(entrada), conf.lower())
    return bool(conf) and hmac.compare_digest(entrada, conf)


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

        email = st.text_input("E-mail ou administrador", key="_auth_email_input")
        senha = st.text_input(
            "Senha de acesso",
            type="password",
            key="_auth_senha_input",
            placeholder="Digite sua senha",
        )

        if st.button("Entrar", width="stretch", type="primary"):
            if not senha:
                st.warning("Informe a senha.")
            else:
                from core.user_accounts import authenticate
                try:
                    user = authenticate(email, senha)
                except Exception:
                    st.error("Não foi possível acessar as contas. Tente novamente mais tarde.")
                    return
                if user:
                    st.session_state.clear()
                    st.session_state["_app4_user"] = user
                    st.rerun()
                else:
                    st.error("Credenciais inválidas ou acesso temporariamente bloqueado.")
