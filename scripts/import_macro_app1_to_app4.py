"""
Importa a tabela public.macro do App 1 para o App 4.

Uso:
  python scripts/import_macro_app1_to_app4.py
  python scripts/import_macro_app1_to_app4.py --apply

Variaveis esperadas:
  - SOURCE_DB_APP1 ou SUPABASE_DB_URL_B3: banco origem do App 1
  - SUPABASE_UNIFICADO_URL, DATABASE_URL ou SUPABASE_DB_URL: banco destino do App 4

O script e idempotente: cria public.macro se ela nao existir, adiciona colunas
ausentes e faz upsert por ano. Em dry-run ele apenas valida e relata.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _engine(url: str):
    if not url:
        raise RuntimeError("URL de banco ausente.")
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if not url.startswith("sqlite"):
        kwargs.update(
            {
                "pool_size": 1,
                "max_overflow": 1,
                "connect_args": {"connect_timeout": 15, "sslmode": "require"},
            }
        )
    return create_engine(url, **kwargs)


def _source_url() -> str:
    return (
        os.getenv("SOURCE_DB_APP1", "").strip()
        or os.getenv("SUPABASE_DB_URL_B3", "").strip()
    )


def _target_url() -> str:
    return (
        os.getenv("SUPABASE_UNIFICADO_URL", "").strip()
        or os.getenv("DATABASE_URL", "").strip()
        or os.getenv("SUPABASE_DB_URL", "").strip()
    )


def _pg_type(data_type: str, udt_name: str | None = None) -> str:
    dt = (data_type or "").lower()
    udt = (udt_name or "").lower()
    if dt in {"smallint", "integer", "bigint"}:
        return dt
    if dt in {"real", "double precision"} or udt in {"float4", "float8"}:
        return "double precision"
    if dt in {"numeric", "decimal"}:
        return "numeric"
    if dt == "boolean":
        return "boolean"
    if dt == "date":
        return "date"
    if "timestamp" in dt:
        return "timestamp with time zone" if "with time zone" in dt else "timestamp"
    return "text"


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _source_columns(conn) -> list[dict[str, str]]:
    rows = conn.execute(
        text(
            """
            SELECT column_name, data_type, udt_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'macro'
            ORDER BY ordinal_position
            """
        )
    ).mappings()
    return [dict(r) for r in rows]


def _create_or_extend_target(conn, columns: list[dict[str, str]], apply: bool) -> None:
    if not columns:
        raise RuntimeError("Tabela public.macro nao encontrada na origem.")

    names = [c["column_name"] for c in columns]
    if "ano" not in names:
        raise RuntimeError("Tabela public.macro da origem nao tem coluna obrigatoria 'ano'.")

    insp = inspect(conn)
    exists = insp.has_table("macro", schema="public")

    if not exists:
        pieces = ['"ano" integer PRIMARY KEY']
        for col in columns:
            name = col["column_name"]
            if name == "ano":
                continue
            pieces.append(f"{_quote(name)} {_pg_type(col['data_type'], col.get('udt_name'))}")
        sql = f"CREATE TABLE public.macro ({', '.join(pieces)})"
        if apply:
            conn.execute(text(sql))
        return

    target_cols = {c["name"] for c in inspect(conn).get_columns("macro", schema="public")}
    for col in columns:
        name = col["column_name"]
        if name in target_cols:
            continue
        sql = (
            f"ALTER TABLE public.macro ADD COLUMN {_quote(name)} "
            f"{_pg_type(col['data_type'], col.get('udt_name'))}"
        )
        if apply:
            conn.execute(text(sql))


def _normalize_macro(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["ano"] = pd.to_numeric(out["ano"], errors="coerce").astype("Int64")
    out = out.dropna(subset=["ano"]).copy()
    out["ano"] = out["ano"].astype(int)

    if "selic" in out.columns:
        selic = pd.to_numeric(out["selic"], errors="coerce")
        out["selic"] = selic.where(selic.abs() <= 1, selic / 100.0)
    return out


def _upsert(conn, df: pd.DataFrame, apply: bool) -> int:
    if df.empty:
        return 0
    columns = [str(c) for c in df.columns]
    non_key = [c for c in columns if c != "ano"]
    values = ", ".join(f":{c}" for c in columns)
    cols_sql = ", ".join(_quote(c) for c in columns)
    update_sql = ", ".join(f"{_quote(c)} = EXCLUDED.{_quote(c)}" for c in non_key)
    sql = (
        f"INSERT INTO public.macro ({cols_sql}) VALUES ({values}) "
        f"ON CONFLICT (\"ano\") DO UPDATE SET {update_sql}"
        if non_key
        else f"INSERT INTO public.macro ({cols_sql}) VALUES ({values}) ON CONFLICT (\"ano\") DO NOTHING"
    )

    records = df.where(pd.notna(df), None).to_dict(orient="records")
    if apply:
        conn.execute(text(sql), records)
    return len(records)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Grava no App 4.")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    source_url = _source_url()
    target_url = _target_url()

    if not source_url:
        print("SOURCE_DB_APP1/SUPABASE_DB_URL_B3 ausente. Configure a URL do App 1 para importar public.macro.")
        return 2
    if not target_url:
        print("SUPABASE_UNIFICADO_URL ausente. Configure a URL do App 4 como destino.")
        return 2
    if source_url == target_url:
        print("Origem e destino apontam para a mesma URL. Nada a importar entre apps.")
        return 2

    src = _engine(source_url)
    dst = _engine(target_url)

    with src.connect() as src_conn:
        columns = _source_columns(src_conn)
        df = pd.read_sql_query(text("SELECT * FROM public.macro ORDER BY ano"), src_conn)

    df = _normalize_macro(df)
    if df.empty:
        print("public.macro existe na origem, mas nao retornou linhas validas.")
        return 1

    print(f"Origem public.macro: {len(df)} linha(s), anos {df['ano'].min()}-{df['ano'].max()}.")
    with dst.begin() as dst_conn:
        _create_or_extend_target(dst_conn, columns, apply=args.apply)
        count = _upsert(dst_conn, df, apply=args.apply)

    mode = "Importado" if args.apply else "Dry-run"
    print(f"{mode}: {count} linha(s) em public.macro do App 4.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
