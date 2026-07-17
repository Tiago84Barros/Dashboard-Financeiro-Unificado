"""Resolução determinística e auditável das entidades públicas de FIIs."""
from __future__ import annotations

import json

from sqlalchemy import text

from data_pipeline.utils.db_utils import get_pipeline_engine


def resolve_entities() -> dict:
    engine = get_pipeline_engine()
    if engine is None:
        return {"status": "failed", "reason": "banco indisponível"}
    with engine.begin() as conn:
        ready = conn.execute(text(
            "SELECT to_regclass('market.fii_canonical_entities') IS NOT NULL"
        )).scalar()
        if not ready:
            return {"status": "blocked", "reason": "migration 033 pendente"}

        funds = conn.execute(text("""
            WITH raw AS (
              SELECT ticker,name,cnpj,liquidez_diaria,
                     market.normalize_fii_entity_name(
                         COALESCE(NULLIF(name,''),ticker)) normalized_name,
                     NULLIF(regexp_replace(COALESCE(cnpj,''),'\\D','','g'),'') clean_cnpj
              FROM market.fiis WHERE ticker IS NOT NULL
            ), ranked AS (
              SELECT *,row_number() OVER (
                         PARTITION BY normalized_name
                         ORDER BY liquidez_diaria DESC NULLS LAST,ticker) rn,
                       count(*) OVER (PARTITION BY clean_cnpj) cnpj_count
              FROM raw
            )
            INSERT INTO market.fii_canonical_entities (
                entity_type,canonical_name,normalized_name,legal_identifier,metadata_json
            )
            SELECT 'fund',COALESCE(NULLIF(name,''),ticker),
                   normalized_name,
                   CASE WHEN cnpj_count=1 THEN clean_cnpj END,
                   jsonb_build_object('ticker',ticker,'cnpj',cnpj,'source','market.fiis')
            FROM ranked WHERE rn=1
            ON CONFLICT (entity_type,normalized_name) DO UPDATE SET
                canonical_name=EXCLUDED.canonical_name,
                legal_identifier=COALESCE(market.fii_canonical_entities.legal_identifier,
                                          EXCLUDED.legal_identifier),
                metadata_json=market.fii_canonical_entities.metadata_json || EXCLUDED.metadata_json,
                updated_at=now()
        """))

        aliases = conn.execute(text("""
            WITH alias_rows AS (
              SELECT f.ticker,e.id canonical_id,'ticker' method,.99 confidence,
                     f.ticker alias_name,COALESCE(e.legal_identifier,'') legal_identifier
              FROM market.fiis f JOIN market.fii_canonical_entities e
                ON e.entity_type='fund'
               AND e.normalized_name=market.normalize_fii_entity_name(
                    COALESCE(NULLIF(f.name,''),f.ticker))
              UNION ALL
              SELECT f.ticker,e.id,'legal_name',.95,f.name,COALESCE(e.legal_identifier,'')
              FROM market.fiis f JOIN market.fii_canonical_entities e
                ON e.entity_type='fund'
               AND e.normalized_name=market.normalize_fii_entity_name(
                    COALESCE(NULLIF(f.name,''),f.ticker))
              WHERE NULLIF(f.name,'') IS NOT NULL
              UNION ALL
              SELECT f.ticker,e.id,'cnpj_exact',1.0,
                     regexp_replace(f.cnpj,'\\D','','g'),COALESCE(e.legal_identifier,'')
              FROM market.fiis f JOIN market.fii_canonical_entities e
                ON e.entity_type='fund'
               AND e.normalized_name=market.normalize_fii_entity_name(
                    COALESCE(NULLIF(f.name,''),f.ticker))
              WHERE length(regexp_replace(COALESCE(f.cnpj,''),'\\D','','g'))=14
            ), aliases AS (
              SELECT DISTINCT ON (
                       market.normalize_fii_entity_name(alias_name),legal_identifier)
                     ticker,canonical_id,method,confidence,alias_name,legal_identifier
              FROM alias_rows
              ORDER BY market.normalize_fii_entity_name(alias_name),legal_identifier,
                       confidence DESC,ticker
            )
            INSERT INTO market.fii_entity_aliases (
                entity_type,alias_name,normalized_alias,legal_identifier,
                canonical_entity_id,match_method,match_confidence,validation_status,
                evidence_json,reviewer_id,reviewed_at
            )
            SELECT 'fund',alias_name,market.normalize_fii_entity_name(alias_name),
                   legal_identifier,canonical_id,method,confidence,'accepted',
                   jsonb_build_object('ticker',ticker,'deterministic',true),
                   'system:deterministic_entity_v1',now()
            FROM aliases WHERE NULLIF(alias_name,'') IS NOT NULL
            ON CONFLICT (entity_type,normalized_alias,legal_identifier) DO UPDATE SET
                canonical_entity_id=EXCLUDED.canonical_entity_id,
                match_method=EXCLUDED.match_method,
                match_confidence=EXCLUDED.match_confidence,
                validation_status='accepted',evidence_json=EXCLUDED.evidence_json,
                reviewer_id=EXCLUDED.reviewer_id,reviewed_at=EXCLUDED.reviewed_at,
                updated_at=now()
        """))

        generic = conn.execute(text("""
            WITH names AS (
              SELECT CASE exposure_type
                       WHEN 'debtor' THEN 'debtor'
                       WHEN 'issuer' THEN 'issuer'
                       WHEN 'manager' THEN 'manager'
                       WHEN 'tenant' THEN 'tenant' END entity_type,
                     min(exposure_name) canonical_name,
                     market.normalize_fii_entity_name(exposure_name) normalized_name
              FROM market.fii_exposures
              WHERE exposure_type IN ('debtor','issuer','manager','tenant')
                AND NULLIF(exposure_name,'') IS NOT NULL
              GROUP BY 1,market.normalize_fii_entity_name(exposure_name)
            )
            INSERT INTO market.fii_canonical_entities (
                entity_type,canonical_name,normalized_name,metadata_json
            )
            SELECT entity_type,canonical_name,normalized_name,
                   jsonb_build_object('source','exact_normalized_public_exposure')
            FROM names WHERE entity_type IS NOT NULL AND normalized_name<>''
            ON CONFLICT (entity_type,normalized_name) DO NOTHING
        """))

        direct_links = conn.execute(text("""
            UPDATE market.fii_exposures x SET canonical_entity_id=e.id,
                   entity_match_confidence=.95
            FROM market.fii_canonical_entities e
            WHERE x.exposure_type IN ('debtor','issuer','manager','tenant')
              AND e.entity_type=x.exposure_type
              AND e.normalized_name=market.normalize_fii_entity_name(x.exposure_name)
              AND (x.canonical_entity_id IS DISTINCT FROM e.id
                   OR x.entity_match_confidence IS DISTINCT FROM .95)
        """))
        holding_links = conn.execute(text("""
            UPDATE market.fii_exposures x SET canonical_entity_id=a.canonical_entity_id,
                   entity_match_confidence=a.match_confidence
            FROM market.fii_entity_aliases a
            WHERE x.exposure_type='holding' AND a.entity_type='fund'
              AND a.validation_status='accepted'
              AND a.normalized_alias=market.normalize_fii_entity_name(x.exposure_name)
              AND a.canonical_entity_id IS NOT NULL
              AND (x.canonical_entity_id IS DISTINCT FROM a.canonical_entity_id
                   OR x.entity_match_confidence IS DISTINCT FROM a.match_confidence)
        """))
        payload = {
            "fund_entities_affected": max(int(funds.rowcount or 0), 0),
            "aliases_affected": max(int(aliases.rowcount or 0), 0),
            "generic_entities_affected": max(int(generic.rowcount or 0), 0),
            "direct_exposures_linked": max(int(direct_links.rowcount or 0), 0),
            "holdings_linked": max(int(holding_links.rowcount or 0), 0),
        }
        conn.execute(text("""
            INSERT INTO market.fii_audit_events (
                event_type,entity_type,actor_type,actor_id,algorithm_version,payload_json
            ) VALUES ('entity_resolution_completed','fii_entity','service',
                      'fii_entity_resolution','deterministic_entity_v1',CAST(:payload AS jsonb))
        """), {"payload": json.dumps(payload, ensure_ascii=False)})
    return {"status": "completed", **payload}
