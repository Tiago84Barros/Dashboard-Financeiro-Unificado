"""Banda de sustentabilidade de payout, compartilhada entre B3 e EUA.

Até a rodada de correção 1 da task 1, `core/b3_renda_sustentavel.py` e
`core/us_renda_sustentavel.py` declaravam, verbatim, as mesmas constantes e a
mesma função por partes sob o mesmo nome `sustentabilidade_do_ano` — e as duas
cópias já haviam divergido: para ausência (`None`, `nan`, `inf`, texto não
numérico) a do B3 devolvia `0.0` (nota pior possível) e a dos EUA devolvia
`None`. Isso contradiz a Global Constraint do plano — "Ausência nunca pune:
cai no neutro, nunca em zero" — então esta é a ÚNICA definição, e a política
de ausência unificada é a não punitiva: devolver `None`.

Módulo puro (sem pandas, sem Streamlit, sem banco) para que ambos os domínios
possam importá-lo sem herdar dependências alheias.
"""
from __future__ import annotations

import math

JANELA_ANOS = 8
MIN_ANOS = 3

# Faixa de dois lados. Distribuir quase nada e distribuir muito acima do lucro
# são as duas formas de o dividendo não ser um dividendo sustentável — por isso
# a nota cai nas DUAS pontas, e não apenas acima do teto.
PISO, OTIMO_LO, OTIMO_HI, TETO = 0.05, 0.25, 0.80, 1.30

__all__ = [
    "JANELA_ANOS", "MIN_ANOS", "PISO", "OTIMO_LO", "OTIMO_HI", "TETO",
    "sustentabilidade_do_ano",
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
    """Nota ``[0, 1]`` de sustentabilidade da distribuição de UM exercício.

    Devolve ``None`` quando o payout é ausente, não-finito ou inconversível —
    ausência nunca pune (Global Constraint do plano). Os trechos externos são
    lineares: PISO--OTIMO_LO sobe de zero a um e OTIMO_HI--TETO cai de um a
    zero. Payout é uma razão decimal, não percentual.
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
