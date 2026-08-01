import pytest

from core.financeiro import patrimonio_investido_confiavel


def test_patrimonio_usa_valor_de_mercado_sem_somar_saldo_historico():
    carteira = {"total_mercado": 342_924.59}
    patrimonio = {"total": 688_080.92, "saldo_bancario": 345_156.33}

    assert patrimonio_investido_confiavel(carteira, patrimonio) == pytest.approx(
        342_924.59
    )


def test_patrimonio_usa_total_investido_da_visao_quando_carteira_indisponivel():
    assert patrimonio_investido_confiavel({}, {"investido": 125_000}) == 125_000


@pytest.mark.parametrize("valor", [None, "inválido", float("nan"), -1])
def test_patrimonio_nao_converte_dado_ausente_ou_invalido_em_zero(valor):
    assert patrimonio_investido_confiavel(
        {"total_mercado": valor}, {"investido": valor}
    ) is None


def test_carteira_vazia_mantem_zero_como_valor_valido():
    assert patrimonio_investido_confiavel({"total_mercado": 0}, {}) == 0
