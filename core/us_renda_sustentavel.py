"""Leitura pura de sustentabilidade histórica de dividendos nos EUA.

Os dados anuais do EDGAR usam ``dividends_paid`` como fluxo de caixa: em
geral negativo por representar saída de caixa. As comparações usam sua
magnitude, mas preservam lacunas e valores não finitos como ausência.

Esta fundação não decide elegibilidade. ``is_reit`` é devolvido somente como
metadado para que uma etapa futura aplique uma política própria a REITs, cuja
distribuição é normalmente analisada por FFO/AFFO, não por lucro GAAP.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from statistics import median

JANELA_ANOS = 8
MIN_ANOS = 3

# Faixa bilateral: a distribuição quase nula e a persistentemente superior ao
# lucro são ambas pouco representativas de uma política sustentável de renda.
PISO, OTIMO_LO, OTIMO_HI, TETO = 0.05, 0.25, 0.80, 1.30

__all__ = [
    "JANELA_ANOS", "MIN_ANOS", "PISO", "OTIMO_LO", "OTIMO_HI", "TETO",
    "sustentabilidade_do_ano", "leitura_da_serie",
]


def _numero_finito(valor: object) -> float | None:
    """Converte somente observações numéricas finitas; ausência continua ausência."""
    if isinstance(valor, bool):
        return None
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    return numero if math.isfinite(numero) else None


def sustentabilidade_do_ano(payout: object) -> float | None:
    """Nota ``[0, 1]`` de um payout anual, ou ``None`` se ele é ausente.

    Os trechos externos são lineares: 5%--25% sobe de zero a um e
    80%--130% cai de um a zero. Payout é uma razão decimal, não percentual.
    """
    valor = _numero_finito(payout)
    if valor is None:
        return None
    if valor <= PISO or valor >= TETO:
        return 0.0
    if OTIMO_LO <= valor <= OTIMO_HI:
        return 1.0
    if valor < OTIMO_LO:
        return (valor - PISO) / (OTIMO_LO - PISO)
    return (TETO - valor) / (TETO - OTIMO_HI)


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
