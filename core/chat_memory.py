"""Memória persistente, limitada e isolada para os chats do App4.

O banco guarda as mensagens recentes por pessoa autenticada e contexto.
Mensagens restauradas alimentam a LLM, mas ficam ocultas na tela. SQLite é
usado somente quando um caminho explícito é fornecido (testes descartáveis).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, MutableMapping

_MAX_MESSAGES = 40
_MAX_CONTENT_CHARS = 12_000
_RETENTION_DAYS = 30


def _owner_namespace() -> str:
    """Identidade da pessoa; nenhuma reserva para a conta global antiga."""
    from core.user_context import require_user
    return "user-v2:" + require_user()


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=3)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_memory (
            owner_key TEXT NOT NULL,
            conversation_key TEXT NOT NULL,
            messages_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (owner_key, conversation_key)
        )
        """
    )
    return conn


def _sanitize(messages: Any) -> list[dict[str, str]]:
    if not isinstance(messages, list):
        return []
    cleaned: list[dict[str, str]] = []
    for message in messages[-_MAX_MESSAGES:]:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        cleaned.append({"role": role, "content": content[:_MAX_CONTENT_CHARS]})
    while sum(len(message["content"]) for message in cleaned) > 100_000:
        cleaned.pop(0)
    return cleaned


def load_chat_history(
    conversation_key: str,
    *,
    session_key: str | None = None,
    session_state: MutableMapping[str, Any] | None = None,
    path: Path | None = None,
    owner_key: str | None = None,
) -> list[dict[str, str]]:
    """Restaura a última conversa; falhas locais nunca derrubam a interface."""
    state = session_state
    if state is None:
        import streamlit as st
        state = st.session_state
    state_key = session_key or conversation_key
    marker_key = f"_chat_memory_loaded_for:{state_key}"
    identity = owner_key or _owner_namespace()
    marker = (identity, conversation_key)
    existing = state.get(state_key, [])
    if state.get(marker_key) == marker:
        return existing
    try:
        if path is None:
            from core.chat_repository import load
            history = _sanitize(load(conversation_key))
        else:
            with closing(_connect(path)) as conn:
                row = conn.execute(
                    "SELECT messages_json FROM chat_memory WHERE owner_key = ? AND conversation_key = ? "
                    "AND julianday(updated_at) >= julianday('now', '-30 days')",
                    (identity, conversation_key),
                ).fetchone()
            history = _sanitize(json.loads(row[0])) if row else []
    except Exception:
        history = []
        _persistence_warning()
        # Falha de leitura não deve permitir sobrescrever a memória existente.
        state[f"_chat_memory_read_failed:{state_key}"] = True
    else:
        state.pop(f"_chat_memory_read_failed:{state_key}", None)
    state[state_key] = history
    state[marker_key] = marker
    state[f"_chat_visible_start:{state_key}"] = len(history)
    return history


def save_chat_history(
    conversation_key: str,
    messages: Any,
    *,
    session_key: str | None = None,
    session_state: MutableMapping[str, Any] | None = None,
    path: Path | None = None,
    owner_key: str | None = None,
) -> list[dict[str, str]]:
    """Persiste o histórico recente de forma atômica e atualiza a sessão."""
    history = _sanitize(messages)
    state = session_state
    if state is None:
        import streamlit as st
        state = st.session_state
    identity = owner_key or _owner_namespace()
    state_key = session_key or conversation_key
    marker = (identity, conversation_key)
    previous = state.get(f"_chat_memory_loaded_for:{state_key}")
    if previous is not None and previous != marker:
        raise PermissionError("O usuário ou contexto da conversa mudou.")
    # Mantém gráficos apenas na sessão atual, fora da memória durável.
    state[state_key] = messages[-len(history):] if history else []
    removed = max(0, len(messages) - len(history))
    visible_key = f"_chat_visible_start:{state_key}"
    state[visible_key] = max(0, state.get(visible_key, 0) - removed)
    state[f"_chat_memory_loaded_for:{state_key}"] = marker
    if state.get(f"_chat_memory_read_failed:{state_key}"):
        _persistence_warning()
        return history
    try:
        if path is None:
            from core.chat_repository import save
            save(conversation_key, history)
            return history
        now = datetime.now(UTC).isoformat()
        with closing(_connect(path)) as conn, conn:
            conn.execute(
                """INSERT INTO chat_memory (owner_key, conversation_key, messages_json, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(owner_key, conversation_key) DO UPDATE SET
                     messages_json = excluded.messages_json, updated_at = excluded.updated_at""",
                (identity, conversation_key,
                 json.dumps(history, ensure_ascii=False), now),
            )
            conn.execute(
                "DELETE FROM chat_memory WHERE owner_key = ? AND julianday(updated_at) < julianday('now', ?)",
                (identity, f"-{_RETENTION_DAYS} days"),
            )
    except Exception:
        _persistence_warning()
    return history


def clear_chat_history(
    conversation_key: str,
    *,
    session_key: str | None = None,
    session_state: MutableMapping[str, Any] | None = None,
    path: Path | None = None,
    owner_key: str | None = None,
) -> None:
    """Apaga explicitamente uma conversa, tanto na sessão quanto no disco."""
    state = session_state
    if state is None:
        import streamlit as st
        state = st.session_state
    state_key = session_key or conversation_key
    identity = owner_key or _owner_namespace()
    try:
        if path is None:
            from core.chat_repository import clear
            clear(conversation_key)
        else:
            with closing(_connect(path)) as conn, conn:
                conn.execute(
                    "DELETE FROM chat_memory WHERE owner_key = ? AND conversation_key = ?",
                    (identity, conversation_key),
                )
    except Exception:
        _persistence_warning()
        raise RuntimeError("A memória não foi apagada. Tente novamente.") from None
    state.pop(state_key, None)
    state.pop(f"_chat_memory_loaded_for:{state_key}", None)
    state.pop(f"_chat_memory_read_failed:{state_key}", None)
    state.pop(f"_chat_visible_start:{state_key}", None)


def visible_chat_history(history: list, session_key: str, *, session_state=None) -> list:
    """A LLM recebe a memória; a tela mostra só mensagens da sessão atual."""
    if session_state is None:
        import streamlit as st
        session_state = st.session_state
    return history[session_state.get(f"_chat_visible_start:{session_key}", len(history)):]


def _persistence_warning():
    import streamlit as st
    st.warning("Memória de conversa indisponível. As novas mensagens podem não ser recuperadas ao sair.")


def conversation_key(chat_name: str, context_signature: str = "") -> str:
    """Nomeia um diálogo sem usar dados financeiros brutos como chave do banco."""
    digest = hashlib.sha256(context_signature.encode("utf-8")).hexdigest()[:24]
    return f"{chat_name}:{digest}"
