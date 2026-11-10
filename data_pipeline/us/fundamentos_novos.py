"""Quais empresas dos EUA precisam reingerir fundamentos (10-K/10-Q novo).

Nenhum alvo da rotina noturna chamava `fundamentals`: `us_snapshot` roda `daily`
(preços) e `us_sec_filings` coleta só a LISTA de documentos. Resultado: as
demonstrações de `market_us.*_statements` ficaram paradas em 02/10/2026 (o 10-K
FY2026 da AAPL, de 03/11, não entrou).

A lista sai de `market_us.sec_filings` (alvo `us_sec_filings`): empresa com
10-K/10-Q (ou emenda) entregue em data >= a da sua última extração
(`max(updated_at)` das demonstrações; o trigger de `market_us` o renova a cada
upsert). `>=` pela DATA, e não pelo instante: a SEC publica os fatos
(companyfacts) com atraso, e uma extração feita no mesmo dia da entrega pode ter
chegado antes deles -- a rodada seguinte repete a empresa uma única vez.

Ordem: a mais atrasada primeiro (filing mais antigo sem extração), de modo que o
lote que sobra hoje seja retomado amanhã sem perder ninguém.
"""
from __future__ import annotations

FORMULARIOS = ("10-K", "10-K/A", "10-KT", "10-Q", "10-Q/A")

SQL_SIMBOLOS_COM_FILING_NOVO = """
    SELECT symbol FROM (
        SELECT DISTINCT ON (a.company_id) a.symbol, f.pendente_desde
        FROM market_us.assets a
        JOIN (
            SELECT s.symbol, min(s.filing_date) AS pendente_desde
            FROM market_us.sec_filings s
            JOIN (
                SELECT a2.symbol, max(i.updated_at)::date AS ultima
                FROM market_us.assets a2
                JOIN market_us.income_statements i ON i.company_id = a2.company_id
                GROUP BY a2.symbol
            ) u ON u.symbol = s.symbol
            WHERE s.form = ANY(:formularios) AND s.filing_date >= u.ultima
            GROUP BY s.symbol
        ) f ON f.symbol = a.symbol
        WHERE a.company_id IS NOT NULL
          AND a.analysis_status IN ('eligible', 'pending')
          AND a.security_type IN ('common', 'reit')
        ORDER BY a.company_id, a.is_active DESC, a.symbol
    ) t
    ORDER BY pendente_desde ASC, symbol
"""


def simbolos_com_filing_novo(conn, *, limit: int | None = None) -> list[str]:
    """Um símbolo por companhia com demonstração nova por buscar, mais atrasada antes."""
    from sqlalchemy import text

    q = SQL_SIMBOLOS_COM_FILING_NOVO
    if limit:
        q += f" LIMIT {int(limit)}"
    return [r[0] for r in conn.execute(text(q), {"formularios": list(FORMULARIOS)})]
