"""`resultado_anual` como tipo próprio, e a data que uma fonte pode datar.

Duas recusas viraram teste aqui.

**1. DFP é anual.** Chamar a entrega de DFP de ``resultado_trimestral`` para
preencher uma linha da tabela trocaria o tipo do evento pelo tipo que havia. O
trimestral continua declarado *sem fonte*, que é a descrição correta do
armazém: o ITR não foi ingerido.

**2. Nem toda coluna de data é a data do anúncio.** ``ex_date`` data a
aritmética do provento e ``delisted_date`` data o fim da evidência. Uma base
construída sobre elas responde outra pergunta com a mesma cara --
``memoria: medir-a-fonte-que-a-decisao-le``.
"""
from __future__ import annotations

from core.calibracao import catalogo as cat
from core.noticias import eventos, portoes, taxonomia


def test_resultado_anual_existe_na_taxonomia():
    assert "resultado_anual" in taxonomia.POR_CHAVE


def test_resultado_trimestral_continua_sem_fonte():
    """Ter fonte para o anual não dá fonte ao trimestral."""
    assert "resultado_trimestral" in cat.SEM_FONTE
    assert "resultado_trimestral" not in {f.tipo_evento for f in cat.FONTES}


def test_cobertura_recusa_tipo_nao_declarado():
    """O guarda que obriga a declarar fonte OU ausência continua de pé."""
    cobertura = cat.cobertura()
    nomes = set(taxonomia.POR_CHAVE)
    declarados = set(cobertura.com_fonte) | set(cobertura.sem_fonte)
    assert nomes <= declarados


def test_balanco_anual_nao_vira_trimestre():
    """"balanco anual" casa com "balanco"; a ordem da lista é o que decide."""
    for texto in ("Empresa divulga balanco anual de 2025",
                  "Resultado do exercicio social encerrado em dezembro",
                  "Companhia publica demonstracoes financeiras padronizadas"):
        assert eventos.classificar(texto) == "resultado_anual", texto


def test_resultado_trimestral_ainda_classifica():
    assert eventos.classificar(
        "Balanco do 3o trimestre supera estimativas") == "resultado_trimestral"


def test_tipo_novo_entra_no_portao_de_fundamento():
    """Tipo fora de TIPOS_DE_FUNDAMENTO nunca aciona o portão -- decoração."""
    assert "resultado_anual" in portoes.TIPOS_DE_FUNDAMENTO


def test_fontes_datadas_por_mecanica_estao_marcadas():
    por_coluna = {f.coluna_pit: f.data_e_do_anuncio for f in cat.FONTES}
    assert por_coluna.get("ex_date") is False
    assert por_coluna.get("delisted_date") is False
    assert por_coluna.get("primeira_entrega_em") is True
    assert por_coluna.get("source_published_at") is True


def test_toda_fonte_declara_pelo_menos_uma_ressalva():
    """Fonte sem ressalva é fonte que ninguém interrogou."""
    for fonte in cat.FONTES:
        assert fonte.ressalvas, fonte.rotulo
