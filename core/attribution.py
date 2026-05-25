"""
core/attribution.py — Brinson-Hood-Beebower performance attribution.

Implementa a recomendação A2 do parecer da banca examinadora (2026-05-23):
decomposição clássica do retorno entre alocação setorial e seleção de
ativos. Referência: Brinson, Hood & Beebower (1986), "Determinants of
Portfolio Performance", Financial Analysts Journal, 42(4).

Decomposição em 3 efeitos:

  • Allocation Effect (AE):
        AE_s = (w_p,s − w_b,s) × R_b,s
        Mede o impacto de over/under-weight em cada setor versus o benchmark.
        Positivo se sobre-pesou um setor que rendeu mais que a média.

  • Selection Effect (SE):
        SE_s = w_b,s × (R_p,s − R_b,s)
        Mede o impacto de escolher ativos diferentes do benchmark dentro
        do mesmo setor.

  • Interaction Effect (IE):
        IE_s = (w_p,s − w_b,s) × (R_p,s − R_b,s)
        Termo cruzado de allocation × selection.

Total: R_p − R_b = Σ (AE_s + SE_s + IE_s)

Função pura: recebe posições + benchmark setorial e retorna decomposição.
Não acessa DB. Pode ser usada em loop tight (backtest etc).
"""
from __future__ import annotations

from dataclasses import dataclass


# ──────────────────────────────────────────────────────────────────────────
# Benchmark setorial — IBOV decomposto (médias 2020-2025 aproximadas)
# ──────────────────────────────────────────────────────────────────────────
# Aproximação dos pesos do IBOV por setor agregado. Em produção, ler
# composição oficial atual da B3 via b3.com.br/indices.
IBOV_PESOS_SETORIAIS: dict[str, float] = {
    "Financeiro":        0.25,
    "Petróleo":          0.18,
    "Materiais Básicos": 0.12,
    "Utilidade Pública": 0.08,
    "Consumo Cíclico":   0.07,
    "Consumo Não-Cíclico": 0.07,
    "Saúde":             0.06,
    "Industrial":        0.05,
    "Tecnologia":        0.04,
    "Comunicações":      0.03,
    "Bens Industriais":  0.03,
    "Outros":            0.02,
}

# Retornos setoriais médios anuais 2020-2025 (proxy IBX/setoriais)
# Em produção, calcular a partir de IDIV / IFNC / ICON / IMOB / etc.
IBOV_RETORNOS_SETORIAIS_DEF: dict[str, float] = {
    "Financeiro":         0.12,  # IFNC ~12% a.a.
    "Petróleo":           0.18,  # PETR3/PRIO3 puxando
    "Materiais Básicos":  0.05,  # VALE3 lateral
    "Utilidade Pública":  0.10,
    "Consumo Cíclico":   -0.02,
    "Consumo Não-Cíclico": 0.08,
    "Saúde":              0.04,
    "Industrial":         0.06,
    "Tecnologia":         0.15,
    "Comunicações":       0.05,
    "Bens Industriais":   0.07,
    "Outros":             0.04,
}


@dataclass
class AttributionResult:
    """Resultado da decomposição Brinson para um setor."""
    setor:               str
    peso_portfolio:      float    # w_p,s
    peso_benchmark:      float    # w_b,s
    retorno_portfolio:   float    # R_p,s (retorno do setor no portfólio)
    retorno_benchmark:   float    # R_b,s (retorno do setor no benchmark)
    allocation_effect:   float    # AE_s
    selection_effect:    float    # SE_s
    interaction_effect:  float    # IE_s
    total_effect:        float    # AE + SE + IE


def _setor_canonico(classe: str) -> str:
    """Normaliza nome de classe para um setor canônico do benchmark."""
    c = (classe or "Outros").strip()
    # Mapeamentos comuns
    if "ação" in c.lower() or "ações" in c.lower() or "stock" in c.lower():
        return "Financeiro"  # placeholder — quem fornece setor já vem categorizado
    if "fii" in c.lower() or "imob" in c.lower():
        return "Imobiliário"
    if "etf" in c.lower():
        return c  # ETF mantém label próprio (Brasil / Internacional)
    return c if c in IBOV_PESOS_SETORIAIS else "Outros"


def attribution_brinson(
    posicoes: list[dict],
    retornos_portfolio_setor: dict[str, float] | None = None,
    pesos_benchmark: dict[str, float] | None = None,
    retornos_benchmark: dict[str, float] | None = None,
) -> list[AttributionResult]:
    """
    Calcula decomposição Brinson-Hood-Beebower do portfólio versus benchmark.

    Args:
      posicoes: lista de dicts com keys 'setor' (str), 'valor_mercado' (float)
                e opcional 'rentab_pct' (% retorno realizado da posição)
      retornos_portfolio_setor: {setor: retorno_medio_setor_no_portfolio}.
                                Se None, calcula como média ponderada de
                                rentab_pct das posições no setor.
      pesos_benchmark: {setor: peso}. Default = IBOV_PESOS_SETORIAIS.
      retornos_benchmark: {setor: retorno}. Default = IBOV_RETORNOS_SETORIAIS_DEF.

    Returns:
      Lista de AttributionResult, um por setor presente no portfólio
      ou no benchmark. Soma dos total_effect == Active Return total.
    """
    pesos_b = pesos_benchmark or IBOV_PESOS_SETORIAIS
    retornos_b = retornos_benchmark or IBOV_RETORNOS_SETORIAIS_DEF

    # Calcula pesos e retornos do portfólio por setor
    total_p = sum(float(p.get("valor_mercado") or 0) for p in posicoes) or 1.0
    pesos_p: dict[str, float] = {}
    soma_ret_pond: dict[str, float] = {}
    soma_pesos: dict[str, float] = {}

    for pos in posicoes:
        vm = float(pos.get("valor_mercado") or 0)
        if vm <= 0:
            continue
        setor = _setor_canonico(str(pos.get("setor") or pos.get("classe") or "Outros"))
        pesos_p[setor] = pesos_p.get(setor, 0.0) + vm / total_p

        rentab = float(pos.get("rentab_pct") or 0) / 100.0  # vem em %, vira decimal
        soma_ret_pond[setor] = soma_ret_pond.get(setor, 0.0) + rentab * vm
        soma_pesos[setor] = soma_pesos.get(setor, 0.0) + vm

    # Se não passou retornos_portfolio_setor, deriva do rentab_pct das posições
    if retornos_portfolio_setor is None:
        retornos_p = {
            s: (soma_ret_pond[s] / soma_pesos[s]) if soma_pesos.get(s, 0) > 0 else 0.0
            for s in pesos_p
        }
    else:
        retornos_p = retornos_portfolio_setor

    # Calcula decomposição por setor (união portfólio + benchmark)
    setores = set(pesos_p) | set(pesos_b)
    resultados: list[AttributionResult] = []
    for s in sorted(setores):
        wp = pesos_p.get(s, 0.0)
        wb = pesos_b.get(s, 0.0)
        rp = retornos_p.get(s, 0.0)
        rb = retornos_b.get(s, 0.0)

        ae = (wp - wb) * rb
        se = wb * (rp - rb)
        ie = (wp - wb) * (rp - rb)
        total = ae + se + ie

        resultados.append(AttributionResult(
            setor=s, peso_portfolio=wp, peso_benchmark=wb,
            retorno_portfolio=rp, retorno_benchmark=rb,
            allocation_effect=ae, selection_effect=se,
            interaction_effect=ie, total_effect=total,
        ))

    return resultados


def attribution_summary(resultados: list[AttributionResult]) -> dict:
    """Sumariza decomposição agregada (soma sobre todos os setores)."""
    if not resultados:
        return {"allocation": 0.0, "selection": 0.0, "interaction": 0.0,
                "total_active_return": 0.0}
    return {
        "allocation":         sum(r.allocation_effect  for r in resultados),
        "selection":          sum(r.selection_effect   for r in resultados),
        "interaction":        sum(r.interaction_effect for r in resultados),
        "total_active_return": sum(r.total_effect       for r in resultados),
    }
