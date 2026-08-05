"""Montagem, saneamento, digest e teto de tamanho do payload de snapshot.

Modulo puro: nao toca banco nem Streamlit. Coberto por
tests/test_portfolio_snapshots_payload.py.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

# Reaproveita o saneador ja validado (NaN/Infinity/numpy -> JSON valido) em vez
# de reescreve-lo. Ver tests/test_b3_portfolio_model.py.
from core.b3_portfolio_model import _clean_nan

SCHEMA_VERSION = 1
MAX_PAYLOAD_BYTES = 120_000

# Blocos podados quando o payload estoura o teto, na ordem em que sao podados.
TRUNCAVEIS: tuple[str, ...] = ("history", "evidence", "fundamentals")

_BLOCOS = (
    "identity", "fundamentals", "metrics", "classification",
    "history", "assumptions", "evidence", "notes", "provenance",
)


def canonical_json(payload: dict) -> str:
    """JSON deterministico: chaves ordenadas, sem espacos supérfluos."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def payload_size_bytes(payload: dict) -> int:
    return len(canonical_json(payload).encode("utf-8"))


def payload_digest(payload: dict) -> str:
    """SHA-256 do JSON canonico. Estavel para o mesmo conteudo."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def build_payload(blocks: dict) -> dict:
    """Monta o payload versionado a partir dos blocos fornecidos.

    Preenche blocos ausentes, saneia valores nao serializaveis e aplica o teto
    de tamanho podando os blocos volumosos, sempre registrando o que foi podado.
    """
    if not blocks.get("identity"):
        raise ValueError("bloco 'identity' e obrigatorio no payload de snapshot")

    payload: dict[str, Any] = {"schema_version": SCHEMA_VERSION}
    for nome in _BLOCOS:
        valor = blocks.get(nome)
        if nome == "notes":
            payload[nome] = valor if isinstance(valor, str) else ""
        else:
            payload[nome] = _clean_nan(valor) if valor else {}

    provenance = dict(payload.get("provenance") or {})
    provenance.setdefault("truncated", False)
    provenance.setdefault("truncated_blocks", [])
    payload["provenance"] = provenance

    podados: list[str] = []
    for bloco in TRUNCAVEIS:
        if payload_size_bytes(payload) <= MAX_PAYLOAD_BYTES:
            break
        if payload.get(bloco):
            payload[bloco] = {"_truncado": True}
            podados.append(bloco)

    payload["provenance"]["truncated"] = bool(podados)
    payload["provenance"]["truncated_blocks"] = podados
    return payload
