"""Explicações auditáveis para candidatos da carteira de diligência de FIIs.

As mensagens usam somente os scores e indicadores já calculados point-in-time.
Não criam fatos, não consultam rede e não convertem a triagem em recomendação.
"""
from __future__ import annotations

from math import ceil
from statistics import median
from typing import Any, Iterable


COMPONENT_LABELS = {
    "income": "renda", "valuation": "valuation", "liquidity": "liquidez",
    "quality": "qualidade dos ativos/carteira", "risk": "controle de risco",
    "governance": "governança e gestão", "stability": "estabilidade histórica",
}

METRIC_LABELS = {
    "conflict_alignment": "alinhamento de conflitos",
    "contract_quality": "qualidade dos contratos",
    "credit_spread_adequacy": "adequação do spread de crédito",
    "debtor_diversification": "diversificação de devedores",
    "delinquency": "inadimplência",
    "duration_anos": "duration",
    "geographic_diversification": "diversificação geográfica",
    "holdings_overlap": "sobreposição de ativos",
    "holdings_quality": "qualidade dos fundos investidos",
    "implied_cap_rate": "cap rate implícito",
    "income_growth_per_share_3y": "crescimento da renda por cota",
    "income_recurrence": "recorrência da renda",
    "indexer_diversification": "diversificação de indexadores",
    "issuance_concentration": "concentração de emissões",
    "leverage": "alavancagem",
    "ltv": "LTV",
    "mandate_adherence": "aderência ao mandato",
    "nav_discount": "desconto sobre NAV",
    "rating_quality": "qualidade de rating",
    "related_party_exposure": "exposição a partes relacionadas",
    "tenant_concentration": "concentração de locatários",
    "vacancia_financeira": "vacância financeira",
    "vacancia_fisica": "vacância física",
    "wault_anos": "WAULT",
}

TYPE_ROLES = {
    "tijolo": (
        "Exposição à renda de imóveis reais dentro da banda tática de tijolo; "
        "deve ser acompanhada por vacância, contratos e concentração de locatários."
    ),
    "papel": (
        "Exposição a recebíveis e indexadores dentro da banda tática de papel; "
        "o principal contrapeso é o risco de crédito, LTV e concentração de devedores."
    ),
    "fof": (
        "Diversificação por fundos subjacentes e possível captura de desconto sobre NAV; "
        "exige controle de dupla taxa, sobreposição e liquidez da carteira investida."
    ),
    "hibrido": (
        "Combina fontes de renda e ajuda a cumprir a diversificação entre categorias; "
        "a estrutura mais complexa exige maior cobertura look-through."
    ),
}


def _num(value: Any) -> float | None:
    try:
        number = float(value)
        return number if number == number else None
    except (TypeError, ValueError):
        return None


def _median(rows: list[dict], key: str) -> float | None:
    values = [_num(row.get(key)) for row in rows]
    clean = [value for value in values if value is not None]
    return median(clean) if clean else None


def _relative_strengths(item: dict, peers: list[dict]) -> list[str]:
    score = _num(item.get("type_score")) or 0.0
    score_median = _median(peers, "type_score")
    strengths = []
    if score_median is not None:
        strengths.append(
            f"score {score:.1f}, {score - score_median:+.1f} pontos versus a mediana do tipo"
        )

    components = item.get("components") or {}
    component_rows = sorted(
        ((COMPONENT_LABELS.get(str(key), str(key)), _num(value))
         for key, value in components.items()),
        key=lambda pair: pair[1] if pair[1] is not None else -1,
        reverse=True,
    )
    for label, value in component_rows[:2]:
        if value is not None:
            strengths.append(f"{label}: nota relativa {value:.0f}/100 entre os pares")

    for key, label in (("confidence", "confiança"), ("coverage", "cobertura")):
        value, peer_median = _num(item.get(key)), _median(peers, key)
        if value is not None and peer_median is not None and value > peer_median + .01:
            strengths.append(
                f"{label} {value:.0%}, acima da mediana de {peer_median:.0%} dos pares"
            )

    value, peer_median = _num(item.get("dy_12m")), _median(peers, "dy_12m")
    if value is not None and peer_median is not None:
        if value > 1:
            value /= 100
        if peer_median > 1:
            peer_median /= 100
        if value > peer_median + .005:
            strengths.append(
                f"DY de 12 meses {value:.1%}, acima da mediana de {peer_median:.1%} do tipo"
            )
    return strengths[:5]


def _caveats(item: dict) -> list[str]:
    caveats = []
    missing = list(item.get("missing_critical") or [])
    if missing:
        labels = [METRIC_LABELS.get(str(metric), str(metric).replace("_", " "))
                  for metric in missing[:4]]
        suffix = f" e mais {len(missing) - 4}" if len(missing) > 4 else ""
        caveats.append("métricas críticas ainda ausentes: " + ", ".join(labels) + suffix)
    if item.get("data_readiness_status") != "ready":
        reasons = list(item.get("data_readiness_reasons") or [])
        caveats.append("dados ainda insuficientes" + (": " + "; ".join(reasons) if reasons else ""))
    if item.get("publication_status") != "validated":
        caveats.append("permanece candidato de diligência; metodologia PIT ainda não aprovada")
    return caveats


def build_selection_explanations(
    selected_items: Iterable[dict], peer_rows: Iterable[dict], *, regime: str | None = None,
) -> list[dict]:
    """Explica a seleção com comparações restritas à mesma categoria de FII."""
    selected = [dict(item) for item in selected_items]
    universe = [dict(row) for row in peer_rows]
    output = []
    for item in selected:
        fii_type = str(item.get("tipo") or "").lower()
        peers = [row for row in universe if str(row.get("tipo") or "").lower() == fii_type]
        ordered = sorted(peers, key=lambda row: (
            _num(row.get("type_score")) or 0.0, _num(row.get("confidence")) or 0.0
        ), reverse=True)
        ticker = str(item.get("ticker") or "")
        rank = next((index for index, row in enumerate(ordered, 1)
                     if str(row.get("ticker") or "") == ticker), len(ordered) or 1)
        peer_count = len(ordered)
        top_percent = max(1, ceil(rank / max(peer_count, 1) * 100))
        role = TYPE_ROLES.get(fii_type, "Contribui para a diversificação da seleção.")
        if regime:
            role += f" Banda definida para o regime quantitativo “{regime}”."
        output.append({
            "ticker": ticker, "tipo": fii_type, "weight": _num(item.get("weight")) or 0.0,
            "rank": rank, "peer_count": peer_count, "top_percent": top_percent,
            "strengths": _relative_strengths(item, peers), "role": role,
            "caveats": _caveats(item),
        })
    return output
