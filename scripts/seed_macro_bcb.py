"""
Cria/atualiza public.macro no App 4 a partir da fonte macro usada no App 1.

Fonte:
  - BCB/SGS serie 432: meta Selic definida pelo Copom (% a.a.)

Uso:
  python scripts/seed_macro_bcb.py
  python scripts/seed_macro_bcb.py --apply
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _target_url() -> str:
    return (
        os.getenv("SUPABASE_UNIFICADO_URL", "").strip()
        or os.getenv("DATABASE_URL", "").strip()
        or os.getenv("SUPABASE_DB_URL", "").strip()
    )


def _engine(url: str):
    kwargs = {"pool_pre_ping": True}
    if not url.startswith("sqlite"):
        kwargs["connect_args"] = {"connect_timeout": 15, "sslmode": "require"}
    return create_engine(url, **kwargs)


def _fetch_selic(start: date, end: date) -> pd.DataFrame:
    raw: list[dict] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(date(cursor.year + 8, cursor.month, cursor.day), end)
        url = (
            "https://api.bcb.gov.br/dados/serie/bcdata.sgs.432/dados"
            f"?formato=json&dataInicial={cursor:%d/%m/%Y}&dataFinal={chunk_end:%d/%m/%Y}"
        )
        response = requests.get(url, headers={"User-Agent": "MacroSeed/1.0"}, timeout=45)
        response.raise_for_status()
        chunk = response.json()
        if isinstance(chunk, list):
            raw.extend(chunk)
        cursor = chunk_end + timedelta(days=1)

    if not raw:
        raise RuntimeError("BCB/SGS nao retornou dados para a Selic.")

    df = pd.DataFrame(raw)
    df["data"] = pd.to_datetime(df["data"], format="%d/%m/%Y", errors="coerce")
    df["selic"] = pd.to_numeric(
        df["valor"].astype(str).str.replace(",", ".", regex=False),
        errors="coerce",
    )
    df = df.dropna(subset=["data", "selic"]).sort_values("data")
    annual = (
        df.set_index("data")[["selic"]]
        .resample("YE-DEC")
        .last()
        .dropna()
        .reset_index()
    )
    annual["ano"] = annual["data"].dt.year.astype(int)
    annual["selic"] = annual["selic"] / 100.0
    return annual[["ano", "data", "selic"]]


def _upsert_macro(conn, df: pd.DataFrame, apply: bool) -> int:
    create_sql = """
    CREATE TABLE IF NOT EXISTS public.macro (
        ano integer PRIMARY KEY,
        data date,
        selic double precision NOT NULL,
        fonte text DEFAULT 'BCB/SGS 432',
        updated_at timestamp with time zone DEFAULT now()
    )
    """
    alter_sql = [
        "ALTER TABLE public.macro ADD COLUMN IF NOT EXISTS data date",
        "ALTER TABLE public.macro ADD COLUMN IF NOT EXISTS selic double precision",
        "ALTER TABLE public.macro ADD COLUMN IF NOT EXISTS fonte text DEFAULT 'BCB/SGS 432'",
        "ALTER TABLE public.macro ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone DEFAULT now()",
    ]
    upsert_sql = """
    INSERT INTO public.macro (ano, data, selic, fonte, updated_at)
    VALUES (:ano, :data, :selic, 'BCB/SGS 432', now())
    ON CONFLICT (ano) DO UPDATE SET
        data = EXCLUDED.data,
        selic = EXCLUDED.selic,
        fonte = EXCLUDED.fonte,
        updated_at = now()
    """
    if apply:
        conn.execute(text(create_sql))
        for sql in alter_sql:
            conn.execute(text(sql))
        records = df.to_dict(orient="records")
        conn.execute(text(upsert_sql), records)
    return len(df)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Grava em public.macro.")
    parser.add_argument("--start", default="2010-01-01")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    target_url = _target_url()
    if not target_url:
        print("Banco App4 ausente. Configure SUPABASE_UNIFICADO_URL, DATABASE_URL ou SUPABASE_DB_URL.")
        return 2

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.today().date() - timedelta(days=2)
    df = _fetch_selic(start, end)
    print(f"BCB/SGS 432: {len(df)} ano(s), {df['ano'].min()}-{df['ano'].max()}.")

    engine = _engine(target_url)
    with engine.begin() as conn:
        count = _upsert_macro(conn, df, apply=args.apply)

    mode = "Gravado" if args.apply else "Dry-run"
    print(f"{mode}: {count} linha(s) em public.macro.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
