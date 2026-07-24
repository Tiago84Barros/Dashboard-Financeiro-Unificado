# -*- coding: utf-8 -*-
"""
Relatório READ-ONLY do estado do banco (armazém local ou Supabase).

Emite em markdown: contagens por schema, volumetria e frescor das tabelas
centrais, checagens de qualidade (as mesmas da auditoria percentual 2026-07)
e constraints preventivas presentes. Não altera nada.

Uso:
  python scripts/report_db_state.py            # usa DATABASE_URL/SUPABASE_*
  python scripts/report_db_state.py --titulo "Armazém local"
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

# (rótulo, SQL escalar ou de linha única). Tabela ausente vira "—".
CHECKS: list[tuple[str, str]] = [
    ("Preços BR: linhas / tickers / última data",
     "SELECT COUNT(*)::text || ' / ' || COUNT(DISTINCT ticker)::text || ' / ' || MAX(date)::text FROM market.historical_prices"),
    ("Preços BR: closes nulos/<=0 (deve ser 0)",
     "SELECT COUNT(*)::text FROM market.historical_prices WHERE close IS NULL OR close <= 0"),
    ("Dividendos: linhas / tickers / última ex-date",
     "SELECT COUNT(*)::text || ' / ' || COUNT(DISTINCT ticker)::text || ' / ' || MAX(ex_date)::text FROM market.dividends"),
    ("Dividendos: amount<=0 (deve ser 0)",
     "SELECT COUNT(*)::text FROM market.dividends WHERE amount IS NULL OR amount <= 0"),
    ("Dividendos: duplicatas exatas (deve ser 0)",
     "SELECT COUNT(*)::text FROM (SELECT 1 FROM market.dividends GROUP BY ticker, ex_date, type, amount HAVING COUNT(*) > 1) d"),
    ("Métricas calculadas BR: linhas / tickers / atualização",
     "SELECT COUNT(*)::text || ' / ' || COUNT(DISTINCT ticker)::text || ' / ' || MAX(created_at)::date::text FROM market.calculated_metrics"),
    ("Métricas calculadas BR: valores nulos (deve ser 0)",
     "SELECT COUNT(*)::text FROM market.calculated_metrics WHERE metric_value IS NULL"),
    ("Demonstrações BR (DRE): linhas / tickers / último ano",
     "SELECT COUNT(*)::text || ' / ' || COUNT(DISTINCT ticker)::text || ' / ' || MAX(year)::text FROM market.income_statements"),
    ("Empresas: total / sem setor",
     "SELECT COUNT(*)::text || ' / ' || COUNT(*) FILTER (WHERE sector IS NULL OR sector = '')::text FROM market.companies"),
    ("FIIs (com preço): total / sem segmento / sem vacância",
     "SELECT COUNT(*)::text || ' / ' || COUNT(*) FILTER (WHERE COALESCE(segmento,'') = '')::text || ' / ' || COUNT(*) FILTER (WHERE vacancia IS NULL)::text FROM market.fiis WHERE price IS NOT NULL"),
    ("FIIs sem vacância que são papel/FoF (vacância não se aplica)",
     "SELECT COUNT(*)::text FROM market.fiis WHERE price IS NOT NULL AND vacancia IS NULL AND tipo IN ('papel','fof')"),
    ("Score FII: último corte / linhas / validated / diligence",
     "SELECT MAX(reference_date)::text || ' / ' || COUNT(*)::text || ' / ' || COUNT(*) FILTER (WHERE publication_status = 'validated')::text || ' / ' || COUNT(*) FILTER (WHERE publication_status = 'diligence_only')::text FROM market.fii_score_snapshots WHERE reference_date = (SELECT MAX(reference_date) FROM market.fii_score_snapshots)"),
    ("EUA snapshots: ativos / com score+confiança / geração",
     "SELECT COUNT(*) FILTER (WHERE is_active)::text || ' / ' || COUNT(*) FILTER (WHERE score IS NOT NULL AND score_confidence IS NOT NULL)::text || ' / ' || MAX(generated_at)::date::text FROM market_us.company_snapshots"),
    ("EUA preços mensais: linhas / símbolos / última data",
     "SELECT COUNT(*)::text || ' / ' || COUNT(DISTINCT symbol)::text || ' / ' || MAX(month_end)::text FROM market_us.prices_monthly"),
    ("EUA DRE: fiscal_year fora de 1990–2027 (deve ser 0)",
     "SELECT COUNT(*)::text FROM market_us.income_statements WHERE fiscal_year > 2027 OR fiscal_year < 1990"),
    ("EUA balanços: fiscal_year fora de 1990–2027 (deve ser 0)",
     "SELECT COUNT(*)::text FROM market_us.balance_sheets WHERE fiscal_year > 2027 OR fiscal_year < 1990"),
    ("EUA fluxo de caixa: fiscal_year fora de 1990–2027 (deve ser 0)",
     "SELECT COUNT(*)::text FROM market_us.cash_flow_statements WHERE fiscal_year > 2027 OR fiscal_year < 1990"),
]

CONSTRAINT_NAMES = (
    "chk_historical_prices_close_positive",
    "chk_dividends_amount_positive",
    "chk_income_statements_fiscal_year_sane",
    "chk_balance_sheets_fiscal_year_sane",
    "chk_cash_flow_statements_fiscal_year_sane",
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--titulo", default="Banco")
    args = ap.parse_args()

    from core.database import get_engine
    engine = get_engine()
    if engine is None:
        print("ERRO: banco não configurado (DATABASE_URL/SUPABASE_UNIFICADO_URL).")
        return 1

    print(f"## Estado do banco — {args.titulo}\n")
    with engine.connect() as conn:
        schemas = conn.execute(text("""
            SELECT table_schema, COUNT(*) FROM information_schema.tables
            WHERE table_schema IN ('market', 'market_us', 'public')
              AND table_type = 'BASE TABLE'
            GROUP BY 1 ORDER BY 1
        """)).all()
        print("| Schema | Tabelas |")
        print("|---|---:|")
        for schema, count in schemas:
            print(f"| {schema} | {count} |")

        print("\n| Verificação | Resultado |")
        print("|---|---|")
        for label, sql in CHECKS:
            try:
                with conn.begin_nested() if conn.in_transaction() else conn.begin():
                    value = conn.execute(text(sql)).scalar()
            except Exception:
                conn.rollback()
                value = "— (tabela ausente)"
            print(f"| {label} | {value} |")

        present = {
            name for (name,) in conn.execute(text(
                "SELECT conname FROM pg_constraint WHERE conname = ANY(:names)"
            ), {"names": list(CONSTRAINT_NAMES)}).all()
        }
        print("\n| Constraint preventiva | Presente |")
        print("|---|---|")
        for name in CONSTRAINT_NAMES:
            print(f"| {name} | {'sim' if name in present else 'NÃO'} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
