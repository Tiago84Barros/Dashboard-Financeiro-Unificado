"""Piso de negociabilidade do módulo EUA.

Por que NÃO tem troca de classe irmã, ao contrário do módulo B3. Lá o app
trocava BRAP3 por BRAP4 — mesma empresa, mesma exposição econômica, 72× mais
giro. Transplantar isso para os EUA seria perigoso, e a checagem de 03/08/2026
mostrou por quê: o vínculo ``company_id`` do banco americano agrupa instrumentos
que não são classes de ação.

    JPM (US$ 309) e VYLD (US$ 28)  → VYLD não é classe do JPMorgan, é outro papel
    ACON e ACONW (US$ 0,02)        → warrant, com UM pregão em seis meses
    T e TBB                        → baby bond, que é dívida
    AACI / AACIU / AACIW           → SPAC com unit e warrant

Das 448 empresas "multiclasse", a maioria são **ações preferenciais**
(``JPM-PD``, ``BAC-PE``, ``WFC-PC``) — e nos EUA preferred é quase-dívida:
dividendo fixo, sem voto, comportamento de bond. Não é o par ON/PN brasileiro,
onde as duas são capital de risco da mesma empresa. Pior: ``security_type`` só
distingue ``common`` de ``reit``, então o banco marca as preferenciais como
"common" e não há campo confiável para separá-las.

Trocar uma ação por um warrant, um ETF ou uma debênture causaria dano muito
maior que a iliquidez que a troca pretendia corrigir. Então aqui o módulo só
FILTRA — e quem consome sabe que a decisão de classe continua com o usuário.

Puro (sem banco, sem rede). Coberto por tests/test_us_liquidity.py.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

VERSION = "us-liquidity-1.0.0"

# Escolhido com o usuário em 03/08/2026, medido sobre 2.752 empresas com giro:
# mediana de US$ 7,05 mi/dia, p25 em US$ 0,42 mi, p75 em US$ 60,6 mi. O piso de
# US$ 1 mi mantém 1.864 empresas (68%); US$ 5 mi cairia para 1.481 e US$ 20 mi
# para 1.062.
#
# A ordem de grandeza é outra: no B3 o piso equivalente é R$ 500 mil/dia, porque
# a mediana brasileira fica perto disso. Copiar o número de lá para cá não
# filtraria nada — 1 milhão de reais é ruído no mercado americano.
PISO_PADRAO_USD = 1_000_000.0


@dataclass(frozen=True)
class LiquidityPolicy:
    piso_diario_usd: float = PISO_PADRAO_USD


def _num(valor: object) -> float:
    try:
        return float(valor)                    # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float("nan")


def formata_usd(valor: float) -> str:
    """US$ com separador de milhar no padrão americano."""
    return f"{valor:,.0f}"


def aplicar_piso(
    symbols: Sequence[str],
    giro: Mapping[str, float],
    *,
    policy: LiquidityPolicy | None = None,
) -> tuple[list[str], list[dict], list[str]]:
    """Separa quem negocia o bastante de quem não negocia.

    Returns:
        (aprovados, removidos, avisos).

    Símbolo SEM giro medido não é removido: ausência de medição não é prova de
    iliquidez, e o universo americano tem 3.759 ativos contra 2.752 com série de
    volume — remover os 1.007 sem dado cortaria empresa boa por lacuna de
    coleta. Eles saem num aviso à parte, para o usuário saber que não foram
    verificados.
    """
    policy = policy or LiquidityPolicy()
    aprovados: list[str] = []
    removidos: list[dict] = []
    sem_medicao: list[str] = []

    for s in symbols:
        symbol = str(s).upper()
        g = _num(giro.get(symbol))
        if g != g:
            sem_medicao.append(symbol)
            aprovados.append(symbol)
            continue
        if g >= policy.piso_diario_usd:
            aprovados.append(symbol)
        else:
            removidos.append({"symbol": symbol, "giro_usd": g})

    avisos: list[str] = []
    if removidos:
        exemplos = ", ".join(r["symbol"] for r in sorted(
            removidos, key=lambda r: r["symbol"])[:12])
        reticencias = "…" if len(removidos) > 12 else ""
        avisos.append(
            f"{len(removidos)} empresa(s) abaixo de US$ "
            f"{formata_usd(policy.piso_diario_usd)}/dia de volume negociado — "
            f"{exemplos}{reticencias}")
    if sem_medicao:
        avisos.append(
            f"{len(sem_medicao)} empresa(s) sem série de volume: **não foram "
            "verificadas** quanto à negociabilidade e seguem no universo. "
            "Ausência de medição não é prova de iliquidez.")
    return aprovados, removidos, avisos
