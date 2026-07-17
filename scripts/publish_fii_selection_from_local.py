"""Publica no Supabase o snapshot FII reconstruido no warehouse local."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _warehouse_url() -> str:
    inspection = subprocess.run(
        ["docker", "inspect", "dfu_warehouse", "--format", "{{json .Config.Env}}"],
        check=True, capture_output=True, text=True,
    )
    environment = json.loads(inspection.stdout)
    password_entry = next(
        (item for item in environment if str(item).startswith("POSTGRES_PASSWORD=")),
        None,
    )
    if not password_entry:
        raise RuntimeError("senha do warehouse local indisponivel")
    password = str(password_entry).split("=", 1)[1]
    return f"postgresql://postgres:{quote_plus(password)}@localhost:5433/postgres"


def main() -> int:
    # O destino precisa ser capturado antes de a rotina de publicacao apontar os
    # loaders para o warehouse local.
    from core.config import settings
    from scripts.publish_fii_selection_snapshot import publish

    target = settings.db_url
    if not target:
        raise RuntimeError("destino Supabase nao configurado")
    report = publish(_warehouse_url(), target, dry_run=False)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
