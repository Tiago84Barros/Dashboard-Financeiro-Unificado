"""Deduplicação e agrupamento em eventos.

Cobre os cenários exigidos "notícias duplicadas" e "múltiplas empresas", e a
distinção que sustenta o portão de confirmação: **replicação não é
confirmação**. Três veículos publicando o mesmo texto são um veículo; três
veículos apurando o mesmo fato são três.
"""
from __future__ import annotations

import pytest

from core.noticias import dedup, eventos, taxonomia
from tests.apoio_noticias import AGORA, noticia, quando


def _clusters(*noticias):
    return dedup.agrupar_duplicatas(list(noticias))


# ── notícias duplicadas ──────────────────────────────────────────────────────

def test_mesma_url_com_rastreadores_diferentes_vira_uma_noticia():
    """utm_source não cria notícia nova. A URL canônica é a identidade."""
    a = noticia("Companhia Alfa divulga resultado do trimestre",
                "https://www.infomoney.com.br/mercados/alfa-resultado/",
                publicado_em=quando(2))
    b = noticia("Companhia Alfa divulga resultado do trimestre",
                "https://infomoney.com.br/mercados/alfa-resultado/?utm_source=x",
                publicado_em=quando(2))

    assert a.id_dedup == b.id_dedup
    grupos = _clusters(a, b)
    assert len(grupos) == 1
    assert grupos[0].duplicatas
    assert dedup.MOTIVO_URL in grupos[0].motivos


def test_texto_identico_em_dominios_diferentes_colapsa_num_cluster():
    a = noticia("Alfa anuncia aquisicao da Beta por R$ 2 bilhoes",
                "https://www.infomoney.com.br/a/1", publicado_em=quando(3))
    b = noticia("Alfa anuncia aquisicao da Beta por R$ 2 bilhoes",
                "https://finance.yahoo.com/news/a-1", publicado_em=quando(2))

    grupos = _clusters(a, b)
    assert len(grupos) == 1
    assert dedup.MOTIVO_CONTEUDO in grupos[0].motivos
    # O principal é o de fonte mais confiável (InfoMoney 0,75 > Yahoo 0,40),
    # e não o que chegou primeiro na lista.
    assert grupos[0].principal.fonte.dominio == "infomoney.com.br"


def test_reescrita_leve_ainda_e_a_mesma_materia():
    """Acrescentar 'hoje' ao fim do titulo nao cria materia nova."""
    a = noticia(
        "Alfa fecha acordo para comprar a Beta por dois bilhoes de reais",
        "https://www.infomoney.com.br/a/2",
        resumo="A operacao foi comunicada ao mercado nesta manha.",
        publicado_em=quando(4))
    b = noticia(
        "Alfa fecha acordo para comprar a Beta por dois bilhoes de reais hoje",
        "https://exame.com/a/2",
        resumo="A operacao foi comunicada ao mercado nesta manha.",
        publicado_em=quando(3))

    assert a.hash_conteudo != b.hash_conteudo, "o nivel exato nao pega este"
    assert dedup.quase_iguais(a.simhash, b.simhash)
    assert len(_clusters(a, b)) == 1
    assert dedup.MOTIVO_SEMELHANCA in _clusters(a, b)[0].motivos


# Pares medidos, com a distancia observada. Existem para que
# ``LIMITE_HAMMING`` nunca volte a ser um numero escolhido no olho: mexer nele
# quebra este teste, que e onde a medicao esta escrita.
_DEVEM_COLAPSAR = [
    ("prefixo do veiculo",
     "Alfa anuncia aquisicao da Beta por dois bilhoes de reais segundo comunicado",
     "URGENTE Alfa anuncia aquisicao da Beta por dois bilhoes de reais segundo comunicado"),
    ("titulo com uma palavra a mais",
     "Alfa fecha acordo para comprar a Beta por dois bilhoes de reais",
     "Alfa fecha acordo para comprar a Beta por dois bilhoes de reais hoje"),
]

_NAO_PODEM_COLAPSAR = [
    ("Copom sobe x mantem",
     "Banco Central eleva a taxa Selic em reuniao do Copom desta quarta-feira",
     "Banco Central mantem a taxa Selic em reuniao do Copom desta quarta-feira"),
    ("mesma empresa, outro assunto",
     "Alfa registra lucro liquido no terceiro trimestre",
     "Alfa aprova pagamento de dividendos aos acionistas"),
    ("fato oposto",
     "Alfa anuncia aquisicao da Beta por dois bilhoes de reais",
     "Alfa desiste da aquisicao da Beta e encerra as negociacoes"),
    ("outras empresas do mesmo setor",
     "Alfa anuncia aquisicao da Beta por dois bilhoes de reais",
     "Gama negocia a compra de ativos da Delta no exterior"),
]


@pytest.mark.parametrize(("rotulo", "a", "b"), _DEVEM_COLAPSAR,
                         ids=[c[0] for c in _DEVEM_COLAPSAR])
def test_o_limite_de_hamming_cobre_a_sindicalizacao_comum(rotulo, a, b):
    dist = dedup.distancia_hamming(dedup.simhash(a), dedup.simhash(b))
    assert dist <= dedup.LIMITE_HAMMING, f"{rotulo}: {dist} bits"


@pytest.mark.parametrize(("rotulo", "a", "b"), _NAO_PODEM_COLAPSAR,
                         ids=[c[0] for c in _NAO_PODEM_COLAPSAR])
def test_o_limite_de_hamming_nao_funde_materias_distintas(rotulo, a, b):
    """Fundir duas materias diferentes apaga um evento inteiro do painel."""
    dist = dedup.distancia_hamming(dedup.simhash(a), dedup.simhash(b))
    assert dist > dedup.LIMITE_HAMMING, f"{rotulo}: {dist} bits"


def test_deduplicacao_nao_depende_da_ordem_de_chegada():
    """Determinismo: o mesmo lote em outra ordem dá o mesmo principal."""
    a = noticia("Alfa reporta lucro liquido no trimestre",
                "https://www.reuters.com/a/3", publicado_em=quando(5))
    b = noticia("Alfa reporta lucro liquido no trimestre",
                "https://seekingalpha.com/a/3", publicado_em=quando(4))

    um = _clusters(a, b)[0].principal.id_dedup
    outro = _clusters(b, a)[0].principal.id_dedup
    assert um == outro


def test_materias_diferentes_nao_sao_deduplicadas():
    a = noticia("Alfa reporta lucro liquido de R$ 1,2 bilhao no trimestre",
                "https://www.reuters.com/a/4", publicado_em=quando(5))
    b = noticia("Banco Central mantem a taxa Selic e sinaliza cautela",
                "https://valor.globo.com/b/4", publicado_em=quando(5))
    assert len(_clusters(a, b)) == 2


# ── agrupamento em eventos ───────────────────────────────────────────────────

def test_materias_distintas_do_mesmo_fato_viram_um_evento():
    a = noticia("Alfa divulga balanco do terceiro trimestre",
                "https://www.reuters.com/e/1", tickers=("ALFA3",),
                publicado_em=quando(6))
    b = noticia("Receita liquida da Alfa cresce e supera projecoes de analistas",
                "https://valor.globo.com/e/1", tickers=("ALFA3",),
                publicado_em=quando(5))

    grupos = _clusters(a, b)
    assert len(grupos) == 2, "textos distintos nao podem ser deduplicados"

    lista = eventos.agrupar(grupos)
    assert len(lista) == 1
    assert lista[0].chave == ("ALFA3",)
    assert lista[0].n_fontes_independentes == 2
    assert lista[0].estado_verificacao == taxonomia.VERIF_INDEPENDENTE


def test_replicacao_sindicalizada_nao_conta_como_confirmacao():
    """O mesmo texto em três portais continua sendo uma fonte só."""
    titulo = "Alfa anuncia troca de diretor financeiro"
    replicas = [
        noticia(titulo, f"https://{d}/e/2", tickers=("ALFA3",),
                publicado_em=quando(3))
        for d in ("www.infomoney.com.br", "finance.yahoo.com", "msn.com")
    ]
    lista = eventos.agrupar(_clusters(*replicas))
    assert len(lista) == 1
    assert lista[0].n_fontes_independentes == 1
    assert lista[0].estado_verificacao == taxonomia.VERIF_NAO_VERIFICADA


def test_fonte_primaria_marca_o_evento_como_verificado():
    n = noticia("Fato relevante: Alfa comunica acordo de fusao com a Beta",
                "https://www.cvm.gov.br/doc/1", tickers=("ALFA3",),
                publicado_em=quando(1))
    evento = eventos.agrupar(_clusters(n))[0]
    assert evento.confirmado_por_primaria
    assert evento.estado_verificacao == taxonomia.VERIF_FONTE_PRIMARIA


def test_fora_da_janela_nao_e_o_mesmo_evento():
    a = noticia("Alfa divulga balanco do terceiro trimestre",
                "https://www.reuters.com/e/3", tickers=("ALFA3",),
                publicado_em=quando(1))
    b = noticia("Alfa apresenta receita e margem acima do esperado no periodo",
                "https://valor.globo.com/e/3", tickers=("ALFA3",),
                publicado_em=quando(24 * 30))
    assert len(eventos.agrupar(_clusters(a, b))) == 2


def test_noticia_sem_data_abre_o_proprio_evento():
    """Encaixar por ausência de data já produziu evento que nunca houve."""
    com_data = noticia("Alfa divulga balanco do terceiro trimestre",
                       "https://www.reuters.com/e/4", tickers=("ALFA3",),
                       publicado_em=quando(2))
    sem_data = noticia("Alfa apresenta numeros do trimestre a analistas",
                       "https://valor.globo.com/e/4", tickers=("ALFA3",))

    assert sem_data.publicado_em is None
    assert sem_data.idade_em_minutos(AGORA) is None
    assert len(eventos.agrupar(_clusters(com_data, sem_data))) == 2


# ── múltiplas empresas ───────────────────────────────────────────────────────

def test_materia_com_varias_empresas_preserva_todos_os_tickers():
    n = noticia("Alfa anuncia aquisicao da Beta e a Gama estuda proposta rival",
                "https://www.reuters.com/m/1",
                tickers=("ALFA3", "BETA4", "GAMA11"),
                empresas=("Alfa S.A.", "Beta S.A.", "Gama S.A."),
                publicado_em=quando(2))

    assert n.entidades.tickers == ("ALFA3", "BETA4", "GAMA11")
    assert len(n.entidades.empresas) == 3

    evento = eventos.agrupar(_clusters(n))[0]
    assert evento.chave == ("ALFA3", "BETA4", "GAMA11")
    assert set(evento.entidades.tickers) == {"ALFA3", "BETA4", "GAMA11"}


def test_conjuntos_de_empresas_diferentes_nao_se_misturam():
    a = noticia("Alfa anuncia aquisicao da Beta por dois bilhoes",
                "https://www.reuters.com/m/2", tickers=("ALFA3", "BETA4"),
                publicado_em=quando(2))
    b = noticia("Gama negocia a compra de ativos da Delta no exterior",
                "https://valor.globo.com/m/2", tickers=("GAMA11",),
                publicado_em=quando(2))
    lista = eventos.agrupar(_clusters(a, b))
    assert len(lista) == 2
    assert {e.chave for e in lista} == {("ALFA3", "BETA4"), ("GAMA11",)}
