"""A renda que decide é a recorrente, e a ausência precisa continuar ausente.

O piso de DY incidia sobre a renda divulgada e por isso selecionava o yield
inflado: KORE11 entrava com 18,17% sustentado por 58,25% de recorrência.
"""
from __future__ import annotations

from core.fii_renda_recorrente import (
    TETO_LOCATARIO,
    TETO_VENCIMENTO_24M,
    dy_recorrente,
    estado_concentracao,
    protecao_nao_divulgada,
)


def test_dy_recorrente_e_o_produto_do_yield_pela_recorrencia():
    assert dy_recorrente({"dy_12m": .181666, "income_recurrence": .582464}) == 0.1058


def test_recorrencia_ausente_nao_vira_o_dy_bruto():
    """Fallback que só preenche lacuna nunca contradiz — e contradizer é o ponto."""
    assert dy_recorrente({"dy_12m": .18, "income_recurrence": None}) is None
    assert dy_recorrente({"dy_12m": .18}) is None


def test_dy_ausente_tambem_nao_produz_numero():
    assert dy_recorrente({"income_recurrence": .9}) is None


def test_valores_nao_finitos_nao_viram_observacao():
    assert dy_recorrente({"dy_12m": float("nan"), "income_recurrence": .9}) is None


def test_dy_gravado_em_percentual_e_normalizado_antes_do_produto():
    """A vitrine grava ora 0.18 ora 18.0; o produto precisa concordar."""
    assert dy_recorrente({"dy_12m": 18.1666, "income_recurrence": .582464}) == 0.1058


def test_estado_da_concentracao_separa_acima_do_teto_de_nao_divulgado():
    acima = {"tipo": "tijolo", "tenant_concentration": .45}
    abaixo = {"tipo": "tijolo", "tenant_concentration": .2225}
    ausente = {"tipo": "tijolo"}
    assert estado_concentracao(acima, "tenant_concentration", TETO_LOCATARIO) == "acima_do_teto"
    assert estado_concentracao(abaixo, "tenant_concentration", TETO_LOCATARIO) == "ok"
    assert estado_concentracao(ausente, "tenant_concentration", TETO_LOCATARIO) == "nao_divulgado"


def test_papel_e_fof_nao_tem_protecao_de_locatario_a_declarar():
    """Cobrar de papel um dado que só tijolo publica seria punir o tipo errado."""
    assert protecao_nao_divulgada({"tipo": "papel"}) == ()
    assert protecao_nao_divulgada({"tipo": "fof"}) == ()


def test_tijolo_sem_as_duas_metricas_declara_as_duas():
    assert protecao_nao_divulgada({"tipo": "tijolo"}) == (
        "tenant_concentration", "lease_expiry_concentration_24m")


def test_tijolo_com_uma_metrica_declara_so_a_outra():
    row = {"tipo": "tijolo", "tenant_concentration": .10}
    assert protecao_nao_divulgada(row) == ("lease_expiry_concentration_24m",)


def test_tetos_sao_os_valores_aprovados_no_spec():
    assert TETO_LOCATARIO == .40
    assert TETO_VENCIMENTO_24M == .25


def test_elegibilidade_e_score_derivam_o_mesmo_numero():
    """Regra certa em um consumidor só já publicou yield errado na vitrine."""
    from core.fii_integrated_model import (
        IntegratedEligibilityPolicy, apply_integrated_eligibility)
    from core.fii_methodology import score_fiis_by_type

    linhas = [
        {"ticker": "AAA11", "tipo": "papel", "liquidez_diaria": 5e6, "pvp": .95,
         "history_months": 60, "max_drawdown": -.2,
         "dy_12m": .1817, "income_recurrence": .5825},
        {"ticker": "BBB11", "tipo": "papel", "liquidez_diaria": 5e6, "pvp": .98,
         "history_months": 60, "max_drawdown": -.2,
         "dy_12m": .1261, "income_recurrence": .6483},
    ]
    eleg, _ = apply_integrated_eligibility(linhas, IntegratedEligibilityPolicy())
    pontuadas = score_fiis_by_type(eleg)
    por_ticker = {row["ticker"]: row for row in pontuadas}
    assert por_ticker["AAA11"]["dy_recorrente"] == dy_recorrente(linhas[0])
    assert por_ticker["BBB11"]["dy_recorrente"] == dy_recorrente(linhas[1])


def test_a_metodologia_pontua_a_renda_recorrente_e_nao_a_divulgada():
    from core.fii_methodology import COMMON_METRICS

    chaves = {definicao.key: definicao for definicao in COMMON_METRICS}
    assert "dy_12m" not in chaves
    renda = chaves["dy_recorrente"]
    assert (renda.weight, renda.critical, renda.max_age_days) == (.12, True, 15)


def test_versao_da_metodologia_subiu_com_a_formula():
    from core.fii_methodology import FORMULA_VERSION, METHODOLOGY_VERSION

    assert METHODOLOGY_VERSION == "6.9.0"
    assert "6.9.0" in FORMULA_VERSION
