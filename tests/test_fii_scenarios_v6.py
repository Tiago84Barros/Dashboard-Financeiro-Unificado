from core.fii_scenarios import asset_scenario_return, scenario_missing_input_penalty


def test_asset_specific_credit_risk_worsens_credit_scenario():
    safe = {"tipo": "papel", "confidence": .9, "ltv": .45,
            "delinquency": 0, "issuance_concentration": .1}
    risky = {"tipo": "papel", "confidence": .5, "ltv": .9,
             "delinquency": .1, "issuance_concentration": .7}
    assert asset_scenario_return(risky, "credito") < asset_scenario_return(safe, "credito")


def test_unknown_data_has_ambiguity_penalty():
    known = {"tipo": "tijolo", "confidence": 1.0}
    unknown = {"tipo": "tijolo", "confidence": .2}
    assert asset_scenario_return(unknown, "vacancia") < asset_scenario_return(known, "vacancia")


def test_missing_credit_inputs_receive_conservative_penalty_without_fake_values():
    known = {"tipo": "papel", "confidence": .9, "ltv": .5,
             "delinquency": 0.0, "issuance_concentration": .2}
    unknown = {"tipo": "papel", "confidence": .9}

    penalty, missing = scenario_missing_input_penalty(unknown, "credito")

    assert penalty > 0
    assert set(missing) == {"delinquency", "ltv", "concentracao"}
    assert asset_scenario_return(unknown, "credito") < asset_scenario_return(known, "credito")


# Escala dos percentuais. A vitrine pode gravar o mesmo percentual em fracao
# (0.12) ou em ponto (12.0) -- `core.fii_renda_recorrente._fracao` documenta
# isso, e a elegibilidade le esses campos por ele. O motor de cenarios lia o
# valor cru: vacancia 12.0 virava 100% (truncada) e concentracao 18.0 entrava
# como 1800% no choque, eliminando da carteira um fundo que a elegibilidade
# tinha aprovado com 18%.

import pytest  # noqa: E402

from core.fii_portfolio_v4 import SCENARIOS  # noqa: E402

_PONTO_E_FRACAO = [
    ("vacancia_fisica", 12.0, .12),
    ("vacancia_financeira", 9.5, .095),
    ("tenant_concentration", 18.0, .18),
    ("issuance_concentration", 22.0, .22),
    ("holdings_overlap", 35.0, .35),
    ("delinquency", 3.0, .03),
]


@pytest.mark.parametrize(("campo", "em_ponto", "em_fracao"), _PONTO_E_FRACAO)
@pytest.mark.parametrize("tipo", ["tijolo", "papel", "hibrido"])
def test_percentual_em_ponto_e_em_fracao_dao_o_mesmo_choque(campo, em_ponto, em_fracao, tipo):
    base = {"tipo": tipo, "confidence": .9, "ltv": .5, "leverage": .1,
            "delinquency": .01, "vacancia_fisica": .05,
            "tenant_concentration": .1, "issuance_concentration": .1}
    for cenario in SCENARIOS:
        assert asset_scenario_return({**base, campo: em_ponto}, cenario) == pytest.approx(
            asset_scenario_return({**base, campo: em_fracao}, cenario)), (campo, cenario)


def test_alavancagem_acima_de_um_nao_e_lida_como_ponto():
    # Alavancagem e LTV podem passar de 100% legitimamente (o motor aceita
    # ate 1,5). Dividir por 100 tornaria 120% em 1,2%.
    moderada = {"tipo": "tijolo", "confidence": .9, "leverage": 1.2}
    nula = {"tipo": "tijolo", "confidence": .9, "leverage": 0.0}
    assert asset_scenario_return(moderada, "selic_alta") < asset_scenario_return(nula, "selic_alta")
