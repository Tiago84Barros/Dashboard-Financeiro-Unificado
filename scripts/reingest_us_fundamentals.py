# -*- coding: utf-8 -*-
"""Re-ingestao de fundamentos de um recorte de simbolos (remediacao de parser).

Por que existe: quando o parser da SEC muda, o dado ja gravado nao muda junto.
O A-148 -- receita zero de uma tag de rollup vazia vencendo a tag com valor --
deixou 573 linhas anuais com `revenue = 0` em 224 empresas. Corrigir o codigo
nao corrige a tabela; e preciso reler os fatos daquelas empresas.

Por que so fundamentos (`with_prices=False`): o defeito era da leitura de XBRL.
Preco, dividendo e split vem do yfinance, nao mudaram, e sao o gargalo lento da
varredura. Rele o que o conserto tocou, nada alem.

    python scripts/reingest_us_fundamentals.py --zero-revenue [--limit N] [--dry-run]
    python scripts/reingest_us_fundamentals.py --symbols ETN,FLS,CMP
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

from data_pipeline.us import ingest  # noqa: E402

# Recorte do A-148: linha anual com receita exatamente zero. Zero e o valor que
# o parser gravava ao aceitar a tag de rollup vazia; empresa que de fato nunca
# teve receita aparece com NULL, nao com 0, e por isso fica de fora daqui.
_SQL_ZERO_REVENUE = """
  SELECT DISTINCT symbol
  FROM market_us.income_statements
  WHERE period = 'annual' AND revenue = 0
  ORDER BY symbol
"""


def _engine():
    from scripts.publish_fii_selection_from_local import _warehouse_url
    return create_engine(
        _warehouse_url().replace("postgresql://", "postgresql+psycopg2://"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--zero-revenue", action="store_true",
                    help="recorte do A-148: linhas anuais com revenue = 0")
    ap.add_argument("--symbols", default="",
                    help="lista separada por virgula (alternativa ao recorte)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--years", type=int, default=20)
    ap.add_argument("--run-key", default="reingest-a148")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    engine = _engine()
    if args.symbols:
        simbolos = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    elif args.zero_revenue:
        with engine.connect() as conn:
            simbolos = [r[0] for r in conn.execute(text(_SQL_ZERO_REVENUE))]
    else:
        ap.error("informe --zero-revenue ou --symbols")
    if args.limit:
        simbolos = simbolos[:args.limit]

    print(f"simbolos a reler: {len(simbolos)}", flush=True)
    if args.dry_run:
        print(", ".join(simbolos[:40]) + (" ..." if len(simbolos) > 40 else ""))
        return 0

    provider = ingest.make_provider()
    # `resume=False`: retomar de um cursor antigo pularia justamente as
    # empresas do inicio da lista, que sao as que precisam ser relidas.
    resultado = ingest.ingest_symbols(
        provider, engine, simbolos, run_key=args.run_key, years=args.years,
        resume=False, with_prices=False)
    print(resultado, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
