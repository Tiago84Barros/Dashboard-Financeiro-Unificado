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
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv()  # popula os.environ a partir do .env (find_dotenv sobe diretórios)


def _exec_retry(engine, stmt, params=None, tries: int = 5) -> None:
    """Executa uma transação com retry — o pooler do Supabase derruba conexões
    intermitentemente ('server terminated abnormally'). Backoff curto."""
    import time
    from sqlalchemy.exc import DBAPIError, OperationalError
    last = None
    for attempt in range(1, tries + 1):
        try:
            with engine.begin() as conn:
                if params is not None:
                    conn.execute(stmt, params)
                else:
                    conn.execute(stmt)
            return
        except (OperationalError, DBAPIError) as exc:
            last = exc
            time.sleep(1.5 * attempt)
    raise last


def _mask(url: str) -> str:
    """Host/porta sem a senha, para exibir com segurança."""
    try:
        u = make_url(url)
        return f"{u.host}:{u.port or 5432}/{u.database}"
    except Exception:  # noqa: BLE001
        return "(url ilegível)"

_MIGRATION = ROOT / "supabase_unificado" / "schema" / "044_market_us_snapshot.sql"
# Colunas da vitrine (ordem estável para o upsert).
_COLS = [
    "symbol", "cik", "name", "sector", "industry", "exchange", "security_type",
    "is_reit", "is_active", "score", "score_quality", "score_growth",
    "score_solidity", "score_capital_efficiency", "score_valuation",
    "score_shareholder", "coverage", "score_confidence", "score_status",
    "critical_missing", "metrics", "asymmetry", "advanced", "dossie",
    "financials", "last_fiscal_year", "score_version", "generated_at",
]
_JSON_COLS = {"critical_missing", "metrics", "asymmetry", "advanced", "dossie", "financials"}


def _engine(url: str):
    from sqlalchemy.pool import NullPool
    parsed = make_url(url)
    if parsed.drivername in {"postgresql", "postgres"}:
        parsed = parsed.set(drivername="postgresql+psycopg2")
    connect_args: dict = {"connect_timeout": 15}
    is_remote = bool(parsed.host and parsed.host not in {"localhost", "127.0.0.1", "::1"})
    if is_remote:
        parsed = parsed.update_query_dict({"sslmode": "require"})
        # Pooler do Supabase: conexão FRESCA por transação (NullPool) evita
        # conexões empoçadas que ficam meio-abertas e penduram o execute();
        # keepalives detectam socket morto; statement_timeout limita a query.
        connect_args["options"] = "-c statement_timeout=90000"
        connect_args.update(keepalives=1, keepalives_idle=10,
                            keepalives_interval=5, keepalives_count=3)
        if os.getenv("SUPABASE_DB_HOSTADDR"):
            connect_args["hostaddr"] = os.environ["SUPABASE_DB_HOSTADDR"]
    kwargs: dict = {"future": True, "connect_args": connect_args}
    if is_remote:
        kwargs["poolclass"] = NullPool
    else:
        kwargs["pool_pre_ping"] = True
    return create_engine(parsed, **kwargs)


def _build_upsert() -> str:
    cols = ", ".join(_COLS)
    # JSONB precisa de cast explícito no bind (o driver manda texto).
    binds = ", ".join(
        (f"CAST(:{c} AS JSONB)" if c in _JSON_COLS else f":{c}") for c in _COLS)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in _COLS if c != "symbol")
    return (f"INSERT INTO market_us.company_snapshots ({cols}) VALUES ({binds}) "
            f"ON CONFLICT (symbol) DO UPDATE SET {updates}, published_at = NOW()")


def _build_deactivate_stale() -> str:
    """Preserva, mas retira do universo ativo, registros ausentes do snapshot."""
    return (
        "UPDATE market_us.company_snapshots "
        "SET is_active = FALSE, score_status = 'stale', published_at = NOW() "
        "WHERE NOT (symbol = ANY(:symbols))"
    )


def _ensure_schema(engine) -> str:
    """Aplica somente o DDL necessário e mantém RLS/menor privilégio."""
    with engine.connect() as conn:
        exists = bool(conn.execute(text(
            "SELECT to_regclass('market_us.company_snapshots')"
        )).scalar())
        columns = set(conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='market_us' AND table_name='company_snapshots'
        """)).scalars()) if exists else set()
    if not exists:
        _exec_retry(engine, text(_MIGRATION.read_text(encoding="utf-8")))
        return "criado"
    missing = {
        "score_confidence": "NUMERIC(6,2)",
        "score_status": "TEXT",
        "critical_missing": "JSONB",
    }
    missing_columns = [column for column in missing if column not in columns]
    if not missing_columns:
        # O primeiro upgrade já aplicou RLS/revogações. Evita adquirir lock DDL
        # novamente em cada retomada de uma carga grande.
        return "verificado"
    for column, data_type in missing.items():
        if column not in columns:
            _exec_retry(engine, text(
                f"ALTER TABLE market_us.company_snapshots "
                f"ADD COLUMN IF NOT EXISTS {column} {data_type}"
            ))
    _exec_retry(engine, text("""
        ALTER TABLE market_us.company_snapshots ENABLE ROW LEVEL SECURITY;
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
                REVOKE ALL ON TABLE market_us.company_snapshots FROM anon;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
                REVOKE ALL ON TABLE market_us.company_snapshots FROM authenticated;
            END IF;
        END $$
    """))
    return "atualizado"


def main() -> int:
    p = argparse.ArgumentParser(description="Publica a vitrine EUA no Supabase")
    p.add_argument("--source-url", default=os.getenv("US_WAREHOUSE_URL",
                   "postgresql://postgres:changeme@127.0.0.1:5433/postgres"),
                   help="warehouse local (default: 127.0.0.1:5433 changeme)")
    p.add_argument("--target-url", default=None,
                   help="Supabase (default: SUPABASE_UNIFICADO_URL do .env). Use a "
                        "conexão com privilégio de escrita/DDL.")
    p.add_argument("--dry-run", action="store_true", help="não grava no target")
    p.add_argument("--batch", type=int, default=150,
                   help="linhas por transação (lotes pequenos p/ o pooler do Supabase)")
    args = p.parse_args()

    target = args.target_url or os.getenv("SUPABASE_UNIFICADO_URL") \
        or os.getenv("DATABASE_URL")
    if not target:
        try:  # mesma resolução do app (lê o .env via find_dotenv que sobe diretórios)
            from core.config import settings
            target = settings.db_url or None
        except Exception:  # noqa: BLE001
            target = None
    if not target:
        print("ERRO: sem target. Defina SUPABASE_UNIFICADO_URL no .env ou passe --target-url.")
        return 1
    print(f"source: {_mask(args.source_url)}  ->  target: {_mask(target)}")
    src, tgt = _engine(args.source_url), _engine(target)

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

    schema_status = _ensure_schema(tgt)
    print(f"schema market_us.company_snapshots {schema_status} no Supabase.")

    # Retomada por identidade do artefato. Evita regravar milhares de JSONB já
    # confirmados quando o Supabase interrompe um lote perto do fim.
    with tgt.connect() as conn:
        existing = {
            (str(row.symbol), str(row.score_version or ""), row.generated_at)
            for row in conn.execute(text("""
                SELECT symbol,score_version,generated_at
                FROM market_us.company_snapshots
            """))
        }
    pending = [
        row for row in rows
        if (str(row["symbol"]), str(row.get("score_version") or ""), row.get("generated_at"))
        not in existing
    ]
    print(f"já confirmadas: {len(rows) - len(pending)}; pendentes: {len(pending)}")

    # Upsert em LOTES pequenos, cada um em sua transação com retry: 1 statement
    # com todas as linhas (JSONB grandes) estoura o pooler do Supabase, e o pooler
    # ainda derruba conexões intermitentemente. Upsert idempotente = seguro retomar.
    upsert = text(_build_upsert())
    batch = max(1, int(args.batch))
    done = 0
    for i in range(0, len(pending), batch):
        chunk = pending[i:i + batch]
        _exec_retry(tgt, upsert, chunk)
        done += len(chunk)
        print(f"  ... {done}/{len(pending)} pendentes", flush=True)

    # Preserva sobras para rollback, mas as marca como inativas somente após todos
    # os lotes. Uma falha intermediária não reduz nem invalida a vitrine remota.
    symbols = [str(row["symbol"]) for row in rows]
    _exec_retry(tgt, text(_build_deactivate_stale()), {"symbols": symbols})
    expected = {
        (str(row["symbol"]), str(row.get("score_version") or ""), row.get("generated_at"))
        for row in rows
    }
    with tgt.connect() as conn:
        confirmed = {
            (str(row.symbol), str(row.score_version or ""), row.generated_at)
            for row in conn.execute(text("""
                SELECT symbol,score_version,generated_at
                FROM market_us.company_snapshots
                WHERE symbol = ANY(:symbols)
            """), {"symbols": symbols})
        }
    remote_count = len(expected & confirmed)
    if remote_count != len(expected):
        raise RuntimeError(
            f"publicação incompleta: esperado {len(expected)}, obtido {remote_count}"
        )
    print(
        f"publicado nesta execução: {done}; snapshot confirmado: {remote_count} "
        "empresa(s) em market_us.company_snapshots (Supabase)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
