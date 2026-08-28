# -*- coding: utf-8 -*-
"""Preenche `market_us.*.filed_at` nas linhas anuais já ingeridas (A-159).

A migration 054 criou a coluna, mas coluna vazia não muda decisão nenhuma: sem
`filed_at`, `core.us_pit` cai de volta na regra por linha — a que consulta o
futuro da empresa e que a 054 existe para aposentar. Enquanto este backfill não
roda, a correção está no código e não no painel.

Por que não a reingestão inteira: ela reescreveria valores financeiros de 3.048
empresas para corrigir um metadado. Aqui só a coluna `filed_at` é escrita, o que
mantém `content_hash` e os números intactos e torna a operação repetível — quem
já tem procedência é pulado por padrão.

Por que não o `companyfacts.zip` da SEC (1,4 GB, um download só): o disco desta
máquina tem menos de 2 GB livres e o diretório do projeto é sincronizado pelo
OneDrive. O JSON por empresa é baixado, lido e descartado, sem tocar o disco.

Escreve APENAS no armazém local (`_warehouse_url`). A vitrine remota é outra
decisão, com outra autorização.

    python scripts/backfill_us_filed_at.py --dry-run --limite 20
    python scripts/backfill_us_filed_at.py --apply
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text  # noqa: E402

from core.config import settings  # noqa: E402
from data_pipeline.us.edgar import EdgarProvider  # noqa: E402
from data_pipeline.us.edgar_facts import (  # noqa: E402
    build_balance_rows,
    build_cashflow_rows,
    build_income_rows,
)
from scripts.publish_fii_selection_from_local import _warehouse_url  # noqa: E402

log = logging.getLogger("backfill_filed_at")

TABELAS = (
    ("income_statements", build_income_rows),
    ("balance_sheets", build_balance_rows),
    ("cash_flow_statements", build_cashflow_rows),
)

# Casa pela chave natural da linha anual; `filed_at` é o único alvo da escrita.
SQL_UPDATE = """
UPDATE market_us.{tabela}
   SET filed_at = CAST(:filed AS jsonb)
 WHERE company_id = :cid AND period = 'annual'
   AND fiscal_year = :fy AND fiscal_quarter = 0
"""


def alvos(conn, so_faltantes: bool, limite: int | None) -> list[tuple[int, str]]:
    """Empresas com demonstração anual, as sem procedência primeiro."""
    # O simbolo nao mora em `companies` -- mora na propria demonstracao, que e
    # a mesma chave que a ingestao usa para pedir o companyfacts.
    filtro = ""
    if so_faltantes:
        # Basta uma linha sem `filed_at` para a empresa valer a viagem: o
        # companyfacts vem uma vez e serve as tres demonstracoes.
        filtro = "AND i.filed_at IS NULL"
    sql = f"""
        SELECT i.company_id, MIN(i.symbol) AS symbol
          FROM market_us.income_statements i
         WHERE i.period = 'annual' AND i.symbol IS NOT NULL
           {filtro}
         GROUP BY i.company_id
         ORDER BY symbol
    """
    if limite:
        sql += f" LIMIT {int(limite)}"
    return [(r[0], r[1]) for r in conn.execute(text(sql))]


def _json(mapa: dict) -> str:
    import json
    return json.dumps({k: str(v) for k, v in mapa.items()}, sort_keys=True)


def backfill_empresa(conn, provider: EdgarProvider, cid: int, symbol: str,
                     aplicar: bool) -> tuple[int, int]:
    fatos = provider.company_facts(symbol)
    if not fatos:
        return 0, 0
    linhas = escritas = 0
    for tabela, builder in TABELAS:
        for row in builder(fatos, symbol):
            filed = row.get("filed_at") or {}
            fy = row.get("fiscal_year")
            if not filed or fy is None:
                continue
            linhas += 1
            if not aplicar:
                continue
            res = conn.execute(text(SQL_UPDATE.format(tabela=tabela)),
                               {"filed": _json(filed), "cid": cid, "fy": fy})
            escritas += res.rowcount or 0
    return linhas, escritas


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true",
                   help="grava; sem isto so conta o que seria gravado")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limite", type=int, default=None)
    p.add_argument("--todas", action="store_true",
                   help="inclui quem ja tem filed_at (reprocessa tudo)")
    args = p.parse_args()
    aplicar = args.apply and not args.dry_run
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    engine = create_engine(_warehouse_url(), pool_pre_ping=True)
    provider = EdgarProvider(user_agent=settings.SEC_USER_AGENT)
    with engine.connect() as conn:
        pendentes = alvos(conn, not args.todas, args.limite)
    log.info("empresas a processar: %d (aplicar=%s)", len(pendentes), aplicar)

    tot_linhas = tot_escritas = falhas = 0
    for i, (cid, symbol) in enumerate(pendentes, 1):
        try:
            # Uma transacao por empresa: interromper no meio deixa o que ja
            # passou gravado e o resto pendente -- e a proxima rodada retoma.
            with engine.begin() as conn:
                linhas, escritas = backfill_empresa(conn, provider, cid,
                                                    symbol, aplicar)
        except Exception as exc:  # noqa: BLE001
            falhas += 1
            log.warning("%s: %s", symbol, exc)
            continue
        tot_linhas += linhas
        tot_escritas += escritas
        if i % 50 == 0:
            log.info("%d/%d %s linhas=%d escritas=%d falhas=%d",
                     i, len(pendentes), symbol, tot_linhas, tot_escritas, falhas)
    log.info("FIM empresas=%d linhas_com_procedencia=%d escritas=%d falhas=%d",
             len(pendentes), tot_linhas, tot_escritas, falhas)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
