"""Contas individuais sobre profiles; SQL parametrizado e senhas scrypt.

Nenhuma migração ou criação de schema em runtime. O perfil já existente do
administrador conserva seu UUID e todos os seus registros financeiros.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from uuid import uuid4

from sqlalchemy import text

from core.config import settings
from core.database import get_engine
from core.user_context import require_admin, require_user


def hash_password(password: str) -> str:
    if len(password) != 8:
        raise ValueError("Use uma senha de exatamente 8 caracteres.")
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return f"scrypt-v1${salt.hex()}${key.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    if not isinstance(password, str) or len(password) > 256:
        return False
    try:
        version, salt, expected = encoded.split("$")
        if version != "scrypt-v1" or len(salt) != 32 or len(expected) != 128:
            return False
        key = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
        return hmac.compare_digest(key.hex(), expected)
    except (ValueError, TypeError):
        return False


def _engine():
    engine = get_engine()
    if engine is None:
        raise RuntimeError("Banco indisponível. Tente novamente mais tarde.")
    return engine


def _extra(value) -> dict:
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else {}


def locked_preferences(conn, user_id: str) -> dict:
    conn.execute(text("INSERT INTO user_settings (user_id) VALUES (:uid) ON CONFLICT (user_id) DO NOTHING"), {"uid": user_id})
    return _extra(conn.execute(text(
        "SELECT extra_settings FROM user_settings WHERE user_id = :uid FOR UPDATE"
    ), {"uid": user_id}).scalar())


def write_preferences(conn, user_id: str, extra: dict) -> None:
    conn.execute(text("UPDATE user_settings SET extra_settings = CAST(:extra AS jsonb), updated_at = NOW() WHERE user_id = :uid"),
                 {"uid": user_id, "extra": json.dumps(extra, ensure_ascii=False)})


def _credential_version(row) -> str:
    credential = str(row["password_hash"])
    if str(row["id"]) == settings.ADMIN_USER_ID and not credential.startswith("scrypt-v1$"):
        credential = settings.APP_PASSWORD
    return hashlib.sha256(credential.encode()).hexdigest()


def authenticate(email: str, password: str) -> dict | None:
    """Cinco falhas bloqueiam a conta por 15 min, inclusive entre processos."""
    email = email.strip().lower()
    if len(email) > 255 or len(password) > 256:
        return None
    with _engine().begin() as conn:
        row = conn.execute(text(
            "SELECT id, name, email, password_hash, active FROM profiles "
            "WHERE lower(email) = :email OR (:email = 'administrador' AND id = CAST(:admin AS uuid)) "
            "FOR UPDATE"
        ), {"email": email, "admin": settings.ADMIN_USER_ID or None}).mappings().first()
        if not row or not row["active"]:
            # Mesmo custo criptográfico para conta inexistente.
            hashlib.scrypt(password.encode(), salt=b"app4-login-dummy", n=16384, r=8, p=1)
            return None
        uid = str(row["id"])
        extra = locked_preferences(conn, uid)
        guard = extra.get("app4_login_guard", {})
        now = time.time()
        if guard.get("blocked_until", 0) > now:
            return None
        encoded = str(row["password_hash"])
        valid = verify_password(password, encoded)
        # Transição só para o dono original. Nunca aceita o hash como senha.
        if uid == settings.ADMIN_USER_ID and not encoded.startswith("scrypt-v1$"):
            conf = settings.APP_PASSWORD
            valid = bool(conf) and (
                hmac.compare_digest(hashlib.sha256(password.encode()).hexdigest(), conf.lower())
                if re.fullmatch(r"[a-fA-F0-9]{64}", conf)
                else hmac.compare_digest(password, conf)
            )
        if not valid:
            attempts = guard.get("attempts", 0) + 1 if now - guard.get("last", 0) < 900 else 1
            extra["app4_login_guard"] = {"attempts": attempts, "last": now,
                                           "blocked_until": now + 900 if attempts >= 5 else 0}
            write_preferences(conn, uid, extra)
            return None
        extra.pop("app4_login_guard", None)
        write_preferences(conn, uid, extra)
        return {"id": uid, "name": row["name"], "email": row["email"],
                "credential_version": _credential_version(row), "expires_at": now + 12 * 3600}


def session_valid(user: dict) -> bool:
    if not user or user.get("expires_at", 0) <= time.time():
        return False
    with _engine().connect() as conn:
        row = conn.execute(text("SELECT id, password_hash, active FROM profiles WHERE id = :uid"),
                           {"uid": user["id"]}).mappings().first()
    return bool(row and row["active"] and hmac.compare_digest(
        _credential_version(row), user.get("credential_version", "")))


def create_user(name: str, email: str, password: str) -> str:
    require_admin()
    name, email = name.strip(), email.strip().lower()
    if not name or len(name) > 150 or len(email) > 255 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise ValueError("Informe nome e e-mail válidos.")
    encoded = hash_password(password)
    uid = str(uuid4())
    with _engine().begin() as conn:
        # Serializa emails normalizados sem alterar índices do schema existente.
        conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:email))"), {"email": email})
        if conn.execute(text("SELECT 1 FROM profiles WHERE lower(email) = :email"), {"email": email}).first():
            raise ValueError("Este e-mail já está cadastrado.")
        conn.execute(text("INSERT INTO profiles (id, name, email, password_hash) VALUES (:uid, :name, :email, :password)"),
                     {"uid": uid, "name": name, "email": email, "password": encoded})
    return uid


def list_users() -> list[dict]:
    """Perfis cadastrados, para o administrador conferir quem tem acesso.

    ``require_admin`` antes da consulta, e não só no formulário da tela: a
    lista é dado pessoal de outras pessoas (nome e e-mail), e um gate que mora
    apenas na interface protege a interface, não a função.

    ``password_hash`` fica fora do SELECT de propósito -- quem lê a lista quer
    saber quem entra, nunca a credencial.
    """
    require_admin()
    with _engine().begin() as conn:
        rows = conn.execute(text(
            "SELECT id, name, email, created_at, active FROM profiles "
            "ORDER BY active DESC, lower(name)"
        )).mappings().all()
    return [dict(row) for row in rows]


def change_password(old_password: str, new_password: str) -> None:
    uid = require_user()
    encoded = hash_password(new_password)
    with _engine().begin() as conn:
        row = conn.execute(text("SELECT email FROM profiles WHERE id = :uid"), {"uid": uid}).first()
    verified = authenticate(row.email, old_password) if row else None
    if not verified or verified["id"] != uid:
        raise ValueError("Não foi possível validar a senha atual.")
    with _engine().begin() as conn:
        conn.execute(text("UPDATE profiles SET password_hash = :password WHERE id = :uid AND active = TRUE"),
                     {"uid": uid, "password": encoded})


def create_account(name: str, account_type: str) -> None:
    uid = require_user()
    name = name.strip()
    if not name or len(name) > 100 or account_type not in {"checking", "savings", "investment", "credit_card"}:
        raise ValueError("Informe nome e tipo de conta válidos.")
    with _engine().begin() as conn:
        conn.execute(text("INSERT INTO accounts (user_id, name, type) VALUES (:uid, :name, :type)"),
                     {"uid": uid, "name": name, "type": account_type})
