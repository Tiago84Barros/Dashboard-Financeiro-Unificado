"""Publica a vitrine americana local no Supabase sem expor URLs no terminal."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch", type=int, default=75)
    args = parser.parse_args()

    # Captura o destino antes de apontar qualquer configuração para o warehouse.
    from core.config import settings
    from scripts.publish_fii_selection_from_local import _warehouse_url
    from scripts.publish_us_snapshot import main as publish_main

    target = settings.db_url
    if not target:
        raise RuntimeError("destino Supabase não configurado")
    argv = [
        "publish_us_snapshot",
        "--source-url", _warehouse_url(),
        "--target-url", target,
        "--batch", str(max(1, args.batch)),
    ]
    if args.dry_run:
        argv.append("--dry-run")
    previous = sys.argv
    try:
        sys.argv = argv
        return publish_main()
    finally:
        sys.argv = previous


if __name__ == "__main__":
    raise SystemExit(main())
