# -*- coding: utf-8 -*-
"""Popula market.cvm_filing_publications com o DT_RECEB das DFP (A-155).

Sem esta tabela, `availability_quality` so alcanca `first_seen_proxy` -- o dia
em que o ETL rodou -- e `core.b3_validation.validation_readiness` reprova a
validacao temporal da B3 com um bloqueador que nao tem como sair.

Destino padrao e o armazem local. Gravar no Supabase exige `--destino supabase`
E `--apply`, porque gravacao remota e decisao humana.

    python scripts/ingest_cvm_publicacao.py --de 2010 --ate 2025
    python scripts/ingest_cvm_publicacao.py --backfill-vintages --apply

Para levar isso ao Supabase, o caminho e ESTE script com
`--destino supabase --backfill-vintages`, nao `publish_b3_vintages_from_local`.
O publicador insere linhas, e seu indice unico inclui `available_at`: republicar
criaria uma SEGUNDA linha por (ticker, ano, metrica), convivendo com a antiga de
`migration_baseline`. Duas vintages da mesma metrica com datas de
disponibilidade diferentes nao e historico, e ambiguidade -- e a leitura PIT
escolheria uma delas sem criterio. O backfill e UPDATE no lugar.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

DDL = RAIZ / "supabase_unificado" / "schema" / "052_cvm_filing_publications.sql"


def _engine(destino: str):
    if destino == "supabase":
        from core.database import get_engine
        eng = get_engine()
        if eng is None:
            raise SystemExit("DATABASE_URL nao configurada.")
        return eng
    from sqlalchemy import create_engine

    from scripts.publish_fii_selection_from_local import _warehouse_url
    return create_engine(
        _warehouse_url().replace("postgresql://", "postgresql+psycopg2://"))


def garantir_tabela(conn) -> None:
    from sqlalchemy import text
    conn.execute(text(DDL.read_text(encoding="utf-8")))


def gravar(conn, entregas) -> int:
    """Upsert idempotente. Reapresentacao posterior sobrescreve a data anterior;
    e o ponto: a versao mais recente e a que o banco guarda."""
    from sqlalchemy import text
    if not entregas:
        return 0
    payload = [{"cd": e.codigo_cvm, "ex": e.exercicio, "cat": e.categoria,
                "disp": e.disponivel_em, "prim": e.primeira_entrega_em,
                "v": e.versoes} for e in entregas]
    conn.execute(text("""
        INSERT INTO market.cvm_filing_publications (
            codigo_cvm, exercicio, categoria, disponivel_em,
            primeira_entrega_em, versoes)
        VALUES (:cd, :ex, :cat, :disp, :prim, :v)
        ON CONFLICT (codigo_cvm, exercicio, categoria) DO UPDATE SET
            disponivel_em = EXCLUDED.disponivel_em,
            primeira_entrega_em = LEAST(
                market.cvm_filing_publications.primeira_entrega_em,
                EXCLUDED.primeira_entrega_em),
            versoes = GREATEST(market.cvm_filing_publications.versoes,
                               EXCLUDED.versoes),
            atualizado_em = now()
    """), payload)
    return len(payload)


# A UPDATE abaixo reescreve `available_at` de linhas ja gravadas. E deliberado e
# limitado: so toca linhas anuais cuja qualidade AINDA e proxy ou baseline --
# isto e, linhas que declaram nao saber. Nunca sobrescreve `published_at`, para
# que rodar duas vezes nao possa degradar evidencia em evidencia.
_BACKFILL = """
UPDATE market.calculated_metric_vintages v
   SET available_at = p.disponivel_em::timestamptz,
       availability_quality = 'published_at'
  FROM market.cvm_filing_publications p
  JOIN market.companies c ON c.codigo_cvm = p.codigo_cvm
  JOIN market.assets a ON a.company_id = c.id
 WHERE v.ticker = a.ticker
   AND v.period = 'annual'
   AND v.year = p.exercicio
   AND p.categoria = 'DFP'
   AND v.availability_quality IN ('migration_baseline', 'first_seen_proxy')
"""


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--de", type=int, default=2010)
    p.add_argument("--ate", type=int, default=2025)
    p.add_argument("--destino", choices=("local", "supabase"), default="local")
    p.add_argument("--backfill-vintages", action="store_true",
                   help="reescreve available_at das linhas que declaram nao saber")
    p.add_argument("--apply", action="store_true")
    args = p.parse_args(argv)

    if args.destino == "supabase" and not args.apply:
        raise SystemExit("gravacao remota exige --apply explicito.")

    from sqlalchemy import text

    from data_pipeline.market.cvm_publicacao import entregas_do_ano

    eng = _engine(args.destino)
    with eng.begin() as conn:
        garantir_tabela(conn)
        total = 0
        for ano in range(args.de, args.ate + 1):
            entregas = entregas_do_ano(ano)
            if not entregas:
                print(f"  {ano}: fonte indisponivel ou vazia")
                continue
            gravados = gravar(conn, entregas)
            total += gravados
            reap = sum(e.reapresentado for e in entregas)
            print(f"  {ano}: {gravados} exercicios ({reap} reapresentados)")
        print(f"entregas gravadas: {total}")

        casadas = conn.execute(text("""
            SELECT count(DISTINCT (a.ticker, p.exercicio))
            FROM market.cvm_filing_publications p
            JOIN market.companies c ON c.codigo_cvm = p.codigo_cvm
            JOIN market.assets a ON a.company_id = c.id
            WHERE p.categoria = 'DFP'""")).scalar()
        print(f"pares (ticker, exercicio) alcancaveis: {casadas}")

        if args.backfill_vintages:
            if not args.apply:
                print("[dry-run] backfill nao executado; use --apply.")
            else:
                n = conn.execute(text(_BACKFILL)).rowcount
                print(f"vintages promovidos a published_at: {n}")

        for r in conn.execute(text("""
                SELECT availability_quality, count(*)
                FROM market.calculated_metric_vintages GROUP BY 1 ORDER BY 2 DESC""")):
            print("  ", r[0], r[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
