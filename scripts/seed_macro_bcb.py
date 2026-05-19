"""
Cria/atualiza public.macro no App 4 a partir das fontes macro do App 1.

Fonte:
  - BCB/SGS: Selic, IPCA, cambio, balanca comercial, ICC, PIB e divida publica.

Uso:
  python scripts/seed_macro_bcb.py
  python scripts/seed_macro_bcb.py --apply
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

import pandas as pd
import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SERIES: dict[str, int] = {
    "selic": 432,
    "ipca": 433,
    "cambio": 1,
    "balanca_comercial": 22707,
    "icc": 4393,
    "pib": 4380,
    "divida_publica": 4502,
}

MACRO_COLUMNS = [
    "selic",
    "ipca",
    "cambio",
    "balanca_comercial",
    "icc",
    "icc_delta",
    "pib",
    "divida_publica",
    "juros_real_ex_ante",
]


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


def _sgs_url(code: int, start: date, end: date) -> str:
    return (
        f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados"
        f"?formato=json&dataInicial={start:%d/%m/%Y}&dataFinal={end:%d/%m/%Y}"
    )


def _add_years(d: date, years: int) -> date:
    try:
        return date(d.year + years, d.month, d.day)
    except ValueError:
        return date(d.year + years, d.month, 28)


def _fetch_sgs_series(name: str, code: int, start: date, end: date) -> pd.DataFrame:
    raw: list[dict] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(_add_years(cursor, 8), end)
        response = None
        for attempt in range(3):
            try:
                response = requests.get(
                    _sgs_url(code, cursor, chunk_end),
                    headers={"User-Agent": "MacroSeed/1.0"},
                    timeout=45,
                )
                if response.status_code < 500:
                    break
            except requests.exceptions.RequestException:
                pass
            time.sleep(5 * (attempt + 1))
        if response is None or response.status_code >= 500:
            cursor = chunk_end + timedelta(days=1)
            continue
        if response.status_code == 404 and "Value(s) not found" in (response.text or ""):
            cursor = chunk_end + timedelta(days=1)
            continue
        response.raise_for_status()
        if not response.text.strip():
            cursor = chunk_end + timedelta(days=1)
            continue
        try:
            chunk = response.json()
        except ValueError:
            cursor = chunk_end + timedelta(days=1)
            continue
        if isinstance(chunk, list):
            raw.extend(chunk)
        cursor = chunk_end + timedelta(days=1)
        time.sleep(0.15)

    if not raw:
        return pd.DataFrame(columns=[name])

    df = pd.DataFrame(raw)
    df["data"] = pd.to_datetime(df["data"], format="%d/%m/%Y", errors="coerce")
    df[name] = pd.to_numeric(
        df["valor"].astype(str).str.replace(",", ".", regex=False),
        errors="coerce",
    )
    df = df.dropna(subset=["data", name]).sort_values("data")
    df = df.drop_duplicates(subset=["data"], keep="last")
    return df.set_index("data")[[name]]


def _annual_last(df: pd.DataFrame, col: str) -> pd.DataFrame:
    out = df.resample("YE-DEC").last()
    out.columns = [col]
    return out


def _annual_mean(df: pd.DataFrame, col: str) -> pd.DataFrame:
    out = df.resample("YE-DEC").mean()
    out.columns = [col]
    return out


def _annual_sum(df: pd.DataFrame, col: str) -> pd.DataFrame:
    out = df.resample("YE-DEC").sum()
    out.columns = [col]
    return out


def _annual_compound_pct(df: pd.DataFrame, col: str) -> pd.DataFrame:
    out = df.resample("YE-DEC").apply(lambda x: (1 + (x / 100.0)).prod() - 1)
    out.columns = [col]
    out[col] = out[col] * 100.0
    return out


def _build_macro(dados: dict[str, pd.DataFrame], icc_mode: str = "final") -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    transforms: list[tuple[str, str, Callable[[pd.DataFrame, str], pd.DataFrame]]] = [
        ("selic", "selic", _annual_last),
        ("cambio", "cambio", _annual_last),
        ("divida_publica", "divida_publica", _annual_last),
        ("pib", "pib", _annual_sum),
        ("balanca_comercial", "balanca_comercial", _annual_sum),
    ]
    for name, col, fn in transforms:
        if not dados[name].empty:
            parts.append(fn(dados[name], col))

    if not dados["ipca"].empty:
        parts.append(_annual_compound_pct(dados["ipca"], "ipca"))

    if not dados["icc"].empty:
        icc_fn = _annual_mean if icc_mode == "mean" else _annual_last
        parts.append(icc_fn(dados["icc"], "icc"))

    if not parts:
        return pd.DataFrame()

    df = pd.concat(parts, axis=1).sort_index()
    df.index.name = "data"
    df = df.reset_index()
    df["ano"] = pd.to_datetime(df["data"]).dt.year.astype(int)

    if "selic" in df.columns:
        df["selic"] = pd.to_numeric(df["selic"], errors="coerce") / 100.0
    if "icc" in df.columns:
        df["icc_delta"] = pd.to_numeric(df["icc"], errors="coerce").diff()
    if "selic" in df.columns and "ipca" in df.columns:
        df["juros_real_ex_ante"] = (df["selic"] * 100.0) - df["ipca"]

    for col in MACRO_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df[["ano", "data", *MACRO_COLUMNS]].dropna(subset=MACRO_COLUMNS, how="all")


def _fetch_macro(start: date, end: date) -> tuple[pd.DataFrame, dict[str, int]]:
    dados: dict[str, pd.DataFrame] = {}
    counts: dict[str, int] = {}
    for name, code in SERIES.items():
        df = _fetch_sgs_series(name, code, start, end)
        dados[name] = df
        counts[name] = len(df)
        print(f"BCB/SGS {code} {name}: {len(df)} ponto(s).")
    return _build_macro(dados), counts


def _upsert_macro(conn, df: pd.DataFrame, apply: bool) -> int:
    create_sql = """
    CREATE TABLE IF NOT EXISTS public.macro (
        ano integer PRIMARY KEY,
        data date,
        fonte text DEFAULT 'BCB/SGS',
        updated_at timestamp with time zone DEFAULT now()
    )
    """
    alter_sql = [
        "ALTER TABLE public.macro ADD COLUMN IF NOT EXISTS data date",
        "ALTER TABLE public.macro ADD COLUMN IF NOT EXISTS fonte text DEFAULT 'BCB/SGS'",
        "ALTER TABLE public.macro ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone DEFAULT now()",
        *[f"ALTER TABLE public.macro ADD COLUMN IF NOT EXISTS {col} double precision" for col in MACRO_COLUMNS],
    ]
    cols = ["ano", "data", *MACRO_COLUMNS]
    cols_sql = ", ".join(cols + ["fonte", "updated_at"])
    placeholders = ", ".join(f":{c}" for c in cols) + ", 'BCB/SGS', now()"
    updates = ", ".join(
        [f"{c} = EXCLUDED.{c}" for c in ["data", *MACRO_COLUMNS]]
        + ["fonte = EXCLUDED.fonte", "updated_at = now()"]
    )
    upsert_sql = f"""
    INSERT INTO public.macro ({cols_sql})
    VALUES ({placeholders})
    ON CONFLICT (ano) DO UPDATE SET {updates}
    """
    if apply:
        conn.execute(text(create_sql))
        for sql in alter_sql:
            conn.execute(text(sql))
        records = df.where(pd.notna(df), None).to_dict(orient="records")
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
    df, _ = _fetch_macro(start, end)
    if df.empty:
        print("Nenhuma serie macro anual valida foi retornada.")
        return 1
    filled = [c for c in MACRO_COLUMNS if df[c].notna().any()]
    print(f"Macro anual: {len(df)} ano(s), {df['ano'].min()}-{df['ano'].max()}.")
    print(f"Variaveis com dados: {len(filled)} ({', '.join(filled)}).")

    engine = _engine(target_url)
    with engine.begin() as conn:
        count = _upsert_macro(conn, df, apply=args.apply)

    mode = "Gravado" if args.apply else "Dry-run"
    print(f"{mode}: {count} linha(s) em public.macro.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
