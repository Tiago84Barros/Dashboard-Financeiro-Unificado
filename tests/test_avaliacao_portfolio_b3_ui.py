"""Contratos da aba Avaliação de Portfólio B3 (Empresas B3 → etapa 3 de 3).

Cobre o que a interface promete ao usuário: card de Confiança com escala
correta, modelo único de redistribuição alimentado por banco + web, cards do
relatório que acomodam o texto, e volta ao topo ao trocar de aba.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import core.llm_b3 as llm
import core.portfolio_report_b3 as report

_RAIZ = Path(__file__).resolve().parents[1]


# ── Card de Confiança ────────────────────────────────────────────────────────

def test_confianca_em_fracao_vira_escala_0_100():
    """Regressão: a LLM devolvia 0.85 e o card exibia 1."""
    assert report._normalize_confidence(0.85) == 85
    assert report._normalize_confidence(1) == 100
    assert report._normalize_confidence(0.5) == 50


def test_confianca_ja_em_percentual_e_preservada():
    assert report._normalize_confidence(81) == 81
    assert report._normalize_confidence(100) == 100
    assert report._normalize_confidence(37.4) == 37


def test_confianca_ausente_ou_invalida_permanece_zero():
    # Zero é o sinal que a UI usa para detectar relatório não gerado.
    for entrada in (None, 0, -5, "n/d", float("nan")):
        assert report._normalize_confidence(entrada) == 0


def test_confianca_acima_de_cem_e_limitada():
    assert report._normalize_confidence(140) == 100


def test_relatorio_sanitizado_expoe_confianca_utilizavel():
    sanitized = report.sanitize_company_report(
        {"perspectiva": "forte", "confianca": 0.9}, "TEST3",
    )
    assert sanitized["confianca"] == 90


def test_prompt_proibe_confianca_em_fracao():
    assert "NUNCA fração" in report._PROMPT_COMPANY_PORTFOLIO


# ── Modelo único de redistribuição ───────────────────────────────────────────

def _itens() -> list[dict]:
    return [
        {"ticker": "AAAA3", "score": 80, "alpha_selic": 5,
         "analise": {"perspectiva": "forte", "score_qualitativo": 80, "confianca": 90}},
        {"ticker": "BBBB4", "score": 60, "alpha_selic": 0,
         "analise": {"perspectiva": "moderada", "score_qualitativo": 60, "confianca": 70}},
        {"ticker": "CCCC3", "score": 40, "alpha_selic": -2,
         "analise": {"perspectiva": "fraca", "score_qualitativo": 40, "confianca": 60}},
    ]


def test_redistribuicao_nao_tem_mais_modo():
    parametros = inspect.signature(llm.redistribuir_pesos).parameters
    assert "mode" not in parametros
    assert "sinal_web" in parametros


def test_perspectiva_fraca_permanece_na_carteira_com_peso_menor():
    """O modo Rígido excluía; o modelo único mantém e reduz."""
    pesos = llm.redistribuir_pesos(_itens())
    assert set(pesos) == {"AAAA3", "BBBB4", "CCCC3"}
    assert pesos["AAAA3"] > pesos["BBBB4"] > pesos["CCCC3"]


def test_pesos_somam_um_e_respeitam_piso_e_teto():
    # 12 nomes distintos: com n ≥ 6 o teto de 25% é atingível.
    itens = []
    for i in range(4):
        for base in _itens():
            item = {**base, "analise": dict(base["analise"])}
            item["ticker"] = f"TK{len(itens):02d}"
            itens.append(item)
    pesos = llm.redistribuir_pesos(itens)
    assert abs(sum(pesos.values()) - 1.0) < 1e-6
    for peso in pesos.values():
        assert llm.PESO_MIN - 1e-9 <= peso <= llm.PESO_MAX + 1e-9


def test_teto_inatingivel_nao_achata_a_carteira_em_pesos_iguais():
    """Com 3 nomes o peso médio (33%) já supera o teto de 25%.

    O clip + renormalização convergia para 1/3 para todos e jogava a análise
    fora: forte, moderada e fraca saíam com o mesmo peso.
    """
    pesos = llm.redistribuir_pesos(_itens())
    assert abs(sum(pesos.values()) - 1.0) < 1e-6
    assert len(set(round(p, 6) for p in pesos.values())) == 3


def test_carteira_de_um_nome_recebe_peso_total():
    pesos = llm.redistribuir_pesos(_itens()[:1])
    assert abs(pesos["AAAA3"] - 1.0) < 1e-6


def test_divergencia_com_a_web_reduz_o_peso_da_empresa():
    base = llm.redistribuir_pesos(_itens())
    com_divergencia = llm.redistribuir_pesos(
        _itens(), sinal_web={"AAAA3": {"fator": 0.90}},
    )
    assert com_divergencia["AAAA3"] < base["AAAA3"]


def test_falha_de_llm_nao_zera_a_empresa():
    """Confiança 0 vem de fallback técnico, não de julgamento sobre a empresa."""
    itens = _itens()
    itens[0]["analise"]["confianca"] = 0
    pesos = llm.redistribuir_pesos(itens)
    assert pesos["AAAA3"] >= llm.PESO_MIN


def test_carteira_vazia_devolve_dicionario_vazio():
    assert llm.redistribuir_pesos([]) == {}


# ── Evidência web no relatório consolidado ───────────────────────────────────

def test_relatorio_consolidado_recebe_segunda_fonte():
    assert "web_context" in inspect.signature(report.analyze_portfolio_report).parameters
    assert "{web_context}" in report._PROMPT_PORTFOLIO


def test_view_remove_o_seletor_de_modo_e_liga_a_evidencia_web():
    fonte = (_RAIZ / "views" / "analise_portfolio_b3.py").read_text(encoding="utf-8")
    assert "Modo de redistribuição" not in fonte
    assert '"Rígida"' not in fonte and '"Flexível"' not in fonte
    assert "get_web_evidence_context" in fonte
    assert "sinal_web=web_sinal" in fonte


# ── Cards do relatório consolidado ───────────────────────────────────────────

def test_cards_do_relatorio_acomodam_texto_longo():
    fonte = (_RAIZ / "views" / "analise_portfolio_b3.py").read_text(encoding="utf-8")
    bloco = fonte[fonte.index(".apb3-kpi-row{"):fonte.index(".apb3-kpi-pos{")]
    # grid com colunas iguais em vez de flex:1 dependente do conteúdo
    assert "display:grid" in bloco
    assert "repeat(auto-fit,minmax(190px,1fr))" in bloco
    # quebra da palavra única longa ("CONSTRUTIVA") e fonte que encolhe
    assert bloco.count("overflow-wrap:anywhere") >= 3
    assert "clamp(" in bloco
    assert "min-width:0" in bloco
    assert "@media (max-width:720px)" in fonte


# ── Volta ao topo ao trocar de aba ───────────────────────────────────────────

def test_trilho_de_abas_rola_para_o_topo():
    import design.market_companies as mc

    fonte = inspect.getsource(mc.render_market_tabs)
    assert "rolar_para_topo()" in fonte
    # pop e não get: a rolagem vale só para o rerun da troca de aba.
    assert "st.session_state.pop(rolar_flag, False)" in fonte
