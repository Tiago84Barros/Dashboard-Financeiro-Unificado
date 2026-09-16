"""Memória persistente, limitada e isolada para os chats do App4.

O ``session_state`` do Streamlit desaparece quando o processo reinicia ou a
sessão expira. Este repositório local complementa (não substitui) esse estado:
guarda apenas as mensagens recentes, por proprietário e por contexto de chat.
Não grava prompt, chaves, snapshots financeiros nem telemetria da LLM.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, MutableMapping

_MAX_MESSAGES = 40
_MAX_CONTENT_CHARS = 12_000
_RETENTION_DAYS = 30


def _default_path() -> Path:
    """Resolve um caminho local ignorado pelo Git, sem expor configurações."""
    from core.config import settings
    return Path(str(settings.CHAT_MEMORY_DB_PATH or "data/chat_memory.sqlite3"))


def _owner_namespace() -> str:
    """Cria uma chave estável sem persistir a senha de acesso em texto claro."""
    from core.config import settings

    owner = str(settings.OWNER_USER_ID or "").strip()
    if owner:
        return f"owner:{owner}"
    password = str(settings.APP_PASSWORD or "").strip()
    if password:
        return "password:" + hashlib.sha256(password.encode("utf-8")).hexdigest()
    # Modo local sem login já não distingue pessoas; não fingir que distingue.
    return "local-no-auth"


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
    existing = _sanitize(state.get(state_key, []))
    if existing and state.get(marker_key) == conversation_key:
        return existing
    try:
        with _connect(path or _default_path()) as conn:
            row = conn.execute(
                "SELECT messages_json FROM chat_memory WHERE owner_key = ? AND conversation_key = ?",
                (owner_key or _owner_namespace(), conversation_key),
            ).fetchone()
        history = _sanitize(json.loads(row[0])) if row else []
    except (OSError, sqlite3.Error, TypeError, ValueError, json.JSONDecodeError):
        history = []
    state[state_key] = history
    state[marker_key] = conversation_key
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
    state[session_key or conversation_key] = history
    state[f"_chat_memory_loaded_for:{session_key or conversation_key}"] = conversation_key
    try:
        now = datetime.now(UTC).isoformat()
        with _connect(path or _default_path()) as conn:
            conn.execute(
                """INSERT INTO chat_memory (owner_key, conversation_key, messages_json, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(owner_key, conversation_key) DO UPDATE SET
                     messages_json = excluded.messages_json, updated_at = excluded.updated_at""",
                (owner_key or _owner_namespace(), conversation_key,
                 json.dumps(history, ensure_ascii=False), now),
            )
            conn.execute(
                "DELETE FROM chat_memory WHERE owner_key = ? AND julianday(updated_at) < julianday('now', ?)",
                (owner_key or _owner_namespace(), f"-{_RETENTION_DAYS} days"),
            )
    except (OSError, sqlite3.Error, TypeError, ValueError):
        pass
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
    state.pop(state_key, None)
    state.pop(f"_chat_memory_loaded_for:{state_key}", None)
    try:
        with _connect(path or _default_path()) as conn:
            conn.execute(
                "DELETE FROM chat_memory WHERE owner_key = ? AND conversation_key = ?",
                (owner_key or _owner_namespace(), conversation_key),
            )
    except (OSError, sqlite3.Error):
        pass


def conversation_key(chat_name: str, context_signature: str = "") -> str:
    """Nomeia um diálogo sem usar dados financeiros brutos como chave do banco."""
    digest = hashlib.sha256(context_signature.encode("utf-8")).hexdigest()[:24]
    return f"{chat_name}:{digest}"
