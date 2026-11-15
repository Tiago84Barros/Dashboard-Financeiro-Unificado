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
            _marcar_presenca(user)
            return
        try:
            valido = session_valid(user)
        except Exception:
            logger.exception("falha ao revalidar a sessão no banco")
            if _ainda_vale(marca, user, agora, _TOLERANCIA_SEM_BANCO):
                # Banco fora do ar não é credencial inválida. Antes isto
                # parava o app com uma tarja de erro no lugar da página.
                _marcar_presenca(user)
                return
            st.error("Não foi possível validar sua sessão. Tente novamente.")
            st.stop()
        if valido:
            st.session_state[_VALIDADA] = (
                str(user.get("id", "")), str(user.get("credential_version", "")), agora
            )
            _marcar_presenca(user)
            return
        _limpar_sessao()

    _renderizar_login()
    st.stop()


def _marcar_presenca(user: dict) -> None:
    """Põe esta sessão no painel de logados do administrador.

    Presença é acessório: falha aqui nunca derruba o login de ninguém.
    """
    try:
        from core.presenca import registrar
        registrar(user)
    except Exception:
        logger.exception("falha ao registrar presença")


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

    try:
        from core.presenca import remover
        remover()
    except Exception:
        logger.exception("falha ao tirar a sessão do painel de logados")
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


#: Cadastros que uma mesma sessão do navegador pode fazer. O teto global
#: por hora mora em ``core.user_accounts._CADASTROS_POR_HORA``.
_CADASTROS_POR_SESSAO = 3


def _renderizar_login() -> None:
    """Tela de entrada: aba para entrar e aba para criar a própria conta."""
    # Import local: ``design`` depende de ``core`` (ver ``_limpar_sessao``).
    from design.componentes import logo_img_html

    _, col, _ = st.columns([1, 2, 1])
    with col:
        st.markdown("<br><br>", unsafe_allow_html=True)
        # O monograma TSB (15/11/2026) no lugar do 📊, o mesmo do ícone da aba
        # e do card da marca. Um bloco só, numa linha: logo e título juntos.
        st.markdown(
            '<div style="text-align:center">'
            f'<div style="display:inline-block;margin-bottom:6px">{logo_img_html(84, "login-logo")}</div>'
            '<h2 style="text-align:center;margin:0 0 2px">Dashboard Financeiro</h2>'
            "</div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            '<p style="text-align:center;color:#718096;margin-top:0">'
            "Entre na sua conta ou crie uma nova</p>",
            unsafe_allow_html=True,
        )
        st.markdown("<br>", unsafe_allow_html=True)

        aba_entrar, aba_criar = st.tabs(["Entrar", "Criar conta"])
        with aba_entrar:
            _renderizar_entrada()
        with aba_criar:
            _renderizar_cadastro()


def _entrar(user: dict) -> None:
    st.session_state.clear()
    st.session_state["_app4_user"] = user
    st.rerun()


def _renderizar_entrada() -> None:
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
                _entrar(user)
            else:
                st.error("Credenciais inválidas ou acesso temporariamente bloqueado.")


def _renderizar_cadastro() -> None:
    """Cadastro aberto: qualquer pessoa cria a própria conta.

    A conta nasce no Supabase das contas novas (``core.user_accounts.
    cadastrar_usuario``). Sem ele configurado, a aba diz que o cadastro ainda
    não abriu, em vez de gravar no banco principal, que está no limite.
    """
    from core.database import novos_configurado
    if not novos_configurado():
        # Não depende de aprovação de ninguém: falta só o banco das contas
        # novas estar configurado. A mensagem não manda procurar o admin.
        st.info("O cadastro de novas contas está temporariamente indisponível. "
                "Tente de novo mais tarde.")
        return
    with st.form("_auth_cadastro", clear_on_submit=False):
        nome = st.text_input("Nome", max_chars=150)
        email = st.text_input("E-mail", max_chars=255)
        senha = st.text_input("Senha (exatamente 8 caracteres)", type="password", max_chars=8)
        confirma = st.text_input("Repita a senha", type="password", max_chars=8)
        enviado = st.form_submit_button("Criar conta", width="stretch", type="primary")
    if not enviado:
        return
    feitos = int(st.session_state.get("_auth_cadastros_feitos", 0))
    if feitos >= _CADASTROS_POR_SESSAO:
        st.error("Limite de cadastros desta sessão atingido.")
        return
    if senha != confirma:
        st.error("As duas senhas não conferem.")
        return
    from core.user_accounts import authenticate, cadastrar_usuario
    try:
        cadastrar_usuario(nome, email, senha)
    except ValueError as exc:
        st.error(str(exc))
        return
    except RuntimeError as exc:
        st.error(str(exc))
        return
    except Exception:
        logger.exception("falha no cadastro aberto")
        st.error("Não foi possível criar a conta agora. Tente novamente mais tarde.")
        return
    st.session_state["_auth_cadastros_feitos"] = feitos + 1
    try:
        user = authenticate(email, senha)
    except Exception:
        user = None
    if user:
        _entrar(user)
    st.success("Conta criada. Entre pela aba Entrar.")
