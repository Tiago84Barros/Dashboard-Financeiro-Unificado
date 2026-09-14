"""Frescor de métrica derivada mede a idade do INSUMO, não a da linha.

``dy_recorrente`` é a métrica crítica de maior peso (.12) e o único prazo de
15 dias do bloco de renda. Ela não existe na fonte: é derivada de ``dy_12m`` e
``income_recurrence``. Sem procedência própria, ``_freshness_for_metric`` caía
em ``metrics_fetched_at``/``updated_at`` — a data em que a LINHA foi tocada —
e um fundo com DY de quatro meses atrás entrava como ``ready`` porque o
pipeline rodou ontem.
"""
from datetime import date

from core.fii_methodology import (
    COMMON_METRICS,
    _freshness_for_metric,
    score_fiis_by_type,
)


def _tijolo(ticker: str, metadata: dict) -> dict:
    return {
        "ticker": ticker, "tipo": "tijolo", "dy_12m": .09,
        "income_growth_per_share_3y": .04, "income_recurrence": .95, "pvp": .95,
        "liquidez_diaria": 3_000_000, "issuance_discipline": .9,
        "issuance_price_discipline": .9, "management_efficiency": .85,
        "fee_efficiency": .8, "conflict_alignment": .9, "mandate_adherence": .95,
        "cvm_event_quality": .9, "related_party_exposure": .02,
        "vacancia_fisica": .05, "vacancia_financeira": .04, "wault_anos": 5,
        "tenant_concentration": .12, "geographic_diversification": .8,
        "implied_cap_rate": .09, "asset_quality": .9, "contract_quality": .85,
        "lease_expiry_concentration_24m": .15, "leverage": .05,
        "history_months": 60, "data_consistency": 1,
        # A linha inteira foi tocada hoje: é exatamente o que mascarava a idade
        # do insumo.
        "metrics_fetched_at": "2026-09-11", "updated_at": "2026-09-11",
        "metric_metadata": metadata,
    }


def test_dy_recorrente_declara_a_chave_de_procedencia_sem_pontuar_o_dy_bruto():
    definicao = next(item for item in COMMON_METRICS if item.key == "dy_recorrente")
    assert "dy_12m" in definicao.provenance_keys
    # A restrição que o comentário do módulo documenta: procedência é um campo
    # separado justamente para NÃO virar fallback do valor pontuado.
    assert definicao.fallback_keys == ()


def test_dy_de_quatro_meses_rebaixa_o_frescor_mesmo_com_a_linha_tocada_hoje():
    velho = _tijolo("AAAA11", {"dy_12m": {"available_at": "2026-05-01"}})
    novo = _tijolo("BBBB11", {"dy_12m": {"available_at": "2026-09-10"}})
    scored = {row["ticker"]: row
              for row in score_fiis_by_type([velho, novo], as_of=date(2026, 9, 13),
                                            validation_status="passed")}
    assert scored["AAAA11"]["freshness_score"] < scored["BBBB11"]["freshness_score"]
    assert scored["AAAA11"]["confidence"] < scored["BBBB11"]["confidence"]
    # O valor pontuado continua sendo a renda recorrente; procedência não
    # reabre a porta do yield inflado.
    assert scored["AAAA11"]["score_input_sources"]["dy_recorrente"] == "dy_recorrente"


def test_recorrencia_velha_tambem_envelhece_a_renda_recorrente():
    """Os dois fatores são insumos; o pior deles é que manda.

    Medido direto em ``_freshness_for_metric`` de propósito: pelo agregado da
    linha, ``income_recurrence`` velha já derrubaria o frescor pela métrica
    homônima, e o teste passaria sem a derivada ter aprendido nada.
    """
    definicao = next(item for item in COMMON_METRICS if item.key == "dy_recorrente")
    velho = _tijolo("AAAA11", {"dy_12m": {"available_at": "2026-09-10"},
                               "income_recurrence": {"available_at": "2026-05-01"}})
    assert _freshness_for_metric(velho, definicao, date(2026, 9, 13)) < 1.0
