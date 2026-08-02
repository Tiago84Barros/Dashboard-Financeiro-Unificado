from datetime import datetime, timezone

import pandas as pd
import pytest

from scripts.publish_fii_selection_snapshot import (
    _engine,
    _ensure_target_methodology,
    _insert_stage_rows,
    _publication_preflight,
    _replace_target_snapshot,
    _sync_methodology_status,
    _write_local_snapshot_artifact,
    build_rows,
)


def test_build_rows_is_deterministic_and_auditable():
    now = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
    frame = pd.DataFrame([{
        "ticker": "ABCD11.SA",
        "tipo": "tijolo",
        "dy_12m": 0.10,
        "pvp": 0.90,
        "liquidez_diaria": 1_000_000,
        "history_months": 36,
        "max_drawdown": -0.20,
        "vacancia_fisica": 0.05,
        "property_count": 8,
        "region_count": 2,
        "updated_at": pd.Timestamp("2026-07-14", tz="UTC"),
    }])

    rows = build_rows(frame, now=now)

    assert len(rows) == 1
    assert rows[0]["ticker"] == "ABCD11"
    assert rows[0]["as_of_date"] == now.date()
    assert len(rows[0]["payload_sha256"]) == 64
    assert '"ticker":"ABCD11"' in rows[0]["payload_json"]
    assert '"coverage_pct": 100.0' in rows[0]["coverage_json"]


def test_publisher_preserves_configured_pooler_mode():
    engine = _engine(
        "postgresql://reader:password@aws-0-region.pooler.supabase.com:5432/postgres"
    )

    assert engine.url.port == 5432
    engine.dispose()


def test_build_rows_compacts_metric_metadata_without_losing_provenance():
    frame = pd.DataFrame([{
        "ticker": "ABCD11",
        "tipo": "tijolo",
        "metric_metadata": {
            "dy_12m": {
                "source": "brapi",
                "available_at": "2026-07-14T12:00:00+00:00",
                "source_quality": .8,
                "knowledge_at": "2026-07-14T13:00:00+00:00",
                "vintage": "redundant-per-metric",
                "unused_debug_payload": "x" * 10_000,
            },
            "unrelated_metric": {"source": "unused"},
            "vacancia_fisica": {
                "source": "cvm_informe_trimestral",
                "available_at": "2026-07-13T12:00:00+00:00",
                "source_quality": .95,
            },
        },
    }])

    row = build_rows(frame)[0]
    payload = __import__("json").loads(row["payload_json"])

    assert payload["metric_metadata"]["dy_12m"] == [
        "2026-07-14T12:00:00+00:00", .8, None, "brapi",
    ]
    assert "unrelated_metric" not in payload["metric_metadata"]
    assert payload["metric_metadata"]["vacancia_fisica"] == [
        "2026-07-13T12:00:00+00:00", .95, None, "cvm_informe_trimestral",
    ]
    assert row["schema_version"] == "fii_selection_inputs.v2"


def test_build_rows_records_lookthrough_without_inventing_missing_tenants():
    frame = pd.DataFrame([{
        "ticker": "ABCD11",
        "tipo": "tijolo",
        "sector": "Logística",
        "regions": {"SP": .6, "MG": .4},
        "tenant_concentration": .20,
    }])

    coverage = __import__("json").loads(build_rows(frame)[0]["coverage_json"])

    assert coverage["lookthrough"]["sector"]["observed"] is True
    assert coverage["lookthrough"]["region"]["observed"] is True
    assert coverage["lookthrough"]["tenant"]["applicable"] is True
    assert coverage["lookthrough"]["tenant"]["observed"] is False


def test_build_rows_rejects_duplicate_tickers():
    frame = pd.DataFrame([{"ticker": "ABCD11"}, {"ticker": "ABCD11.SA"}])

    with pytest.raises(ValueError, match="tickers duplicados"):
        build_rows(frame)


def test_build_rows_rejects_empty_warehouse():
    with pytest.raises(ValueError, match="zero inputs"):
        build_rows(pd.DataFrame())


def test_publication_ensures_methodology_before_validation_fk():
    class Connection:
        statement = None
        parameters = None

        def execute(self, statement, parameters):
            self.statement = str(statement)
            self.parameters = parameters

    connection = Connection()

    _ensure_target_methodology(connection)

    assert "INSERT INTO market.fii_methodology_versions" in connection.statement
    assert connection.parameters["version"] == "6.6.0"
    assert "fii_integrated_robust_optimizer.v6.6" in connection.parameters["manifest"]


def test_publication_promotes_only_passed_methodology():
    class Connection:
        def __init__(self):
            self.statements = []

        def execute(self, statement, parameters):
            self.statements.append((str(statement), parameters))

    connection = Connection()
    _sync_methodology_status(connection, {
        "methodology_version": "6.6.0", "status": "blocked",
    })
    assert connection.statements == []

    _sync_methodology_status(connection, {
        "methodology_version": "6.6.0", "status": "passed",
    })
    assert "SET status='passed'" in connection.statements[0][0]


def test_publication_preflight_requires_exact_strategy_and_required_lookthrough():
    validation = {
        "status": "passed",
        "metrics_json": {
            "strategy_id": "legacy.strategy",
            "backtest": {"periods": 65},
        },
        "blockers_json": [],
    }

    blockers = _publication_preflight(
        validation=validation,
        lookthrough={"required_ready": False},
        row_count=394,
    )

    assert "validação PIT não corresponde ao otimizador vigente" in blockers
    assert "cobertura look-through obrigatória insuficiente" in blockers


def test_publication_preflight_accepts_current_approved_validation():
    validation = {
        "status": "passed",
        "metrics_json": {
            "strategy_id": "fii_integrated_robust_optimizer.v6.6",
            "backtest": {"periods": 65},
        },
        "blockers_json": [],
    }

    blockers = _publication_preflight(
        validation=validation,
        lookthrough={"required_ready": True},
        row_count=394,
    )

    assert blockers == []


def test_snapshot_replacement_uses_upsert_instead_of_truncate():
    class Result:
        def scalar_one(self):
            return 394

    class Connection:
        def __init__(self):
            self.statements = []

        def execute(self, statement, parameters=None):
            self.statements.append(str(statement))
            return Result()

    connection = Connection()

    count = _replace_target_snapshot(connection)
    sql = "\n".join(connection.statements).upper()

    assert count == 394
    assert "ON CONFLICT (TICKER) DO UPDATE" in sql
    assert "DELETE FROM MARKET.FII_SELECTION_INPUTS" in sql
    assert "TRUNCATE" not in sql


def test_snapshot_stage_insert_uses_one_json_batch():
    class Connection:
        def __init__(self):
            self.calls = []

        def execute(self, statement, parameters=None):
            self.calls.append((str(statement), parameters))

    connection = Connection()
    rows = [{
        "ticker": "ABCD11",
        "payload_json": '{"ticker":"ABCD11"}',
        "as_of_date": datetime(2026, 7, 29, tzinfo=timezone.utc).date(),
        "available_at": datetime(2026, 7, 29, tzinfo=timezone.utc),
        "knowledge_at": datetime(2026, 7, 29, tzinfo=timezone.utc),
        "reference_date": datetime(2026, 7, 29, tzinfo=timezone.utc).date(),
        "vintage": "test",
        "source": "synthetic",
        "quality_status": "published",
        "schema_version": "fii_selection_inputs.v2",
        "generated_at": datetime(2026, 7, 29, tzinfo=timezone.utc),
        "payload_sha256": "0" * 64,
        "coverage_json": '{"coverage_pct":100}',
    }]

    _insert_stage_rows(connection, rows)

    assert len(connection.calls) == 1
    statement, parameters = connection.calls[0]
    assert "jsonb_to_recordset" in statement
    assert __import__("json").loads(parameters["rows_json"])[0]["ticker"] == "ABCD11"


def test_local_snapshot_artifact_roundtrip_is_hash_verified(tmp_path):
    from core.market_read import _load_fii_snapshot_artifact

    rows = build_rows(pd.DataFrame([{
        "ticker": "ABCD11",
        "tipo": "tijolo",
        "metric_metadata": {
            "dy_12m": {
                "available_at": datetime.now(timezone.utc).isoformat(),
                "source_quality": .95,
                "source": "synthetic",
            },
        },
    }]))
    path = tmp_path / "snapshot.json.gz"

    _write_local_snapshot_artifact(rows, path)
    frame = _load_fii_snapshot_artifact(path)

    assert len(frame) == 1
    assert frame.attrs["snapshot_source"] == "local_verified_artifact"
    assert frame.iloc[0]["payload_json"]["ticker"] == "ABCD11"

    rows[0]["payload_sha256"] = "0" * 64
    _write_local_snapshot_artifact(rows, path)
    corrupted = _load_fii_snapshot_artifact(path)
    assert corrupted.attrs["load_error"] == "snapshot_hash_invalid"
