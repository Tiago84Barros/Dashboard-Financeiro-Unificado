"""Distingue renda de devolucao de capital em ``market.dividends``.

A-128: ``AMORTIZACAO`` e ``REST CAP DIN`` nao sao rendimento -- sao devolucao
do capital do proprio cotista. Somados ao provento, inflam o dividend yield e,
em FII, inflam justamente a metrica de manchete da decisao. Medido em
2026-08-24 sobre o Supabase: 415 pares ticker-ano inflados em 234 tickers,
inflacao media de 139%; RBRI11/2026 exibia 252,20 de "provento" com renda
real zero.

A-129: a mesma agregacao somava o eco de classe da brapi (o mesmo evento
gravado sob CEBR5 e CEBR6, por exemplo). Dentro de um mesmo (data, tipo), o
valor honesto e o MINIMO -- somar repete o evento. Entre tipos DISTINTOS na
mesma data, somar e correto: dividendo e JCP no mesmo dia sao dois eventos de
verdade, e sao 1.120 ocorrencias na base.
"""
from __future__ import annotations

#: Eventos que remuneram o investidor sem devolver o principal.
TIPOS_RENDA: frozenset[str] = frozenset({"RENDIMENTO", "JCP", "DIVIDENDO"})

#: Eventos que devolvem capital. Entram no fluxo de caixa, nunca no yield.
TIPOS_DEVOLUCAO_CAPITAL: frozenset[str] = frozenset({"AMORTIZAÇÃO", "REST CAP DIN"})


def eh_renda(tipo: str | None) -> bool:
    """``True`` para evento de renda. Tipo desconhecido conta como renda.

    A escolha e deliberada: um tipo novo que a ingestao traga aparece no yield
    (e portanto e visivel) em vez de sumir em silencio. Sumir seria o erro
    caro -- a-124 mostrou o que custa um sinal que ninguem ve.
    """
    if tipo is None:
        return True
    return str(tipo).strip().upper() not in _CAPITAL_UPPER


_CAPITAL_UPPER = frozenset(t.upper() for t in TIPOS_DEVOLUCAO_CAPITAL)

#: Predicado SQL que mantem apenas eventos de renda. ``col`` e a coluna tipo.
def sql_apenas_renda(col: str = "type") -> str:
    lista = ", ".join(f"'{t}'" for t in sorted(TIPOS_DEVOLUCAO_CAPITAL))
    return f"({col} IS NULL OR upper({col}) NOT IN ({lista}))"
