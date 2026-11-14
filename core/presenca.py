"""Quem está logado no app4 agora, para o painel do administrador.

Registro em memória do processo (``st.cache_resource``), chaveado pela sessão
do navegador. Nada vai ao banco: presença é um fato do servidor que está de pé,
não um dado a guardar, e gravar a cada clique disputaria a conexão única que a
Streamlit Cloud dá ao app (ver ``core.auth._VALIDADE_DA_REVALIDACAO``).

Uma sessão conta como logada enquanto:

- o Streamlit a mantém ativa (aba aberta com o websocket conectado), conferido
  em ``Runtime.instance()``; quando o runtime não responde, vale a última
  atividade dos últimos ``_SEM_SINAL`` segundos;
- a identidade da sessão não expirou (``expires_at``, 12 h após o login).

Limite: o registro é por processo. Com mais de uma réplica do app, cada uma vê
só as próprias sessões. A Streamlit Cloud roda uma réplica por app.
"""
from __future__ import annotations

import threading
import time

import streamlit as st

#: Sem resposta do runtime, a sessão some depois deste silêncio.
_SEM_SINAL = 30 * 60


@st.cache_resource(show_spinner=False)
def _registro() -> tuple[threading.Lock, dict[str, dict]]:
    return threading.Lock(), {}


def _sessao_atual() -> str | None:
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        ctx = get_script_run_ctx(suppress_warning=True)
    except Exception:
        return None
    return getattr(ctx, "session_id", None) if ctx is not None else None


def registrar(user: dict) -> None:
    """Marca esta sessão do navegador como logada por ``user``."""
    sessao = _sessao_atual()
    if not sessao or not user or not user.get("id"):
        return
    trava, sessoes = _registro()
    agora = time.time()
    with trava:
        anterior = sessoes.get(sessao, {})
        mesma_pessoa = anterior.get("id") == str(user["id"])
        sessoes[sessao] = {
            "id": str(user["id"]),
            "name": str(user.get("name") or ""),
            "banco": str(user.get("banco") or "principal"),
            "desde": anterior.get("desde", agora) if mesma_pessoa else agora,
            "visto": agora,
            "expira": float(user.get("expires_at") or 0),
        }


def remover() -> None:
    """Tira esta sessão do registro (saída da conta)."""
    sessao = _sessao_atual()
    if not sessao:
        return
    trava, sessoes = _registro()
    with trava:
        sessoes.pop(sessao, None)


def esquecer_usuario(user_id: str) -> None:
    """Tira todas as sessões de uma conta (conta excluída)."""
    trava, sessoes = _registro()
    with trava:
        for sessao in [s for s, v in sessoes.items() if v.get("id") == str(user_id)]:
            sessoes.pop(sessao, None)


def _ativa_no_runtime(sessao: str) -> bool | None:
    """True/False pelo Streamlit; None quando o runtime não sabe responder."""
    try:
        from streamlit.runtime import Runtime
        return bool(Runtime.instance()._session_mgr.is_active_session(sessao))
    except Exception:
        return None


def usuarios_logados() -> list[dict]:
    """Uma linha por pessoa logada, com quantas abas ela tem abertas.

    Só administrador: são nomes de outras pessoas. Limpa do registro as
    sessões fechadas ou expiradas antes de contar.
    """
    from core.user_context import require_admin
    require_admin()
    agora = time.time()
    trava, sessoes = _registro()
    with trava:
        for sessao, info in list(sessoes.items()):
            ativa = _ativa_no_runtime(sessao)
            if ativa is None:
                ativa = agora - info.get("visto", 0) < _SEM_SINAL
            if not ativa or info.get("expira", 0) <= agora:
                sessoes.pop(sessao, None)
        vivas = list(sessoes.values())
    pessoas: dict[str, dict] = {}
    for info in vivas:
        linha = pessoas.setdefault(info["id"], {
            "id": info["id"], "name": info["name"], "banco": info["banco"],
            "desde": info["desde"], "visto": info["visto"], "abas": 0,
        })
        linha["abas"] += 1
        linha["desde"] = min(linha["desde"], info["desde"])
        linha["visto"] = max(linha["visto"], info["visto"])
    return sorted(pessoas.values(), key=lambda p: p["name"].lower())
