# -*- coding: utf-8 -*-
"""Preenche market_us.assets.first_trade_date a partir da serie mensal.

Motivo em `core/us_primeira_negociacao`: o portao de estreia de
`data_pipeline/us/scoring_history.py` le esta coluna, ela esta NULL nas 7.654
linhas, e por isso 11,5% das linhas do painel PIT (2.695 de 23.522 na versao
0.7.1) eram empresa que ainda nao negociava na data.

Grava so onde a coluna esta NULL: se algum dia a estreia legal for ingerida de
uma fonte melhor, este script nao a sobrescreve com o piso observado.

Uso:
    python scripts/backfill_first_trade_date_us.py            # so mede
    python scripts/backfill_first_trade_date_us.py --aplicar
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from sqlalchemy import text  # noqa: E402

SQL_MEDIR = """
SELECT count(*) AS ativos,
       count(a.first_trade_date) AS ja_preenchidos,
       count(p.ini) FILTER (WHERE a.first_trade_date IS NULL) AS preencheveis
  FROM market_us.assets a
  LEFT JOIN (SELECT symbol, min(month_end) AS ini
               FROM market_us.prices_monthly GROUP BY symbol) p
    ON p.symbol = a.symbol
"""

SQL_GRAVAR = """
UPDATE market_us.assets a
   SET first_trade_date = p.ini, updated_at = now()
  FROM (SELECT symbol, min(month_end) AS ini
          FROM market_us.prices_monthly GROUP BY symbol) p
 WHERE p.symbol = a.symbol AND a.first_trade_date IS NULL
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--aplicar", action="store_true")
    args = ap.parse_args()

    from sqlalchemy import create_engine

    from scripts.publish_fii_selection_from_local import _warehouse_url

    eng = create_engine(_warehouse_url())
    with eng.connect() as conn:
        antes = dict(conn.execute(text(SQL_MEDIR)).mappings().one())
    if not args.aplicar:
        print(json.dumps({"modo": "medicao", **antes}, default=str))
        return 0
    with eng.begin() as conn:
        gravadas = conn.execute(text(SQL_GRAVAR)).rowcount
    with eng.connect() as conn:
        depois = dict(conn.execute(text(SQL_MEDIR)).mappings().one())
    print(json.dumps({"modo": "aplicado", "gravadas": gravadas,
                      "antes": antes, "depois": depois}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
