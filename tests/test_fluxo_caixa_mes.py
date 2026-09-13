"""O mês só é deficitário quando a despesa supera a receita.

O defeito que estes testes travam: quatro telas subtraíam o aporte do saldo do
mês. Com receitas 27.636,02, despesas 25.577,70 e aporte de 10.000,00, o
investidor via -7.941,68 em vermelho e 128,7% de renda comprometida por ter
investido. Investir não empobrece ninguém; move patrimônio de lugar.
"""
from core.fluxo_caixa_mes import (
    COR_APORTE,
    COR_NEGATIVO,
    COR_POSITIVO,
    cor_comprometimento,
    fluxo_do_mes,
)


def test_aporte_nao_torna_o_mes_deficitario():
    fluxo = fluxo_do_mes(27_636.02, 25_577.70, 10_000.00)
    assert fluxo.saldo_caixa == 2058.32
    assert not fluxo.deficit
    assert fluxo.cor_saldo != COR_NEGATIVO


def test_renda_comprometida_ignora_o_aporte():
    fluxo = fluxo_do_mes(27_636.02, 25_577.70, 10_000.00)
    # 128,7% era o número antigo -- e ele dizia que o investidor gastou mais do
    # que ganhou, o que não aconteceu.
    assert fluxo.renda_comprometida_pct == 92.6


def test_deficit_real_continua_vermelho():
    fluxo = fluxo_do_mes(5_000, 6_000, 0)
    assert fluxo.deficit and fluxo.cor_saldo == COR_NEGATIVO


def test_sobra_dentro_da_renda_fica_verde():
    fluxo = fluxo_do_mes(10_000, 5_000, 2_000)
    assert fluxo.total_retido == 7_000
    assert not fluxo.acima_da_receita and fluxo.cor_saldo == COR_POSITIVO


def test_retencao_acima_da_renda_fica_azul():
    """Só alcançável quando o aporte supera a despesa: o investidor reteve mais
    do que entrou no mês, o que é bom e não é sobra de caixa."""
    fluxo = fluxo_do_mes(10_000, 3_000, 5_000)
    assert fluxo.total_retido == 12_000 > 10_000
    assert fluxo.acima_da_receita and fluxo.cor_saldo == COR_APORTE


def test_sem_receita_nao_inventa_percentual():
    fluxo = fluxo_do_mes(0, 1_200, 0)
    assert fluxo.renda_comprometida_pct is None
    assert fluxo.taxa_poupanca_pct is None


def test_cor_do_comprometimento_ausente_nao_alarma():
    assert cor_comprometimento(None) != COR_NEGATIVO
    assert cor_comprometimento(95) == COR_NEGATIVO
