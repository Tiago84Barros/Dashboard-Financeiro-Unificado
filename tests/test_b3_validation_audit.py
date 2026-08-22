"""Contratos da trilha auditavel de validacao Empresas B3."""
from __future__ import annotations

import hashlib

from core.b3_validation import (
    _canonical,
    _clean,
    persist_readiness_snapshot,
    validation_readiness,
)


def test_canonical_is_stable_and_removes_non_finite_values():
    payload = {"b": float("nan"), "a": [1, float("inf"), {"x": 2}]}
    expected = '{"a":[1.0,null,{"x":2.0}],"b":null}'
    assert _canonical(payload) == expected
    assert hashlib.sha256(_canonical(payload).encode()).hexdigest()


def test_clean_preserves_boolean_before_numeric_coercion():
    assert _clean({"ok": True, "n": 3}) == {"ok": True, "n": 3.0}


def test_readiness_never_promotes_proxy_pit_to_strict_validation():
    result = validation_readiness({
        "pit": {"strict_available": False},
        "survivorship": {"strict_available": False},
    })
    assert result["ready"] is False
    assert len(result["blockers"]) == 2


def test_readiness_snapshot_fails_closed_without_engine():
    assert persist_readiness_snapshot(engine=None) is None


def test_b3_schema_is_private_and_requires_published_at_for_strict_pit():
    sql = open("supabase_unificado/schema/043_b3_validation_and_pit_audit.sql", encoding="utf-8").read()
    assert "market.b3_validation_runs" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "REVOKE ALL PRIVILEGES" in sql
    assert "published_at" in sql


def test_audit_records_are_append_only():
    sql = open("supabase_unificado/schema/044_b3_audit_immutability.sql", encoding="utf-8").read()
    assert "BEFORE UPDATE OR DELETE" in sql
    assert "prevent_b3_audit_mutation" in sql
    assert "REVOKE ALL ON FUNCTION" in sql


def test_market_refresh_runs_b3_before_fii_job():
    workflow = open(".github/workflows/market-refresh.yml", encoding="utf-8").read()
    assert "refresh-b3:" in workflow
    assert "refresh-fiis:" in workflow
    assert "needs: refresh-b3" in workflow
    assert workflow.index("Daily B3") < workflow.index("Ranking FIIs")
    assert "Snapshot de prontidao B3" in workflow
