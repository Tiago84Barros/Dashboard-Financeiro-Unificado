"""Backup verificável de vitrines compactas antes da publicação no Supabase.

As credenciais são resolvidas pela configuração do projeto e nunca são exibidas.
O arquivo JSONL é comprimido, acompanhado de manifesto e validado por contagem.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import settings


SAFE_TABLE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")


def _engine(url: str):
    parsed = make_url(url)
    if parsed.drivername in {"postgresql", "postgres"}:
        parsed = parsed.set(drivername="postgresql+psycopg2")
    is_remote = bool(parsed.host and parsed.host not in {"localhost", "127.0.0.1", "::1"})
    connect_args: dict = {"connect_timeout": 15}
    if is_remote:
        parsed = parsed.update_query_dict({"sslmode": "require"})
        connect_args.update(
            options="-c statement_timeout=60000",
            keepalives=1,
            keepalives_idle=10,
            keepalives_interval=5,
            keepalives_count=3,
        )
    kwargs: dict = {"future": True, "connect_args": connect_args}
    if is_remote:
        kwargs["poolclass"] = NullPool
    else:
        kwargs["pool_pre_ping"] = True
    return create_engine(parsed, **kwargs)


def backup_table(table: str, output_dir: Path) -> dict:
    if not SAFE_TABLE.fullmatch(table):
        raise ValueError(f"nome de tabela inválido: {table!r}")
    url = settings.db_url
    if not url:
        raise RuntimeError("Supabase não configurado")
    schema, name = table.split(".", 1)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{schema}.{name}.dump"
    partial_path = path.with_suffix(path.suffix + ".partial")
    parsed = make_url(url)
    if not parsed.password:
        raise RuntimeError("senha do Supabase indisponível")
    engine = _engine(url)
    try:
        with engine.connect() as conn:
            columns = [col["name"] for col in inspect(conn).get_columns(name, schema=schema)]
            if not columns:
                raise RuntimeError(f"tabela ausente ou sem colunas: {table}")
            count = int(conn.execute(text(
                f'SELECT count(*) FROM "{schema}"."{name}"'
            )).scalar_one())
    finally:
        engine.dispose()

    # O pg_dump usa COPY binário/streaming, mais resiliente para os JSONB grandes.
    # A senha segue por stdin para o shell do container, nunca pela linha de comando.
    shell = (
        'IFS= read -r PGPASSWORD; export PGPASSWORD; export PGSSLMODE=require; '
        'export PGCONNECT_TIMEOUT=15; '
        'exec pg_dump --format=custom --no-owner --no-acl '
        '--host="$1" --port="$2" --username="$3" --dbname="$4" --table="$5"'
    )
    command = [
        "docker", "exec", "-i", "dfu_warehouse", "sh", "-c", shell, "sh",
        str(parsed.host), str(parsed.port or 5432), str(parsed.username),
        str(parsed.database), table,
    ]
    result = None
    for attempt in range(1, 6):
        with partial_path.open("wb") as handle:
            result = subprocess.run(
                command,
                input=(str(parsed.password) + "\n").encode("utf-8"),
                stdout=handle,
                stderr=subprocess.PIPE,
                check=False,
            )
        if result.returncode == 0:
            break
        partial_path.unlink(missing_ok=True)
        if attempt < 5:
            time.sleep(2.0 * attempt)
    assert result is not None
    if result.returncode != 0:
        partial_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"pg_dump falhou para {table}: "
            f"{result.stderr.decode('utf-8', errors='replace')[:300]}"
        )
    partial_path.replace(path)
    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    with path.open("rb") as handle:
        catalog = subprocess.run(
            ["docker", "exec", "-i", "dfu_warehouse", "pg_restore", "--list"],
            stdin=handle,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    if catalog.returncode != 0 or f"TABLE DATA {schema} {name}" not in catalog.stdout.decode(
        "utf-8", errors="replace"
    ):
        raise RuntimeError(f"catálogo do backup não contém os dados de {table}")
    return {
        "table": table,
        "rows": count,
        "catalog_verified": True,
        "sha256": digest,
        "path": str(path),
        "bytes": path.stat().st_size,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", action="append", required=True)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    stamp = dt.datetime.now().strftime("remote_snapshots_%Y%m%d_%H%M%S")
    output_dir = args.output_dir or ROOT / "migration" / "backup" / stamp
    reports = [backup_table(table, output_dir) for table in args.table]
    manifest = output_dir / "manifest.json"
    manifest.write_text(
        json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"output_dir": str(output_dir), "tables": reports}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
