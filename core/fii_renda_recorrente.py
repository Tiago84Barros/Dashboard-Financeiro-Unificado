"""Renda recorrente e evidência de proteção: fonte única para os dois consumidores.

O piso de elegibilidade incidia sobre o DY divulgado, e por isso selecionava
ativamente o yield inflado por receita não recorrente. A renda que decide passa
a ser ``dy_12m * income_recurrence``.

Elegibilidade e score importam deste módulo. Ter a regra em um consumidor só já
fez a vitrine publicar dividend yield que a tela não reconhecia.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

DY_RECORRENTE_FORMULA = "dy_12m * income_recurrence"

TETO_LOCATARIO = .40
TETO_VENCIMENTO_24M = .25

#: Métricas cuja ausência é omissão do gestor, não do nosso pipeline. Só fazem
#: sentido para carteira própria de imóveis.
PROTECAO_NAO_DIVULGADA = ("tenant_concentration", "lease_expiry_concentration_24m")

_TIPOS_COM_IMOVEL = frozenset({"tijolo", "hibrido"})


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _fracao(value: Any) -> float | None:
    """A vitrine grava percentuais ora em fração (0.18) ora em ponto (18.0)."""
    number = _finite(value)
    if number is None:
        return None
    return number / 100.0 if abs(number) > 1 else number


def dy_recorrente(row: Mapping[str, Any]) -> float | None:
    """Parcela do yield sustentada por resultado recorrente.

    Devolve ``None`` — nunca o DY bruto — quando falta qualquer um dos dois
    insumos. Um fallback que só preenche lacuna nunca contradiz a entrada, e
    contradizer o yield divulgado é exatamente a função desta métrica.
    """
    dy = _fracao(row.get("dy_12m"))
    recorrencia = _fracao(row.get("income_recurrence"))
    if dy is None or recorrencia is None:
        return None
    # round() mantém o número estável entre o widget, o prompt e o teste.
    return round(dy * recorrencia, 4)


def _tem_imovel(row: Mapping[str, Any]) -> bool:
    return str(row.get("tipo") or "").strip().lower() in _TIPOS_COM_IMOVEL


def estado_concentracao(row: Mapping[str, Any], chave: str, teto: float) -> str:
    """``ok`` | ``acima_do_teto`` | ``nao_divulgado``.

    Os três estados são distintos de propósito: vetar apenas quem divulga
    premiaria quem cala, e tratar ausência como zero aprovaria o opaco.
    """
    valor = _fracao(row.get(chave))
    if valor is None:
        return "nao_divulgado"
    return "acima_do_teto" if valor > teto else "ok"


def protecao_nao_divulgada(row: Mapping[str, Any]) -> tuple[str, ...]:
    """Métricas de proteção que o gestor não publicou, para tijolo e híbrido."""
    if not _tem_imovel(row):
        return ()
    return tuple(chave for chave in PROTECAO_NAO_DIVULGADA
                 if _fracao(row.get(chave)) is None)
