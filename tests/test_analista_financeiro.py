from datetime import date

import pytest

from core.analista_financeiro import (
    detectar_anomalias,
    montar_diagnostico,
    simular_patrimonio,
)


def _controle(source="real"):
    return {
        "data_source": source,
        "receitas": 10_000,
        "despesas": 7_500,
        "categorias": [
            {"nome": "Moradia", "gasto": 3_000},
            {"nome": "Alimentação", "gasto": 1_500},
        ],
        "transacoes": [
            {"descricao": "Streaming", "valor": -29.9, "data": date(2026, 7, 5), "eh_despesa": True},
            {"descricao": "Streaming", "valor": -29.9, "data": date(2026, 7, 5), "eh_despesa": True},
            {"descricao": "Streaming", "valor": -29.9, "data": date(2026, 6, 5), "eh_despesa": True},
        ],
    }


def _carteira(source="real"):
    return {
        "data_source": source,
        "total_mercado": 100_000,
        "posicoes": [{"ticker": "TEST3", "pct_carteira": 45}],
        "por_setor": [{"nome": "Financeiro", "pct_carteira": 45}],
    }


def test_diagnostico_calcula_metricas_e_separa_recomendacoes():
    diagnostico = montar_diagnostico(
        [_controle()], _carteira(),
        {"data_source": "real", "metas": [], "metas_atras": 0},
        {"data_source": "real", "total_12m": 2_400},
    )

    assert diagnostico["dados_reais"] is True
    assert diagnostico["metricas"]["resultado"] == 2_500
    assert diagnostico["metricas"]["taxa_poupanca_pct"] == 25
    assert diagnostico["metricas"]["maior_posicao_pct"] == 45
    assert all(item["tipo"] == "recomendacao" for item in diagnostico["recomendacoes"])
    assert any(item["titulo"] == "Concentração por ativo" for item in diagnostico["recomendacoes"])


def test_mock_fallback_nunca_e_marcado_como_real():
    diagnostico = montar_diagnostico(
        [_controle("mock_fallback")], _carteira("mock_fallback"),
        {"data_source": "mock_fallback", "metas": []},
        {"data_source": "mock_fallback"},
    )
    assert diagnostico["dados_reais"] is False
    assert diagnostico["fontes"]["controle"] == ["mock_fallback"]


def test_anomalias_sao_candidatos_para_revisao():
    achados = detectar_anomalias(_controle()["transacoes"])
    assert {item["tipo"] for item in achados} >= {"possivel_duplicidade", "recorrencia"}
    assert all(item["requer_revisao"] for item in achados)


def test_simulador_sem_retorno_equivale_a_aportes():
    serie = simular_patrimonio(10_000, 1_000, 1, 0)
    assert serie[-1]["patrimonio"] == pytest.approx(22_000)
    assert serie[-1]["aportado"] == pytest.approx(22_000)


def test_simulador_rejeita_valores_negativos_sem_quebrar():
    serie = simular_patrimonio(-100, -50, 1, 7)
    assert serie[-1]["patrimonio"] == 0
