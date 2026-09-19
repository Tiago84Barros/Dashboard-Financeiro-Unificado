"""Sessão sem login não pode derrubar a página que abriga o chat.

`load_chat_history` promete na docstring que falhas nunca derrubam a
interface, mas resolvia a identidade fora do `try`: o `PermissionError` de
quem não entrou escapava e levava a tela inteira junto.
"""
from __future__ import annotations

import sqlite3

import pytest

from core import chat_memory


@pytest.fixture
def sem_login(monkeypatch):
    def recusa() -> str:
        raise PermissionError("Entre na sua conta para continuar.")

    monkeypatch.setattr("core.user_context.require_user", recusa)


def test_load_sem_login_devolve_vazio_em_vez_de_derrubar_a_tela(sem_login, tmp_path):
    estado: dict = {}
    assert chat_memory.load_chat_history(
        "chat:abc", session_state=estado, path=tmp_path / "m.db") == []
    # Sem marcador de carga: ao entrar na conta, o histórico real é lido.
    assert "_chat_memory_loaded_for:chat:abc" not in estado


def test_load_sem_login_preserva_a_conversa_da_sessao(sem_login, tmp_path):
    estado = {"chat:abc": [{"role": "user", "content": "oi"}]}
    assert chat_memory.load_chat_history(
        "chat:abc", session_state=estado, path=tmp_path / "m.db") == estado["chat:abc"]


def test_save_sem_login_fica_na_sessao_e_nao_grava_no_disco(sem_login, tmp_path):
    caminho = tmp_path / "m.db"
    estado: dict = {}
    mensagens = [{"role": "user", "content": "oi"}]
    assert chat_memory.save_chat_history(
        "chat:abc", mensagens, session_state=estado, path=caminho) == mensagens
    assert estado["chat:abc"] == mensagens
    assert not caminho.exists()


def test_clear_sem_login_limpa_a_sessao_sem_erro(sem_login, tmp_path):
    estado = {"chat:abc": [{"role": "user", "content": "oi"}],
              "_chat_visible_start:chat:abc": 0}
    chat_memory.clear_chat_history(
        "chat:abc", session_state=estado, path=tmp_path / "m.db")
    assert estado == {}


def test_com_login_a_memoria_duravel_continua_funcionando(monkeypatch, tmp_path):
    monkeypatch.setattr("core.user_context.require_user", lambda: "42")
    caminho = tmp_path / "m.db"
    mensagens = [{"role": "user", "content": "oi"},
                 {"role": "assistant", "content": "olá"}]
    chat_memory.save_chat_history("chat:abc", mensagens, session_state={}, path=caminho)
    with sqlite3.connect(caminho) as conn:
        (dono,) = conn.execute("SELECT owner_key FROM chat_memory").fetchone()
    assert dono == "user-v2:42"
    assert chat_memory.load_chat_history(
        "chat:abc", session_state={}, path=caminho) == mensagens
