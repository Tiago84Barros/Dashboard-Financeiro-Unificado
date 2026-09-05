"""Índice de Relevância e estimativa de impacto.

Cobre os cenários exigidos "notícia antiga", "fonte não confiável", "notícia
sem ticker", "ausência de carteira" e "baixa e alta relevância" -- e a regra
que o usuário escreveu com todas as letras: nada de "impacto de 72%". As
quatro dimensões (direção, probabilidade, magnitude e confiança) viajam em
campos distintos e são afirmadas separadamente aqui.

O eixo silencioso de quase todo teste deste arquivo é a doutrina do repositório
de que **ausente não é zero**. Nota sem data, nota sem ticker e nota sem
carteira não são notas baixas: são notas com cobertura menor, e a diferença
aparece na ``cobertura`` e nas ``limitacoes``, nunca disfarçada de 0,0.
"""
from __future__ import annotations

import dataclasses

import pytest

from core.noticias import (
    fontes,
    frescor_noticias,
    impacto,
    modelos,
    relevancia,
    taxonomia,
)
from tests.apoio_noticias import AGORA, noticia, quando

# ── fixtures sintéticas ─────────────────────────────────────────────────────
# Domínios .test e blogspot inventados; nenhum endereço real é consultado.


def _alta():
    return noticia("Fato relevante: Alfa comunica acordo de fusao com a Beta",
                   "https://www.cvm.gov.br/doc/9", tickers=("ALFA3",),
                   publicado_em=quando(1))


def _agencia(url="https://www.reuters.com/n/1", horas=2.0):
    return noticia("Alfa anuncia aquisicao da Beta por dois bilhoes de reais",
                   url, tickers=("ALFA3",), publicado_em=quando(horas))


def _blog(horas=2.0):
    return noticia("Alfa anuncia aquisicao da Beta por dois bilhoes de reais",
                   "https://algumblog.blogspot.com/n/1", tickers=("ALFA3",),
                   publicado_em=quando(horas))


def _sem_ticker():
    return noticia("Boletim semanal comenta o humor do mercado",
                   "https://boletim-exemplo-ficticio.test/x/1",
                   publicado_em=quando(2))


def _antiga():
    return noticia("Cinco acoes baratas para ficar de olho neste mes",
                   "https://algumblog.blogspot.com/p/1",
                   publicado_em=quando(24 * 20))


def _calc(n, **kw):
    return relevancia.calcular(n, agora=AGORA, **kw)


# ── pesos: a nota é configurável, e a configuração é auditável ──────────────

def test_os_pesos_padrao_sao_os_pedidos_e_somam_cem_por_cento():
    assert relevancia.PESOS_PADRAO.como_dicionario() == {
        relevancia.MATERIALIDADE: 0.25,
        relevancia.RELACAO_ATIVO: 0.20,
        relevancia.CONFIABILIDADE: 0.15,
        relevancia.NOVIDADE: 0.10,
        relevancia.CONFIRMACAO: 0.10,
        relevancia.PERSISTENCIA: 0.10,
        relevancia.EXPOSICAO: 0.10,
    }
    assert relevancia.PESOS_PADRAO.total == pytest.approx(1.0)
    assert relevancia.PESOS_PADRAO.validar() == []


def test_configuracao_torta_avisa_em_vez_de_explodir():
    """Quem configura decide; o motor só não pode fingir que soma 1."""
    torto = dataclasses.replace(relevancia.PESOS_PADRAO, materialidade=0.50)
    avisos = torto.validar()
    assert avisos and "nao 1,000" in avisos[0]


def test_mudar_o_peso_muda_a_nota():
    n = _agencia()
    padrao = _calc(n).nota
    so_confiabilidade = relevancia.Pesos(
        materialidade=0.0, relacao_ativo=0.0, confiabilidade=1.0,
        novidade=0.0, confirmacao=0.0, persistencia=0.0, exposicao=0.0)
    assert _calc(n, pesos=so_confiabilidade).nota != padrao


# ── faixas ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("nota", "faixa"), [
    (0.0, taxonomia.FAIXA_INFORMATIVA),
    (59.9, taxonomia.FAIXA_INFORMATIVA),
    (60.0, taxonomia.FAIXA_OBSERVACAO),
    (79.9, taxonomia.FAIXA_OBSERVACAO),
    (80.0, taxonomia.FAIXA_REVISAO),
    (100.0, taxonomia.FAIXA_REVISAO),
])
def test_os_limiares_das_faixas_sao_60_e_80(nota, faixa):
    assert relevancia._faixa(nota) == faixa


# ── baixa e alta relevância ─────────────────────────────────────────────────

def test_alta_relevancia_com_tudo_medido():
    r = _calc(_alta(), confirmado_por_primaria=True, tickers_alvo=("ALFA3",),
              exposicao_carteira=0.4)
    assert r.nota == pytest.approx(90.5)
    assert r.faixa == taxonomia.FAIXA_REVISAO
    assert r.cobertura == pytest.approx(1.0)
    assert r.limitacoes == ()
    assert not r.nao_medidos


def test_baixa_relevancia_de_materia_generica_e_velha():
    r = _calc(_antiga())
    assert r.nota == pytest.approx(22.5)
    assert r.faixa == taxonomia.FAIXA_INFORMATIVA
    assert r.componentes[relevancia.NOVIDADE] == pytest.approx(0.05)


# ── notícia antiga ──────────────────────────────────────────────────────────

def test_noticia_antiga_e_rotulada_como_antiga_e_perde_novidade():
    """Requisito literal: nunca apresentar notícia antiga como se fosse atual."""
    velha = _antiga()
    texto, recente = frescor_noticias.rotular_idade(velha, agora=AGORA)
    assert recente is False
    assert "ANTIGA" in texto
    assert "12/08/2026" in texto and "UTC" in texto

    nova = _agencia(horas=1.0)
    assert (_calc(nova).componentes[relevancia.NOVIDADE]
            > _calc(velha).componentes[relevancia.NOVIDADE])


def test_noticia_sem_data_nao_vira_noticia_velha_nem_noticia_nova():
    """Sem carimbo da fonte a resposta é "não sei" -- não é 0 nem é agora."""
    sem_data = noticia("Alfa apresenta numeros do trimestre a analistas",
                       "https://www.reuters.com/n/2", tickers=("ALFA3",))
    assert sem_data.publicado_em is None
    assert sem_data.idade_em_minutos(AGORA) is None

    texto, recente = frescor_noticias.rotular_idade(sem_data, agora=AGORA)
    assert recente is None
    assert texto == "sem data de publicacao informada pela fonte"

    r = _calc(sem_data)
    assert r.componentes[relevancia.NOVIDADE] is None
    assert relevancia.NOVIDADE in r.nao_medidos
    assert ("sem data de publicacao: novidade nao entrou na nota"
            in r.limitacoes)
    assert r.cobertura < 1.0


# ── fonte não confiável ─────────────────────────────────────────────────────

def test_fonte_nao_confiavel_pontua_menos_que_agencia_na_mesma_materia():
    ag = _calc(_agencia())
    bl = _calc(_blog())

    assert bl.nota < ag.nota
    assert bl.componentes[relevancia.CONFIABILIDADE] == pytest.approx(0.20)
    assert ag.componentes[relevancia.CONFIABILIDADE] == pytest.approx(0.90)

    # Mesma matéria: só a confiabilidade pode divergir. Se outra coisa mudar,
    # a comparação não está medindo o que diz medir.
    diferentes = {k for k in ag.componentes
                  if ag.componentes[k] != bl.componentes[k]}
    assert diferentes == {relevancia.CONFIABILIDADE}


def test_dominio_fora_do_catalogo_nao_ganha_confianca_por_omissao():
    n = _sem_ticker()
    assert n.fonte.classe == fontes.CLASSE_DESCONHECIDA
    assert n.fonte.confiabilidade == pytest.approx(0.20)


def test_replicacao_e_apuracao_independente_valem_diferente():
    n = _agencia()
    sozinha = _calc(n, n_fontes_independentes=1)
    duas = _calc(n, n_fontes_independentes=2)
    tres = _calc(n, n_fontes_independentes=3)
    primaria = _calc(n, confirmado_por_primaria=True)

    notas = [c.componentes[relevancia.CONFIRMACAO]
             for c in (sozinha, duas, tres, primaria)]
    assert notas == sorted(notas), notas
    assert notas[0] < notas[-1]
    assert primaria.componentes[relevancia.CONFIRMACAO] == pytest.approx(1.0)


# ── notícia sem ticker ──────────────────────────────────────────────────────

def test_noticia_sem_ticker_nao_recebe_relacao_zero():
    r = _calc(_sem_ticker())
    assert r.componentes[relevancia.RELACAO_ATIVO] is None
    assert relevancia.RELACAO_ATIVO in r.nao_medidos
    assert r.cobertura == pytest.approx(0.70)
    assert r.faixa == taxonomia.FAIXA_INFORMATIVA


def test_noticia_sem_ticker_mas_com_macro_tem_relacao_medida():
    """Sem ticker não é sem relação. Macro relacionado é valor observado."""
    macro = noticia("Banco Central eleva a taxa Selic em reuniao do Copom",
                    "https://valor.globo.com/macro/1", publicado_em=quando(2))
    assert macro.entidades.tickers == ()
    assert "selic" in macro.entidades.ativos
    r = _calc(macro)
    assert r.componentes[relevancia.RELACAO_ATIVO] == pytest.approx(0.35)


def test_o_pais_do_veiculo_nao_paga_bonus_de_relacao_macro():
    """A procedência saiu de ``entidades``, e este teste é o motivo (A-145).

    Enquanto o país do veículo entrava em ``entidades.paises``, qualquer
    matéria de qualquer jornal chegava aqui com um país -- e ``_relacao``
    devolve 0,35 para ``paises or moedas or ativos``. O bônus de vínculo macro
    era pago pela nacionalidade de quem publicou, não pelo assunto da matéria.
    O mesmo país falso ia para ``exposicao`` como exposição da carteira e para
    o agrupamento por evento como chave de último recurso.
    """
    from core.noticias import fontes

    generica = noticia("Diretoria aprova mudanca no calendario de reunioes",
                       "https://www.reuters.com/geral/1", publicado_em=quando(2))

    assert fontes.classificar("https://www.reuters.com/geral/1").pais == "GB", (
        "cenario invalido: a fonte perdeu o pais, e ai o teste passa sozinho")
    assert generica.pais == "GB", "a procedencia deixou de ser registrada"
    assert generica.entidades.paises == (), (
        "o pais do veiculo voltou a entrar nas entidades do fato")
    r = _calc(generica)
    assert r.componentes[relevancia.RELACAO_ATIVO] is None, (
        "sem ticker e sem entidade macro, a relacao e 'nao medida'; o 0,35 "
        "que saia antes era o pais do veiculo")
    assert relevancia.RELACAO_ATIVO in r.nao_medidos


# ── ausência de carteira ────────────────────────────────────────────────────

def test_sem_carteira_a_exposicao_fica_ausente_e_nao_zerada():
    r = _calc(_agencia())
    assert r.componentes[relevancia.EXPOSICAO] is None
    assert ("sem carteira cadastrada: exposicao nao entrou na nota"
            in r.limitacoes)
    assert r.cobertura == pytest.approx(0.90)


def test_com_carteira_sem_o_ativo_a_exposicao_e_zero_medido():
    """Aqui 0,0 é legítimo: existe carteira, e a exposição a ela é nenhuma."""
    r = _calc(_agencia(), tickers_alvo=("PETR4",), exposicao_carteira=0.0)
    assert r.componentes[relevancia.EXPOSICAO] == pytest.approx(0.0)
    assert relevancia.EXPOSICAO in r.medidos
    assert not any("carteira cadastrada" in lim for lim in r.limitacoes)


def test_exposicao_maior_puxa_a_nota_para_cima():
    n = _agencia()
    pouco = _calc(n, tickers_alvo=("ALFA3",), exposicao_carteira=0.05)
    muito = _calc(n, tickers_alvo=("ALFA3",), exposicao_carteira=0.60)
    assert muito.nota > pouco.nota


# ── cobertura: nota alta apurada com pouca evidência não vira revisão ───────

def test_nota_alta_com_cobertura_baixa_cai_para_observacao():
    crua = dataclasses.replace(_alta(), publicado_em=None,
                               entidades=modelos.Entidades())
    r = _calc(crua, confirmado_por_primaria=True)

    assert r.nota >= 80.0
    assert r.cobertura == pytest.approx(0.60)
    assert r.faixa == taxonomia.FAIXA_OBSERVACAO, (
        "nota de 94 apurada sobre 60% dos criterios nao pode abrir revisao")
    assert any("abaixo do minimo" in lim for lim in r.limitacoes)


def test_o_minimo_de_cobertura_e_configuravel():
    r = _calc(_alta(), cobertura_minima=0.95)
    assert r.nota >= 80.0
    assert r.cobertura == pytest.approx(0.90)
    assert r.faixa == taxonomia.FAIXA_OBSERVACAO
    assert any("minimo de 95%" in lim for lim in r.limitacoes)


def test_a_cobertura_e_publicada_por_extenso():
    r = _calc(_sem_ticker())
    texto = r.texto_cobertura()
    assert "70%" in texto
    assert relevancia.ROTULO_COMPONENTE[relevancia.RELACAO_ATIVO] in texto


# ── impacto: nada de "impacto de 72%" ───────────────────────────────────────

_SENT_POSITIVO = modelos.Sentimento(valor_api=0.8, valor_app4=0.7,
                                    rotulo_api="Bullish", metodo_app4="teste")
_SENT_FRACO = modelos.Sentimento(valor_api=None, valor_app4=0.05,
                                 rotulo_api=None, metodo_app4="teste")


def _base(n_observacoes: int) -> impacto.BaseHistorica:
    return impacto.BaseHistorica(
        tipo_evento="fusao_aquisicao",
        n_observacoes=n_observacoes,
        limiar_relevante=3.0,
        horizonte=taxonomia.HORIZONTE_CURTO,
        prob_movimento_relevante=0.62,
        p10=-2.5,
        p90=9.0,
        fonte="base sintetica de teste",
        janela="2015-2025",
    )


def test_sem_base_historica_nao_ha_numero_nenhum():
    i = impacto.estimar(tipo_evento="fusao_aquisicao",
                        sentimento=_SENT_POSITIVO, confiabilidade_fonte=1.0,
                        estado_verificacao=taxonomia.VERIF_FONTE_PRIMARIA,
                        cobertura_relevancia=0.9)
    assert i.probabilidade is None
    assert i.faixa is None
    assert i.tem_base_estatistica is False
    assert "sem base estatistica suficiente" in i.texto()
    # Nenhum percentual na frase: sem base observada não há o que percentualizar.
    assert "%" not in i.texto()


def test_base_pequena_e_recusada_mesmo_trazendo_probabilidade():
    """A base de 10 eventos traz prob=0,62. Publicar isso seria inventar."""
    pequena = _base(10)
    assert pequena.n_observacoes < impacto.N_MINIMO_BASE
    assert pequena.suficiente is False

    i = impacto.estimar(tipo_evento="fusao_aquisicao",
                        sentimento=_SENT_POSITIVO, base=pequena,
                        confiabilidade_fonte=1.0, cobertura_relevancia=0.9)
    assert i.probabilidade is None
    assert i.faixa is None


def test_base_suficiente_publica_as_quatro_dimensoes_separadas():
    i = impacto.estimar(tipo_evento="fusao_aquisicao",
                        sentimento=_SENT_POSITIVO, base=_base(48),
                        confiabilidade_fonte=1.0,
                        estado_verificacao=taxonomia.VERIF_FONTE_PRIMARIA,
                        cobertura_relevancia=0.9)

    assert i.direcao == taxonomia.DIRECAO_ALTA
    assert i.probabilidade == pytest.approx(0.62)
    assert (i.faixa.minimo, i.faixa.maximo, i.faixa.unidade) == (-2.5, 9.0, "%")
    assert i.horizonte == taxonomia.HORIZONTE_CURTO
    assert i.grau_confianca == impacto.CONFIANCA_ALTA

    # Probabilidade e confiança são números diferentes e não se confundem.
    assert i.probabilidade != i.confianca

    texto = i.texto()
    assert ("probabilidade estimada de variacao acima de 3.0% no horizonte "
            "curto: 62%") in texto
    assert "faixa provavel de -2.5% a +9.0%" in texto
    assert "base de 48 eventos comparaveis" in texto
    assert "grau de confianca da analise: alta" in texto


@pytest.mark.parametrize(("sentimento", "esperada"), [
    (_SENT_POSITIVO, taxonomia.DIRECAO_ALTA),
    (_SENT_FRACO, taxonomia.DIRECAO_NEUTRA),
    (None, taxonomia.DIRECAO_INDEFINIDA),
])
def test_direcao_sai_do_sentimento_mas_ruido_nao_vira_direcao(sentimento,
                                                              esperada):
    i = impacto.estimar(tipo_evento="resultado_trimestral",
                        sentimento=sentimento, confiabilidade_fonte=0.9,
                        cobertura_relevancia=0.9)
    assert i.direcao == esperada


def test_o_fato_manda_mais_que_o_tom_do_texto():
    """Recuperação judicial não vira boa notícia por o texto soar elogioso."""
    i = impacto.estimar(tipo_evento="recuperacao_judicial",
                        sentimento=_SENT_POSITIVO, confiabilidade_fonte=0.9,
                        estado_verificacao=taxonomia.VERIF_INDEPENDENTE,
                        cobertura_relevancia=0.9)
    assert i.direcao == taxonomia.DIRECAO_BAIXA


def test_confianca_acompanha_a_qualidade_do_que_sustenta_a_analise():
    fraca = impacto.estimar(tipo_evento="resultado_trimestral",
                            sentimento=_SENT_POSITIVO,
                            confiabilidade_fonte=0.2,
                            estado_verificacao=taxonomia.VERIF_NAO_VERIFICADA,
                            cobertura_relevancia=0.5)
    forte = impacto.estimar(tipo_evento="resultado_trimestral",
                            sentimento=_SENT_POSITIVO,
                            confiabilidade_fonte=1.0,
                            estado_verificacao=taxonomia.VERIF_FONTE_PRIMARIA,
                            cobertura_relevancia=1.0)
    assert fraca.confianca < forte.confianca
    assert fraca.grau_confianca != impacto.CONFIANCA_ALTA
    assert forte.grau_confianca == impacto.CONFIANCA_ALTA
