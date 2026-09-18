# -*- coding: utf-8 -*-
"""Registra a prova de vida dos FIIs que nenhuma foto de universo alcancou.

A regra e o motivo moram em `core/fii_universo_vivo.py`; aqui e' so a borda:
ler, mostrar e -- somente com `--aplicar` -- gravar.

O destino padrao e o banco publicado, entao `--aplicar` e' GRAVACAO REMOTA e
exige decisao humana. `--armazem` aponta para o Postgres local (porta 5433),
que e' a fonte de onde a vitrine de FII e' reconstruida: fechar a lacuna so no
Supabase deixaria a proxima publicacao reabri-la.

Uso::

    python scripts/registrar_universo_vivo.py               # so relata (remoto)
    python scripts/registrar_universo_vivo.py --armazem     # so relata (local)
    python scripts/registrar_universo_vivo.py --aplicar     # grava
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--aplicar", action="store_true",
                    help="grava as observacoes (GRAVACAO REMOTA sem --armazem)")
    ap.add_argument("--armazem", action="store_true",
                    help="usa o armazem local (Docker dfu_warehouse) como destino")
    args = ap.parse_args()

    from core.fii_universo_vivo import registrar, tickers_sem_observacao

    if args.armazem:
        from sqlalchemy import create_engine

        from scripts.publish_fii_selection_from_local import _warehouse_url
        engine = create_engine(
            _warehouse_url().replace("postgresql://", "postgresql+psycopg2://"))
        destino = "armazem local"
    else:
        from core.database import get_engine
        engine = get_engine()
        destino = "banco publicado"
    if engine is None:
        print("destino indisponivel")
        return 1

    with engine.connect() as conn:
        ausentes = tickers_sem_observacao(conn)
    print(f"destino: {destino}")
    print(f"{len(ausentes)} fundo(s) com preco vivo e sem linha de universo:")
    for ticker in ausentes:
        print(f"  {ticker}")

    if not ausentes:
        return 0
    if not args.aplicar:
        print("\n[somente relato] use --aplicar para gravar.")
        return 0

    with engine.begin() as conn:
        gravadas = registrar(conn, datetime.now(timezone.utc))
    print(f"\ngravadas {gravadas} observacao(oes) de vida.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
