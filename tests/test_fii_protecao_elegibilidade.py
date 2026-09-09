"""Proteção é critério de entrada, não observação no relatório."""
from __future__ import annotations

import pytest

from core.fii_integrated_model import (
    IntegratedEligibilityPolicy,
    apply_integrated_eligibility,
)

_BASE = {
    "ticker": "TEST11", "tipo": "tijolo", "liquidez_diaria": 5e6,
    "pvp": .95, "history_months": 60, "max_drawdown": -.20,
    "dy_12m": .12, "income_recurrence": .90,
    "tenant_concentration": .10, "lease_expiry_concentration_24m": .10,
}


def _linha(**mudancas):
    return {**_BASE, **mudancas}


def _reasons(row, policy=None):
    from core.fii_integrated_model import _eligibility_reasons
    return tuple(_eligibility_reasons(row, policy or IntegratedEligibilityPolicy()))


def test_yield_alto_sustentado_por_renda_nao_recorrente_e_excluido():
    """KORE11: 18,17% de DY com 58,25% de recorrência = 10,58% recorrentes."""
    row = _linha(dy_12m=.1817, income_recurrence=.30)  # 5,45% recorrentes
    assert "renda recorrente abaixo do mínimo" in _reasons(row)


def test_yield_recorrente_acima_do_piso_passa():
    assert _reasons(_linha(dy_12m=.1817, income_recurrence=.5825)) == ()


def test_recorrencia_ausente_exclui_com_razao_propria():
    row = _linha(income_recurrence=None)
    assert "renda recorrente ausente" in _reasons(row)
    assert "renda recorrente abaixo do mínimo" not in _reasons(row)


def test_concentracao_de_locatario_acima_do_teto_exclui():
    assert "concentração de locatário acima do teto" in _reasons(
        _linha(tenant_concentration=.45))


def test_concentracao_de_locatario_ausente_nao_exclui():
    """Vetar só quem divulga premiaria quem cala."""
    assert _reasons(_linha(tenant_concentration=None)) == ()


def test_vencimentos_em_24m_acima_do_teto_excluem():
    assert "vencimentos em 24m acima do teto" in _reasons(
        _linha(lease_expiry_concentration_24m=.29))


def test_vencimentos_ausentes_nao_excluem():
    assert _reasons(_linha(lease_expiry_concentration_24m=None)) == ()


@pytest.mark.parametrize("tipo", ["papel", "fof"])
def test_papel_e_fof_nao_sao_cobrados_por_metrica_de_imovel(tipo):
    row = _linha(tipo=tipo, tenant_concentration=None,
                 lease_expiry_concentration_24m=None)
    assert _reasons(row) == ()


def test_teto_de_plausibilidade_de_20_por_cento_continua_sobre_o_dy_bruto():
    """Ali o teto testa a sanidade da fonte, não a qualidade da renda."""
    row = _linha(dy_12m=.35, income_recurrence=.50)  # 17,5% recorrentes
    assert "DY 12m acima do limite de plausibilidade" in _reasons(row)


def test_a_politica_nao_aceita_mais_o_nome_antigo_do_piso():
    """O rename impede que um chamador passe a semântica antiga em silêncio."""
    with pytest.raises(TypeError):
        IntegratedEligibilityPolicy(min_dy_12m=.08)


def test_relatorio_agrega_as_razoes_novas():
    linhas = [_linha(ticker="A11", income_recurrence=.20),
              _linha(ticker="B11", tenant_concentration=.90)]
    _, relatorio = apply_integrated_eligibility(linhas, IntegratedEligibilityPolicy())
    assert relatorio["eligible_count"] == 0
    assert relatorio["exclusion_counts"]["renda recorrente abaixo do mínimo"] == 1
    assert relatorio["exclusion_counts"]["concentração de locatário acima do teto"] == 1


def test_fronteira_do_teto_e_exclusiva():
    """0.40 e 0.25 exatos ainda passam; 0.4001/0.2501 já excluem — fronteira é > , não >=."""
    assert _reasons(_linha(tenant_concentration=.40,
                            lease_expiry_concentration_24m=.25)) == ()
    row_acima = _linha(tenant_concentration=.4001,
                        lease_expiry_concentration_24m=.2501)
    reasons = _reasons(row_acima)
    assert "concentração de locatário acima do teto" in reasons
    assert "vencimentos em 24m acima do teto" in reasons
