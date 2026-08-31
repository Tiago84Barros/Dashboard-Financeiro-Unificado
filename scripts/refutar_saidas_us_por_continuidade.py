# -*- coding: utf-8 -*-
"""Derruba a saida cujo PAPEL continuou negociando depois dela.

`scripts/resolver_simbolo_deslistadas_us.py` refuta pela SEC: relatorio anual
sob o mesmo CIK em ano igual ou posterior ao da ausencia. Essa porta nao
alcanca a reorganizacao societaria -- o registrante antigo para de arquivar
para sempre e um CIK novo assume. Foi o que se mediu no armazem: das 60 saidas
ja nomeadas que tinham cotacao, 60 seguiam negociando, e uma unica estava
marcada como refutada. BlackRock constava como saida de 2025.

Aqui a prova e a do papel, com a cotacao que ja esta no armazem, sem rede. A
regra vive em `core.us_saidas_sec.refuta_por_continuidade` e exige
CONTINUIDADE em volta da data, para que ticker reciclado por outra empresa nao
derrube a saida verdadeira do dono anterior.

Simulacao por padrao; grava somente com --aplicar.

Uso::

    python scripts/refutar_saidas_us_por_continuidade.py
    python scripts/refutar_saidas_us_por_continuidade.py --aplicar
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

from core.us_saidas_sec import refuta_por_continuidade  # noqa: E402
from scripts.publish_fii_selection_from_local import _warehouse_url  # noqa: E402

SQL_SAIDAS = (
    "SELECT cik, symbol, delisted_date FROM market_us.delistings "
    "WHERE symbol IS NOT NULL AND refuted_by IS NULL ORDER BY cik")

SQL_PRECOS = (
    "SELECT p.symbol, p.month_end FROM market_us.prices_monthly p "
    "WHERE p.symbol = ANY(:symbols) ORDER BY p.symbol, p.month_end")

SQL_GRAVA = (
    "UPDATE market_us.delistings SET refuted_by = :motivo, "
    "  refuted_date = COALESCE(refuted_date, CAST(:data AS DATE)) "
    "WHERE cik = :cik")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--aplicar", action="store_true")
    args = ap.parse_args()

    engine = create_engine(_warehouse_url())
    with engine.begin() as conn:
        if not conn.execute(text(
                "SELECT 1 FROM information_schema.columns WHERE "
                "table_schema='market_us' AND table_name='delistings' "
                "AND column_name='refuted_by'")).first():
            print("coluna refuted_by ausente: rode a migration 060 antes.")
            return 2
        saidas = conn.execute(text(SQL_SAIDAS)).fetchall()
        simbolos = sorted({s.symbol for s in saidas})
        negocios: dict[str, list] = defaultdict(list)
        for sym, mes in conn.execute(text(SQL_PRECOS), {"symbols": simbolos}):
            negocios[sym].append(mes)

        refutadas = []
        for s in saidas:
            veredito = refuta_por_continuidade(s.delisted_date,
                                               negocios.get(s.symbol, []))
            if veredito:
                refutadas.append((s.cik, s.symbol, s.delisted_date, veredito))

        print(f"saidas nomeadas e nao refutadas : {len(saidas)}")
        print(f"  com cotacao no armazem        : "
              f"{sum(1 for s in saidas if negocios.get(s.symbol))}")
        print(f"  REFUTADAS por continuidade    : {len(refutadas)}")
        for cik, sym, dt, v in refutadas[:25]:
            print(f"    {sym:8s} cik={cik:<10d} saida={dt} vida={v['data']}")
        if len(refutadas) > 25:
            print(f"    ... e mais {len(refutadas) - 25}")

        if not args.aplicar:
            print("\nsimulacao: nada gravado (use --aplicar)")
            return 0
        for cik, _sym, _dt, v in refutadas:
            conn.execute(text(SQL_GRAVA), {"cik": cik, "motivo": v["motivo"],
                                           "data": v["data"]})
        print(f"\ngravadas {len(refutadas)} refutacoes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
