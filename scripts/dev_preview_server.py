"""
Sobe app.py real (nao o stub APP_TEST_MODE) para validacao manual de navegador,
apontando DATABASE_URL para o warehouse local Docker (dfu_warehouse, porta 5433,
somente leitura). Nunca toca no Supabase remoto real nem escreve em .env.

Uso: py -3.12 scripts/dev_preview_server.py
"""
from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.publish_fii_selection_from_local import _warehouse_url  # noqa: E402

os.environ["DATABASE_URL"] = _warehouse_url()
os.environ["MOCK_MODE"] = "false"
os.environ.pop("APP_TEST_MODE", None)

subprocess.run(
    [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        "app.py",
        "--server.port=8623",
        "--server.headless=true",
        "--server.address=127.0.0.1",
    ]
)
