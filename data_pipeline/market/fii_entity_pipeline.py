"""Materialização de entidades canônicas e propostas de correspondência."""
from __future__ import annotations

import json

from sqlalchemy import text

from core.fii_entity_resolution import match_entity, normalize_entity_name
from data_pipeline.utils.db_utils import get_pipeline_engine


ENTITY_TYPES = ("tenant", "debtor", "issuer", "manager", "holding", "security")


def resolve_entities() -> dict:
    engine = get_pipeline_engine()
    if engine is None:
        return {"status": "failed", "error": "banco indisponível"}
    report = {"status": "completed", "canonical_created": 0, "accepted": 0,
              "proposed": 0, "unresolved": 0}
    with engine.begin() as conn:
        existing = [dict(row._mapping) for row in conn.execute(text("""
            SELECT id, entity_type, canonical_name, legal_identifier
            FROM market.fii_canonical_entities WHERE status='active'
        """))]
        by_type = {kind: [row for row in existing if row["entity_type"] == kind]
                   for kind in ENTITY_TYPES}
        exposures = [dict(row._mapping) for row in conn.execute(text("""
            SELECT DISTINCT ON (exposure_type, exposure_name)
                   exposure_type, exposure_name,
                   metadata_json->>'cnpj' AS cnpj,
                   metadata_json->>'identifier' AS identifier
            FROM market.fii_exposures
            WHERE exposure_type = ANY(:types)
            ORDER BY exposure_type, exposure_name, reference_date DESC, knowledge_at DESC
        """), {"types": list(ENTITY_TYPES)})]
        for row in exposures:
            kind, name = str(row["exposure_type"]), str(row["exposure_name"])
            identifier = row.get("cnpj") or row.get("identifier")
            match = match_entity(name, by_type.get(kind, []), raw_identifier=identifier)
            if match.status == "unresolved":
                canonical_id = conn.execute(text("""
                    INSERT INTO market.fii_canonical_entities
                        (entity_type, canonical_name, normalized_name, legal_identifier,
                         status, metadata_json)
                    VALUES (:kind,:name,:normalized,NULLIF(:identifier,''),'active','{}'::jsonb)
                    ON CONFLICT (entity_type, normalized_name)
                    DO UPDATE SET legal_identifier=COALESCE(
                        market.fii_canonical_entities.legal_identifier, EXCLUDED.legal_identifier)
                    RETURNING id
                """), {"kind": kind, "name": name,
                        "normalized": normalize_entity_name(name),
                        "identifier": str(identifier or "")}).scalar()
                report["canonical_created"] += 1
                match_status, confidence, method = "accepted", 1.0, "first_canonical_occurrence"
                by_type.setdefault(kind, []).append({
                    "id": canonical_id, "entity_type": kind, "canonical_name": name,
                    "legal_identifier": identifier,
                })
            else:
                canonical_id = match.canonical_id
                match_status, confidence, method = match.status, match.confidence, match.method
            conn.execute(text("""
                INSERT INTO market.fii_entity_aliases (
                    entity_type,alias_name,normalized_alias,legal_identifier,
                    canonical_entity_id,match_method,match_confidence,validation_status,
                    evidence_json
                ) VALUES (:kind,:name,:normalized,:identifier,:canonical,
                          :method,:confidence,:status,CAST(:evidence AS jsonb))
                ON CONFLICT (entity_type, normalized_alias, legal_identifier)
                DO UPDATE SET canonical_entity_id=EXCLUDED.canonical_entity_id,
                              match_method=EXCLUDED.match_method,
                              match_confidence=EXCLUDED.match_confidence,
                              validation_status=CASE
                                WHEN market.fii_entity_aliases.validation_status='accepted'
                                THEN 'accepted' ELSE EXCLUDED.validation_status END,
                              updated_at=now()
            """), {"kind": kind, "name": name, "normalized": normalize_entity_name(name),
                    "identifier": str(identifier or ""), "canonical": canonical_id,
                    "method": method, "confidence": confidence, "status": match_status,
                    "evidence": json.dumps({"raw_name": name}, ensure_ascii=False)})
            if match_status == "accepted" and canonical_id is not None:
                conn.execute(text("""
                    UPDATE market.fii_exposures
                    SET canonical_entity_id=:canonical,
                        entity_match_confidence=:confidence
                    WHERE exposure_type=:kind AND exposure_name=:name
                """), {"canonical": canonical_id, "confidence": confidence,
                        "kind": kind, "name": name})
            report[match_status] = report.get(match_status, 0) + 1
    return report
