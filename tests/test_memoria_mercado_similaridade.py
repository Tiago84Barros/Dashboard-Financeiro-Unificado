"""Fator de Similaridade do Cenário (0-100).

Cenário pedido coberto aqui: **regimes macroeconômicos diferentes**. Junto vêm
as duas propriedades que sustentam o número: dimensão ausente sai do
denominador (nunca é creditada nem debitada) e tipo de evento diferente
invalida a comparação inteira, por mais parecido que esteja o resto.
"""
from __future__ import annotations

from core.memoria_mercado import similaridade as sim
from tests.apoio_memoria import cenario


def test_cenarios_identicos_dao_cem_com_cobertura_total():
    s = sim.calcular(cenario(), cenario())
    assert s.fator == 100.0
    assert s.cobertura == 1.0
    assert s.utilizavel
    assert s.invalidantes == ()
    assert all(d.medida for d in s.dimensoes)


def test_regimes_macroeconomicos_diferentes_derrubam_o_fator():
    """Mesmo evento, mesma empresa, outro mundo: Selic de 13% para 2%, juros
    americanos de 0,25% para 5,5%, IPCA de 9% para 3%, dólar de 3,90 para
    5,60."""
    hoje = cenario(juros_br=2.0, juros_us=5.5, inflacao=3.0, cambio=5.60)
    s = sim.calcular(hoje, cenario())

    assert s.fator is not None
    assert s.fator < 85.0
    assert s.valor(sim.DIM_JUROS_BR) < 0.1     # 11 p.p. contra escala de 8
    assert s.valor(sim.DIM_JUROS_US) == 0.0    # 5,25 p.p. contra escala de 4
    assert s.valor(sim.DIM_INFLACAO) == 0.0    # 6 p.p. contra escala de 6
    # Câmbio compara em variação, não em nível: 5,60/3,90 - 1 = 43,6%.
    assert 0.10 < s.valor(sim.DIM_CAMBIO) < 0.15
    # O tipo do evento continua igual, então a comparação segue de pé.
    assert s.invalidantes == ()


def test_regime_irreconhecivel_invalida_a_comparacao():
    hoje = cenario(tipo_evento="recuperacao_judicial", juros_br=2.0,
                   juros_us=5.5, inflacao=1.0, cambio=6.5, commodity=140.0,
                   valuation=40.0, endividamento=8.0, expectativa_lucro=0.2,
                   liquidez=0.2, volatilidade=1.2,
                   politico_regulatorio="ruptura", situacao_setorial="crise")
    s = sim.calcular(hoje, cenario())

    assert s.fator < sim.SIMILARIDADE_INVALIDANTE
    assert any("tipo diferente" in x for x in s.invalidantes)
    assert any("nao comparaveis" in x for x in s.invalidantes)


def test_tipo_de_evento_diferente_invalida_mesmo_com_o_resto_identico():
    s = sim.calcular(cenario(tipo_evento="fusao"), cenario())
    assert s.valor(sim.DIM_TIPO_EVENTO) == 0.0
    assert any("tipo diferente" in x for x in s.invalidantes)
    # O fator continua alto -- e é exatamente por isso que o invalidante existe
    # separado dele: 80/100 de similaridade macro entre uma fusão e um resultado
    # trimestral não torna os dois comparáveis.
    assert s.fator > 60.0


def test_dimensao_ausente_sai_do_denominador_e_nao_e_creditada():
    """`memoria: medicao-que-pune-a-evidencia`: ausente é ausente, não zero."""
    parcial = cenario()
    for chave in (sim.DIM_CAMBIO, sim.DIM_COMMODITY, sim.DIM_VALUATION):
        parcial.pop(chave)

    s = sim.calcular(parcial, cenario())
    assert s.fator == 100.0          # o que foi medido bate perfeitamente
    assert s.cobertura < 1.0
    assert {d.chave for d in s.ausentes} == {
        sim.DIM_CAMBIO, sim.DIM_COMMODITY, sim.DIM_VALUATION}
    assert any("dimensoes nao comparadas (3 de 15)" in x for x in s.limitacoes)


def test_cobertura_abaixo_do_minimo_publica_mas_nao_ajusta():
    hoje = {sim.DIM_JUROS_BR: 13.0, sim.DIM_INFLACAO: 9.0}
    s = sim.calcular(hoje, {sim.DIM_JUROS_BR: 13.0, sim.DIM_INFLACAO: 9.0})
    assert s.fator == 100.0
    assert s.cobertura < sim.COBERTURA_MINIMA
    assert not s.utilizavel
    assert any("abaixo do minimo" in x for x in s.limitacoes)


def test_cenario_vazio_nao_produz_fator():
    s = sim.calcular({}, cenario())
    assert s.fator is None
    assert s.cobertura == 0.0
    assert not s.utilizavel
    assert s.invalidantes == ("cenario atual sem dados",)


def test_razao_indefinida_quando_a_referencia_historica_e_zero():
    s = sim.calcular(cenario(cambio=5.0), cenario(cambio=0.0))
    d = next(x for x in s.dimensoes if x.chave == sim.DIM_CAMBIO)
    assert not d.medida
    assert "razao indefinida" in d.motivo


def test_cenario_medio_usa_mediana_e_moda_com_desempate_alfabetico():
    cenarios = [
        cenario(juros_br=2.0, politico_regulatorio="calmo"),
        cenario(juros_br=13.0, politico_regulatorio="tenso"),
        cenario(juros_br=100.0, politico_regulatorio="calmo"),
    ]
    medio = sim.cenario_medio(cenarios)
    # Mediana, não média: o regime de 100% não desloca a referência.
    assert medio[sim.DIM_JUROS_BR] == 13.0
    assert medio[sim.DIM_POLITICO_REGULATORIO] == "calmo"

    # Empate categórico resolve pela ordem alfabética, não pela de chegada.
    empate = [cenario(politico_regulatorio="tenso"),
              cenario(politico_regulatorio="calmo")]
    assert (sim.cenario_medio(empate)[sim.DIM_POLITICO_REGULATORIO]
            == sim.cenario_medio(list(reversed(empate)))[
                sim.DIM_POLITICO_REGULATORIO]
            == "calmo")


def test_cenario_medio_vazio_devolve_dicionario_vazio():
    assert sim.cenario_medio([]) == {}
    assert sim.cenario_medio([{}, {}]) == {}


def test_pesos_calibrados_mudam_o_fator_e_ficam_declarados():
    hoje = cenario(juros_br=2.0)
    prior = sim.calcular(hoje, cenario())
    # Concentrar o peso nos juros brasileiros -- a dimensão que divergiu --
    # tem de derrubar o fator, senão o peso não está sendo usado.
    pesos = dict.fromkeys(sim.DIMENSOES, 0.01)
    pesos[sim.DIM_JUROS_BR] = 0.86
    calibrado = sim.calcular(hoje, cenario(), pesos=pesos,
                             pesos_calibrados=True)

    assert calibrado.fator < prior.fator
    assert calibrado.pesos_calibrados
    assert not any("nao calibrados" in x for x in calibrado.limitacoes)
    assert any("nao calibrados" in x for x in prior.limitacoes)


def test_texto_do_fator_traz_cobertura_e_nao_so_o_numero():
    s = sim.calcular(cenario(), cenario())
    texto = s.texto()
    assert "100" in texto
    assert "%" in texto
