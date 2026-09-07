"""Sincronização mínima e unidirecional de posições para o banco macro local."""

from __future__ import annotations

from sqlalchemy import text

from core.portfolio.registry import SPECS


def sync_portfolio_assets(*, source_engine, local_engine) -> int:
    """Replica somente símbolos, pesos, moeda, data e digest de snapshots ativos.

    A origem é consultada em leitura; o destino é exclusivamente o PostgreSQL
    Docker macro. Identificadores de tabela vêm do registro interno allowlisted.
    """
    rows: list[dict] = []
    with source_engine.connect() as source:
        for asset_class, spec in sorted(SPECS.items()):
            sector_column = "tipo" if asset_class == "fii" else "setor"
            result = source.execute(
                text(
                    f"""
                    SELECT s.model_id, s.symbol, s.as_of_date, s.payload_digest, i.weight,
                           i.{sector_column} AS sector
                    FROM portfolio_asset_snapshots s
                    JOIN {spec.items_table} i
                      ON i.model_id = s.model_id AND i.{spec.symbol_column} = s.symbol
                    WHERE s.asset_class = :asset_class
                    """
                ),
                {"asset_class": asset_class},
            ).mappings()
            rows.extend(
                {
                    "asset_class": asset_class,
                    "model_id": str(row["model_id"]),
                    "symbol": str(row["symbol"]),
                    "weight": float(row["weight"] or 0),
                    "currency": spec.currency,
                    "sector": str(row["sector"] or "").strip()[:120] or None,
                    "as_of_date": row["as_of_date"],
                    "source_digest": str(row["payload_digest"]),
                }
                for row in result
            )
    if not rows:
        return 0
    with local_engine.begin() as local:
        for row in rows:
            local.execute(
                text(
                    """
                    INSERT INTO macro_portfolio_assets
                      (asset_class, model_id, symbol, weight, currency, sector, as_of_date, source_digest)
                    VALUES
                      (:asset_class, :model_id, :symbol, :weight, :currency, :sector, :as_of_date, :source_digest)
                    ON CONFLICT (asset_class, model_id, symbol) DO UPDATE SET
                      weight = EXCLUDED.weight,
                      currency = EXCLUDED.currency,
                      sector = EXCLUDED.sector,
                      as_of_date = EXCLUDED.as_of_date,
                      source_digest = EXCLUDED.source_digest,
                      imported_at = NOW()
                    """
                ),
                row,
            )
    return len(rows)
