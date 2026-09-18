"""Preferências pessoais: lista permitida e atualização com lock da linha."""
from sqlalchemy import text

from core.user_accounts import _engine, _extra, locked_preferences, write_preferences
from core.user_context import require_user

THEMES = ("dark", "light")
FIELD = "app4_theme"


def load_theme() -> str:
    uid = require_user()
    with _engine().connect() as conn:
        value = conn.execute(text(
            "SELECT extra_settings FROM user_settings WHERE user_id = :uid"
        ), {"uid": uid}).scalar()
    theme = _extra(value).get(FIELD)
    return theme if theme in THEMES else "dark"


def save_theme(theme: str) -> None:
    uid = require_user()
    if theme not in THEMES:
        raise ValueError("Tema inválido.")
    with _engine().begin() as conn:
        extra = locked_preferences(conn, uid)
        extra[FIELD] = theme
        write_preferences(conn, uid, extra)
