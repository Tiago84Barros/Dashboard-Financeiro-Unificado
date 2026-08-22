"""Explicações auditáveis para candidatos da carteira de diligência de FIIs.

As mensagens usam somente os scores e indicadores já calculados point-in-time.
Não criam fatos, não consultam rede e não convertem a triagem em recomendação.
"""
from __future__ import annotations

from math import ceil
from statistics import median
from typing import Any, Iterable

import pandas as pd

COMPONENT_LABELS = {
    "income": "renda", "valuation": "valuation", "liquidity": "liquidez",
    "quality": "qualidade dos ativos/carteira", "risk": "controle de risco",
    "governance": "governança e gestão", "stability": "estabilidade histórica",
}

METRIC_LABELS = {
    "conflict_alignment": "alinhamento de conflitos",
    "contract_quality": "qualidade dos contratos",
    "credit_spread_adequacy": "adequação do spread de crédito",
    "credit_spread": "spread de crédito observado",
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
    "subordination_protection": "proteção por subordinação",
    "related_party_exposure": "exposição a partes relacionadas",
    "tenant_concentration": "concentração de locatários",
    "vacancia_operacional": "vacância operacional (financeira ou física)",
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


def _percent(value: Any) -> float | None:
    number = _num(value)
    if number is None:
        return None
    return number / 100 if abs(number) > 1 else number


def _money(value: Any) -> str:
    number = _num(value)
    if number is None:
        return "—"
    if abs(number) >= 1_000_000_000:
        return f"R$ {number / 1_000_000_000:.2f} bi"
    if abs(number) >= 1_000_000:
        return f"R$ {number / 1_000_000:.1f} mi"
    return f"R$ {number:,.0f}".replace(",", ".")


def _top_exposures(value: Any, *, limit: int = 3) -> str | None:
    if not isinstance(value, dict) or not value:
        return None
    clean = [(str(name), _percent(weight)) for name, weight in value.items()]
    clean = [(name, weight) for name, weight in clean if weight is not None]
    if not clean:
        return None
    clean.sort(key=lambda pair: pair[1], reverse=True)
    return ", ".join(f"{name} {weight:.1%}" for name, weight in clean[:limit])


def _market_relationships(ticker: str, prices: pd.DataFrame | None) -> dict:
    output = {"months": 0, "correlation_ifix": None, "correlation_ibov": None,
              "beta_ifix": None}
    if prices is None or prices.empty or ticker not in prices:
        return output
    returns = prices.pct_change(fill_method=None)
    for benchmark, key in (("XFIX11", "correlation_ifix"), ("BOVA11", "correlation_ibov")):
        if benchmark not in returns:
            continue
        common = returns[[ticker, benchmark]].dropna().tail(36)
        if len(common) < 12:
            continue
        output[key] = _num(common[ticker].corr(common[benchmark]))
        output["months"] = max(output["months"], len(common))
        if benchmark == "XFIX11":
            variance = float(common[benchmark].var(ddof=1))
            if variance > 0:
                output["beta_ifix"] = _num(common[ticker].cov(common[benchmark]) / variance)
    return output


def _metric_line(label: str, value: Any, *, percent: bool = True) -> str | None:
    number = _percent(value) if percent else _num(value)
    if number is None:
        return None
    return f"{label}: {number:.1%}" if percent else f"{label}: {number:.1f}"


def _specific_structure(row: dict) -> list[str]:
    fii_type = str(row.get("tipo") or "").lower()
    lines: list[str | None] = []
    if fii_type in ("tijolo", "hibrido"):
        properties = _num(row.get("property_count"))
        regions = _num(row.get("region_count"))
        lines.extend([
            (f"{int(properties)} imóveis identificados" if properties is not None else None),
            (f"presença em {int(regions)} regiões" if regions is not None and regions > 0 else None),
            _metric_line("vacância física", row.get("vacancia_fisica")),
            _metric_line("concentração do maior locatário", row.get("tenant_concentration")),
            _metric_line("vencimentos em 24 meses", row.get("lease_expiry_concentration_24m")),
            _metric_line("alavancagem", row.get("leverage")),
            _metric_line("WAULT", row.get("wault_anos"), percent=False),
            ("principais regiões: " + _top_exposures(row.get("regions"))
             if _top_exposures(row.get("regions")) else None),
        ])
    elif fii_type == "papel":
        lines.extend([
            _metric_line("duration", row.get("duration_anos"), percent=False),
            _metric_line("LTV", row.get("ltv")),
            _metric_line("spread de crédito", row.get("credit_spread")),
            _metric_line("qualidade de rating", row.get("rating_quality")),
            _metric_line("proteção por subordinação", row.get("subordination_protection")),
            _metric_line("inadimplência", row.get("delinquency")),
            _metric_line("concentração do maior devedor", row.get("debtor_concentration")),
            ("indexadores: " + _top_exposures(row.get("indexers"))
             if _top_exposures(row.get("indexers")) else None),
            ("principais devedores: " + _top_exposures(row.get("debtors"))
             if _top_exposures(row.get("debtors")) else None),
        ])
    elif fii_type == "fof":
        lines.extend([
            _metric_line("desconto sobre NAV", row.get("nav_discount")),
            _metric_line("custo de dupla taxa", row.get("double_fee_burden")),
            _metric_line("sobreposição da carteira", row.get("holdings_overlap")),
            _metric_line("liquidez da carteira investida", row.get("invested_portfolio_liquidity")),
            ("maiores posições: " + _top_exposures(row.get("holdings"))
             if _top_exposures(row.get("holdings")) else None),
        ])
    return [line for line in lines if line]


def build_selection_reports(
    selected_items: Iterable[dict], peer_rows: Iterable[dict], *,
    scenario: Any = None, prices: pd.DataFrame | None = None,
) -> list[dict]:
    """Produz relatórios específicos por FII sem inventar métricas ausentes.

    Cada afirmação deriva do snapshot corrente, da comparação com pares do mesmo tipo
    ou da série histórica armazenada. Ausência de dado permanece explícita.
    """
    selected = [dict(item) for item in selected_items]
    universe = [dict(row) for row in peer_rows]
    source_by_ticker = {str(row.get("ticker") or ""): row for row in universe}
    explanations = build_selection_explanations(selected, universe)
    explanation_by_ticker = {row["ticker"]: row for row in explanations}
    reports = []
    for selected_item in selected:
        ticker = str(selected_item.get("ticker") or "")
        row = {**source_by_ticker.get(ticker, {}), **selected_item}
        fii_type = str(row.get("tipo") or "").lower()
        peers = [peer for peer in universe if str(peer.get("tipo") or "").lower() == fii_type]
        dy, peer_dy = _percent(row.get("dy_12m")), _percent(_median(peers, "dy_12m"))
        pvp = _num(row.get("pvp"))
        liquidity = _num(row.get("liquidez_diaria"))
        relationship = _market_relationships(ticker, prices)
        valuation = "P/VP indisponível"
        if pvp is not None:
            valuation = (f"P/VP {pvp:.2f} · desconto patrimonial de {1-pvp:.1%}"
                         if pvp <= 1 else f"P/VP {pvp:.2f} · prêmio patrimonial de {pvp-1:.1%}")
        income = f"DY 12m {dy:.1%}" if dy is not None else "DY 12m indisponível"
        if dy is not None and peer_dy is not None:
            income += f" · {dy-peer_dy:+.1%} vs. mediana do tipo ({peer_dy:.1%})"
        facts = [valuation, income,
                 f"liquidez diária {_money(liquidity)}",
                 f"patrimônio líquido {_money(row.get('patrimonio_liquido'))}"]
        holders = _num(row.get("num_cotistas"))
        if holders is not None:
            facts.append(f"{holders:,.0f} cotistas".replace(",", "."))
        recurrence = _percent(row.get("income_recurrence"))
        growth = _percent(row.get("income_growth_per_share_3y"))
        operating = []
        if recurrence is not None:
            operating.append(f"recorrência da renda {recurrence:.1%}")
        if growth is not None:
            operating.append(f"crescimento da renda por cota {growth:+.1%}")
        market = []
        if relationship["correlation_ifix"] is not None:
            market.append(f"correlação com IFIX {relationship['correlation_ifix']:.2f}")
        if relationship["beta_ifix"] is not None:
            market.append(f"beta vs. IFIX {relationship['beta_ifix']:.2f}")
        if relationship["correlation_ibov"] is not None:
            market.append(f"correlação com Ibovespa {relationship['correlation_ibov']:.2f}")
        if scenario is not None:
            real_rate = _num(getattr(scenario, "real_rate", None))
            selic_change = _num(getattr(scenario, "selic_change_12m", None))
            if real_rate is not None and real_rate >= 5:
                market.append("juros reais elevados aumentam o custo de oportunidade e exigem margem de segurança")
            if selic_change is not None and selic_change <= -2 and fii_type in ("tijolo", "fof"):
                market.append("queda de juros tende a aliviar a taxa de desconto, sem garantir valorização")
            if fii_type == "papel":
                market.append("renda depende dos indexadores e da qualidade de crédito dos recebíveis")
        base = explanation_by_ticker.get(ticker, {})
        metadata = row.get("metric_metadata") or {}
        refs = sorted({str(meta.get("reference_date"))[:10] for meta in metadata.values()
                       if isinstance(meta, dict) and meta.get("reference_date") and
                       str(meta.get("reference_date")) not in ("None", "NaT")}, reverse=True)
        reports.append({
            **base,
            "facts": facts,
            "operating": operating,
            "structure": _specific_structure(row),
            "market": market,
            "relationship_months": relationship["months"],
            "data_reference": refs[0] if refs else str(row.get("updated_at") or "")[:10],
            "confidence": _num(row.get("confidence")),
            "coverage": _num(row.get("coverage")),
        })
    return reports
