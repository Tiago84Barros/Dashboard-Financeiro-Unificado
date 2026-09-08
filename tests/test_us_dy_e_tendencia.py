# -*- coding: utf-8 -*-
"""Rendimento de dividendo derivado do EDGAR e crescimento por regressao.

Os tres blocos cobrem defeitos medidos no armazem local em 08/09/2026, nao
hipoteses: o dividendo que vinha de um exercicio antigo, o valor de mercado
corrompido em 54 simbolos e a taxa de ponta a ponta que ignora o meio da serie.
"""
import pytest

import core.us_metrics as um

# --- dividendo: qual exercicio responde ------------------------------------

def test_dividendo_vem_do_ultimo_exercicio_e_nao_do_ultimo_preenchido():
    """`_latest` andava para tras ate achar valor; a QCOM saia com 1,91 de um
    exercicio antigo dividido pelo valor de mercado de hoje. 261 simbolos
    tinham essa defasagem de um ano ou mais."""
    cf = [{"fiscal_year": 2021, "dividends_paid": -900.0},
          {"fiscal_year": 2024, "dividends_paid": None}]

    assert um._dividendo_desembolsado(cf) is None
    assert um._latest(cf, "dividends_paid") == -900.0   # o comportamento antigo


def test_ultimo_exercicio_e_o_maior_ano_nao_a_ultima_linha():
    cf = [{"fiscal_year": 2024, "dividends_paid": -10.0},
          {"fiscal_year": 2022, "dividends_paid": -99.0}]

    assert um._linha_do_ultimo_exercicio(cf)["fiscal_year"] == 2024


def test_ausencia_de_dividendo_nao_vira_zero():
    """Medido contra market_us.dividends nos 1.383 simbolos que ela cobre: dos
    303 com dividends_paid nulo no ultimo exercicio, 127 (42%) pagaram nos 12
    meses. Zero seria afirmar nao-pagamento sem ter visto."""
    assert um._dividend_yield([{"fiscal_year": 2024}], 1_000.0) is None


def test_zero_publicado_pelo_edgar_e_valor_observado():
    assert um._dividend_yield(
        [{"fiscal_year": 2024, "dividends_paid": 0.0}], 1_000.0) == 0.0


def test_dividend_yield_usa_o_modulo_do_desembolso():
    """O EDGAR publica saida de caixa com sinal negativo."""
    dy = um._dividend_yield(
        [{"fiscal_year": 2024, "dividends_paid": -50.0}], 1_000.0)

    assert dy == pytest.approx(0.05)


def test_sem_valor_de_mercado_nao_ha_yield():
    assert um._dividend_yield(
        [{"fiscal_year": 2024, "dividends_paid": -50.0}], None) is None
    assert um._dividend_yield(
        [{"fiscal_year": 2024, "dividends_paid": -50.0}], 0.0) is None


# --- valor de mercado: duas fontes se conferindo ---------------------------

def test_market_cap_divergente_por_ordem_de_grandeza_vira_lacuna():
    """Sem esta guarda o DY maximo do universo era 1.350.826%."""
    assert um.market_cap_confiavel(1_000.0, 1_000_000_000.0) is None


def test_market_cap_com_acoes_implicitas_absurdas_e_recusado():
    """PSKY constava valendo 10.290 dolares: 686 acoes a 15 dolares. As bolsas
    exigem 1 milhao de acoes em circulacao; o piso aqui e uma ordem abaixo."""
    assert um.market_cap_confiavel(10_290.0, None, preco=15.0) is None


def test_market_cap_menor_que_a_receita_por_duas_ordens_e_recusado():
    """ATHS: 5 milhoes de valor de mercado contra 25,7 bilhoes de receita."""
    assert um.market_cap_confiavel(5e6, None, receita=25.7e9) is None


def test_market_cap_coerente_com_o_derivado_e_aceito():
    assert um.market_cap_confiavel(1.01e9, 1.0e9, preco=50.0) == 1.01e9


def test_sem_publicado_o_derivado_responde():
    assert um.market_cap_confiavel(None, 2.0e9) == 2.0e9
    assert um.market_cap_confiavel(0.0, None) is None


# --- crescimento: inclinacao contra ponta a ponta --------------------------

def test_tendencia_usa_todos_os_pontos_e_o_cagr_so_as_pontas():
    """Mesma ponta, meio oposto: o CAGR nao distingue, a regressao sim."""
    subindo = [(2020, 100.0), (2021, 110.0), (2022, 121.0), (2023, 133.0),
               (2024, 146.0)]
    solavanco = [(2020, 100.0), (2021, 200.0), (2022, 60.0), (2023, 90.0),
                 (2024, 146.0)]

    assert um.cagr(100.0, 146.0, 4) == pytest.approx(
        um.cagr(100.0, 146.0, 4))
    assert um.tendencia_composta(subindo)[0] == pytest.approx(0.0999, abs=1e-3)
    assert um.tendencia_composta(subindo)[1] > 0.99
    assert um.tendencia_composta(solavanco)[1] < 0.3, \
        "R2 baixo e o aviso de que a taxa nao descreve a serie"


def test_tendencia_exige_um_minimo_de_pontos():
    assert um.tendencia_composta([(2023, 100.0), (2024, 110.0)]) == (None, None)


def test_tendencia_composta_ignora_serie_nao_positiva():
    """log-linear nao aceita zero nem negativo; para essas series existe a
    tendencia simetrica."""
    assert um.tendencia_composta(
        [(2022, -1.0), (2023, 10.0), (2024, 20.0)]) == (None, None)


def test_tendencia_simetrica_mede_serie_que_cruza_o_zero():
    """Lucro e FCF trocam de sinal; a normalizacao pelo nivel medio absoluto
    mantem a medida finita onde a razao explodiria."""
    taxa, r2 = um.tendencia_simetrica(
        [(2022, -20.0), (2023, 0.0), (2024, 20.0)])

    assert taxa is not None and taxa > 0
    assert r2 == pytest.approx(1.0)


def test_ols_sem_variacao_em_x_nao_inventa_reta():
    assert um._ols([2024, 2024, 2024], [1.0, 2.0, 3.0]) is None


# --------------------------------------------------------------------------
# Contagem de ações: o defeito de escala do último exercício
# --------------------------------------------------------------------------

def _bal(pares):
    return [{"fiscal_year": ano, "shares_outstanding": v} for ano, v in pares]


def test_acoes_recusa_queda_de_mil_vezes_no_ultimo_exercicio():
    """FLS: 176.793 em 2025 contra 176.793.000 nos quatro anos anteriores."""
    bal = _bal([(2021, 176_793_000), (2022, 176_793_000),
                (2023, 176_793_000), (2024, 176_793_000), (2025, 176_793)])
    assert um.acoes_em_circulacao(bal) is None


def test_acoes_recusa_salto_de_mil_vezes_no_ultimo_exercicio():
    bal = _bal([(2023, 5_882_266), (2024, 5_882_266), (2025, 5_882_266_000)])
    assert um.acoes_em_circulacao(bal) is None


def test_acoes_aceita_variacao_ordinaria():
    bal = _bal([(2023, 100_000_000), (2024, 98_000_000), (2025, 95_000_000)])
    assert um.acoes_em_circulacao(bal) == 95_000_000


def test_acoes_com_um_unico_exercicio_passa_por_falta_de_contradicao():
    """Sem série ao lado não há como saber; recusar seria inventar evidência."""
    assert um.acoes_em_circulacao(_bal([(2025, 95_000_000)])) == 95_000_000


def test_acoes_recusa_serie_inteira_em_milhoes():
    """GBL: 26,70 em todos os exercícios — coerente consigo mesma, e errada."""
    bal = _bal([(2018, 29.00), (2019, 27.40), (2020, 27.50), (2021, 26.70)])
    assert um.acoes_em_circulacao(bal) is None


def test_acoes_recusa_o_informado_abaixo_do_piso():
    """HY: balanço sem contagem nenhuma e um divisor de 100 chegando por fora."""
    assert um.acoes_em_circulacao([], informado=100.0) is None


def test_acoes_informado_prevalece_sobre_o_balanco():
    bal = _bal([(2024, 90_000_000), (2025, 95_000_000)])
    assert um.acoes_em_circulacao(bal, informado=97_000_000) == 97_000_000


def test_acoes_sem_nada_devolve_none():
    assert um.acoes_em_circulacao([]) is None


def test_crescimento_de_acoes_recusa_a_recompra_falsa():
    """A queda de escala virava -99,89%: a maior recompra líquida do universo."""
    bal = _bal([(2022, 176_793_000), (2023, 176_793_000),
                (2024, 176_793_000), (2025, 176_793)])
    assert um.crescimento_de_acoes(bal, 3) is None


def test_crescimento_de_acoes_recusa_serie_em_milhoes():
    bal = _bal([(2018, 29.00), (2019, 27.40), (2020, 27.50), (2021, 26.70)])
    assert um.crescimento_de_acoes(bal, 3) is None


def test_crescimento_de_acoes_mede_a_recompra_real():
    bal = _bal([(2022, 100_000_000), (2023, 97_000_000), (2024, 94_000_000),
                (2025, 91_000_000)])
    taxa = um.crescimento_de_acoes(bal, 3)
    assert taxa is not None and -0.04 < taxa < -0.02
