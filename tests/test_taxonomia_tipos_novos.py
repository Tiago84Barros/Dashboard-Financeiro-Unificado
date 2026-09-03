"""Os três tipos de evento que faltavam: pandemia, quebra de banco, clima.

A taxonomia fechada tinha 25 tipos e nenhum deles cobria três fatos que este
sistema existe para enxergar. Uma matéria sobre a OMS declarando pandemia caía
em ``indefinido`` -- materialidade 0,25, o prior do resíduo -- e o índice de
relevância a tratava como notícia sem consequência conhecida.

Os testes daqui cobrem três coisas distintas, e a terceira é a que costuma
faltar:

* os tipos existem, com prior declarado e escopo coerente;
* o classificador chega neles a partir de texto real;
* **a ordem entre eles e os tipos antigos** -- "falencia do banco" não pode
  virar recuperação judicial de uma empresa qualquer, e uma seca que quebra a
  safra não pode virar "preço de commodity", que é o efeito e não a causa.
"""
from __future__ import annotations

from core.calibracao import catalogo as cat
from core.calibracao import pesos as pw
from core.noticias import eventos as ev
from core.noticias import taxonomia as tax

NOVOS = ("pandemia", "quebra_bancaria", "evento_climatico")


# -- Os tipos ----------------------------------------------------------------
def test_os_tres_tipos_existem_com_prior_declarado():
    for chave in NOVOS:
        t = tax.POR_CHAVE[chave]
        assert t.rotulo and t.escopo in (tax.ESCOPO_ATIVO, tax.ESCOPO_SETOR,
                                         tax.ESCOPO_MACRO)
        assert t.horizonte in tax.HORIZONTES
        assert 0.0 <= t.materialidade <= 1.0
        assert 0.0 <= t.persistencia <= 1.0


def test_a_versao_da_taxonomia_subiu_com_o_vocabulario():
    """Tipo novo muda o índice sem que peso nenhum tenha sido tocado.

    O que antes caía em ``indefinido`` (0,25) passa a cair no tipo certo. Sem
    subir a versão, os dois resultados ficam indistinguíveis
    (``memoria: versao-de-metodologia-sem-safra``).
    """
    assert tax.TAXONOMIA_VERSAO != "1.0.0"
    assert "1.1.0" in tax.__doc__ and "prior" in tax.__doc__


def test_pandemia_nao_e_mais_material_que_crise_sistemica():
    """Prior de pandemia abaixo do de crise sistêmica, e persistência alta.

    2020: o choque de preço se desfez em meses, o de comportamento durou anos.
    """
    p, c = tax.POR_CHAVE["pandemia"], tax.POR_CHAVE["crise_sistemica"]
    assert p.materialidade < c.materialidade
    assert p.persistencia >= c.persistencia
    assert p.horizonte == tax.HORIZONTE_LONGO


def test_quebra_bancaria_e_setorial_e_nao_se_confunde_com_crise_sistemica():
    """Quebrar não é contagiar. Quem decide se escalou é o motor de mercado."""
    q = tax.POR_CHAVE["quebra_bancaria"]
    assert q.escopo == tax.ESCOPO_SETOR
    assert q.chave != "crise_sistemica"
    assert "crise_sistemica" in tax.POR_CHAVE


# -- O classificador chega neles ---------------------------------------------
def test_texto_real_cai_no_tipo_certo():
    casos = {
        "OMS declara pandemia de gripe aviaria": "pandemia",
        "Governo decreta lockdown em tres estados": "pandemia",
        "Banco Central decreta liquidacao extrajudicial do banco": "quebra_bancaria",
        "Reguladores fecham banco regional; FDIC assume": "quebra_bancaria",
        "Enchente no Rio Grande do Sul interrompe producao": "evento_climatico",
        "Furacao atinge refinarias no Golfo do Mexico": "evento_climatico",
    }
    for titulo, esperado in casos.items():
        assert ev.classificar(titulo) == esperado, titulo


def test_antes_estes_textos_caiam_em_indefinido():
    """O ganho é este: o resíduo tem prior 0,25 e apaga a consequência."""
    assert tax.TIPO_INDEFINIDO.materialidade < 0.3
    for titulo in ("OMS declara pandemia de gripe aviaria",
                   "Reguladores fecham banco regional; FDIC assume"):
        assert tax.tipo(ev.classificar(titulo)).materialidade > 0.6


# -- A ordem entre os tipos --------------------------------------------------
def test_quebra_de_banco_vence_recuperacao_judicial():
    """"falencia do banco" casaria com "falencia" e viraria evento de um ativo."""
    assert ev.classificar("Insolvencia do banco leva a falencia da holding") \
        == "quebra_bancaria"


def test_desastre_climatico_vence_preco_de_commodity():
    """A seca é a causa; o preço da safra é o efeito. Registrar o efeito
    apagaria a causa, e é a causa que o Motor de Eventos Extremos precisa."""
    assert ev.classificar(
        "Seca severa reduz safra de soja e pressiona preco da commodity") \
        == "evento_climatico"


def test_os_tipos_antigos_continuam_classificando_como_antes():
    """Tipo novo não pode roubar matéria de tipo velho."""
    inalterados = {
        "Empresa entra em recuperacao judicial": "recuperacao_judicial",
        "Corrida bancaria derruba acoes de bancos regionais": "crise_sistemica",
        "Petrobras aprova dividendo extraordinario": "dividendo",
        "Copom eleva a Selic em 0,5 ponto": "juros_politica_monetaria",
        "Petroleo brent sobe apos reuniao da Opep": "commodity",
        "Vale divulga balanco do trimestre": "resultado_trimestral",
    }
    for titulo, esperado in inalterados.items():
        assert ev.classificar(titulo) == esperado, titulo


# -- Cadência emergencial ----------------------------------------------------
def test_pandemia_e_quebra_de_banco_encurtam_a_cadencia():
    assert {"pandemia", "quebra_bancaria"} <= tax.TIPOS_EMERGENCIAIS


def test_clima_fica_de_fora_e_o_motivo_esta_escrito():
    """Frequente e local: um gatilho por enchente seria pago em falso alarme.

    Falso alarme é o dano mais caro deste sistema -- é o critério de
    homologação com limiar zero. O motivo fica no código para que a ausência
    não pareça esquecimento.
    """
    assert "evento_climatico" not in tax.TIPOS_EMERGENCIAIS
    assert "cadencia" in tax.CLIMATICO_NAO_E_EMERGENCIAL


# -- Calibração: os três entram sem buraco silencioso ------------------------
def test_a_cobertura_da_calibracao_declara_os_tres():
    """Tipo novo sem declaração de fonte derruba ``cobertura()`` na hora."""
    cob = cat.cobertura()
    for chave in NOVOS:
        assert chave in cob.sem_fonte
        assert cob.sem_fonte[chave] and "indisponivel" not in cob.sem_fonte[chave]
    assert set(cob.com_fonte) | set(cob.sem_fonte) == set(tax.POR_CHAVE)


def test_a_cobertura_caiu_e_o_numero_publicado_e_o_de_agora():
    """Três tipos a mais sem fonte **baixam** a cobertura. É a verdade.

    Arredondar para cima aqui seria publicar a cobertura que se gostaria de
    ter. O denominador cresceu e o numerador não.
    """
    cob = cat.cobertura()
    assert cob.total_tipos == len(tax.TIPOS) >= 28
    assert cob.fracao < 3 / 25   # menor do que era antes dos três

def test_o_prior_de_pesos_herda_os_tres_sem_avisos():
    assert set(pw.PRIOR.notas_tipo) == set(tax.POR_CHAVE)
    for chave in NOVOS:
        assert chave in pw.PRIOR.notas_tipo
    assert not [a for a in pw.PRIOR.validar() if "tipo desconhecido" in a]
