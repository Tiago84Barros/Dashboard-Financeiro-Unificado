"""Publica o IPCA por grupo e os pesos do IBGE (SIDRA 7060) num arquivo versionado.

Uso:
    python scripts/publicar_ipca_grupos.py            # baixa e grava
    python scripts/publicar_ipca_grupos.py --dry-run  # baixa e só relata

Sem rede ou com resposta vazia, não grava nada e sai com código 1: o arquivo
anterior continua valendo e a aba IPCA Pessoal segue com ele.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.ipca_grupos import (  # noqa: E402
    CAMINHO_IPCA_GRUPOS,
    baixar_sidra,
    serializar_ipca_grupos,
)


def main(argv: list[str] | None = None, *, baixar=baixar_sidra) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--saida", type=Path, default=CAMINHO_IPCA_GRUPOS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    serie, erro = baixar()
    meses = serie.meses()
    if erro or not meses:
        print(json.dumps({"status": "erro",
                          "erro": erro or "nenhum mês com Índice geral"},
                         ensure_ascii=False))
        return 1

    relatorio = {
        "status": "ok",
        "meses": len(meses),
        "primeiro": f"{meses[0]:%Y-%m}",
        "ultimo": f"{meses[-1]:%Y-%m}",
        "linhas": len(set(serie.variacao) | set(serie.peso)),
        "gravado": False,
    }
    if not args.dry_run:
        args.saida.parent.mkdir(parents=True, exist_ok=True)
        args.saida.write_bytes(serializar_ipca_grupos(serie, datetime.now(timezone.utc)))
        relatorio["gravado"] = True
        relatorio["saida"] = str(args.saida)
    print(json.dumps(relatorio, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
