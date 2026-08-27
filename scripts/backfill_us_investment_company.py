# -*- coding: utf-8 -*-
"""Backfill de `companies.is_investment_company` a partir da SEC (A-147).

Por que existe: a marca de BDC/fundo fechado passou a ser apurada no perfil,
mas o cadastro ja tinha ~2.800 empresas gravadas sem ela. Re-rodar a ingestao
inteira so para isso custaria as demonstracoes de novo; este script pede
apenas o `submissions` de cada CIK.

Por que consulta TODAS e nao so as suspeitas: restringir a quem tem `sector`
nulo ou nome "Capital Corp" faria a procedencia seguir a suspeita, e nao a
evidencia -- exatamente o vies que ja produziu qualidade inflada antes. O
universo inteiro passa pelo mesmo teste.

    python scripts/backfill_us_investment_company.py [--limit N] [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

from data_pipeline.us.edgar import (SUBMISSIONS_URL,  # noqa: E402
                                    _e_companhia_de_investimento,
                                    build_edgar_provider)


def _engine():
    from scripts.publish_fii_selection_from_local import _warehouse_url
    return create_engine(
        _warehouse_url().replace("postgresql://", "postgresql+psycopg2://"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.11)
    args = ap.parse_args(argv)

    eng = _engine()
    with eng.begin() as conn:
        q = ("SELECT id, cik, name FROM market_us.companies "
             "WHERE cik IS NOT NULL ORDER BY id")
        if args.limit:
            q += f" LIMIT {int(args.limit)}"
        empresas = [(r[0], r[1], r[2]) for r in conn.execute(text(q))]
    print(f"empresas com CIK: {len(empresas)}")

    prov = build_edgar_provider()
    marcadas, erros, vistas = [], 0, 0
    for cid, cik, nome in empresas:
        vistas += 1
        sub = prov._get(SUBMISSIONS_URL.format(cik=str(cik).zfill(10)))
        if not sub:
            erros += 1
        elif _e_companhia_de_investimento(sub):
            marcadas.append((cid, nome))
        if vistas % 250 == 0:
            print(f"   {vistas}/{len(empresas)}  marcadas={len(marcadas)}  erros={erros}")
        time.sleep(args.sleep)

    print(f"\ncompanhias de investimento identificadas: {len(marcadas)}  (erros: {erros})")
    for _, nome in marcadas[:25]:
        print("   ", nome)
    if args.dry_run:
        print("[dry-run] nada gravado.")
        return 0
    with eng.begin() as conn:
        conn.execute(text("UPDATE market_us.companies SET is_investment_company=false"))
        if marcadas:
            conn.execute(
                text("UPDATE market_us.companies SET is_investment_company=true "
                     "WHERE id = ANY(:ids)"), {"ids": [m[0] for m in marcadas]})
    print("gravado no armazem local.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
