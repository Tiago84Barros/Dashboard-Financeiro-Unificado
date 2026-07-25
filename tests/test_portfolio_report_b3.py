"""Contrato institucional exclusivo da Avaliação de Portfólio B3."""
from __future__ import annotations

import json

import pandas as pd

import core.portfolio_report_b3 as report


def _score_payload(note: float = 8.0) -> dict:
    return {
        key: {
            "nota": note,
            "justificativa": f"Justificativa de {key}",
            "evidencia_ou_lacuna": "Evidência disponível",
        }
        for key in report.QUALITATIVE_WEIGHTS
    }


def _raw_report() -> dict:
    scores = _score_payload()
    scores["valuation"]["nota"] = 4
    return {
        "perspectiva": "forte",
        "confianca": 81,
        "resumo": "Vale comprar porque a margem melhorou.",
        "relatorio": {
            "analise_pares": "A companhia negocia com desconto frente aos pares setoriais.",
            "qualidade_resultados": "FCO converte o lucro; FCF exige acompanhamento.",
        },
        "riscos": [{"risco": "Execução", "mecanismo": "Pressiona margem", "indicador_monitorado": "Margem"}],
        "catalisadores": [{"catalisador": "Eficiência", "mecanismo": "Eleva margem", "janela_ou_gatilho": "Próximo resultado"}],
        "sensibilidade_macro": ["Selic -> custo financeiro"],
        "cenarios": [
            {"cenario": "Otimista", "probabilidade_pct": 10, "impacto_esperado": "Expansão", "fundamentacao": "Margens"},
            {"cenario": "Base", "probabilidade_pct": 20, "impacto_esperado": "Estável", "fundamentacao": "Execução"},
            {"cenario": "Pessimista", "probabilidade_pct": 10, "impacto_esperado": "Pressão", "fundamentacao": "Custos"},
        ],
        "score_qualitativo_detalhado": scores,
        "adequacao_investidor": {
            "perfil": "Longo prazo",
            "horizonte": "5 anos",
            "tolerancia_volatilidade": "Alta",
            "condicoes": "Acompanhar execução",
        },
        "conclusao": {
            "faixa_valor": "barata",
            "desconto_justificavel": "Parcialmente",
            "percepcao_mercado": "pessimista",
            "risco_retorno": "Assimetria condicionada à execução",
            "principal_positivo": "Caixa",
            "principal_risco": "Margem",
            "resumo_executivo": "Desconto requer execução operacional.",
        },
    }


def test_prioriza_pares_setoriais_que_ja_estao_na_carteira():
    in_portfolio, universe = report.prioritize_peer_tickers(
        ["CRFB3", "ASAI3", "PCAR3", "GMAT3", "ASAI3"],
        ["GMAT3", "ASAI3"],
        "GMAT3",
        max_peers=3,
    )
    assert in_portfolio == ["ASAI3"]
    assert universe == ["CRFB3", "PCAR3"]


def test_historico_expoe_fco_fcf_conversao_e_tendencias():
    frame = pd.DataFrame({
        "Data": pd.to_datetime(["2023-12-31", "2024-12-31"]),
        "Receita_Liquida": [100.0, 120.0],
        "EBITDA": [20.0, 30.0],
        "Lucro_Liquido": [10.0, 12.0],
        "Divida_Liquida": [40.0, 30.0],
        "FCO": [15.0, 18.0],
        "FCF": [8.0, 10.0],
    })
    context = report.build_financial_history_context(frame)
    assert "Margem_EBITDA=25.0%" in context
    assert "FCO_Lucro=1.50x" in context
    assert "Conversão FCF/lucro mais recente: 0.83x" in context
    assert "FCO não é FCF" in context


def test_historico_de_multiplos_inclui_roic_margens_e_valuation():
    frame = pd.DataFrame({
        "Data": pd.to_datetime(["2023-12-31", "2024-12-31"]),
        "ROIC": [0.11, 0.14],
        "Margem_Operacional": [0.08, 0.10],
        "Endividamento_Total": [1.2, 0.9],
        "P/L": [9.0, 11.0],
    })
    context = report.build_multiples_history_context(frame)
    assert "ROIC=14.0%" in context
    assert "Margem_Operacional=10.0%" in context
    assert "Endividamento_Total=0.90x" in context
    assert "P/L=11.00x" in context


def test_sanitizacao_normaliza_cenarios_score_e_recomendacoes():
    sanitized = report.sanitize_company_report(_raw_report(), "TEST3")
    assert sum(row["probabilidade_pct"] for row in sanitized["cenarios"]) == 100.0
    assert [row["probabilidade_pct"] for row in sanitized["cenarios"]] == [25.0, 50.0, 25.0]
    assert sanitized["score_qualitativo_ponderado"] == 7.76
    assert sanitized["score_qualitativo"] == 78
    assert "vale comprar" not in sanitized["resumo"].lower()
    assert "acao_sugerida" not in sanitized
    assert "alocacao_sugerida_pct" not in sanitized


def test_prompt_impede_descricao_rasa_e_comparacao_entre_setores():
    dossier = {
        "identificacao": {
            "nome": "Empresa Teste",
            "setor": "Consumo",
            "subsetor": "Varejo",
            "segmento": "Alimentos",
        },
        "series_anuais": [],
    }
    prompt = report.build_company_prompt(
        "TEST3", dossier, pd.DataFrame(), pd.DataFrame(), {},
        "PARES DO MESMO SETOR: PAR13", "Evento CVM 2025", "Carteira com 5 ativos",
    )
    for required in (
        "Peer Analysis", "Qualidade dos Resultados", "acelerando", "desacelerando",
        "Cenários", "Modelo de Negócio", "Vantagem Competitiva", "cara|justa|barata",
        "não escreva", "não autoriza invenção",
    ):
        assert required.lower() in prompt.lower()
    assert "outros setores são" in prompt.lower()
    assert "apenas contexto de diversificação" in prompt.lower()


def test_geracao_usa_motor_exclusivo_sem_parecer_compartilhado(monkeypatch):
    captured: dict[str, str] = {}
    dossier = {
        "identificacao": {"nome": "Teste", "setor": "Setor", "subsetor": "Sub", "segmento": "Seg"},
        "series_anuais": [],
    }
    monkeypatch.setattr(report, "build_dossie", lambda ticker: dossier)
    monkeypatch.setattr(report, "build_peer_context", lambda *args, **kwargs: "PARES SETORIAIS")

    def fake_call(prompt: str, model: str | None = None) -> str:
        captured["prompt"] = prompt
        return json.dumps(_raw_report(), ensure_ascii=False)

    monkeypatch.setattr(report, "_call_llm", fake_call)
    result, returned_dossier = report.generate_company_portfolio_report(
        "TEST3",
        df_fin=pd.DataFrame(),
        portfolio_tickers=["TEST3", "PAR13"],
        portfolio_context="SUPLEMENTAR",
    )
    assert returned_dossier is dossier
    assert result["score_qualitativo"] == 78
    assert "Esta chamada pertence EXCLUSIVAMENTE" in captured["prompt"]
    assert "PARES SETORIAIS" in captured["prompt"]


def test_relatorio_institucional_renderiza_no_streamlit():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string(r'''
from views.analise_portfolio_b3 import _render_empresa_expander

analysis = {
    "perspectiva": "forte",
    "confianca": 80,
    "score_qualitativo": 78,
    "score_qualitativo_ponderado": 7.8,
    "resumo": "Síntese causal.",
    "relatorio": {
        "analise_pares": "Desconto frente aos pares setoriais.",
        "valuation_interpretado": "O desconto reflete risco de execução.",
        "tendencias": "Receita acelera; margem permanece estável.",
        "qualidade_resultados": "FCO converte o lucro; FCF é positivo.",
    },
    "riscos": [{"risco": "Execução", "mecanismo": "Pressiona margem", "indicador_monitorado": "Margem"}],
    "catalisadores": [{"catalisador": "Eficiência", "mecanismo": "Eleva margem", "janela_ou_gatilho": "Resultados"}],
    "cenarios": [
        {"cenario": "Otimista", "probabilidade_pct": 25, "impacto_esperado": "Expansão", "fundamentacao": "Margem"},
        {"cenario": "Base", "probabilidade_pct": 50, "impacto_esperado": "Estável", "fundamentacao": "Execução"},
        {"cenario": "Pessimista", "probabilidade_pct": 25, "impacto_esperado": "Pressão", "fundamentacao": "Custos"},
    ],
    "score_qualitativo_detalhado": {
        "modelo_negocio": {"label": "Modelo de Negócio", "nota": 8, "peso_pct": 10,
                            "justificativa": "Recorrência", "evidencia_ou_lacuna": "Histórico"},
    },
    "adequacao_investidor": {"perfil": "Longo prazo", "horizonte": "5 anos",
                              "tolerancia_volatilidade": "Alta", "condicoes": "Monitorar margem"},
    "conclusao": {"faixa_valor": "barata", "desconto_justificavel": "Parcialmente",
                   "percepcao_mercado": "pessimista", "risco_retorno": "Condicionado à execução",
                   "principal_positivo": "Caixa", "principal_risco": "Margem",
                   "resumo_executivo": "Desconto exige execução."},
}
item = {"ticker": "TEST3", "nome": "Empresa Teste", "peso_pct": 10.0,
        "analise": analysis, "dossie": {}, "n_docs": 0, "rag_stats": {}}
_render_empresa_expander(item, {"TEST3": 0.10})
''')
    app.run(timeout=60)
    assert not app.exception
    assert any("TEST3" in exp.label and "BARATA" in exp.label for exp in app.expander)
