"""
core/rebalancing.py — políticas de rebalanceamento de portfólio.

Implementa a recomendação M6 do parecer da banca examinadora (2026-05-23):
substituir rebalanceamento anual fixo por threshold-based rebalancing
(rebalancear apenas quando o peso de algum ativo desviar mais que X%
da meta). Reduz custos sem comprometer tracking error.

Referência: Kritzman, Page & Turkington (2009), "What Practitioners
Need to Know... About Rebalancing", Financial Analysts Journal, 65(2).

Três políticas disponíveis:

  • CalendarRebalance   — rebalanceia em intervalos fixos (1m, 3m, 6m, 1a)
  • ThresholdRebalance  — rebalanceia quando |desvio| > banda
  • HybridRebalance     — calendário + threshold (rebal no mais cedo)

Função pura — recebe estado atual e meta, decide se deve rebalancear.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

# ──────────────────────────────────────────────────────────────────────────
# Interface comum
# ──────────────────────────────────────────────────────────────────────────

class RebalancePolicy(Protocol):
    """Política de rebalanceamento."""

    def deve_rebalancear(
        self,
        pesos_atuais:  dict[str, float],
        pesos_meta:    dict[str, float],
        data_atual:    date,
        ultimo_rebal:  date | None,
    ) -> tuple[bool, str]:
        """Retorna (deve_rebalancear, motivo)."""
        ...


# ──────────────────────────────────────────────────────────────────────────
# Políticas concretas
# ──────────────────────────────────────────────────────────────────────────

def _maior_desvio(
    pesos_atuais: dict[str, float],
    pesos_meta:   dict[str, float],
) -> tuple[float, str]:
    """Maior |peso_atual - peso_meta| sobre a UNIAO dos dois conjuntos.

    Iterar so `pesos_meta` deixa invisivel exatamente o maior desvio possivel:
    uma posicao que o alvo mandou zerar some do dicionario de metas, e o ativo
    nunca e examinado -- uma carteira com 30% num ticker que saiu da selecao
    reportava desvio 0,0 e "nao precisa rebalancear". A assimetria era
    unidirecional: entradas novas (`pesos_atuais.get(tk, 0.0)`) eram detectadas,
    saidas nao. Ver `tests/test_rebalancing_saida_de_posicao.py`.
    """
    maior, critico = 0.0, ""
    for tk in sorted(set(pesos_atuais) | set(pesos_meta)):
        desvio = abs(pesos_atuais.get(tk, 0.0) - pesos_meta.get(tk, 0.0))
        if desvio > maior:
            maior, critico = desvio, tk
    return maior, critico


@dataclass
class CalendarRebalance:
    """Política calendárica clássica — rebalanceia a cada N dias."""
    intervalo_dias: int = 365  # default anual

    def deve_rebalancear(self, pesos_atuais, pesos_meta, data_atual,
                          ultimo_rebal):
        if ultimo_rebal is None:
            return True, "Primeira execução — alocação inicial"
        delta = (data_atual - ultimo_rebal).days
        if delta >= self.intervalo_dias:
            return True, f"Calendário: {delta}d desde último rebal ({self.intervalo_dias}d)"
        return False, ""


@dataclass
class ThresholdRebalance:
    """Política threshold-based — rebalanceia quando desvio > banda.

    banda_abs: float em pontos percentuais (ex.: 0.05 = 5 p.p.)
    Se o peso atual de algum ativo desviar mais que `banda_abs` do peso
    meta, dispara rebalanceamento. Análogo ao 'no-trade region' usado
    em Almgren-Chriss para execução algorítmica.
    """
    banda_abs:       float = 0.05  # 5 p.p. default
    rebal_inicial:   bool  = True  # rebal sempre na primeira execução

    def deve_rebalancear(self, pesos_atuais, pesos_meta, data_atual,
                          ultimo_rebal):
        if ultimo_rebal is None and self.rebal_inicial:
            return True, "Primeira execução — alocação inicial"
        if not pesos_meta and not pesos_atuais:
            return False, ""

        max_desvio, ticker_critico = _maior_desvio(pesos_atuais, pesos_meta)

        if max_desvio > self.banda_abs:
            return True, (f"Threshold: {ticker_critico} desviou "
                          f"{max_desvio*100:.1f}p.p. (banda {self.banda_abs*100:.0f}p.p.)")
        return False, ""


@dataclass
class HybridRebalance:
    """Calendário + threshold — rebalanceia no que vier primeiro.

    Pratica padrão em gestoras institucionais: dispara rebal por
    threshold se mercado se move rápido, garante revisão calendária
    se o mercado fica lateralizado por muito tempo.
    """
    intervalo_dias_max: int   = 365
    banda_abs:          float = 0.05

    def deve_rebalancear(self, pesos_atuais, pesos_meta, data_atual,
                          ultimo_rebal):
        if ultimo_rebal is None:
            return True, "Primeira execução — alocação inicial"

        delta = (data_atual - ultimo_rebal).days
        if delta >= self.intervalo_dias_max:
            return True, f"Calendário max: {delta}d ≥ {self.intervalo_dias_max}d"

        if pesos_meta or pesos_atuais:
            max_desvio, tk_crit = _maior_desvio(pesos_atuais, pesos_meta)
            if max_desvio > self.banda_abs:
                return True, (f"Threshold: {tk_crit} desviou "
                              f"{max_desvio*100:.1f}p.p. (banda {self.banda_abs*100:.0f}p.p.)")
        return False, ""


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────

def pesos_atuais_de_cotas(
    cotas:    dict[str, float],
    precos:   dict[str, float],
) -> dict[str, float]:
    """Converte {ticker: qty} + {ticker: preço} em {ticker: peso}."""
    valores = {tk: cotas.get(tk, 0.0) * precos.get(tk, 0.0) for tk in cotas}
    total = sum(valores.values()) or 1.0
    return {tk: v / total for tk, v in valores.items()}


def reducao_turnover_estimada(
    politica_legada:   "RebalancePolicy",
    politica_nova:     "RebalancePolicy",
    historico_pesos:   list[dict[str, float]],
    historico_metas:   list[dict[str, float]],
    datas:             list[date],
) -> dict[str, float]:
    """
    Compara duas políticas em um histórico de pesos, retornando:
      n_rebal_legada:   quantos rebals a política legada dispararia
      n_rebal_nova:     quantos a política nova dispararia
      reducao_pct:      redução de EVENTOS em %

    Útil para calibrar banda_abs em ThresholdRebalance.

    Atenção ao que esta função NÃO mede: turnover. Ela conta com que
    frequência cada política dispara, não quanto volume cada disparo move.
    Uma política que rebalanceia metade das vezes mas move o dobro em cada
    vez tem `reducao_pct = 50%` e turnover idêntico — e portanto custo de
    corretagem idêntico. Para a conclusão sobre custo, o volume precisa ser
    somado (`0.5 * Σ|Δw|` por evento, como em `core.us_backtest.turnover`),
    coisa que esta função não faz porque não recebe os pesos pós-rebal.
    """
    if not historico_pesos or not datas:
        return {"n_rebal_legada": 0, "n_rebal_nova": 0, "reducao_pct": 0.0}

    n_leg = 0
    n_new = 0
    ultimo_leg = None
    ultimo_new = None

    for w_atual, w_meta, dt in zip(historico_pesos, historico_metas, datas):
        if politica_legada.deve_rebalancear(w_atual, w_meta, dt, ultimo_leg)[0]:
            n_leg += 1
            ultimo_leg = dt
        if politica_nova.deve_rebalancear(w_atual, w_meta, dt, ultimo_new)[0]:
            n_new += 1
            ultimo_new = dt

    reducao = ((n_leg - n_new) / n_leg * 100) if n_leg > 0 else 0.0
    return {
        "n_rebal_legada": n_leg,
        "n_rebal_nova":   n_new,
        "reducao_pct":    reducao,
    }
