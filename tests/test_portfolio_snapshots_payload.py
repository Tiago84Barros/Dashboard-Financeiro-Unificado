"""Saneamento, digest estavel e teto de tamanho do payload de snapshot."""
import json

import numpy as np
import pytest

from core.portfolio.snapshots import (
    MAX_PAYLOAD_BYTES,
    SCHEMA_VERSION,
    build_payload,
    canonical_json,
    payload_digest,
    payload_size_bytes,
)


def test_build_payload_injeta_schema_version():
    out = build_payload({"identity": {"symbol": "PETR4"}})
    assert out["schema_version"] == SCHEMA_VERSION


def test_build_payload_saneia_nan_e_infinito():
    out = build_payload({"identity": {"symbol": "TEST"}, "metrics": {"dy": float("nan"), "pl": float("inf"), "ok": 1.5}})
    assert out["metrics"]["dy"] is None
    assert out["metrics"]["pl"] is None
    assert out["metrics"]["ok"] == 1.5
    json.loads(canonical_json(out))  # nao deve levantar


def test_build_payload_converte_tipos_numpy():
    out = build_payload({"identity": {"symbol": "TEST"}, "metrics": {"i": np.int64(7), "f": np.float64(2.5)}})
    assert out["metrics"]["i"] == 7 and isinstance(out["metrics"]["i"], int)
    assert out["metrics"]["f"] == 2.5


def test_build_payload_preenche_blocos_ausentes():
    out = build_payload({"identity": {"symbol": "PETR4"}})
    for bloco in ("fundamentals", "metrics", "classification", "history",
                  "assumptions", "evidence", "provenance"):
        assert bloco in out, f"bloco ausente: {bloco}"


def test_digest_independe_da_ordem_das_chaves():
    a = build_payload({"identity": {"symbol": "PETR4", "nome": "Petrobras"}})
    b = build_payload({"identity": {"nome": "Petrobras", "symbol": "PETR4"}})
    assert payload_digest(a) == payload_digest(b)


def test_digest_muda_quando_o_conteudo_muda():
    a = build_payload({"identity": {"symbol": "A"}, "metrics": {"dy": 1.0}})
    b = build_payload({"identity": {"symbol": "A"}, "metrics": {"dy": 2.0}})
    assert payload_digest(a) != payload_digest(b)


def test_payload_acima_do_teto_e_truncado_e_marcado():
    grande = {"linhas": ["x" * 1000 for _ in range(300)]}   # ~300 KB
    out = build_payload({"identity": {"symbol": "X"}, "history": grande})
    assert payload_size_bytes(out) <= MAX_PAYLOAD_BYTES
    assert out["provenance"]["truncated"] is True
    assert "history" in out["provenance"]["truncated_blocks"]


def test_payload_dentro_do_teto_nao_e_marcado():
    out = build_payload({"identity": {"symbol": "X"}, "metrics": {"dy": 1.0}})
    assert out["provenance"]["truncated"] is False
    assert out["provenance"]["truncated_blocks"] == []


def test_identity_e_obrigatorio():
    with pytest.raises(ValueError, match="identity"):
        build_payload({"metrics": {"dy": 1.0}})
