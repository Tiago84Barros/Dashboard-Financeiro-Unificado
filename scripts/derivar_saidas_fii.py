# -*- coding: utf-8 -*-
"""Deriva saidas do universo de FIIs a partir dos snapshots ja gravados.

Um fundo que estava na foto de T1 e sumiu da foto de T2 deixou o universo. A
guarda de cobertura vive em `core/fii_saidas.py`; aqui e' so a borda: ler,
mostrar e -- somente com `--aplicar` -- gravar.

Grava no banco publicado, entao `--aplicar` e' gravacao remota e exige decisao
humana. Sem a flag o script apenas relata, que e' o modo util no dia a dia:
mostra quantas saidas seriam derivadas e, quando nenhuma, POR QUE.

Uso::

    python scripts/derivar_saidas_fii.py            # so relata
    python scripts/derivar_saidas_fii.py --aplicar  # grava (decisao humana)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--aplicar", action="store_true",
                    help="grava as saidas derivadas (GRAVACAO REMOTA)")
    args = ap.parse_args()

    from sqlalchemy import text

    from core.database import get_engine
    from core.fii_saidas import derivar_saidas, fotos_do_banco

    engine = get_engine()
    with engine.connect() as conn:
        fotos = fotos_do_banco(conn)
    diag = derivar_saidas(fotos)

    print(f"fotos lidas: {len(fotos)}")
    for data in sorted(fotos):
        marca = "ok" if data in diag.comparaveis else "DESCARTADA"
        print(f"  {data}: {len(fotos[data]):>5} tickers  [{marca}]")

    if not diag.saidas:
        print(f"\nnenhuma saida derivada -- {diag.motivo}")
        return 0

    print(f"\n{len(diag.saidas)} saida(s) derivada(s):")
    for s in diag.saidas[:50]:
        print(f"  {s['ticker']}: visto por ultimo em {s['visto_por_ultimo_em']}, "
              f"ausente em {s['reference_date']}")
    if len(diag.saidas) > 50:
        print(f"  ... e mais {len(diag.saidas) - 50}")

    if not args.aplicar:
        print("\n[somente relato] use --aplicar para gravar (gravacao remota).")
        return 0

    linhas = [{
        "ticker": s["ticker"], "reference_date": s["reference_date"],
        "available_at": s["reference_date"], "knowledge_at": s["reference_date"],
        "availability_quality": "derived_from_absence",
        "active_status": s["active_status"], "successor_ticker": None,
        "source": s["source"],
        "metadata_json": json.dumps(
            {"visto_por_ultimo_em": str(s["visto_por_ultimo_em"]),
             "regra": "ausencia entre fotos completas do universo"},
            ensure_ascii=False),
    } for s in diag.saidas]

    from data_pipeline.market import repository as repo
    with engine.begin() as conn:
        if not conn.execute(text(
                "SELECT to_regclass('market.fii_universe_history') IS NOT NULL")).scalar():
            print("tabela market.fii_universe_history ausente nesta base")
            return 1
        gravadas = repo.upsert(conn, "fii_universe_history", linhas)
    print(f"\ngravadas {gravadas} linha(s) de saida.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
