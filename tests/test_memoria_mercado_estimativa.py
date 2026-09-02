"""Faixa, valor central, horizonte, direção, confiança -- e o que os invalida.

Cenário pedido coberto aqui: **notícia já precificada**. Junto vêm as duas
propriedades que o requisito exige explicitamente: o resultado não é uma
promessa pontual, e base histórica insuficiente não produz falsa precisão --
produz recusa declarada.

O último bloco cobre a travessia para o Motor Conjuntural de notícias, onde mora
a conversão de fração para pontos percentuais.
"""
from __future__ import annotations

from functools import lru_cache

from core.memoria_mercado import amostra as am
from core.memoria_mercado import estimativa as est
from core.memoria_mercado import ponte_noticias as ponte
from core.memoria_mercado import similaridade as sim
from core.noticias.impacto import (
    CONFIANCA_ALTA,
    CONFIANCA_BAIXA,
    N_MINIMO_BASE,
)
from core.noticias.taxonomia import (
    DIRECAO_BAIXA,
    DIRECAO_INDEFINIDA,
    DIRECAO_NEUTRA,
)
from tests.apoio_memoria import cenario, dias_uteis, evento, indice_plano, painel


@lru_cache(maxsize=None)
def amostra(n: int = 30, *, horizonte: int = 20, com_indice: bool = True,
            passo: int = 40) -> am.AmostraHistorica:
    """Amostra de `n` eventos de queda. Em cache: `painel` mede o painel
    inteiro e este arquivo o reutiliza em quase todo teste."""
    return am.resumir(painel(n, reacao=-0.06, dispersao=0.03,
                             com_indice=com_indice, passo=passo),
                      tipo_evento="resultado", horizonte=horizonte)


def identica() -> sim.Similaridade:
    return sim.calcular(cenario(), cenario())


# ── a forma da saída ─────────────────────────────────────────────────────────

def test_saida_traz_os_onze_campos_do_requisito():
    e = est.estimar(amostra(), identica(), simbolo="ATV00")

    assert e.faixa is not None                     # faixa provável de impacto
    assert e.valor_central is not None             # valor central estimado
    assert e.horizonte is not None                 # horizonte
    assert e.direcao == DIRECAO_BAIXA              # direção
    assert e.n_amostra == 30                       # tamanho da amostra
    assert e.similaridade == 100.0                 # similaridade
    assert e.confianca in ("alta", "media", "baixa")   # confiança
    assert e.fatores_ampliam or e.fatores_reduzem  # fatores dos dois lados
    assert isinstance(e.condicoes_invalidam, tuple)   # condições invalidantes
    assert e.intervalo_historico is not None
    assert e.mediana_historica is not None


def test_o_resultado_e_faixa_e_nao_promessa_pontual():
    e = est.estimar(amostra(), identica())
    lo, hi = e.faixa
    assert lo < hi
    assert lo <= e.valor_central <= hi
    # A largura não é decorativa: vem de p25-p75 observados, alargados pela
    # incerteza da amostra.
    assert (hi - lo) > 0.005
    assert e.detalhes["alargamento"] > 1.0


def test_amostra_menor_alarga_mais_a_faixa():
    pequena = est.estimar(amostra(10), identica())
    grande = est.estimar(amostra(30), identica())
    assert pequena.detalhes["alargamento"] > grande.detalhes["alargamento"]
    assert pequena.experimental and not grande.experimental


def test_horizonte_sai_como_intervalo_de_pregoes_dentro_da_janela():
    e = est.estimar(amostra(), identica())
    lo, hi = e.horizonte
    assert 1 <= lo < hi <= e.horizonte_base == 20


# ── base histórica insuficiente ───────────────────────────────────────────────

def test_amostra_insuficiente_recusa_faixa_em_vez_de_inventar_precisao():
    """Cenário pedido: sem base histórica suficiente, informar a ausência --
    não publicar um número estreito."""
    e = est.estimar(amostra(5), identica())

    assert not e.publicavel
    assert e.faixa is None and e.valor_central is None and e.horizonte is None
    assert e.direcao == DIRECAO_INDEFINIDA
    assert e.confianca == CONFIANCA_BAIXA
    assert e.experimental
    assert not e.acionavel
    assert any(f"abaixo do minimo de {am.N_MINIMO_EXPERIMENTAL}" in x
               for x in e.limitacoes)
    assert "amostra insuficiente" in e.texto()


def test_amostra_no_piso_publica_marcada_como_experimental():
    e = est.estimar(amostra(8), identica())
    assert e.publicavel and e.experimental
    assert any("EXPERIMENTAL" in x for x in e.limitacoes)
    assert "(experimental)" in e.texto()


# ── notícia já precificada ────────────────────────────────────────────────────

def test_parcela_ja_precificada_encolhe_o_impacto_proporcionalmente():
    """Cenário pedido. 80% já no preço deixa 20% do movimento histórico."""
    cheia = est.estimar(amostra(), identica(), parcela_ja_precificada=0.0)
    quase = est.estimar(amostra(), identica(), parcela_ja_precificada=0.8)

    assert abs(quase.valor_central) < abs(cheia.valor_central)
    assert abs(quase.valor_central - cheia.valor_central * 0.2) < 1e-6
    assert quase.detalhes["desconto_ja_precificado"] == 0.2
    assert any("ja refletida no preco" in x for x in quase.fatores_reduzem)


def test_informacao_toda_precificada_derruba_a_confianca_ate_o_piso():
    """Uma amostra que sozinha daria confiança alta não dá quando o mercado já
    sabia: o que resta para reagir é pequeno, e a estimativa fica frágil."""
    alta = est.estimar(amostra(), identica(), parcela_ja_precificada=0.0)
    assert alta.confianca == CONFIANCA_ALTA

    precificada = est.estimar(amostra(), identica(), parcela_ja_precificada=0.85)
    assert precificada.confianca == CONFIANCA_BAIXA
    assert precificada.n_amostra == alta.n_amostra   # a amostra não mudou


def test_precificacao_total_zera_o_central_e_a_direcao_vira_neutra():
    e = est.estimar(amostra(), identica(), parcela_ja_precificada=1.0)
    assert e.valor_central == 0.0
    assert e.direcao == DIRECAO_NEUTRA
    assert not e.acionavel      # sem direção não há prioridade a ajustar


def test_parcela_e_lida_do_cenario_quando_nao_vem_explicita():
    s = sim.calcular(cenario(parcela_ja_precificada=0.30), cenario())
    e = est.estimar(amostra(), s)
    assert e.parcela_ja_precificada == 0.30


def test_parcela_explicita_ganha_da_dimensao_do_cenario():
    s = sim.calcular(cenario(parcela_ja_precificada=0.30), cenario())
    e = est.estimar(amostra(), s, parcela_ja_precificada=0.90)
    assert e.parcela_ja_precificada == 0.90


# ── similaridade entrando na conta ────────────────────────────────────────────

def test_cenario_menos_parecido_atenua_a_referencia_historica():
    hoje = cenario(juros_br=2.0, juros_us=5.5, inflacao=3.0, cambio=5.60,
                   valuation=20.0, endividamento=5.0)
    distante = sim.calcular(hoje, cenario())
    assert 25.0 < distante.fator < 100.0

    a = est.estimar(amostra(), identica())
    b = est.estimar(amostra(), distante)
    assert abs(b.valor_central) < abs(a.valor_central)
    # E nunca até zero: cenário pouco parecido não é evidência de reação nula.
    assert abs(b.valor_central) >= abs(a.valor_central) * est.ATENUACAO_PISO


def test_sem_similaridade_assume_o_meio_e_declara_a_omissao():
    e = est.estimar(amostra(), None)
    assert e.similaridade is None
    assert e.detalhes["atenuacao_similaridade"] == 0.75   # 0,50 + 0,50 * 0,50
    assert any("similaridade neutra" in x for x in e.limitacoes)


def test_tipo_de_evento_diferente_invalida_a_estimativa_publicada():
    s = sim.calcular(cenario(tipo_evento="fusao"), cenario())
    e = est.estimar(amostra(), s)
    assert e.publicavel            # a faixa sai...
    assert e.condicoes_invalidam   # ...mas com a condição que a desautoriza
    assert not e.acionavel


# ── condições invalidantes próprias da amostra ────────────────────────────────

def test_amostra_de_um_unico_ativo_e_historico_nao_padrao_de_mercado():
    dias = dias_uteis(1200)
    idx = indice_plano(dias)
    eventos = [evento("ATV", reacao=-0.06, dias=dias, offset=200 + i * 60,
                      indice=idx, chave=f"k{i}") for i in range(9)]
    e = est.estimar(am.resumir(eventos, tipo_evento="resultado", horizonte=20),
                    identica())
    assert any("mesmo ativo" in x for x in e.condicoes_invalidam)
    assert not e.acionavel


def test_amostra_concentrada_em_menos_de_doze_meses_e_um_regime_so():
    e = est.estimar(amostra(8, passo=5), identica())
    assert any("menos de 12 meses" in x for x in e.condicoes_invalidam)


def test_amostra_em_retorno_bruto_declara_a_troca_de_base():
    e = est.estimar(amostra(30, com_indice=False), identica())
    assert e.base_retorno == "bruto"
    assert e.confianca != CONFIANCA_ALTA
    assert any("retorno bruto" in x for x in e.fatores_reduzem)


def test_texto_segue_o_formato_do_exemplo_conceitual():
    t = est.estimar(amostra(), identica()).texto()
    for pedaco in ("eventos comparaveis:", "reacao historica mediana:",
                   "intervalo historico:", "similaridade atual:",
                   "impacto atual estimado:", "horizonte:", "confianca:"):
        assert pedaco in t


# ── ponte com o Motor Conjuntural de notícias ─────────────────────────────────

def test_ponte_converte_fracao_para_pontos_percentuais():
    """`memoria: defeito-silencioso-vs-erro`: fração onde se espera porcentagem
    publica -0,1% no lugar de -6,4% sem erro nenhum."""
    a = amostra()
    base = ponte.para_base_historica(a)
    assert base is not None
    assert abs(base.p10 - a.principal.p10 * 100.0) < 1e-9
    assert abs(base.p90 - a.principal.p90 * 100.0) < 1e-9
    assert base.limiar_relevante == 3.0          # 0,03 em fração
    assert base.p10 < -1.0                       # pontos percentuais, não fração
    assert base.n_observacoes == a.n_eventos
    assert base.fonte == "memoria_mercado:retorno_anormal"


def test_ponte_nao_forca_a_passagem_do_piso_do_outro_lado():
    a = amostra(12)
    base = ponte.para_base_historica(a)
    assert base.n_observacoes == 12
    assert not base.suficiente          # o motor de notícias exige 30
    assert N_MINIMO_BASE > am.N_MINIMO_EXPERIMENTAL
    assert any("insuficiente para o motor de noticias" in x
               for x in ponte.descrever(a, base))


def test_ponte_sem_eventos_devolve_none_e_nao_base_zerada():
    vazia = am.resumir([], tipo_evento="resultado", horizonte=20)
    assert ponte.para_base_historica(vazia) is None
    assert any("sem eventos comparaveis" in x
               for x in ponte.descrever(vazia, None))


def test_ponte_marca_a_procedencia_do_retorno_bruto():
    a = amostra(30, com_indice=False)
    base = ponte.para_base_historica(a)
    assert base.fonte == "memoria_mercado:retorno_bruto"
    assert any("retorno bruto" in x for x in ponte.descrever(a, base))
