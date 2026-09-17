"""Memória privada na preferência existente do usuário (sem novo schema).

O lock da linha preserva preferências e conversas de outras abas. O histórico
compartilhado da versão local nunca é atribuído a uma pessoa automaticamente.
"""
from __future__ import annotations

import time

from sqlalchemy import text

from core.user_accounts import _engine, _extra, locked_preferences, write_preferences
from core.user_context import require_user

FIELD = "app4_private_chats_v2"
RETENTION_SECONDS = 30 * 86400
MAX_CONVERSATIONS = 30


def load(conversation: str) -> list:
    uid = require_user()
    with _engine().connect() as conn:
        value = conn.execute(text("SELECT extra_settings FROM user_settings WHERE user_id = :uid"),
                             {"uid": uid}).scalar()
    chat = _extra(value).get(FIELD, {}).get(conversation, {})
    if not isinstance(chat, dict) or chat.get("updated", 0) < time.time() - RETENTION_SECONDS:
        return []
    return chat.get("messages", [])


def save(conversation: str, messages: list) -> None:
    uid = require_user()
    now = time.time()
    with _engine().begin() as conn:
        extra = locked_preferences(conn, uid)
        chats = {key: item for key, item in extra.get(FIELD, {}).items()
                 if isinstance(item, dict) and item.get("updated", 0) >= now - RETENTION_SECONDS}
        chats[conversation] = {"messages": messages, "updated": now}
        extra[FIELD] = dict(sorted(chats.items(), key=lambda pair: pair[1]["updated"], reverse=True)[:MAX_CONVERSATIONS])
        write_preferences(conn, uid, extra)


def clear(conversation: str) -> None:
    uid = require_user()
    with _engine().begin() as conn:
        extra = locked_preferences(conn, uid)
        extra.get(FIELD, {}).pop(conversation, None)
        write_preferences(conn, uid, extra)
