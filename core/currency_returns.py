"""Conversão monetária e decomposição de retorno sem proxies silenciosos."""

from __future__ import annotations


def converter_para_brl(valor_moeda_origem: float | None, taxa_brl: float | None) -> float | None:
    if valor_moeda_origem is None or taxa_brl is None:
        return None
    if taxa_brl <= 0:
        return None
    return valor_moeda_origem * taxa_brl


def retorno_moeda_origem(valor_atual: float | None, valor_custo: float | None) -> float | None:
    if valor_atual is None or valor_custo is None or valor_custo <= 0:
        return None
    if valor_atual < 0:
        return None
    return valor_atual / valor_custo - 1.0


def retorno_em_brl(
    valor_atual_origem: float | None,
    valor_custo_origem: float | None,
    cambio_atual: float | None,
    cambio_compra: float | None,
) -> float | None:
    """Retorno BRL; exige câmbio atual e câmbio histórico do custo."""
    atual_brl = converter_para_brl(valor_atual_origem, cambio_atual)
    custo_brl = converter_para_brl(valor_custo_origem, cambio_compra)
    return retorno_moeda_origem(atual_brl, custo_brl)


def decompor_retorno_cambial(
    retorno_ativo: float | None,
    retorno_cambio: float | None,
) -> dict[str, float] | None:
    """Decompõe retorno total: ativo + câmbio + interação."""
    if retorno_ativo is None or retorno_cambio is None:
        return None
    interacao = retorno_ativo * retorno_cambio
    return {
        "ativo": retorno_ativo,
        "cambio": retorno_cambio,
        "interacao": interacao,
        "total_brl": retorno_ativo + retorno_cambio + interacao,
    }
