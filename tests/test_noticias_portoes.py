"""Os seis portões entre uma nota alta e um aporte.

Este arquivo existe por causa de uma frase do requisito: "uma notícia com nota
superior a 80 não poderá, sozinha, alterar definitivamente a carteira". Quase
todo teste aqui é uma tentativa de furar essa trava — nota altíssima, fonte
primária, ativo na carteira — e a afirmação de que ela não fura.

A outra metade cobre "ausência de carteira": sem portfólio cadastrado o portão
de perfil fica ``None``, e ``None`` **não** aprova. Preencher a lacuna com um
palpite otimista é o modo de falha do *fallback que só preenche lacuna e nunca
contradiz*: regra certa, entrada errada, aprovação confiante.
"""
from __future__ import annotations

import pytest

from core.noticias import impacto as imp
from core.noticias import modelos, portoes, relevancia, taxonomia
from tests.apoio_noticias import AGORA, noticia, quando

CARTEIRA = portoes.Perfil(
    horizonte_meses=60,
    limite_por_ativo=0.20,
    exposicao_por_ativo={"ALFA3": 0.04},
    tickers=("ALFA3", "PETR4"),
)


def _avaliada(n, *, primaria=False, n_fontes=1, **kw):
    """Monta uma ``NoticiaAvaliada`` pelo caminho real de cálculo."""
    r = relevancia.calcular(n, agora=AGORA, confirmado_por_primaria=primaria,
                            n_fontes_independentes=n_fontes, **kw)
    i = imp.estimar(
        tipo_evento=n.tipo_evento,
        sentimento=n.sentimento,
        confiabilidade_fonte=n.fonte.confiabilidade if n.fonte else None,
        cobertura_relevancia=r.cobertura,
    )
    return modelos.NoticiaAvaliada(
        noticia=n, relevancia=r, impacto=i,
        n_fontes_independentes=n_fontes, confirmado_por_primaria=primaria)


def _nota_alta(primaria=True, **kw):
    n = noticia("Fato relevante: Alfa comunica acordo de fusao com a Beta",
                "https://www.cvm.gov.br/doc/9", tickers=("ALFA3",),
                publicado_em=quando(1))
    return _avaliada(n, primaria=primaria, tickers_alvo=("ALFA3",),
                     exposicao_carteira=0.4, **kw)


# ── o caminho feliz, para provar que os testes negativos medem algo ─────────

def test_com_os_seis_criterios_a_saida_e_sugestao_de_revisao():
    v = portoes.avaliar(_nota_alta(), perfil=CARTEIRA,
                        confirmacao_quantitativa=True)

    assert v.acao == portoes.ACAO_SUGERIR_REVISAO
    assert v.faixa == taxonomia.FAIXA_REVISAO
    assert len(v.aprovados) == 6
    assert not v.reprovados and not v.indeterminados
    assert "que decide" in v.motivo()


def test_mesmo_aprovada_a_saida_nunca_e_uma_ordem():
    """Invariante do módulo: sugerir é o teto. Nenhum caminho o ultrapassa."""
    v = portoes.avaliar(_nota_alta(), perfil=CARTEIRA,
                        confirmacao_quantitativa=True)
    assert v.exige_confirmacao_humana is True
    assert v.altera_carteira_automaticamente is False
    assert v.libera_revisao is True


# ── uma notícia sozinha não passa: cada portão derruba isoladamente ─────────

def _cai(**kw):
    """Aplica uma alteração sobre o caso aprovado e devolve o veredito."""
    padrao = {"perfil": CARTEIRA, "confirmacao_quantitativa": True}
    avaliada = kw.pop("avaliada", None) or _nota_alta()
    padrao.update(kw)
    return portoes.avaliar(avaliada, **padrao)


def test_fonte_unica_sem_confirmacao_nao_abre_revisao():
    n = noticia("Alfa anuncia aquisicao da Beta por dois bilhoes de reais",
                "https://www.reuters.com/n/1", tickers=("ALFA3",),
                publicado_em=quando(2))
    a = _avaliada(n, tickers_alvo=("ALFA3",), exposicao_carteira=0.4)

    assert a.nota >= 80.0, "o teste precisa de nota alta para significar algo"
    v = _cai(avaliada=a)
    assert v.acao == portoes.ACAO_OBSERVAR
    assert [p.chave for p in v.reprovados] == [portoes.PORTAO_CONFIRMACAO]


def test_apuracao_independente_substitui_a_fonte_primaria():
    """Dois veículos com apuração própria abrem o portão. Réplicas não."""
    n = noticia("Alfa anuncia aquisicao da Beta por dois bilhoes de reais",
                "https://www.reuters.com/n/1", tickers=("ALFA3",),
                publicado_em=quando(2))
    a = _avaliada(n, n_fontes=2, tickers_alvo=("ALFA3",),
                  exposicao_carteira=0.4)
    v = _cai(avaliada=a)
    assert v.acao == portoes.ACAO_SUGERIR_REVISAO


def test_sem_indicador_quantitativo_o_portao_fica_indeterminado_e_barra():
    v = _cai(confirmacao_quantitativa=None)

    assert v.acao == portoes.ACAO_OBSERVAR
    assert [p.chave for p in v.indeterminados] == [portoes.PORTAO_QUANTITATIVO]
    assert not v.reprovados
    # A evidência precisa dizer "não consegui verificar", e não "reprovou".
    assert "nenhum indicador" in v.indeterminados[0].evidencia
    assert any("nao foram todos satisfeitos" in lim for lim in v.limitacoes)


def test_indicador_que_contradiz_nao_e_o_mesmo_que_indicador_ausente():
    ausente = _cai(confirmacao_quantitativa=None).portoes[4]
    contra = _cai(confirmacao_quantitativa=False).portoes[4]

    assert ausente.satisfeito is None
    assert contra.satisfeito is False
    assert ausente.evidencia != contra.evidencia
    # Nenhum dos dois aprova, mas os dois motivos são distintos no relatório.
    assert not ausente.aprovado and not contra.aprovado


def test_ausencia_de_carteira_nao_aprova_o_portao_de_perfil():
    v = _cai(perfil=portoes.PERFIL_VAZIO)

    assert v.acao == portoes.ACAO_OBSERVAR
    assert [p.chave for p in v.indeterminados] == [portoes.PORTAO_CARTEIRA]
    assert "sem carteira cadastrada" in v.indeterminados[0].evidencia
    assert len(v.aprovados) == 5, "os outros cinco continuam sendo avaliados"


def test_ativo_ja_no_limite_de_exposicao_barra_o_aporte():
    cheia = portoes.Perfil(horizonte_meses=60, limite_por_ativo=0.20,
                           exposicao_por_ativo={"ALFA3": 0.25},
                           tickers=("ALFA3",))
    v = _cai(perfil=cheia)

    assert v.acao == portoes.ACAO_OBSERVAR
    assert [p.chave for p in v.reprovados] == [portoes.PORTAO_CARTEIRA]
    assert "ALFA3" in v.reprovados[0].evidencia


def test_ativo_fora_da_carteira_barra_o_aporte():
    outra = portoes.Perfil(horizonte_meses=60, tickers=("PETR4", "VALE3"))
    v = _cai(perfil=outra)
    assert [p.chave for p in v.reprovados] == [portoes.PORTAO_CARTEIRA]


def test_efeito_passageiro_nao_atravessa_o_portao_de_persistencia():
    n = noticia("Alfa aprova pagamento de dividendos aos acionistas",
                "https://www.cvm.gov.br/d/1", tickers=("ALFA3",),
                publicado_em=quando(1))
    assert n.tipo.persistencia < portoes.PISO_PERSISTENCIA

    v = _cai(avaliada=_avaliada(n, primaria=True, tickers_alvo=("ALFA3",),
                                exposicao_carteira=0.4))
    reprovado = {p.chave for p in v.reprovados}
    assert portoes.PORTAO_PERSISTENCIA in reprovado
    assert v.acao != portoes.ACAO_SUGERIR_REVISAO


def test_evento_que_nao_toca_fundamento_nao_atravessa():
    n = noticia("Preco do minerio de ferro sobe na bolsa de Dalian",
                "https://www.reuters.com/c/1", tickers=("ALFA3",),
                publicado_em=quando(1))
    assert n.tipo_evento not in portoes.TIPOS_DE_FUNDAMENTO

    v = _cai(avaliada=_avaliada(n, primaria=True, tickers_alvo=("ALFA3",),
                                exposicao_carteira=0.4))
    assert portoes.PORTAO_FUNDAMENTO in {p.chave for p in v.reprovados}


def test_fundamento_apontado_de_fora_abre_o_portao():
    """Quem sabe quais fundamentos mudaram são os motores de score, não aqui."""
    n = noticia("Preco do minerio de ferro sobe na bolsa de Dalian",
                "https://www.reuters.com/c/1", tickers=("ALFA3",),
                publicado_em=quando(1))
    a = _avaliada(n, primaria=True, tickers_alvo=("ALFA3",),
                  exposicao_carteira=0.4)
    v = _cai(avaliada=a, fundamentos_afetados=("margem_bruta",))
    fundamento = next(p for p in v.portoes
                      if p.chave == portoes.PORTAO_FUNDAMENTO)
    assert fundamento.satisfeito is True
    assert "margem_bruta" in fundamento.evidencia


def test_evento_indefinido_deixa_dois_portoes_indeterminados():
    n = noticia("Boletim semanal comenta o humor do mercado",
                "https://www.reuters.com/i/1", tickers=("ALFA3",),
                publicado_em=quando(1))
    assert n.tipo_evento == taxonomia.TIPO_INDEFINIDO.chave

    v = _cai(avaliada=_avaliada(n, primaria=True, tickers_alvo=("ALFA3",),
                                exposicao_carteira=0.4))
    indeterminados = {p.chave for p in v.indeterminados}
    assert indeterminados == {portoes.PORTAO_PERSISTENCIA,
                              portoes.PORTAO_FUNDAMENTO}


def test_sem_entidade_a_relacao_e_indeterminada_e_nao_zero():
    n = noticia("Boletim semanal comenta o humor do mercado",
                "https://boletim-exemplo-ficticio.test/x/1",
                publicado_em=quando(2))
    v = _cai(avaliada=_avaliada(n))
    relacao = next(p for p in v.portoes if p.chave == portoes.PORTAO_RELACAO)
    assert relacao.satisfeito is None
    assert "nao verificavel" in relacao.evidencia


# ── faixas: abaixo de 80 os portões nem entram na conversa ─────────────────

@pytest.mark.parametrize(("titulo", "url", "horas", "acao"), [
    ("Cinco acoes baratas para ficar de olho neste mes",
     "https://algumblog.blogspot.com/p/1", 24 * 20, portoes.ACAO_INFORMAR),
    ("Alfa anuncia aquisicao da Beta por dois bilhoes de reais",
     "https://algumblog.blogspot.com/n/1", 2, portoes.ACAO_OBSERVAR),
])
def test_abaixo_da_faixa_de_revisao_a_saida_e_informar_ou_observar(
        titulo, url, horas, acao):
    n = noticia(titulo, url, publicado_em=quando(horas))
    v = portoes.avaliar(_avaliada(n, primaria=True), perfil=CARTEIRA,
                        confirmacao_quantitativa=True)
    assert v.nota < 80.0
    assert v.acao == acao


def test_nenhum_veredito_possivel_altera_a_carteira_sozinho():
    """A varredura: todos os desfechos deste arquivo, sob a mesma invariante."""
    casos = [
        portoes.avaliar(_nota_alta(), perfil=CARTEIRA,
                        confirmacao_quantitativa=True),
        _cai(confirmacao_quantitativa=None),
        _cai(confirmacao_quantitativa=False),
        _cai(perfil=portoes.PERFIL_VAZIO),
        portoes.avaliar(_nota_alta(primaria=False), perfil=CARTEIRA,
                        confirmacao_quantitativa=True),
    ]
    assert {v.altera_carteira_automaticamente for v in casos} == {False}
    assert {v.exige_confirmacao_humana for v in casos} == {True}
    assert {v.acao for v in casos} <= {portoes.ACAO_INFORMAR,
                                       portoes.ACAO_OBSERVAR,
                                       portoes.ACAO_SUGERIR_REVISAO}
