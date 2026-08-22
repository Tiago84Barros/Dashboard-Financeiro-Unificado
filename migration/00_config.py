"""
migration/00_config.py
======================
Entry-point CLI para testar a configuracao de migracao.

O modulo importavel com toda a logica esta em migration/config.py.
Este arquivo e apenas o ponto de entrada de linha de comando:
  python migration/00_config.py

Para importar em outros scripts, use sempre:
  from migration.config import MigrationConfig, make_engine
"""

from __future__ import annotations

import sys
from pathlib import Path

# Garantir que o pacote pai esta no path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Re-exportar tudo do modulo importavel
from migration.config import (  # noqa: F401, E402
    OUTPUT_DIR,
    SOURCE_APP1,
    SOURCE_APP2,
    SOURCE_APP3,
    MigrationConfig,
    _env,
    _mask,
    make_engine,
    run_config_check,
)

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sys.exit(run_config_check(dry_run=True))
