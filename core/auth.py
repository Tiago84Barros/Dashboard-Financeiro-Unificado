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
import logging
import time

import streamlit as st

from core.config import settings
from core.user_context import principal

logger = logging.getLogger(__name__)

#: Nome da chave de sessão que guarda a última revalidação bem-sucedida,
#: como ``(uid, credential_version, instante)``.
_VALIDADA = "_app4_sessao_validada"

#: Por quanto tempo uma revalidação vale sem voltar ao banco.
#:
#: Era zero: toda execução do script -- cada clique de botão, cada troca de
#: aba, cada número digitado, cada upload -- gastava um ``SELECT`` em
#: ``profiles`` na ÚNICA conexão que a Streamlit Cloud dá ao app, na mesma
#: execução em que o tema quer essa conexão para ler ou gravar a preferência.
#: Em Configurações, onde quase toda interação dispara rerun e algumas ainda
#: fazem ETL, era a conexão disputada que fazia a escolha de tema não chegar ao
#: banco. Revalidar é guarda de segurança, não contador de cliques.
_VALIDADE_DA_REVALIDACAO = 60.0

#: Tolerância quando o banco está fora do ar: a sessão segue valendo até aqui
#: com a última revalidação boa, em vez de derrubar quem está trabalhando.
_TOLERANCIA_SEM_BANCO = 600.0

# ── Pública ───────────────────────────────────────────────────────────────────

def verificar_autenticacao() -> None:
    """
    Verifica se o usuário está autenticado.
    Para a execução com st.stop() se a senha estiver configurada e não foi informada.

    Valida conta ativa, versão da credencial e prazo antes de liberar as rotas.

    O prazo (``expires_at``) é conferido aqui em toda execução, localmente, por
    :func:`core.user_context.principal`. O que passou a ser conferido por
    tempo, e não por execução, é a ida ao banco que pergunta se a conta foi
    desativada ou se a senha mudou: desativar uma conta ou trocar uma senha
    passa a derrubar a sessão em até ``_VALIDADE_DA_REVALIDACAO`` segundos (e
    em até ``_TOLERANCIA_SEM_BANCO`` enquanto o banco estiver inacessível),
    não no clique seguinte. É a troca consciente que tira a revalidação do
    caminho de toda interação -- ver o comentário das constantes.
    """
    from core.user_accounts import session_valid
    user = principal()
    if user:
        agora = time.time()
        marca = st.session_state.get(_VALIDADA)
        if _ainda_vale(marca, user, agora, _VALIDADE_DA_REVALIDACAO):
            return
        try:
            valido = session_valid(user)
        except Exception:
            logger.exception("falha ao revalidar a sessão no banco")
            if _ainda_vale(marca, user, agora, _TOLERANCIA_SEM_BANCO):
                # Banco fora do ar não é credencial inválida. Antes isto
                # parava o app com uma tarja de erro no lugar da página.
                return
            st.error("Não foi possível validar sua sessão. Tente novamente.")
            st.stop()
        if valido:
            st.session_state[_VALIDADA] = (
                str(user.get("id", "")), str(user.get("credential_version", "")), agora
            )
            return
        _limpar_sessao()

    _renderizar_login()
    st.stop()


def _ainda_vale(marca, user: dict, agora: float, janela: float) -> bool:
    """A marca de revalidação cobre ESTA conta e ESTA credencial, e é recente.

    Guardar a credencial junto do uid é o que impede a marca de sobreviver a
    uma troca de senha dentro da mesma sessão: o ``credential_version`` do
    ``user`` muda e a marca deixa de casar.
    """
    if not (isinstance(marca, tuple) and len(marca) == 3):
        return False
    uid, credencial, instante = marca
    return (
        uid == str(user.get("id", ""))
        and credencial == str(user.get("credential_version", ""))
        and 0 <= agora - float(instante) < janela
    )


def encerrar_sessao() -> None:
    """Encerra a sessão autenticada e recarrega o app."""
    _limpar_sessao()
    st.rerun()


def _limpar_sessao() -> None:
    """Limpa a sessão mantendo o tema que a tela está pintando.

    Import local: ``design`` depende de ``core``, e o caminho inverso no topo
    do módulo fecharia o ciclo.
    """
    from design.theme_selector import limpar_sessao_preservando_o_tema

    limpar_sessao_preservando_o_tema()


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
