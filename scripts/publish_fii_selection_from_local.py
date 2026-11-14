"""Publica no Supabase o snapshot FII reconstruido no warehouse local."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


CONTAINER_WAREHOUSE = "dfu_warehouse"
PRAZO_INSPECAO = 60


def senha_do_warehouse(*, timeout: float = PRAZO_INSPECAO) -> str:
    """Senha efetiva do container do armazém, lida por ``docker inspect``.

    Ponto único de leitura da senha. ``docker compose exec ... printenv``, que
    os ``run_*_ingest.py`` usavam, abre um processo dentro do container e
    depende do projeto compose; em 13/11 e 14/11/2026 ele falhou com o
    container saudável, enquanto o ``docker inspect`` da mesma rotina, minutos
    depois, respondeu. O inspect só lê a configuração do container.

    Levanta ``RuntimeError`` sem a senha. A mensagem nunca carrega a saída do
    inspect, que contém a credencial.
    """
    try:
        inspecao = subprocess.run(
            ["docker", "inspect", CONTAINER_WAREHOUSE, "--format", "{{json .Config.Env}}"],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"docker inspect {CONTAINER_WAREHOUSE} sem resposta em {timeout:.0f}s") from None
    except OSError as exc:
        raise RuntimeError(f"docker indisponível ({type(exc).__name__})") from None
    if inspecao.returncode != 0:
        erro = ((inspecao.stderr or "").strip().splitlines() or [""])[0][:200]
        raise RuntimeError(
            f"docker inspect {CONTAINER_WAREHOUSE} saiu com {inspecao.returncode}: {erro}")
    try:
        ambiente = json.loads(inspecao.stdout or "null") or []
    except ValueError:
        raise RuntimeError("docker inspect devolveu ambiente ilegível") from None
    entrada = next(
        (item for item in ambiente if str(item).startswith("POSTGRES_PASSWORD=")), None)
    senha = str(entrada).split("=", 1)[1] if entrada else ""
    if not senha:
        raise RuntimeError("senha do warehouse local indisponivel")
    return senha


def _warehouse_url() -> str:
    senha = senha_do_warehouse()
    return f"postgresql://postgres:{quote_plus(senha)}@localhost:5433/postgres"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    # O destino precisa ser capturado antes de a rotina de publicacao apontar os
    # loaders para o warehouse local.
    from core.config import settings
    from scripts.publish_fii_selection_snapshot import publish

    target = settings.db_url
    if not target:
        raise RuntimeError("destino Supabase nao configurado")
    report = publish(_warehouse_url(), target, dry_run=args.dry_run)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
