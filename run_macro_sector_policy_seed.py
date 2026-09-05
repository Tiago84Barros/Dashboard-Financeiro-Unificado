"""Aplica no Docker local a política inicial de exposição macro aprovada."""

from sqlalchemy import text

from core.macro_data.database import get_local_macro_engine
from core.macro_data.sector_policy import validated_sector_exposures


def main() -> int:
    engine = get_local_macro_engine()
    if engine is None:
        raise RuntimeError("MACRO_LOCAL_DB_URL não configurada")
    with engine.begin() as connection:
        for asset_class, sector, factor, sensitivity, confidence, channel in validated_sector_exposures():
            connection.execute(text("""
                INSERT INTO macro_sector_exposures
                  (asset_class, sector, factor, sensitivity, confidence, channel)
                VALUES (:asset_class, :sector, :factor, :sensitivity, :confidence, :channel)
                ON CONFLICT (asset_class, sector, factor) DO UPDATE SET
                  sensitivity=EXCLUDED.sensitivity, confidence=EXCLUDED.confidence,
                  channel=EXCLUDED.channel, approved_at=NOW()
            """), {"asset_class": asset_class, "sector": sector, "factor": factor,
                   "sensitivity": sensitivity, "confidence": confidence, "channel": channel})
    print(f"macro_sector_exposures_seeded={len(validated_sector_exposures())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
