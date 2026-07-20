"""Perform an optional, non-disclosing read-only database smoke test."""
from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

settings = importlib.import_module("core.config").settings


def configured_kind() -> str:
    if not settings.db_url:
        return "not configured"
    scheme = urlparse(settings.db_url).scheme.lower()
    return "sqlite" if scheme.startswith("sqlite") else "postgresql" if scheme.startswith("postgres") else "other"


def execute_readonly() -> None:
    from sqlalchemy import create_engine, text

    if not settings.db_url:
        raise RuntimeError("database is not configured")
    engine = create_engine(settings.db_url, pool_pre_ping=True, connect_args={"connect_timeout": 10} if configured_kind() == "postgresql" else {})
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            if configured_kind() == "postgresql":
                connection.execute(text("SET TRANSACTION READ ONLY"))
            assert connection.execute(text("SELECT 1")).scalar_one() == 1
        finally:
            transaction.rollback()
    engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="connect and run SELECT 1 in a rolled-back read-only transaction")
    args = parser.parse_args()
    print(f"Database configuration: {configured_kind()} (connection value withheld).")
    if args.execute:
        execute_readonly()
        print("OK: read-only SELECT 1 succeeded; transaction rolled back.")
    else:
        print("Not executed. Pass --execute to perform the read-only smoke test.")
