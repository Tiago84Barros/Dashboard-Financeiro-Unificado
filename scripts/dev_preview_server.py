"""
Sobe app.py real (nao o stub APP_TEST_MODE) para validacao manual de navegador,
apontando DATABASE_URL para o warehouse local Docker (dfu_warehouse, porta 5433,
somente leitura). Nunca toca no Supabase remoto real nem escreve em .env.

Uso: py -3.12 scripts/dev_preview_server.py [--fase N] [--port N]

``--fase`` existe porque a tela de Homologacao so mostra os criterios da fase
*seguinte*: em Fase 1 nao ha como ver na tela o medidor dos cenarios
historicos, que e criterio da Fase 4. Sem isso, so restaria conferir por
inspecao de codigo -- e foi exatamente assim que dois defeitos de render
passaram batido. A fase entra por variavel de ambiente deste processo, como no
deploy; nada e escrito em .env.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.publish_fii_selection_from_local import _warehouse_url  # noqa: E402

_ap = argparse.ArgumentParser(description=__doc__)
_ap.add_argument("--fase", type=int, choices=(1, 2, 3, 4),
                 help="valor de APP4_FASE so para este processo")
_ap.add_argument("--port", type=int, default=8623)
_args = _ap.parse_args()

os.environ["DATABASE_URL"] = _warehouse_url()
os.environ["MOCK_MODE"] = "false"
os.environ.pop("APP_TEST_MODE", None)
if _args.fase is not None:
    os.environ["APP4_FASE"] = str(_args.fase)

subprocess.run(
    [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        "app.py",
        f"--server.port={_args.port}",
        "--server.headless=true",
        "--server.address=127.0.0.1",
    ]
)
