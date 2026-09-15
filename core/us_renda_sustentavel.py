"""Leitura pura de sustentabilidade histórica de dividendos nos EUA.

Os dados anuais do EDGAR usam ``dividends_paid`` como fluxo de caixa: em
geral negativo por representar saída de caixa. As comparações usam sua
magnitude, mas preservam lacunas e valores não finitos como ausência.

Esta fundação não decide elegibilidade. ``is_reit`` é devolvido somente como
metadado para que uma etapa futura aplique uma política própria a REITs, cuja
distribuição é normalmente analisada por FFO/AFFO, não por lucro GAAP.

As constantes da banda e ``sustentabilidade_do_ano`` moraram aqui até a
rodada de correção 1 da task 1, quando a mesma faixa duplicada em
``core/b3_renda_sustentavel.py`` foi encontrada já divergente na política de
ausência (C-1). Ambas agora importam de ``core/renda_sustentavel_banda.py``;
este módulo não muda de comportamento — já devolvia ``None`` para ausência.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from statistics import median

from core.renda_sustentavel_banda import (
    JANELA_ANOS,
    MIN_ANOS,
    OTIMO_HI,
    OTIMO_LO,
    PISO,
    TETO,
    _numero_finito,
    sustentabilidade_do_ano,
)

__all__ = [
    "JANELA_ANOS", "MIN_ANOS", "PISO", "OTIMO_LO", "OTIMO_HI", "TETO",
    "sustentabilidade_do_ano", "leitura_da_serie",
]


def _anos_unicos(serie_anual: Iterable[Mapping[str, object]] | None) -> list[Mapping[str, object]]:
    """Ordena exercícios anuais e descarta anos duplicados como dados ambíguos."""
    por_ano: dict[int, list[Mapping[str, object]]] = {}
    for linha in serie_anual or []:
        if not isinstance(linha, Mapping):
            continue
        ano = _numero_finito(linha.get("fiscal_year"))
        if ano is None or not ano.is_integer():
            continue
        por_ano.setdefault(int(ano), []).append(linha)
    return [linhas[0] for _, linhas in sorted(por_ano.items()) if len(linhas) == 1][-JANELA_ANOS:]


def _payout_valido(linha: Mapping[str, object]) -> float | None:
    """Razão dividendos/lucro, definida apenas para lucro anual positivo."""
    lucro = _numero_finito(linha.get("net_income"))
    dividendos = _numero_finito(linha.get("dividends_paid"))
    if lucro is None or dividendos is None or lucro <= 0:
        return None
    return abs(dividendos) / lucro


def leitura_da_serie(
    serie_anual: Iterable[Mapping[str, object]] | None,
    *,
    is_reit: bool = False,
) -> dict[str, float | int | bool | None]:
    """Resume até oito exercícios EDGAR, sem decidir elegibilidade.

    ``payout_mediano_hist`` e ``payout_sustentabilidade`` exigem pelo menos
    três payouts válidos (lucro positivo e ambos os campos finitos). A leitura
    de FCL considera apenas anos com FCL positivo e dividendos observados.
    """
    anos = _anos_unicos(serie_anual)
    payouts = [payout for linha in anos if (payout := _payout_valido(linha)) is not None]
    n_payout = len(payouts)

    comparaveis_fcl = 0
    acima_fcl = 0
    for linha in anos:
        dividendos = _numero_finito(linha.get("dividends_paid"))
        fcl = _numero_finito(linha.get("free_cash_flow"))
        if dividendos is None or fcl is None or fcl <= 0:
            continue
        comparaveis_fcl += 1
        if abs(dividendos) > fcl:
            acima_fcl += 1

    if n_payout < MIN_ANOS:
        payout_mediano = None
        nota = None
    else:
        payout_mediano = float(median(payouts))
        notas = [sustentabilidade_do_ano(payout) for payout in payouts]
        nota = sum(nota_ano for nota_ano in notas if nota_ano is not None) / n_payout

    return {
        "payout_mediano_hist": payout_mediano,
        "n_anos_payout": n_payout,
        "payout_sustentabilidade": nota,
        "n_anos_fcl_positivo": comparaveis_fcl,
        "n_anos_dividendos_acima_fcl": acima_fcl,
        "fracao_dividendos_acima_fcl": (
            acima_fcl / comparaveis_fcl if comparaveis_fcl else None
        ),
        "is_reit": bool(is_reit),
    }
