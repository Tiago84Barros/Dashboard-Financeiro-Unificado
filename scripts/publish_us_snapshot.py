"""Publica a vitrine Empresas Americanas (market_us.company_snapshots) no Supabase.

Espelha scripts/publish_fii_selection_snapshot.py: lê a vitrine JÁ CONSTRUÍDA no
warehouse local (run_us_ingest.py snapshot) e a carrega no Supabase, que passa a
servir a leitura no deploy. Só a vitrine vai para a nuvem — os históricos pesados
continuam warehouse-only.

Uso:
    # 1) construa a vitrine no warehouse
    python run_us_ingest.py snapshot --warehouse --json
    # 2) publique no Supabase
    python scripts/publish_us_snapshot.py \
        --source-url postgresql://postgres:<senha>@127.0.0.1:5433/postgres \
        --target-url <SUPABASE_UNIFICADO_URL> [--dry-run]

O target deve ser a conexão DIRETA do Supabase (não o pooler). A publicação é
idempotente: cria o schema/tabela via migration 044 e faz upsert por symbol.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_MIGRATION = ROOT / "supabase_unificado" / "schema" / "044_market_us_snapshot.sql"
# Colunas da vitrine (ordem estável para o upsert).
_COLS = [
    "symbol", "cik", "name", "sector", "industry", "exchange", "security_type",
    "is_reit", "is_active", "score", "score_quality", "score_growth",
    "score_solidity", "score_capital_efficiency", "score_valuation",
    "score_shareholder", "coverage", "metrics", "asymmetry", "advanced",
    "dossie", "financials", "last_fiscal_year", "score_version", "generated_at",
]
_JSON_COLS = {"metrics", "asymmetry", "advanced", "dossie", "financials"}


def _engine(url: str):
    parsed = make_url(url)
    if parsed.drivername in {"postgresql", "postgres"}:
        parsed = parsed.set(drivername="postgresql+psycopg2")
    if parsed.host and parsed.host not in {"localhost", "127.0.0.1", "::1"}:
        parsed = parsed.update_query_dict({"sslmode": "require"})
    return create_engine(parsed, pool_pre_ping=True, future=True)


def _build_upsert() -> str:
    cols = ", ".join(_COLS)
    # JSONB precisa de cast explícito no bind (o driver manda texto).
    binds = ", ".join(
        (f"CAST(:{c} AS JSONB)" if c in _JSON_COLS else f":{c}") for c in _COLS)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in _COLS if c != "symbol")
    return (f"INSERT INTO market_us.company_snapshots ({cols}) VALUES ({binds}) "
            f"ON CONFLICT (symbol) DO UPDATE SET {updates}, published_at = NOW()")


def main() -> int:
    p = argparse.ArgumentParser(description="Publica a vitrine EUA no Supabase")
    p.add_argument("--source-url", required=True, help="warehouse local (source da vitrine)")
    p.add_argument("--target-url", required=True, help="Supabase (conexão direta)")
    p.add_argument("--dry-run", action="store_true", help="não grava no target")
    args = p.parse_args()

    src, tgt = _engine(args.source_url), _engine(args.target_url)

    with src.connect() as conn:
        if not conn.execute(text(
                "SELECT to_regclass('market_us.company_snapshots')")).scalar():
            print("ERRO: vitrine ausente no source. Rode primeiro: "
                  "python run_us_ingest.py snapshot --warehouse")
            return 1
        rows = [dict(r._mapping) for r in conn.execute(text(
            f"SELECT {', '.join(_COLS)} FROM market_us.company_snapshots"))]
    print(f"vitrine no source: {len(rows)} empresa(s)")
    if not rows:
        return 1

    # JSONB vem como dict do psycopg2; reserializa para texto no bind.
    import json
    for r in rows:
        for c in _JSON_COLS:
            if isinstance(r.get(c), (dict, list)):
                r[c] = json.dumps(r[c], ensure_ascii=False)

    if args.dry_run:
        print(f"[dry-run] aplicaria a migration 044 e faria upsert de {len(rows)} linhas "
              "no Supabase. Nada gravado.")
        print("exemplo:", {k: rows[0][k] for k in ("symbol", "name", "sector", "score")})
        return 0

    migration = _MIGRATION.read_text(encoding="utf-8")
    with tgt.begin() as conn:
        conn.execute(text(migration))          # schema + tabela (idempotente)
        conn.execute(text(_build_upsert()), rows)
    print(f"publicado: {len(rows)} empresa(s) em market_us.company_snapshots (Supabase).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
