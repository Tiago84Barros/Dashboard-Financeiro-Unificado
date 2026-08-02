"""Metodologia profissional de FIIs, específica para o mercado brasileiro.

O módulo é puro: não acessa banco nem rede. Ele separa três conceitos que não
podem ser confundidos:

* ``score``: ordenação relativa com os dados observados;
* ``confidence``: cobertura, frescor, consistência e qualidade das fontes;
* ``publication_status``: autorização para apresentar o resultado como método
  validado. Sem backtest point-in-time aprovado, a saída é sempre uma Lista de
  Diligência.

Valores ausentes nunca são convertidos em zero ou em valor neutro. Eles reduzem
a cobertura e podem bloquear a publicação quando a métrica é crítica.
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from typing import Any

METHODOLOGY_VERSION = "6.6.0"
FORMULA_VERSION = "br-fii-integrated-income-resilience-6.6.0"
VALID_TYPES = ("tijolo", "papel", "fof", "hibrido")


@dataclass(frozen=True)
class MetricDefinition:
    key: str
    component: str
    weight: float
    direction: str = "higher"  # higher | lower | target
    target: float | None = None
    critical: bool = False
    max_age_days: int = 120
    fallback_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class PublicationGate:
    can_publish_recommendation: bool
    status: str
    universe_coverage: float
    validated_fraction: float
    median_confidence: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class MacroScenario:
    selic: float
    ipca: float
    cdi: float | None = None
    selic_change_12m: float = 0.0
    vacancy_shock: float = 0.0
    credit_event_rate: float = 0.0

    @property
    def real_rate(self) -> float:
        return float(self.selic) - float(self.ipca)


COMMON_METRICS = (
    MetricDefinition("dy_12m", "income", .12, "higher", critical=True, max_age_days=15),
    MetricDefinition("income_growth_per_share_3y", "income", .10, "higher", critical=True),
    MetricDefinition("income_recurrence", "income", .08, "higher", critical=True),
    MetricDefinition("pvp", "valuation", .10, "target", critical=True, max_age_days=45),
    MetricDefinition("liquidez_diaria", "liquidity", .10, "higher", critical=True, max_age_days=15),
    # Sinais históricos antes usados em uma carteira concorrente agora entram
    # uma única vez como estabilidade/risco. A validação PIT continua obrigatória.
    MetricDefinition("total_return_trend", "stability", .04, "higher", critical=True),
    MetricDefinition("max_drawdown", "stability", .04, "higher", critical=True),
    MetricDefinition("issuance_discipline", "governance", .007, "higher"),
    MetricDefinition("issuance_price_discipline", "governance", .007, "higher"),
    MetricDefinition("management_efficiency", "governance", .007, "higher"),
    MetricDefinition("fee_efficiency", "governance", .007, "higher"),
    MetricDefinition("conflict_alignment", "governance", .007, "higher", critical=True),
    MetricDefinition("mandate_adherence", "governance", .007, "higher", critical=True),
    MetricDefinition("cvm_event_quality", "governance", .005, "higher"),
    MetricDefinition("related_party_exposure", "governance", .003, "lower"),
    MetricDefinition("governance_disclosure_quality", "governance", .006, "higher"),
    MetricDefinition("governance_integrity", "governance", .006, "higher"),
    MetricDefinition("auditor_opinion_quality", "governance", .008, "higher"),
)


TYPE_METRICS: dict[str, tuple[MetricDefinition, ...]] = {
    "tijolo": (
        # Vacância financeira é preferida por refletir receita; a física é uma
        # aproximação estrutural válida quando a gestão não publica a financeira.
        # As duas medem o mesmo risco e não devem gerar dupla penalização.
        MetricDefinition("vacancia_operacional", "risk", .10, "lower", critical=True,
                         fallback_keys=("vacancia_financeira", "vacancia_fisica")),
        MetricDefinition("wault_anos", "quality", .05, "higher", critical=True),
        MetricDefinition("tenant_concentration", "risk", .04, "lower", critical=True),
        MetricDefinition("geographic_diversification", "quality", .035, "higher"),
        MetricDefinition("implied_cap_rate", "quality", .04, "higher", critical=True),
        MetricDefinition("asset_quality", "quality", .04, "higher"),
        MetricDefinition("contract_quality", "quality", .03, "higher"),
        MetricDefinition("lease_expiry_concentration_24m", "risk", .035, "lower", critical=True),
        MetricDefinition("leverage", "risk", .08, "lower", critical=True),
    ),
    "papel": (
        MetricDefinition("duration_anos", "risk", .04, "target", target=3.0, critical=True),
        MetricDefinition("indexer_diversification", "quality", .04, "higher", critical=True),
        MetricDefinition("credit_spread_adequacy", "quality", .04, "higher"),
        MetricDefinition("ltv", "risk", .06, "lower", critical=True),
        MetricDefinition("rating_quality", "quality", .06, "higher", critical=True),
        MetricDefinition("subordination_protection", "risk", .05, "higher"),
        MetricDefinition("delinquency", "risk", .06, "lower", critical=True),
        MetricDefinition("debtor_diversification", "quality", .05, "higher", critical=True),
        MetricDefinition("issuance_concentration", "risk", .05, "lower", critical=True),
        MetricDefinition("issuer_diversification", "quality", .02, "higher"),
    ),
    "fof": (
        MetricDefinition("nav_discount", "valuation", .07, "higher", critical=True),
        MetricDefinition("double_fee_burden", "risk", .07, "lower", critical=True),
        MetricDefinition("holdings_overlap", "risk", .08, "lower", critical=True),
        MetricDefinition("invested_portfolio_liquidity", "liquidity", .06, "higher", critical=True),
        MetricDefinition("holdings_quality", "quality", .09, "higher", critical=True),
        MetricDefinition("underlying_manager_concentration", "risk", .04, "lower"),
        MetricDefinition("portfolio_income_recurrence", "income", .04, "higher"),
    ),
    "hibrido": (
        MetricDefinition("vacancia_operacional", "risk", .05, "lower", critical=True,
                         fallback_keys=("vacancia_financeira", "vacancia_fisica")),
        MetricDefinition("wault_anos", "quality", .04, "higher"),
        MetricDefinition("tenant_concentration", "risk", .04, "lower"),
        MetricDefinition("geographic_diversification", "quality", .03, "higher"),
        MetricDefinition("ltv", "risk", .05, "lower"),
        MetricDefinition("rating_quality", "quality", .04, "higher"),
        MetricDefinition("delinquency", "risk", .04, "lower"),
        MetricDefinition("debtor_diversification", "quality", .04, "higher"),
        MetricDefinition("issuer_diversification", "quality", .015, "higher"),
        MetricDefinition("holdings_overlap", "risk", .04, "lower"),
        MetricDefinition("holdings_quality", "quality", .04, "higher"),
        MetricDefinition("leverage", "risk", .04, "lower", critical=True),
    ),
}


PVP_TARGETS = {"tijolo": .95, "papel": .98, "fof": .90, "hibrido": .95}
_COMPACT_METADATA_FIELDS = (
    "available_at", "source_quality", "reference_date", "source",
)


SOURCE_PLANS: dict[str, dict[str, tuple[str, ...]]] = {
    "tijolo": {
        "required": ("brapi_quote_monthly", "brapi_fii_indicators", "brapi_fii_reports",
                     "brapi_fii_properties", "cvm_informe_mensal", "cvm_informe_trimestral"),
        "optional": ("cvm_eventuais", "cvm_informe_anual", "public_fii_documents"),
    },
    "papel": {
        "required": ("brapi_quote_monthly", "brapi_fii_indicators", "brapi_fii_reports",
                     "brapi_fii_portfolio", "cvm_informe_mensal", "cvm_informe_trimestral"),
        "optional": ("cvm_eventuais", "cvm_informe_anual", "cvm_cri_monthly",
                     "public_fii_documents"),
    },
    "fof": {
        "required": ("brapi_quote_monthly", "brapi_fii_indicators", "brapi_fii_reports",
                     "brapi_fii_portfolio", "cvm_informe_mensal", "cvm_informe_trimestral"),
        "optional": ("cvm_eventuais", "cvm_informe_anual", "public_fii_documents"),
    },
    "hibrido": {
        "required": ("brapi_quote_monthly", "brapi_fii_indicators", "brapi_fii_reports",
                     "brapi_fii_properties", "brapi_fii_portfolio",
                     "cvm_informe_mensal", "cvm_informe_trimestral"),
        "optional": ("cvm_eventuais", "cvm_informe_anual", "cvm_cri_monthly",
                     "public_fii_documents"),
    },
}


def methodology_manifest() -> dict[str, Any]:
    """Manifesto serializável persistido junto a cada snapshot de score."""
    return {
        "methodology_version": METHODOLOGY_VERSION,
        "formula_version": FORMULA_VERSION,
        "objective": "renda recorrente com crescimento patrimonial e resiliência",
        "common_metrics": [asdict(m) for m in COMMON_METRICS],
        "type_metrics": {key: [asdict(m) for m in value] for key, value in TYPE_METRICS.items()},
        "pvp_targets": PVP_TARGETS,
        "integrated_pipeline": {
            "eligibility_version": "6.6.0",
            "portfolio_strategy_id": "fii_integrated_robust_optimizer.v6.6",
            "stages": ("eligibility", "type_score", "empirical_confidence",
                       "pit_walk_forward", "robust_scenario_optimization"),
            "correlation_min_months": 12,
            "legacy_scores_combined": False,
        },
        "confidence_formula": {
            "type": "weighted_geometric_mean",
            "weights": {"coverage": .40, "freshness": .12, "source_quality": .12,
                        "consistency": .12, "history": .09, "parser_calibration": .15},
        },
        "missing_data_policy": "missing_reduces_coverage_and_confidence; never_zero_or_neutral",
        "publication_policy": "diligence_only_until_point_in_time_validation_passes",
    }


def source_plan_for_type(fii_type: str) -> dict[str, tuple[str, ...]]:
    return SOURCE_PLANS.get(str(fii_type or "").lower(), {"required": (), "optional": ()})


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        return None


def _rank_scores(values: list[float | None], *, higher: bool) -> dict[int, float]:
    indexes = [index for index, value in enumerate(values) if value is not None]
    if not indexes:
        return {}
    ordered = sorted(indexes, key=lambda index: float(values[index]), reverse=not higher)
    result: dict[int, float] = {}
    size = len(ordered)
    start = 0
    while start < size:
        end = start + 1
        value = values[ordered[start]]
        while end < size and values[ordered[end]] == value:
            end += 1
        rank = (start + end - 1) / 2
        percentile = .5 if size == 1 else rank / (size - 1)
        for position in range(start, end):
            result[ordered[position]] = percentile
        start = end
    return result


def _metric_key_and_value(row: dict, definition: MetricDefinition) -> tuple[str, float | None]:
    """Resolve uma métrica virtual sem fabricar nem combinar observações.

    A ordem dos ``fallback_keys`` é parte da fórmula versionada. O primeiro
    valor numérico finito é usado e sua chave original preserva a linhagem.
    """
    keys = definition.fallback_keys or (definition.key,)
    for key in keys:
        value = _number(row.get(key))
        if value is not None:
            return key, value
    return definition.key, None


def _metric_values(rows: list[dict], definition: MetricDefinition, fii_type: str) -> list[float | None]:
    values = [_metric_key_and_value(row, definition)[1] for row in rows]
    if definition.direction != "target":
        return values
    target = definition.target
    if definition.key == "pvp":
        target = PVP_TARGETS.get(fii_type, .95)
    if not target or target <= 0:
        return [None for _ in values]
    return [abs(math.log(value / target)) if value and value > 0 else None for value in values]


def _metric_metadata_for(row: dict, metric_key: str) -> dict[str, Any]:
    """Normaliza proveniência nominal ou vetorial na fronteira de cálculo.

    O snapshot público v2 compacta cada proveniência na ordem definida em
    ``_COMPACT_METADATA_FIELDS``. O leitor normalmente restaura os nomes, mas a
    metodologia também aceita o vetor para permanecer segura diante de caches,
    artefatos ou consumidores que entreguem o payload bruto.
    """
    metadata = row.get("metric_metadata")
    if not isinstance(metadata, dict):
        return {}
    details = metadata.get(metric_key)
    if isinstance(details, dict):
        return details
    if isinstance(details, (list, tuple)):
        return {
            key: value
            for key, value in zip(_COMPACT_METADATA_FIELDS, details)
            if value is not None
        }
    return {}


def _freshness_for_metric(row: dict, definition: MetricDefinition, today: date,
                          *, source_key: str | None = None) -> float:
    metadata = _metric_metadata_for(row, source_key or definition.key)
    available = _date(metadata.get("available_at") or row.get("metrics_fetched_at") or row.get("updated_at"))
    if available is None:
        return .50
    age = max((today - available).days, 0)
    if age <= definition.max_age_days:
        return 1.0
    return max(0.0, 1.0 - (age - definition.max_age_days) / max(definition.max_age_days * 2, 1))


def _source_quality_for_metric(row: dict, definition: MetricDefinition,
                               *, source_key: str | None = None) -> float:
    metadata = _metric_metadata_for(row, source_key or definition.key)
    quality = _number(metadata.get("source_quality"))
    if quality is not None:
        return min(max(quality, 0.0), 1.0)
    source = str(metadata.get("source") or "").lower()
    if "cvm" in source or "b3" in source or "bcb" in source:
        return .95
    if "brapi" in source:
        return .80
    return .60


def score_fiis_by_type(
    rows: Iterable[dict],
    *,
    as_of: date | None = None,
    validation_status: str = "unvalidated",
    min_confidence: float = .75,
) -> list[dict]:
    """Pontua FIIs dentro de cada categoria e devolve uma Lista de Diligência.

    A pontuação relativa é calculada somente entre fundos do mesmo tipo. Métricas
    faltantes são excluídas do numerador e reduzem cobertura/confiança.
    """
    today = as_of or datetime.now(timezone.utc).date()
    clean = [dict(row) for row in rows if str(row.get("tipo") or "").lower() in VALID_TYPES]
    output: list[dict] = []

    for fii_type in VALID_TYPES:
        group = [row for row in clean if str(row.get("tipo") or "").lower() == fii_type]
        if not group:
            continue
        definitions = list(COMMON_METRICS) + list(TYPE_METRICS[fii_type])
        metric_scores: dict[str, dict[int, float]] = {}
        for definition in definitions:
            transformed = _metric_values(group, definition, fii_type)
            higher = definition.direction == "higher"
            metric_scores[definition.key] = _rank_scores(transformed, higher=higher)

        total_weight = sum(definition.weight for definition in definitions)
        total_critical_weight = sum(definition.weight for definition in definitions
                                    if definition.critical)
        for index, row in enumerate(group):
            observed_weight = 0.0
            observed_critical_weight = 0.0
            weighted_score = 0.0
            freshness_weighted = 0.0
            source_weighted = 0.0
            missing: list[str] = []
            missing_critical: list[str] = []
            components_num: dict[str, float] = {}
            components_den: dict[str, float] = {}
            used_inputs: dict[str, Any] = {}
            input_sources: dict[str, str] = {}

            for definition in definitions:
                source_key, value = _metric_key_and_value(row, definition)
                score = metric_scores[definition.key].get(index)
                if value is None or score is None:
                    missing.append(definition.key)
                    if definition.critical:
                        missing_critical.append(definition.key)
                    continue
                observed_weight += definition.weight
                if definition.critical:
                    observed_critical_weight += definition.weight
                weighted_score += definition.weight * score
                components_num[definition.component] = components_num.get(definition.component, 0.0) + definition.weight * score
                components_den[definition.component] = components_den.get(definition.component, 0.0) + definition.weight
                freshness_weighted += definition.weight * _freshness_for_metric(
                    row, definition, today, source_key=source_key)
                source_weighted += definition.weight * _source_quality_for_metric(
                    row, definition, source_key=source_key)
                used_inputs[definition.key] = value
                input_sources[definition.key] = source_key

            coverage = observed_weight / total_weight if total_weight else 0.0
            critical_coverage = (observed_critical_weight / total_critical_weight
                                 if total_critical_weight else 0.0)
            raw_score = 100.0 * weighted_score / observed_weight if observed_weight else 0.0
            # A penalização evita que poucos indicadores produzam um falso 90/100.
            final_score = raw_score * (.55 + .45 * coverage)
            freshness = freshness_weighted / observed_weight if observed_weight else 0.0
            source_quality = source_weighted / observed_weight if observed_weight else 0.0
            raw_consistency = _number(row.get("data_consistency"))
            consistency = min(max(.60 if raw_consistency is None else raw_consistency, 0.0), 1.0)
            history_months = max(_number(row.get("history_months") or row.get("Hist_Meses")) or 0.0, 0.0)
            history_factor = min(history_months / 36.0, 1.0) if history_months else .50
            raw_calibration = _number(row.get("parser_calibration"))
            calibration = min(max(.60 if raw_calibration is None else raw_calibration, .01), 1.0)
            dimensions = ((coverage, .40), (freshness, .12), (source_quality, .12),
                          (consistency, .12), (history_factor, .09), (calibration, .15))
            confidence = math.exp(sum(weight * math.log(max(value, .01))
                                      for value, weight in dimensions))

            data_reasons: list[str] = []
            if critical_coverage < .70:
                data_reasons.append(
                    f"cobertura de métricas críticas {critical_coverage:.0%} abaixo de 70%")
            if confidence < min_confidence:
                data_reasons.append(f"confiança {confidence:.0%} abaixo do mínimo {min_confidence:.0%}")
            data_readiness_status = "ready" if not data_reasons else "insufficient"
            reasons = list(data_reasons)
            if validation_status != "passed":
                reasons.append("metodologia sem validação point-in-time aprovada")
            publication_status = "validated" if not reasons else "diligence_only"
            components = {
                component: round(100.0 * components_num[component] / components_den[component], 2)
                for component in components_num if components_den.get(component)
            }
            output.append({
                **row,
                "methodology_version": METHODOLOGY_VERSION,
                "formula_version": FORMULA_VERSION,
                "type_score": round(final_score, 2),
                "raw_score": round(raw_score, 2),
                "confidence": round(confidence, 4),
                "coverage": round(coverage, 4),
                "critical_coverage": round(critical_coverage, 4),
                "freshness_score": round(freshness, 4),
                "source_quality": round(source_quality, 4),
                "parser_calibration": round(calibration, 4),
                "confidence_assumptions": tuple(
                    name for name, value in (
                        ("data_consistency_missing", raw_consistency),
                        ("parser_calibration_missing", raw_calibration),
                    ) if value is None
                ),
                "components": components,
                "missing_metrics": tuple(sorted(missing)),
                "missing_critical": tuple(sorted(missing_critical)),
                "data_readiness_status": data_readiness_status,
                "data_readiness_reasons": tuple(data_reasons),
                "publication_status": publication_status,
                "publication_reasons": tuple(reasons),
                "score_inputs": used_inputs,
                "score_input_sources": input_sources,
                "as_of_date": today.isoformat(),
            })
    output.sort(key=lambda row: (float(row.get("type_score") or 0), float(row.get("confidence") or 0)), reverse=True)
    return output


def evaluate_publication_gate(
    scored_rows: Iterable[dict],
    *,
    expected_universe: int,
    validation_status: str,
    min_universe_coverage: float = .85,
    min_validated_fraction: float = .80,
    min_median_confidence: float = .75,
    snapshot_as_of: date | None = None,
    max_snapshot_age_days: int = 4,
    as_of: date | None = None,
) -> PublicationGate:
    rows = list(scored_rows)
    coverage = len(rows) / expected_universe if expected_universe > 0 else 0.0
    validated = sum(row.get("data_readiness_status") == "ready" for row in rows)
    validated_fraction = validated / len(rows) if rows else 0.0
    confidences = sorted(float(row.get("confidence") or 0.0) for row in rows)
    median = confidences[len(confidences) // 2] if confidences else 0.0
    reasons: list[str] = []
    if coverage < min_universe_coverage:
        reasons.append(f"cobertura do universo {coverage:.0%} abaixo de {min_universe_coverage:.0%}")
    if validated_fraction < min_validated_fraction:
        reasons.append(f"fundos com dados suficientes {validated_fraction:.0%} abaixo de {min_validated_fraction:.0%}")
    if median < min_median_confidence:
        reasons.append(f"confiança mediana {median:.0%} abaixo de {min_median_confidence:.0%}")
    if validation_status != "passed":
        reasons.append("backtest point-in-time/robustez estatística ainda não aprovado")
    if snapshot_as_of is not None:
        age_days = max(((as_of or datetime.now(timezone.utc).date()) - snapshot_as_of).days, 0)
        if age_days > max_snapshot_age_days:
            reasons.append(
                f"snapshot com {age_days} dias; máximo permitido {max_snapshot_age_days}"
            )
    return PublicationGate(
        can_publish_recommendation=not reasons,
        status="publishable" if not reasons else "diligence_only",
        universe_coverage=round(coverage, 4),
        validated_fraction=round(validated_fraction, 4),
        median_confidence=round(median, 4),
        reasons=tuple(reasons),
    )


def classify_macro_regime(scenario: MacroScenario) -> str:
    # 8% de vacância e 3% de eventos são os choques-base usados na análise de
    # sensibilidade. Eles não devem, por si sós, transformar toda execução no
    # regime tático de estresse; somente uma hipótese mais severa o faz.
    if scenario.credit_event_rate > .03 or scenario.vacancy_shock > .08:
        return "stress"
    if scenario.ipca >= 6.0:
        return "inflation_stress"
    if scenario.real_rate >= 5.0 and scenario.selic_change_12m >= 0:
        return "high_real_rate"
    if scenario.selic_change_12m <= -2.0:
        return "easing"
    return "neutral"


def tactical_type_bands(scenario: MacroScenario) -> dict[str, tuple[float, float]]:
    """Bandas, não alvos fixos, para renda resiliente em diferentes regimes."""
    regime = classify_macro_regime(scenario)
    bands = {
        "tijolo": [.30, .55],
        "papel": [.20, .45],
        "fof": [.05, .20],
        "hibrido": [.05, .20],
    }
    if regime == "high_real_rate":
        bands["tijolo"] = [.25, .45]
        bands["papel"] = [.30, .50]
        bands["fof"] = [.05, .20]
    elif regime == "easing":
        bands["tijolo"] = [.40, .60]
        bands["papel"] = [.15, .35]
        bands["fof"] = [.10, .25]
    elif regime == "inflation_stress":
        bands["tijolo"] = [.30, .50]
        bands["papel"] = [.25, .45]
        bands["fof"] = [.05, .20]
    elif regime == "stress":
        bands["tijolo"] = [.25, .45]
        bands["papel"] = [.20, .40]
        bands["fof"] = [.05, .15]
        bands["hibrido"] = [.05, .15]
    return {key: (float(value[0]), float(value[1])) for key, value in bands.items()}


def type_scenario_return(fii_type: str, scenario_name: str) -> float:
    """Choques conservadores usados como risco estrutural, não como previsão."""
    shocks = {
        "base": {"tijolo": .06, "papel": .08, "fof": .07, "hibrido": .07},
        "selic_alta": {"tijolo": -.10, "papel": .03, "fof": -.08, "hibrido": -.05},
        "queda_selic": {"tijolo": .14, "papel": .02, "fof": .12, "hibrido": .10},
        "inflacao_alta": {"tijolo": .02, "papel": .06, "fof": .01, "hibrido": .03},
        "vacancia": {"tijolo": -.16, "papel": -.04, "fof": -.10, "hibrido": -.12},
        "credito": {"tijolo": -.07, "papel": -.20, "fof": -.12, "hibrido": -.14},
    }
    return shocks.get(scenario_name, shocks["base"]).get(str(fii_type), 0.0)
