"""Elegibilidade auditável da Metodologia Integrada de FIIs.

O módulo mantém os filtros do usuário separados do score. Um fundo reprovado
não recebe nota artificialmente menor: ele sai do universo elegível com razões
explícitas. Valores ausentes também não são convertidos em zero.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from core.fii_renda_recorrente import (
    TETO_LOCATARIO,
    TETO_VENCIMENTO_24M,
    dy_recorrente,
    estado_concentracao,
)

INTEGRATED_MODEL_VERSION = "6.8.0"

#: Portões de proteção ao investidor e a severidade de cada um. São os únicos
#: que admitem concessão por viabilidade: reprovar em qualquer outro portão
#: (liquidez, histórico, drawdown, faixa de P/VP, teto de plausibilidade do DY)
#: deixa o fundo fora em definitivo. A severidade ordena a readmissão — quanto
#: maior, mais tarde o fundo volta, e a opacidade (métrica que o gestor não
#: divulgou) é a pior das quatro porque não tem tamanho conhecido.
PORTOES_DE_PROTECAO: dict[str, int] = {
    "renda recorrente abaixo do mínimo": 1,
    "vencimentos em 24m acima do teto": 2,
    "concentração de locatário acima do teto": 3,
    "renda recorrente ausente": 4,
}


class ColunasDeElegibilidadeAusentes(ValueError):
    """O quadro não traz as colunas que a política lê.

    Coluna ausente era lida como métrica ausente e reprovava o universo
    inteiro: uma falha de leitura chegava à tela como "0 elegíveis", que é um
    resultado legítimo de aparência. Erro tem que parecer erro.
    """

    def __init__(self, missing_columns: Iterable[str]) -> None:
        self.missing_columns = tuple(missing_columns)
        super().__init__(
            "leitura incompleta do universo de FIIs: as colunas exigidas pela "
            "política não existem no quadro ("
            + ", ".join(self.missing_columns)
            + "); nenhum fundo foi classificado"
        )


@dataclass(frozen=True)
class IntegratedEligibilityPolicy:
    min_daily_liquidity: float = 1_000_000.0
    # O piso incide sobre a renda recorrente, não sobre a divulgada: sobre o DY
    # bruto ele selecionava ativamente o yield inflado por evento não
    # recorrente. O rename é deliberado — impede que um chamador passe a
    # semântica antiga em silêncio.
    min_recurrent_dy_12m: float = .08
    min_history_months: int = 24
    max_drawdown: float = .35
    pvp_min: float = .55
    pvp_max: float = 1.30
    max_tenant_concentration: float = TETO_LOCATARIO
    max_lease_expiry_24m: float = TETO_VENCIMENTO_24M
    require_pvp_below_one: bool = False
    require_multi_region: bool = False
    require_min_properties: bool = False
    min_properties: int = 8
    require_multicategory: bool = False


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _normalized_yield(value: Any) -> float | None:
    number = _number(value)
    if number is None:
        return None
    return number / 100.0 if number > 1 else number


def _eligibility_reasons(row: dict, policy: IntegratedEligibilityPolicy) -> list[str]:
    reasons: list[str] = []
    fii_type = str(row.get("tipo") or "").lower()
    liquidity = _number(row.get("liquidez_diaria"))
    dy = _normalized_yield(row.get("dy_12m"))
    pvp = _number(row.get("pvp"))
    history = _number(row.get("history_months"))
    drawdown = _number(row.get("max_drawdown"))

    if liquidity is None:
        reasons.append("liquidez ausente")
    elif liquidity < policy.min_daily_liquidity:
        reasons.append("liquidez abaixo do mínimo")
    recorrente = dy_recorrente(row)
    if dy is None:
        reasons.append("DY 12m ausente")
    elif dy > .20:
        # Teto de sanidade da fonte, deliberadamente sobre o DY bruto.
        reasons.append("DY 12m acima do limite de plausibilidade")
    elif recorrente is None:
        reasons.append("renda recorrente ausente")
    elif recorrente < policy.min_recurrent_dy_12m:
        reasons.append("renda recorrente abaixo do mínimo")
    if pvp is None:
        reasons.append("P/VP ausente")
    elif not policy.pvp_min <= pvp <= policy.pvp_max:
        reasons.append("P/VP fora da faixa de plausibilidade")
    elif policy.require_pvp_below_one and pvp >= 1:
        reasons.append("P/VP não está abaixo de 1")
    if policy.min_history_months > 0:
        if history is None:
            reasons.append("histórico ausente")
        elif history < policy.min_history_months:
            reasons.append("histórico abaixo do mínimo")
    if policy.max_drawdown > 0:
        if drawdown is None:
            reasons.append("drawdown ausente")
        elif drawdown < -policy.max_drawdown:
            reasons.append("drawdown acima da tolerância")

    if fii_type in {"tijolo", "hibrido"}:
        regions = _number(row.get("region_count"))
        properties = _number(row.get("property_count"))
        if policy.require_multi_region and (regions is None or regions < 2):
            reasons.append("menos de duas regiões identificadas")
        if policy.require_min_properties and (
            properties is None or properties < policy.min_properties
        ):
            reasons.append(f"menos de {policy.min_properties} imóveis identificados")
        if policy.require_multicategory and not bool(row.get("multi_category")):
            reasons.append("não classificado como multicategoria/híbrido")
        if estado_concentracao(row, "tenant_concentration",
                               policy.max_tenant_concentration) == "acima_do_teto":
            reasons.append("concentração de locatário acima do teto")
        if estado_concentracao(row, "lease_expiry_concentration_24m",
                               policy.max_lease_expiry_24m) == "acima_do_teto":
            reasons.append("vencimentos em 24m acima do teto")
    return reasons


def colunas_exigidas(policy: IntegratedEligibilityPolicy) -> tuple[str, ...]:
    """Colunas cuja AUSÊNCIA reprova o fundo sob esta política.

    Deliberadamente não inclui ``tenant_concentration`` nem
    ``lease_expiry_concentration_24m``: a ausência delas é ``nao_divulgado``,
    que não reprova ninguém, e cobrá-las aqui transformaria opacidade do gestor
    em falha do nosso pipeline.
    """
    exigidas = ["liquidez_diaria", "dy_12m", "income_recurrence", "pvp"]
    if policy.min_history_months > 0:
        exigidas.append("history_months")
    if policy.max_drawdown > 0:
        exigidas.append("max_drawdown")
    return tuple(exigidas)


def _colunas_ausentes(
    source: list[dict], policy: IntegratedEligibilityPolicy,
) -> tuple[str, ...]:
    if not source:
        return ()
    presentes: set[str] = set()
    for row in source:
        presentes.update(row.keys())
    return tuple(coluna for coluna in colunas_exigidas(policy)
                 if coluna not in presentes)


def severidade_da_concessao(reasons: Iterable[str]) -> tuple[int, int, int]:
    """Chave de ordenação da readmissão: pior portão, quantidade, soma."""
    pesos = [PORTOES_DE_PROTECAO[reason] for reason in reasons
             if reason in PORTOES_DE_PROTECAO]
    if not pesos:
        return (0, 0, 0)
    return (max(pesos), len(pesos), sum(pesos))


def apply_integrated_eligibility(
    rows: Iterable[dict], policy: IntegratedEligibilityPolicy,
) -> tuple[list[dict], dict]:
    """Aplica filtros determinísticos e devolve razões agregadas de exclusão.

    O relatório também devolve, em ``concession_candidates``, os fundos
    reprovados EXCLUSIVAMENTE pelos portões de proteção ao investidor — o
    material de que o orquestrador da carteira precisa quando o universo
    estrito não comporta a carteira. O conjunto estrito e ``eligible_count``
    não mudam por causa disso: a proteção só cede na composição da carteira, e
    de forma nomeada, nunca no silêncio da contagem.
    """
    source = [dict(row) for row in rows]
    ausentes = _colunas_ausentes(source, policy)
    if ausentes:
        raise ColunasDeElegibilidadeAusentes(ausentes)
    eligible: list[dict] = []
    concession: list[dict] = []
    exclusions: Counter[str] = Counter()
    for row in source:
        reasons = _eligibility_reasons(row, policy)
        protecao = [reason for reason in reasons if reason in PORTOES_DE_PROTECAO]
        duros = [reason for reason in reasons if reason not in PORTOES_DE_PROTECAO]
        enriched = {
            **row,
            "integrated_model_version": INTEGRATED_MODEL_VERSION,
            "eligibility_status": "eligible" if not reasons else "excluded",
            "eligibility_reasons": tuple(reasons),
        }
        if reasons:
            exclusions.update(reasons)
            if protecao and not duros:
                concession.append({
                    **enriched,
                    "protecao_cedivel": tuple(protecao),
                    "severidade_da_concessao": severidade_da_concessao(protecao),
                })
        else:
            eligible.append(enriched)
    # Ordem crescente de severidade; ticker desempata para a carteira não
    # depender da ordem de leitura da fonte.
    concession.sort(key=lambda row: (row["severidade_da_concessao"],
                                     str(row.get("ticker") or "")))
    report = {
        "model_version": INTEGRATED_MODEL_VERSION,
        "universe_count": len(source),
        "eligible_count": len(eligible),
        "eligible_fraction": len(eligible) / len(source) if source else 0.0,
        "exclusion_counts": dict(exclusions.most_common()),
        "concession_candidates": tuple(concession),
        "concession_count": len(concession),
        "concession_summary": tuple({
            "ticker": str(row.get("ticker") or ""),
            "reasons": list(row["protecao_cedivel"]),
            "severity": row["severidade_da_concessao"][0],
        } for row in concession),
        "policy": asdict(policy),
    }
    return eligible, report
