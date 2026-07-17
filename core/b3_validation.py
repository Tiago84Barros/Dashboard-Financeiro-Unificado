"""Persistencia auditavel das validacoes da metodologia Empresas B3.

O modulo registra um manifesto compacto e deterministico de cada execucao da
criacao de portfolio. Ele nao converte ``first_seen_proxy`` em data de
publicacao: a qualidade PIT e declarada explicitamente no manifesto.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
from numbers import Real
from typing import Any
from uuid import uuid4

from sqlalchemy import text

logger = logging.getLogger(__name__)


def _clean(value: Any) -> Any:
    """Normaliza estruturas para JSON estavel, sem NaN/objetos pandas."""
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_clean(v) for v in value]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, Real):
        number = float(value)
        return number if number == number and abs(number) != float("inf") else None
    if hasattr(value, "item"):
        try:
            return _clean(value.item())
        except Exception:
            pass
    return str(value)


def _canonical(value: Any) -> str:
    return json.dumps(_clean(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _table_exists(conn, table: str) -> bool:
    return bool(conn.execute(text("SELECT to_regclass(:table) IS NOT NULL"), {"table": table}).scalar())


def build_data_manifest(engine) -> dict[str, Any]:
    """Resume cobertura e qualidade de disponibilidade sem carregar dados brutos."""
    with engine.connect() as conn:
        if not _table_exists(conn, "market.assets"):
            return {"status": "unavailable", "reason": "market.assets ausente"}
        base = conn.execute(text("""
            SELECT count(*) FILTER (WHERE is_active
                                      AND asset_type IN ('stock','unit')
                                      AND company_id IS NOT NULL) AS universe,
                   count(*) FILTER (WHERE is_active
                                      AND asset_type IN ('stock','unit')
                                      AND company_id IS NULL) AS unmapped_company
            FROM market.assets
        """)).mappings().one()
        manifest: dict[str, Any] = {
            "universe_definition": "active stock/unit with company_id",
            "universe": int(base["universe"] or 0),
            "unmapped_company": int(base["unmapped_company"] or 0),
            "pit": {"strict_available": False, "reason": "published_at CVM ainda nao integrado"},
            "survivorship": {
                "strict_available": False,
                "reason": "universo historico completo de deslistadas ainda nao integrado",
            },
        }
        if _table_exists(conn, "market.calculated_metric_vintages"):
            vintages = conn.execute(text("""
                SELECT availability_quality, count(*) AS n
                FROM market.calculated_metric_vintages
                GROUP BY availability_quality
            """)).mappings().all()
            quality = {str(r["availability_quality"]): int(r["n"] or 0) for r in vintages}
            manifest["metric_vintages"] = quality
            manifest["pit"] = {
                "strict_available": int(quality.get("published_at", 0)) > 0,
                "published_at_rows": int(quality.get("published_at", 0)),
                "first_seen_proxy_rows": int(quality.get("first_seen_proxy", 0)),
                "migration_baseline_rows": int(quality.get("migration_baseline", 0)),
            }
        for table, key in (
            ("market.income_statements", "income"),
            ("market.balance_sheets", "balance"),
            ("market.cash_flow_statements", "cashflow"),
        ):
            if _table_exists(conn, table):
                row = conn.execute(text(f"""
                    SELECT count(*) AS rows,
                           count(*) FILTER (WHERE raw_payload_id IS NOT NULL) AS traced
                    FROM {table}
                    WHERE period='annual'
                """)).mappings().one()
                manifest.setdefault("lineage", {})[key] = {
                    "rows": int(row["rows"] or 0),
                    "traced_rows": int(row["traced"] or 0),
                }
    return manifest


def validation_readiness(manifest: dict[str, Any]) -> dict[str, Any]:
    """Define se um resultado pode ser tratado como validacao estrita.

    A ausencia de PIT publicado ou de universo historico completo nao invalida a
    analise exploratoria; apenas impede que ela seja promovida a recomendacao
    estatisticamente validada.
    """
    pit_ok = bool((manifest.get("pit") or {}).get("strict_available"))
    survivorship_ok = bool((manifest.get("survivorship") or {}).get("strict_available"))
    blockers: list[str] = []
    if not pit_ok:
        blockers.append("PIT estrito sem published_at/revisoes CVM")
    if not survivorship_ok:
        blockers.append("universo historico de deslistadas incompleto")
    return {"ready": not blockers, "blockers": blockers}


def persist_validation_run(
    *,
    engine,
    methodology_version: str,
    score_version: str,
    validation_mode: str,
    input_params: dict[str, Any],
    result_summary: dict[str, Any],
    status: str = "completed",
    notes: str | None = None,
) -> str | None:
    """Grava uma execucao; falha de auditoria nunca interrompe a analise da UI."""
    try:
        manifest = build_data_manifest(engine)
        readiness = validation_readiness(manifest)
        if not readiness["ready"] and status == "completed":
            status = "blocked"
        with engine.begin() as conn:
            if not _table_exists(conn, "market.b3_validation_runs"):
                logger.info("b3_validation_runs ausente; execute a migration 043")
                return None
            payload = {
                "methodology_version": methodology_version,
                "score_version": score_version,
                "validation_mode": validation_mode,
                "input_params": input_params,
                "result_summary": result_summary,
                "data_manifest": manifest,
                "status": status,
                "readiness": readiness,
            }
            artifact_hash = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
            run_id = str(uuid4())
            now = datetime.now(timezone.utc)
            conn.execute(text("""
                INSERT INTO market.b3_validation_runs (
                    run_id, methodology_version, score_version, validation_mode,
                    data_as_of, status, input_params, result_summary,
                    data_manifest, artifact_hash, notes
                ) VALUES (
                    CAST(:run_id AS uuid), :methodology_version, :score_version, :validation_mode,
                    :data_as_of, :status, CAST(:input_params AS jsonb), CAST(:result_summary AS jsonb),
                    CAST(:data_manifest AS jsonb), :artifact_hash, :notes
                )
            """), {
                "run_id": run_id,
                "methodology_version": methodology_version,
                "score_version": score_version,
                "validation_mode": validation_mode,
                "data_as_of": now,
                "status": status,
                "input_params": _canonical(input_params),
                "result_summary": _canonical(result_summary),
                "data_manifest": _canonical(manifest),
                "artifact_hash": artifact_hash,
                "notes": notes,
            })
            snapshot = {"manifest": manifest, "result_summary": result_summary}
            snapshot_hash = hashlib.sha256(_canonical(snapshot).encode("utf-8")).hexdigest()
            conn.execute(text("""
                INSERT INTO market.b3_data_readiness_snapshots (
                    universe_definition, snapshot_json, artifact_hash
                ) VALUES (:definition, CAST(:snapshot AS jsonb), :hash)
                ON CONFLICT (artifact_hash) DO NOTHING
            """), {
                "definition": manifest.get("universe_definition", "unknown"),
                "snapshot": _canonical(snapshot),
                "hash": snapshot_hash,
            })
        return run_id
    except Exception as exc:
        logger.warning("Nao foi possivel persistir validacao B3: %s", exc)
        return None
