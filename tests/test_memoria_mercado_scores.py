"""Dois scores separados, e o teto de ação que não inclui vender.

O requisito é explícito nas duas pontas: a carteira é formada **principalmente
pelo Score Estrutural**, e o Score Conjuntural *"não deverá liquidar
automaticamente posições"*. O primeiro teste deste arquivo existe para que a
segunda frase continue verdadeira depois de refatorações que ninguém desta
sessão vai revisar.

O último bloco liga a decisão em :mod:`core.aporte` e prova as duas coisas que
importam ali: sem os parâmetros novos o plano é bit a bit o de antes, e com um
bloqueio o dinheiro é redistribuído em vez de sumir.
"""
from __future__ import annotations

from core import aporte as ap
from core.memoria_mercado import estimativa as est
from core.memoria_mercado import scores as sc
from core.noticias.impacto import CONFIANCA_ALTA, CONFIANCA_BAIXA, CONFIANCA_MEDIA
from core.noticias.taxonomia import DIRECAO_BAIXA


def estrut(valor: float = 80.0) -> sc.ScoreEstrutural:
    return sc.estrutural(dict.fromkeys(sc.PESOS_ESTRUTURAIS_PRIOR, valor))


def conj(valor: float, *, experimental: bool = False,
         confianca: str = CONFIANCA_MEDIA) -> sc.ScoreConjuntural:
    return sc.conjuntural(dict.fromkeys(sc.PESOS_CONJUNTURAIS_PRIOR, valor),
                          experimental=experimental, confianca=confianca)


# ── a invariante: nada aqui vende ─────────────────────────────────────────────

def test_nenhuma_acao_permitida_reduz_posicao_existente():
    """Documentação executável da regra. Uma ação futura que vendesse teria de
    entrar em ``ACOES_QUE_REDUZEM_POSICAO``, e este teste quebraria."""
    assert sc.ACOES_QUE_REDUZEM_POSICAO == frozenset()
    proibidos = ("vend", "liquid", "zerar", "resgat", "reduzir_posicao",
                 "stop", "ordem")
    for acao in sc.ACOES:
        assert not any(p in acao for p in proibidos), acao
    assert sc.ACOES_QUE_BLOQUEIAM_APORTE <= set(sc.ACOES)


def test_nenhum_score_conjuntural_produz_decisao_que_altera_posicao():
    e = estrut()
    for valor in (-100.0, -80.0, -60.0, -25.0, 0.0, 25.0, 40.0, 100.0):
        d = sc.avaliar(e, conj(valor), simbolo="ATV", queda_recente=-0.30,
                       fundamentos_deteriorados=False)
        assert not d.altera_posicao_existente
        assert set(d.acoes) <= set(sc.ACOES)
        assert d.fator_prioridade >= sc.PRIORIDADE_MINIMA


def test_suspensao_bloqueia_dinheiro_novo_e_diz_que_a_posicao_fica():
    d = sc.avaliar(estrut(), conj(-80.0), simbolo="ATV")
    assert sc.SUSPENDER_APORTE in d.acoes
    assert d.bloqueia_aporte
    assert not d.altera_posicao_existente
    assert "posicao existente inalterada" in d.motivo
    # Bloqueio não é prioridade zero: os dois estados continuam distinguíveis.
    assert d.fator_prioridade == 1.0


# ── os dois scores são separados ──────────────────────────────────────────────

def test_as_duas_escalas_sao_diferentes_de_proposito():
    assert sc.estrutural(dict.fromkeys(sc.PESOS_ESTRUTURAIS_PRIOR, 500)).valor == 100.0
    assert sc.estrutural(dict.fromkeys(sc.PESOS_ESTRUTURAIS_PRIOR, -500)).valor == 0.0
    assert conj(500).valor == 100.0
    assert conj(-500).valor == -100.0


def test_score_estrutural_nao_muda_quando_a_conjuntura_muda():
    e = estrut(72.0)
    piorou = sc.avaliar(e, conj(-90.0), simbolo="ATV")
    melhorou = sc.avaliar(e, conj(+90.0), simbolo="ATV")
    assert piorou.score_estrutural == melhorou.score_estrutural == 72.0
    assert piorou.score_conjuntural != melhorou.score_conjuntural


def test_componente_ausente_sai_do_denominador_dos_dois_scores():
    e = sc.estrutural({"fundamentos": 80.0})
    assert e.valor == 80.0
    assert abs(e.cobertura - 0.30) < 1e-9
    assert not e.utilizavel                       # cobertura abaixo de 50%
    assert e.ausentes == ("valuation", "qualidade", "vantagem_competitiva",
                          "risco_longo_prazo")

    c = sc.conjuntural({"noticias": -40.0, "memoria_mercado": -60.0})
    assert abs(c.valor - (-40 * 0.35 - 60 * 0.30) / 0.65) < 0.01   # arredondado a 2 casas
    assert abs(c.cobertura - 0.65) < 1e-9
    assert c.utilizavel


def test_sem_nenhum_componente_o_score_nao_e_calculado():
    e = sc.estrutural({})
    assert e.valor is None and not e.utilizavel
    assert any("nenhum componente estrutural medido" in x for x in e.limitacoes)

    c = sc.conjuntural({})
    assert c.valor is None
    assert any("carteira segue apenas pelo score estrutural" in x
               for x in c.limitacoes)


def test_pesos_nao_calibrados_sao_declarados_como_prior():
    assert any("nao calibrados" in x for x in sc.estrutural({}).limitacoes)
    assert any("nao calibrados" in x for x in sc.conjuntural({}).limitacoes)
    calibrado = sc.estrutural(dict.fromkeys(sc.PESOS_ESTRUTURAIS_PRIOR, 70),
                              calibrado=True)
    assert calibrado.calibrado
    assert not any("nao calibrados" in x for x in calibrado.limitacoes)


# ── a Memória de Mercado virando componente ───────────────────────────────────

def falsa_estimativa(**kwargs) -> est.Estimativa:
    campos = dict(
        tipo_evento="resultado", simbolo="ATV", faixa=(-0.09, -0.03),
        valor_central=-0.06, horizonte=(5, 20), horizonte_base=20,
        direcao=DIRECAO_BAIXA, n_amostra=30, similaridade=80.0,
        confianca=CONFIANCA_ALTA, experimental=False, publicavel=True,
    )
    campos.update(kwargs)
    return est.Estimativa(**campos)


def test_estimativa_publicavel_vira_componente_com_sinal_preservado():
    v = sc.componente_de_estimativa(falsa_estimativa())
    assert v == -60.0        # -0,06 / 0,10 * 100, confiança alta
    assert sc.componente_de_estimativa(
        falsa_estimativa(valor_central=0.04)) == 40.0


def test_confianca_menor_encolhe_o_componente_em_vez_de_anula_lo():
    alta = sc.componente_de_estimativa(falsa_estimativa())
    media = sc.componente_de_estimativa(
        falsa_estimativa(confianca=CONFIANCA_MEDIA))
    baixa = sc.componente_de_estimativa(
        falsa_estimativa(confianca=CONFIANCA_BAIXA))
    assert abs(alta) > abs(media) > abs(baixa) > 0


def test_impacto_extremo_satura_e_nao_domina_o_score():
    assert sc.componente_de_estimativa(falsa_estimativa(valor_central=-0.90)) == -100.0


def test_estimativa_nao_publicavel_ou_invalidada_sai_do_denominador():
    """`memoria: medicao-que-pune-a-evidencia`: sem amostra, o componente é
    ausente -- não é zero, que seria "a memória diz que nada acontece"."""
    assert sc.componente_de_estimativa(None) is None
    assert sc.componente_de_estimativa(
        falsa_estimativa(publicavel=False, faixa=None, valor_central=None)) is None
    assert sc.componente_de_estimativa(
        falsa_estimativa(condicoes_invalidam=("amostra de um unico ativo",))) is None


# ── as cinco ações que a conjuntura pode tomar ────────────────────────────────

def test_conjuntura_sem_cobertura_nao_altera_prioridade():
    d = sc.avaliar(estrut(), sc.conjuntural({"noticias": -90.0}), simbolo="ATV")
    assert d.acoes == (sc.MANTER,)
    assert d.fator_prioridade == 1.0 and not d.bloqueia_aporte


def test_score_muito_negativo_suspende_observa_e_pede_reavaliacao():
    d = sc.avaliar(estrut(), conj(-70.0), simbolo="ATV")
    assert set(d.acoes) == {sc.SUSPENDER_APORTE, sc.OBSERVAR,
                            sc.REAVALIAR_FUNDAMENTOS}


def test_score_negativo_moderado_reduz_prioridade_e_observa():
    d = sc.avaliar(estrut(), conj(-30.0), simbolo="ATV")
    assert set(d.acoes) == {sc.REDUZIR_PRIORIDADE_APORTE, sc.OBSERVAR}
    assert d.fator_prioridade == 0.85
    assert not d.bloqueia_aporte


def test_ruido_nao_mexe_em_nada():
    d = sc.avaliar(estrut(), conj(10.0), simbolo="ATV")
    assert d.acoes == (sc.MANTER,)
    assert d.fator_prioridade == 1.05


def test_score_positivo_aumenta_prioridade_de_aporte():
    d = sc.avaliar(estrut(), conj(30.0), simbolo="ATV")
    assert d.acoes == (sc.PRIORIZAR_APORTE,)
    assert d.fator_prioridade == 1.15


def test_queda_com_fundamentos_verificados_libera_aporte_gradual():
    d = sc.avaliar(estrut(80.0), conj(50.0), simbolo="ATV",
                   queda_recente=-0.18, fundamentos_deteriorados=False)
    assert d.acoes == (sc.OPORTUNIDADE_GRADUAL,)
    assert "sem compra de uma vez" in d.motivo
    assert not d.bloqueia_aporte


def test_queda_sem_verificacao_de_fundamentos_so_observa():
    """`memoria: fallback-nunca-contradiz`: ``None`` não aprova. Comprar na
    queda de uma tese que se deteriorou é o modo caro de errar."""
    d = sc.avaliar(estrut(80.0), conj(50.0), simbolo="ATV",
                   queda_recente=-0.18, fundamentos_deteriorados=None)
    assert d.acoes == (sc.OBSERVAR,)
    assert any("verificacao explicita" in x for x in d.limitacoes)


def test_queda_em_empresa_fraca_nao_vira_oportunidade():
    d = sc.avaliar(estrut(40.0), conj(50.0), simbolo="ATV",
                   queda_recente=-0.30, fundamentos_deteriorados=False)
    assert sc.OPORTUNIDADE_GRADUAL not in d.acoes
    assert d.acoes == (sc.PRIORIZAR_APORTE,)


def test_fundamentos_deteriorados_tambem_fecham_a_porta_da_oportunidade():
    d = sc.avaliar(estrut(80.0), conj(50.0), simbolo="ATV",
                   queda_recente=-0.30, fundamentos_deteriorados=True)
    assert sc.OPORTUNIDADE_GRADUAL not in d.acoes


def test_estimativa_invalidada_com_direcao_definida_pede_observacao():
    d = sc.avaliar(estrut(), conj(10.0), simbolo="ATV",
                   estimativa=falsa_estimativa(
                       condicoes_invalidam=("cenarios nao comparaveis",)))
    assert sc.OBSERVAR in d.acoes
    assert "cenarios nao comparaveis" in d.limitacoes


def test_decisao_experimental_carrega_o_aviso():
    d = sc.avaliar(estrut(), conj(30.0, experimental=True), simbolo="ATV")
    assert any("experimental" in x for x in d.limitacoes)


# ── travessia para o plano de aporte ──────────────────────────────────────────

def test_para_aporte_separa_bloqueio_de_prioridade():
    decisoes = [
        sc.avaliar(estrut(), conj(-80.0), simbolo="RUIM"),
        sc.avaliar(estrut(), conj(30.0), simbolo="BOA"),
        sc.avaliar(estrut(), conj(0.0), simbolo="NEUTRA"),
    ]
    bloqueios, prioridades = sc.para_aporte(decisoes)
    assert set(bloqueios) == {"RUIM"}
    assert set(prioridades) == {"BOA"}      # neutra fica de fora, fator 1,0
    assert prioridades["BOA"] == 1.15
    assert "RUIM" not in prioridades        # bloqueio não vira prioridade


def test_sem_os_parametros_novos_o_plano_e_identico_ao_de_antes():
    atuais = {"A": 1000.0, "B": 1000.0, "C": 1000.0}
    alvos = {"A": 1 / 3, "B": 1 / 3, "C": 1 / 3}
    antes = ap.plano_de_aporte(atuais, alvos, 900.0)
    depois = ap.plano_de_aporte(atuais, alvos, 900.0,
                                bloqueios_conjunturais={}, prioridades={})
    assert antes == depois
    assert all(a.valor_aportado == 300.0 for a in antes.alocacoes)


def test_bloqueio_conjuntural_redistribui_e_nao_vende_nada():
    # C acima do alvo, A e B abaixo: so A e B disputam o dinheiro novo, e a
    # soma dos deficits deles (1.400) supera o aporte -- e nessa condicao que
    # bloquear um muda o que o outro recebe.
    atuais = {"A": 500.0, "B": 500.0, "C": 2000.0}
    alvos = {"A": 1 / 3, "B": 1 / 3, "C": 1 / 3}
    sem_bloqueio = ap.plano_de_aporte(atuais, alvos, 600.0)
    assert {a.symbol: a.valor_aportado for a in sem_bloqueio.alocacoes} == {
        "A": 300.0, "B": 300.0, "C": 0.0}

    decisoes = [sc.avaliar(estrut(), conj(-80.0), simbolo="B")]
    bloqueios, prioridades = sc.para_aporte(decisoes)
    plano = ap.plano_de_aporte(atuais, alvos, 600.0,
                               bloqueios_conjunturais=bloqueios,
                               prioridades=prioridades)
    por_ticker = {a.symbol: a for a in plano.alocacoes}

    assert por_ticker["B"].valor_aportado == 0.0
    assert por_ticker["B"].bloqueado
    assert "aporte NOVO suspenso" in por_ticker["B"].motivo_bloqueio
    # Os 300 de B foram para A. Nada foi vendido e nada evaporou.
    assert por_ticker["A"].valor_aportado == 600.0
    assert plano.sobra == 0.0
    assert all(a.valor_aportado >= 0 for a in plano.alocacoes)
    assert por_ticker["B"].valor_atual == 500.0


def test_prioridade_reordena_sem_bloquear_ninguem():
    atuais = {"A": 500.0, "B": 500.0, "C": 2000.0}
    alvos = {"A": 1 / 3, "B": 1 / 3, "C": 1 / 3}
    decisoes = [sc.avaliar(estrut(), conj(60.0), simbolo="A")]
    bloqueios, prioridades = sc.para_aporte(decisoes)
    assert not bloqueios and prioridades == {"A": 1.30}

    plano = ap.plano_de_aporte(atuais, alvos, 600.0, prioridades=prioridades)
    por_ticker = {a.symbol: a for a in plano.alocacoes}
    # A recebe mais que B, mas B continua recebendo: prioridade reordena, nao
    # exclui. Excluir e a outra operacao, com outro parametro.
    assert por_ticker["A"].valor_aportado > por_ticker["B"].valor_aportado > 0
    assert not plano.bloqueadas
    assert abs(sum(a.valor_aportado for a in plano.alocacoes) - 600.0) < 1e-6
