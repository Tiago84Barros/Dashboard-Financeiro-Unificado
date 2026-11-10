"""Quais empresas dos EUA precisam reingerir fundamentos (10-K/10-Q novo).

Nenhum alvo da rotina noturna chamava `fundamentals`: `us_snapshot` roda `daily`
(preços) e `us_sec_filings` coleta só a LISTA de documentos. Resultado: as
demonstrações de `market_us.*_statements` ficaram paradas em 02/10/2026 (o 10-K
FY2026 da AAPL, de 03/11, não entrou).

A lista sai de `market_us.sec_filings` (alvo `us_sec_filings`): empresa com
10-K/10-Q (ou emenda) entregue DEPOIS da data de disponibilidade mais recente
das suas demonstracoes (`max(available_at)`, PIT). Nao usa `updated_at`: o
`enrich` regrava `quality_status` de todas as linhas e o trigger renovaria
`updated_at` de quem nunca foi reingerido (achado do Codex no PR #708). O fato
chega da SEC com atraso: extrair no dia da entrega nao encerra a pendencia,
porque `available_at` so avanca quando o fato entra.

Ordem: a mais atrasada primeiro (filing mais antigo sem extração), de modo que o
lote que sobra hoje seja retomado amanhã sem perder ninguém.
"""
from __future__ import annotations

from datetime import date, timedelta

FORMULARIOS = ("10-K", "10-K/A", "10-KT", "10-Q", "10-Q/A")

#: Entrega mais velha que isto e ainda sem dado correspondente e emenda sem
#: fatos (10-K/A): nao ocupa vaga do lote para sempre.
JANELA_DIAS = 120

SQL_SIMBOLOS_COM_FILING_NOVO = """
    SELECT symbol FROM (
        SELECT DISTINCT ON (a.company_id) a.symbol, f.pendente_desde
        FROM market_us.assets a
        JOIN (
            SELECT s.symbol, min(s.filing_date) AS pendente_desde
            FROM market_us.sec_filings s
            JOIN (
                SELECT a2.symbol, max(i.available_at) AS ultima
                FROM market_us.assets a2
                JOIN market_us.income_statements i ON i.company_id = a2.company_id
                GROUP BY a2.symbol
            ) u ON u.symbol = s.symbol
            WHERE s.form = ANY(:formularios) AND s.filing_date > u.ultima
              AND s.filing_date >= :desde
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
    desde = date.today() - timedelta(days=JANELA_DIAS)
    return [r[0] for r in conn.execute(
        text(q), {"formularios": list(FORMULARIOS), "desde": desde})]
