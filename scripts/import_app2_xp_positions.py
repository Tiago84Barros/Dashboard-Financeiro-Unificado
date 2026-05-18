"""
Importa snapshots XP do App2 (SQLite) para o App4.

Uso:
  python scripts/import_app2_xp_positions.py          # dry run
  python scripts/import_app2_xp_positions.py --apply  # grava no App4
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import uuid
from pathlib import Path

from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.config import settings  # noqa: E402

PORTFOLIO_NAME = "Carteira Principal"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS portfolio_position_snapshots (
    id              UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID          NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    portfolio_id    UUID          NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
    asset_id        UUID          NOT NULL REFERENCES assets(id) ON DELETE RESTRICT,
    report_date     DATE          NOT NULL,
    quantity        NUMERIC(18,8) NOT NULL,
    market_price    NUMERIC(15,6),
    market_value    NUMERIC(15,2) NOT NULL,
    invested_value  NUMERIC(15,2),
    original_market_value  NUMERIC(15,2),
    original_invested_value NUMERIC(15,2),
    fx_rate_to_brl  NUMERIC(15,6),
    asset_name      VARCHAR(300),
    asset_type      VARCHAR(50),
    is_loaned       BOOLEAN       NOT NULL DEFAULT FALSE,
    institution     VARCHAR(150),
    currency        CHAR(3)       NOT NULL DEFAULT 'BRL',
    country         CHAR(2)       NOT NULL DEFAULT 'BR',
    source_system   VARCHAR(30)   NOT NULL DEFAULT 'app2',
    source_table    VARCHAR(50)   NOT NULL DEFAULT 'xp_positions',
    source_id       TEXT,
    imported_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    UNIQUE (portfolio_id, asset_id, report_date, source_system, source_table, source_id)
)
"""

CREATE_INDEXES_SQL = [
    "ALTER TABLE portfolio_position_snapshots ADD COLUMN IF NOT EXISTS original_market_value NUMERIC(15,2)",
    "ALTER TABLE portfolio_position_snapshots ADD COLUMN IF NOT EXISTS original_invested_value NUMERIC(15,2)",
    "ALTER TABLE portfolio_position_snapshots ADD COLUMN IF NOT EXISTS fx_rate_to_brl NUMERIC(15,6)",
    """
    CREATE INDEX IF NOT EXISTS idx_position_snapshots_user_date
        ON portfolio_position_snapshots (user_id, report_date DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_position_snapshots_asset_date
        ON portfolio_position_snapshots (asset_id, report_date DESC)
    """,
]

ASSET_CLASS_MAP = {
    "stock": "stock",
    "fii": "reit",
    "reit": "reit",
    "etf": "etf",
    "tesouro": "fixed_income",
    "renda_fixa": "fixed_income",
    "fixed_income": "fixed_income",
    "fip": "fixed_income",
    "other": "other",
}


def _discover_sqlite() -> Path | None:
    configured = (settings.SOURCE_DB_APP2 or "").strip()
    if configured:
        raw = configured.removeprefix("sqlite:///")
        path = Path(raw)
        if path.exists():
            return path

    root = Path.home() / "OneDrive"
    matches = [
        p for p in root.rglob("investment_dashboard.db")
        if "Dashboard-Investimentos" in str(p)
    ]
    return matches[0] if matches else None


def _load_xp_rows(sqlite_path: Path) -> list[dict]:
    con = sqlite3.connect(str(sqlite_path))
    con.row_factory = sqlite3.Row
    tables = {
        row[0]
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "xp_positions" not in tables:
        return []
    rows = con.execute("""
        SELECT
            id, report_date, ticker, asset_name, asset_type, quantity,
            market_price, market_value, invested_value, is_loaned,
            institution, currency, country
        FROM xp_positions
        WHERE ticker IS NOT NULL
          AND report_date IS NOT NULL
          AND quantity IS NOT NULL
          AND market_value IS NOT NULL
        ORDER BY report_date, id
    """).fetchall()
    return [dict(row) for row in rows]


def _asset_class(raw_type: str | None) -> str:
    value = (raw_type or "other").strip().lower()
    return ASSET_CLASS_MAP.get(value, "other")


def _ensure_portfolio(conn, owner_id: str, apply: bool) -> str:
    row = conn.execute(
        text("""
            SELECT id
            FROM portfolios
            WHERE user_id = :uid AND name = :name
            LIMIT 1
        """),
        {"uid": owner_id, "name": PORTFOLIO_NAME},
    ).fetchone()
    if row:
        return str(row[0])
    if not apply:
        return "dry_portfolio"

    new_id = str(uuid.uuid4())
    conn.execute(
        text("""
            INSERT INTO portfolios (id, user_id, name, type, active)
            VALUES (:id, :uid, :name, 'personal', true)
        """),
        {"id": new_id, "uid": owner_id, "name": PORTFOLIO_NAME},
    )
    return new_id


def _ensure_assets(conn, rows: list[dict], apply: bool) -> dict[str, str]:
    existing = {
        ticker.upper(): str(asset_id)
        for ticker, asset_id in conn.execute(
            text("SELECT ticker, id FROM assets")
        ).fetchall()
    }

    inserted = 0
    for row in rows:
        ticker = str(row["ticker"]).strip().upper()
        if not ticker or ticker in existing:
            continue

        if apply:
            asset_id = str(uuid.uuid4())
            conn.execute(
                text("""
                    INSERT INTO assets (id, ticker, name, class, currency)
                    VALUES (:id, :ticker, :name, :class, :currency)
                    ON CONFLICT (ticker) DO UPDATE
                    SET name = EXCLUDED.name,
                        class = EXCLUDED.class,
                        currency = EXCLUDED.currency
                    RETURNING id
                """),
                {
                    "id": asset_id,
                    "ticker": ticker,
                    "name": row.get("asset_name") or ticker,
                    "class": _asset_class(row.get("asset_type")),
                    "currency": row.get("currency") or "BRL",
                },
            )
            existing[ticker] = asset_id
        else:
            existing[ticker] = f"dry_{ticker}"
        inserted += 1

    print(f"Ativos XP ausentes cadastrados: {inserted}")
    return existing


def _import_snapshots(conn, rows: list[dict], asset_map: dict[str, str], portfolio_id: str, owner_id: str, apply: bool) -> int:
    imported = 0
    for row in rows:
        ticker = str(row["ticker"]).strip().upper()
        asset_id = asset_map.get(ticker)
        if not asset_id:
            continue
        imported += 1
        if not apply:
            continue
        conn.execute(
            text("""
                INSERT INTO portfolio_position_snapshots (
                    user_id, portfolio_id, asset_id, report_date, quantity,
                    market_price, market_value, invested_value, asset_name,
                    asset_type, is_loaned, institution, currency, country,
                    source_system, source_table, source_id
                )
                VALUES (
                    :uid, :portfolio_id, :asset_id, :report_date, :quantity,
                    :market_price, :market_value, :invested_value, :asset_name,
                    :asset_type, :is_loaned, :institution, :currency, :country,
                    'app2', 'xp_positions', :source_id
                )
                ON CONFLICT (portfolio_id, asset_id, report_date, source_system, source_table, source_id)
                DO UPDATE SET
                    quantity = EXCLUDED.quantity,
                    market_price = EXCLUDED.market_price,
                    market_value = EXCLUDED.market_value,
                    invested_value = EXCLUDED.invested_value,
                    asset_name = EXCLUDED.asset_name,
                    asset_type = EXCLUDED.asset_type,
                    is_loaned = EXCLUDED.is_loaned,
                    institution = EXCLUDED.institution,
                    currency = EXCLUDED.currency,
                    country = EXCLUDED.country,
                    imported_at = NOW()
            """),
            {
                "uid": owner_id,
                "portfolio_id": portfolio_id,
                "asset_id": asset_id,
                "report_date": str(row["report_date"])[:10],
                "quantity": row["quantity"],
                "market_price": row.get("market_price"),
                "market_value": row["market_value"],
                "invested_value": row.get("invested_value"),
                "asset_name": row.get("asset_name"),
                "asset_type": row.get("asset_type"),
                "is_loaned": bool(row.get("is_loaned")),
                "institution": row.get("institution"),
                "currency": row.get("currency") or "BRL",
                "country": row.get("country") or "BR",
                "source_id": str(row["id"]),
            },
        )
    return imported


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Grava no App4")
    args = parser.parse_args()

    if not settings.db_url or not settings.OWNER_USER_ID:
        print("Banco App4 ou OWNER_USER_ID ausente.")
        return 1

    sqlite_path = _discover_sqlite()
    if not sqlite_path:
        print("SQLite do App2 nao encontrado.")
        return 1

    rows = _load_xp_rows(sqlite_path)
    if not rows:
        print("Nenhuma linha em xp_positions.")
        return 0

    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"Modo: {mode}")
    print(f"SQLite App2: {sqlite_path}")
    print(f"Snapshots XP lidos: {len(rows)}")

    engine = create_engine(settings.db_url, pool_pre_ping=True)
    with engine.begin() as conn:
        if args.apply:
            conn.execute(text(CREATE_TABLE_SQL))
            for sql in CREATE_INDEXES_SQL:
                conn.execute(text(sql))

        portfolio_id = _ensure_portfolio(conn, settings.OWNER_USER_ID, args.apply)
        asset_map = _ensure_assets(conn, rows, args.apply)
        imported = _import_snapshots(
            conn, rows, asset_map, portfolio_id, settings.OWNER_USER_ID, args.apply
        )

        if args.apply:
            total = conn.execute(
                text("""
                    SELECT COUNT(*)
                    FROM portfolio_position_snapshots
                    WHERE user_id = :uid
                """),
                {"uid": settings.OWNER_USER_ID},
            ).scalar()
            dates = conn.execute(
                text("""
                    SELECT MIN(report_date), MAX(report_date), COUNT(DISTINCT report_date)
                    FROM portfolio_position_snapshots
                    WHERE user_id = :uid
                """),
                {"uid": settings.OWNER_USER_ID},
            ).fetchone()
            print(f"Snapshots gravados/atualizados: {imported}")
            print(f"Total no App4: {total}")
            print(f"Datas: {dates[0]}..{dates[1]} ({dates[2]} datas)")
        else:
            print(f"Snapshots que seriam importados: {imported}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
