"""Altman e Piotroski como penalidade de risco na seleção EUA.

Ambos eram calculados para todas as empresas e gravados na vitrine, mas só
apareciam na análise individual — nunca tocavam a construção de carteira
(verificação de 27/07/2026: 597 empresas ativas na zona de aflição do Altman).
"""
from __future__ import annotations

import pandas as pd
import pytest

from core.us_advanced_lab import build_entry_scores


def _base(symbol: str, **campos) -> dict:
    """Empresa saudável; os testes acendem um alerta por vez."""
    linha = {
        "symbol": symbol, "sector": "Technology", "industry": "Software",
        "score": 70.0, "score_quality": 70.0, "score_growth": 65.0,
        "score_solidity": 70.0, "score_valuation": 60.0,
        "score_capital_efficiency": 70.0, "score_shareholder": 60.0,
        "fcf_margin": 0.20, "cash_conversion": 1.1, "fcf_yield": 0.06,
        "net_debt_ebitda": 1.0, "current_ratio": 2.0, "net_margin": 0.15,
        "interest_coverage": 12.0,
        "z_zone": "segura", "f_score": 7, "f_evaluable": 9,
    }
    linha.update(campos)
    return linha


def test_empresa_saudavel_nao_recebe_penalidade():
    out = build_entry_scores(pd.DataFrame([_base("OK")]))
    assert out.iloc[0]["risk_penalty"] == 0
    assert out.iloc[0]["risk_driver"] == "sem alerta crítico"


def test_altman_em_aflicao_penaliza_mas_nao_exclui_sozinho():
    """Peso 8: sinaliza sem reprovar isolado — o Z-Score erra em asset-light."""
    out = build_entry_scores(pd.DataFrame([_base("DISTRESS", z_zone="aflição")]))
    linha = out.iloc[0]
    assert linha["risk_penalty"] == 8
    assert "Altman" in linha["risk_driver"]
    assert linha["entry_status"] != "Excluída"


def test_altman_somado_a_outro_alerta_exclui():
    """Sinal de aflição + confirmação independente passa do corte de 10."""
    out = build_entry_scores(pd.DataFrame([
        _base("GRAVE", z_zone="aflição", interest_coverage=1.0)]))
    linha = out.iloc[0]
    assert linha["risk_penalty"] == 16
    assert linha["entry_status"] == "Excluída"


def test_altman_nao_se_aplica_a_bancos_e_reits():
    """O modelo de 1968 não descreve balanço de banco nem de REIT."""
    for setor in ("Financial Services", "Real Estate"):
        out = build_entry_scores(pd.DataFrame([
            _base("FIN", sector=setor, z_zone="aflição")]))
        assert out.iloc[0]["risk_penalty"] == 0, setor


def test_piotroski_fraco_penaliza():
    out = build_entry_scores(pd.DataFrame([_base("FRACA", f_score=2)]))
    linha = out.iloc[0]
    assert linha["risk_penalty"] == 6
    assert "Piotroski" in linha["risk_driver"]


def test_piotroski_com_poucos_criterios_avaliados_nao_pune():
    """Ausência de dado nunca vira penalidade — princípio do projeto."""
    out = build_entry_scores(pd.DataFrame([
        _base("PARCIAL", f_score=2, f_evaluable=3)]))
    assert out.iloc[0]["risk_penalty"] == 0


def test_z_zone_ausente_nao_pune():
    linha = _base("SEMDADO")
    linha.pop("z_zone")
    out = build_entry_scores(pd.DataFrame([linha]))
    assert out.iloc[0]["risk_penalty"] == 0


def test_frame_sem_as_colunas_avancadas_continua_funcionando():
    """Compatibilidade: vitrine antiga não tem os campos expandidos."""
    linha = _base("LEGADO")
    for chave in ("z_zone", "f_score", "f_evaluable"):
        linha.pop(chave)
    out = build_entry_scores(pd.DataFrame([linha]))
    assert not out.empty and out.iloc[0]["risk_penalty"] == 0


def test_altman_e_piotroski_somam_e_aparecem_juntos_no_motivo():
    out = build_entry_scores(pd.DataFrame([
        _base("DUPLO", z_zone="aflição", f_score=1)]))
    linha = out.iloc[0]
    assert linha["risk_penalty"] == 14
    assert "Altman" in linha["risk_driver"] and "Piotroski" in linha["risk_driver"]
    assert linha["entry_status"] == "Excluída"


# ── payout (mesma calibração do B3) ──────────────────────────────────────────

def test_payout_muito_acima_do_lucro_penaliza():
    out = build_entry_scores(pd.DataFrame([_base("DISTRIB", payout_ratio=3.18)]))
    linha = out.iloc[0]
    assert linha["risk_penalty"] == 7
    assert "payout" in linha["risk_driver"]


def test_payout_acima_do_lucro_mas_abaixo_do_limiar_nao_pune():
    out = build_entry_scores(pd.DataFrame([_base("OK", payout_ratio=1.2)]))
    assert out.iloc[0]["risk_penalty"] == 0


def test_reit_com_payout_alto_nao_e_penalizado():
    """REIT distribui FFO por exigência legal; payout > 1 é estrutural."""
    out = build_entry_scores(pd.DataFrame([
        _base("REIT", payout_ratio=2.5, is_reit=True)]))
    assert out.iloc[0]["risk_penalty"] == 0
    out2 = build_entry_scores(pd.DataFrame([
        _base("IMOB", payout_ratio=2.5, sector="Real Estate")]))
    assert out2.iloc[0]["risk_penalty"] == 0


def test_payout_ausente_nao_pune():
    out = build_entry_scores(pd.DataFrame([_base("SEMDIV")]))
    assert out.iloc[0]["risk_penalty"] == 0


# ── accruals de Sloan ────────────────────────────────────────────────────────

def test_accruals_elevados_penalizam():
    """Corte em 0,10 = cauda de ~5% do universo real (p95 = 0,112)."""
    out = build_entry_scores(pd.DataFrame([_base("ACCR", sloan_accruals=0.25)]))
    linha = out.iloc[0]
    assert linha["risk_penalty"] == 5
    assert "accruals" in linha["risk_driver"]


def test_accruals_normais_nao_punem():
    # mediana do universo é −0,050; valores típicos não podem disparar
    for valor in (-0.05, 0.0, 0.03):
        out = build_entry_scores(pd.DataFrame([_base("OK", sloan_accruals=valor)]))
        assert out.iloc[0]["risk_penalty"] == 0, valor


def test_roic_incremental_negativo_nao_e_penalizado():
    """Decisão explícita: 39% dos que têm o dado são negativos e a métrica é um
    delta de 2 anos — penalizar dispararia em vale de ciclo."""
    out = build_entry_scores(pd.DataFrame([
        _base("CICLO", incremental_roic=-0.35)]))
    assert out.iloc[0]["risk_penalty"] == 0


# ── payout derivado do histórico (sem re-ingestão) ───────────────────────────

def test_payout_derivado_do_ultimo_exercicio_com_lucro():
    from core.us_read import _payout_do_historico

    historico = [
        {"fiscal_year": 2023, "net_income": 100.0, "dividends_paid": -50.0},
        {"fiscal_year": 2024, "net_income": 80.0, "dividends_paid": -160.0},
    ]
    assert _payout_do_historico(historico) == pytest.approx(2.0)


def test_payout_ignora_ano_de_prejuizo_e_usa_o_anterior():
    from core.us_read import _payout_do_historico

    historico = [
        {"fiscal_year": 2023, "net_income": 100.0, "dividends_paid": -40.0},
        {"fiscal_year": 2024, "net_income": -20.0, "dividends_paid": -30.0},
    ]
    assert _payout_do_historico(historico) == pytest.approx(0.4)


def test_payout_indeterminavel_devolve_none():
    from core.us_read import _payout_do_historico

    assert _payout_do_historico([]) is None
    assert _payout_do_historico(None) is None
    assert _payout_do_historico([{"fiscal_year": 2024, "net_income": 10.0}]) is None
