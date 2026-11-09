"""O gatilho de rebalanceamento por renda compara renda RECORRENTE.

``evaluate_rebalance_triggers`` comparava ``expected_yield``, que é o DY
divulgado (alias de ``trailing_yield_12m``). Esse DY soma amortização e
resultado não recorrente, exatamente o que a metodologia v6.10 deixou de usar
para ordenar fundos. Uma carteira proposta com DY bruto maior só por devolver
capital disparava "ganho de renda".
"""
from core.fii_portfolio_v4 import evaluate_rebalance_triggers

RENDA = "ganho de renda recorrente supera custos e margem mínima"


def _gatilho_de_renda(motivos):
    """Qualquer gatilho de renda, com o rótulo antigo ou o novo."""
    return any("renda" in motivo for motivo in motivos)


def _carteira(divulgado, recorrente):
    return {"macro_regime": "neutro", "expected_yield": divulgado,
            "trailing_yield_12m": divulgado, "recurrent_yield_12m": recorrente,
            "stress_loss": .10, "confidence": .90}


def test_dy_bruto_maior_por_amortizacao_nao_dispara_troca():
    atual = _carteira(divulgado=.10, recorrente=.09)
    # +4 pontos de DY divulgado, recorrente igual: o "ganho" é devolução de capital.
    proposta = _carteira(divulgado=.14, recorrente=.09)

    assert not _gatilho_de_renda(evaluate_rebalance_triggers(atual, proposta))


def test_ganho_de_renda_recorrente_dispara():
    atual = _carteira(divulgado=.10, recorrente=.08)
    proposta = _carteira(divulgado=.10, recorrente=.10)

    assert RENDA in evaluate_rebalance_triggers(atual, proposta)


def test_ganho_abaixo_do_custo_e_da_margem_nao_dispara():
    atual = _carteira(divulgado=.10, recorrente=.0900)
    proposta = _carteira(divulgado=.10, recorrente=.0970)  # +0,70 pp < 0,75 pp

    assert not _gatilho_de_renda(evaluate_rebalance_triggers(atual, proposta))


def test_sem_recorrente_nao_cai_no_dy_bruto():
    atual = _carteira(divulgado=.08, recorrente=None)
    proposta = _carteira(divulgado=.14, recorrente=.10)

    assert not _gatilho_de_renda(evaluate_rebalance_triggers(atual, proposta))


def test_demais_gatilhos_seguem_iguais():
    atual = _carteira(divulgado=.10, recorrente=.09)
    proposta = {**_carteira(divulgado=.10, recorrente=.09),
                "macro_regime": "stress", "stress_loss": .05}

    motivos = evaluate_rebalance_triggers(atual, proposta)

    assert "mudança material de regime macroeconômico" in motivos
    assert "redução material da perda em cenário de estresse" in motivos
