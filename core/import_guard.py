"""Primitivas compartilhadas de idempotência para importações manuais."""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterator

from sqlalchemy import text


def _canonical_payload(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"bytes_sha256": hashlib.sha256(value).hexdigest()}
    if (
        isinstance(value, tuple)
        and len(value) == 2
        and isinstance(value[0], str)
        and isinstance(value[1], bytes)
    ):
        return {
            "file_name": value[0],
            "bytes_sha256": hashlib.sha256(value[1]).hexdigest(),
        }
    if isinstance(value, list):
        items = [_canonical_payload(item) for item in value]
        if items and all(
            isinstance(item, dict) and "file_name" in item for item in items
        ):
            return sorted(
                items,
                key=lambda item: (item["file_name"], item["bytes_sha256"]),
            )
        return items
    if isinstance(value, tuple):
        return [_canonical_payload(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _canonical_payload(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float):
        if value != value:
            return None
        return format(value, ".15g")
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return str(value)


def import_payload_digest(*parts: Any) -> str:
    """Hash estável para arquivos, registros e parâmetros de uma importação."""
    payload = _canonical_payload(list(parts))
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_import_lock_key(scope: str, *parts: Any) -> str:
    return f"app4-import|{scope}|{import_payload_digest(*parts)}"


def _dialect_name(connection_or_engine) -> str | None:
    dialect = getattr(connection_or_engine, "dialect", None)
    if dialect is None:
        engine = getattr(connection_or_engine, "engine", None)
        dialect = getattr(engine, "dialect", None)
    return getattr(dialect, "name", None)


def acquire_transaction_import_lock(conn, scope: str, *parts: Any) -> str:
    """
    Serializa uma importação dentro da transação atual.

    A segunda execução com a mesma chave aguarda o commit da primeira e só
    então consulta as chaves idempotentes já persistidas.
    """
    lock_key = build_import_lock_key(scope, *parts)
    if _dialect_name(conn) not in (None, "postgresql"):
        return lock_key
    conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": lock_key},
    )
    return lock_key


@contextmanager
def serialized_import(engine, scope: str, *parts: Any) -> Iterator[str]:
    """
    Mantém um advisory lock de sessão durante importadores que abrem suas
    próprias transações/conexões internamente.
    """
    lock_key = build_import_lock_key(scope, *parts)
    if _dialect_name(engine) not in (None, "postgresql"):
        yield lock_key
        return
    with engine.connect() as conn:
        conn.execute(
            text("SELECT pg_advisory_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": lock_key},
        )
        try:
            yield lock_key
        finally:
            conn.execute(
                text("SELECT pg_advisory_unlock(hashtextextended(:lock_key, 0))"),
                {"lock_key": lock_key},
            )
