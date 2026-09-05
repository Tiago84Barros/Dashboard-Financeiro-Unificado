"""Matérias do mesmo fato macro passam a virar um evento só (A-145).

Por que este arquivo existe
---------------------------
A revisão de 02/09 mediu: guerra, quebra de banco e evento climático produziram
**2 eventos a partir de 2 matérias do mesmo fato**. A causa era a chave de
agrupamento, que exigia ticker, empresa, setor, ativo ou país -- e a notícia
macro não carrega nenhum deles. O efeito não parava no agrupamento: com um
cluster por evento, ``n_fontes_independentes`` valia 1, e o portão de
confirmação reprovava um fato que teve duas agências.

O que se cobra aqui, nesta ordem de importância:

1. **Super-agrupar é pior que sub-agrupar**, e por isso vem primeiro: dois
   fatos distintos do mesmo tipo, na mesma janela, não podem virar um evento
   com dois domínios. Isso fabricaria a confirmação independente que o portão
   existe para exigir. Sub-agrupar reprova um fato verdadeiro -- custo visível
   e que não produz ação nenhuma.
2. **O fato coberto por duas agências vira um evento com duas fontes.**
3. **A chave temática não vale para escopo de ativo**: notícia sem entidade
   nenhuma não tem sujeito resolvido, e agrupar por manchete seria adivinhar de
   quem é o fato.
4. **Id temático não colide**: a chave é a mesma sentinela para todo evento
   daquele tipo, então o discriminador tem de estar no identificador.
"""
from __future__ import annotations

import pytest

from core.noticias import dedup, eventos, taxonomia
from tests.apoio_noticias import noticia, quando


def _clusters(*noticias):
    return dedup.agrupar_duplicatas(list(noticias))


def _macro(titulo: str, dominio: str, *, horas: float = 2.0):
    """Notícia macro de verdade: sem ticker declarado, como as reais chegam."""
    return noticia(titulo, f"https://{dominio}/n/{abs(hash(titulo)) % 9999}",
                   publicado_em=quando(horas))


# ── o lado perigoso primeiro: o que NÃO pode ser agrupado ────────────────────

CASOS_SEPARADOS = [
    ("juros: Copom nao e Fed",
     "Copom eleva a Selic em 0,5 ponto percentual",
     "Federal Reserve mantem os juros e sinaliza cautela"),
    ("clima: enchente nao e furacao",
     "Enchente no Rio Grande do Sul desabriga milhares de familias",
     "Furacao atinge refinarias no Golfo do Mexico"),
    ("sanitario: emergencia global nao e surto local",
     "OMS declara emergencia sanitaria por gripe aviaria",
     "Governo decreta lockdown em tres estados apos surto de sarampo"),
    ("banco: liquidacao no Brasil nao e banco regional dos EUA",
     "Banco Central decreta liquidacao extrajudicial do Banco Master",
     "Reguladores dos EUA fecham banco regional e FDIC assume os depositos"),
]


@pytest.mark.parametrize("rotulo,a,b", CASOS_SEPARADOS,
                         ids=[c[0] for c in CASOS_SEPARADOS])
def test_fatos_distintos_do_mesmo_tipo_nao_viram_um_evento(rotulo, a, b):
    """O modo de falha que a chave por tipo, sozinha, teria criado.

    Dois fatos distintos num evento só somam domínios distintos, e o evento
    passa a declarar ``confirmada_independente`` para uma confirmação que nunca
    houve. Fabricar confirmação abre a porta que o portão fecha; não agrupar
    apenas a mantém fechada.
    """
    lista = eventos.agrupar(_clusters(_macro(a, "www.reuters.com"),
                                      _macro(b, "valor.globo.com", horas=1)))

    assert len(lista) == 2, (
        f"{rotulo}: dois fatos diferentes viraram um evento com duas fontes")
    assert all(e.n_fontes_independentes == 1 for e in lista)


# ── o defeito medido em 02/09: o mesmo fato em duas agências ─────────────────

CASOS_JUNTOS = [
    ("sanitario",
     "OMS declara emergencia de saude publica de importancia internacional por surto de gripe aviaria",
     "Organizacao Mundial da Saude decreta emergencia internacional apos surto de gripe aviaria",
     "pandemia"),
    ("clima",
     "Enchente historica no Rio Grande do Sul paralisa producao em tres estados",
     "Enchente no Rio Grande do Sul interrompe operacoes e paralisa a producao",
     "evento_climatico"),
    ("banco",
     "Banco Central decreta liquidacao extrajudicial do Banco Master",
     "BC anuncia liquidacao extrajudicial do Banco Master apos intervencao",
     "quebra_bancaria"),
]


@pytest.mark.parametrize("rotulo,a,b,tipo", CASOS_JUNTOS,
                         ids=[c[0] for c in CASOS_JUNTOS])
def test_o_mesmo_fato_macro_em_duas_agencias_vira_um_evento(rotulo, a, b, tipo):
    """A medição de 02/09, refeita como teste.

    Antes: 2 eventos, ``n_fontes_independentes=1`` em cada, portão de
    confirmação reprovando. Depois: 1 evento com 2 domínios.
    """
    na, nb = _macro(a, "www.reuters.com"), _macro(b, "valor.globo.com", horas=1)

    grupos = _clusters(na, nb)
    assert len(grupos) == 2, (
        "cenario invalido: a deduplicacao ja colapsou as duas materias, e ai "
        "nao ha nada para o agrupamento por evento resolver")

    lista = eventos.agrupar(grupos)
    assert len(lista) == 1, f"{rotulo}: o mesmo fato continua em dois eventos"
    assert lista[0].tipo == tipo
    assert lista[0].n_fontes_independentes == 2
    assert lista[0].estado_verificacao == taxonomia.VERIF_INDEPENDENTE


def test_redacao_independente_do_mesmo_fato_nao_e_quase_duplicata():
    """Por que o agrupamento por evento é uma camada à parte da deduplicação.

    Se ``dedup`` já resolvesse estes pares, a correção certa seria subir o
    limite de Hamming. Ele não resolve, e não deveria: as distâncias medidas em
    05/09/2026 vão de 16 (banco) a 30 (clima), com o limite em 8 -- e o limite 8
    existe porque 13 já separa "Copom sobe" de "Copom mantem". Subir o limite
    até 16 para agrupar aqui colapsaria pares que dizem coisas opostas.
    """
    for _, a, b, _ in CASOS_JUNTOS:
        dist = dedup.distancia_hamming(dedup.simhash(a), dedup.simhash(b))
        assert dist is not None and dist >= dedup.LIMITE_HAMMING * 2, (
            f"par {dist} bits: perto demais do limite de duplicata para que "
            f"este teste continue medindo o que diz medir")


# ── os limites da chave temática ─────────────────────────────────────────────

def test_a_chave_tematica_nao_vale_para_escopo_de_ativo():
    """Sem entidade e sem escopo macro, o sujeito do fato é desconhecido.

    Agrupar por semelhança de manchete aqui atribuiria a notícia a um fato de
    empresa que ninguém resolveu -- o projeto já pagou por atribuir notícia a
    empresa que ela não citou, e a trava do resolvedor de entidades existe por
    isso.
    """
    a = _macro("Companhia divulga producao recorde na fabrica principal",
               "www.reuters.com")
    b = _macro("Producao recorde na fabrica e comunicada ao mercado",
               "valor.globo.com", horas=1)

    assert taxonomia.tipo(a.tipo_evento).escopo == taxonomia.ESCOPO_ATIVO
    assert not a.entidades.tickers
    assert len(eventos.agrupar(_clusters(a, b))) == 2


def test_entidade_continua_mandando_quando_existe():
    """A chave temática é fallback, não substituta: com ticker, ele decide."""
    a = noticia("Alfa divulga balanco do terceiro trimestre",
                "https://www.reuters.com/t/1", tickers=("ALFA3",),
                publicado_em=quando(3))
    b = noticia("Receita liquida da Alfa supera as projecoes dos analistas",
                "https://valor.globo.com/t/1", tickers=("ALFA3",),
                publicado_em=quando(2))

    evento = eventos.agrupar(_clusters(a, b))[0]
    assert evento.chave == ("ALFA3",)
    assert not evento.tokens_tema, (
        "evento com entidade guardou tokens de tema: a afinidade lexical "
        "passaria a interferir onde a entidade ja e a prova")


def test_dois_eventos_tematicos_do_mesmo_tipo_e_dia_tem_ids_distintos():
    """Chave que se repete não pode virar id que se repete.

    A sentinela temática é ``("tema", tipo)`` para todos, então sem
    discriminador no hash duas quebras de banco distintas no mesmo dia
    receberiam o mesmo ``evento_id`` -- e id que colide não identifica: faz
    duas coisas passarem por uma.
    """
    lista = eventos.agrupar(_clusters(
        _macro("Banco Central decreta liquidacao extrajudicial do Banco Master",
               "www.reuters.com"),
        _macro("Reguladores dos EUA fecham banco regional e FDIC assume os "
               "depositos", "valor.globo.com", horas=1)))

    assert len(lista) == 2
    assert lista[0].id != lista[1].id


def test_fora_da_janela_o_tema_nao_junta():
    """Afinidade lexical não vence o tempo: fato de ontem não é o de hoje."""
    lista = eventos.agrupar(_clusters(
        _macro("Enchente historica no Rio Grande do Sul paralisa producao em "
               "tres estados", "www.reuters.com", horas=1),
        _macro("Cheia recorde no Rio Grande do Sul interrompe operacoes e "
               "paralisa producao", "valor.globo.com", horas=24 * 30)))

    assert len(lista) == 2


def test_o_limiar_de_afinidade_fica_do_lado_seguro_da_medicao():
    """A tabela medida, mantida viva.

    O par verdadeiro mais fraco mede 0,33 e o par falso mais forte mede 0,17. O
    limiar tem de caber entre os dois e encostar no lado que erra para menos:
    sub-agrupar reprova um fato verdadeiro, super-agrupar aprova um fato que
    ninguem confirmou -- e so o segundo produz acao.
    """
    assert 0.20 < eventos.LIMIAR_AFINIDADE_TEMATICA <= 0.33

    def tk(t):
        return eventos._tokens_tematicos(_macro(t, "www.reuters.com"))

    verdadeiro = eventos._afinidade(
        tk("Banco Central decreta liquidacao extrajudicial do Banco Master"),
        tk("BC intervem e liquida o Banco Master, segundo comunicado ao "
           "mercado"))
    falso = eventos._afinidade(
        tk("Banco Central decreta liquidacao extrajudicial do Banco Master"),
        tk("Reguladores dos EUA fecham banco regional e FDIC assume os depositos"))

    assert falso < eventos.LIMIAR_AFINIDADE_TEMATICA <= verdadeiro, (
        f"a medicao mudou: verdadeiro={verdadeiro:.2f} falso={falso:.2f}")


def test_a_ancora_e_a_primeira_materia_e_nao_a_uniao_acumulada():
    """Sem âncora fixa, o evento deriva por transitividade.

    A parecido com B, B parecido com C, e C acaba dentro de um evento com que
    não se parece. O teste usa três matérias em cadeia: a terceira só se
    parece com a segunda.
    """
    a = _macro("Enchente historica no Rio Grande do Sul paralisa producao",
               "www.reuters.com", horas=3)
    b = _macro("Cheia recorde no Rio Grande do Sul interrompe a producao",
               "valor.globo.com", horas=2)
    c = _macro("Seca severa no Nordeste interrompe a producao de energia das "
               "usinas", "www.infomoney.com.br", horas=1)

    lista = eventos.agrupar(_clusters(a, b, c))
    por_id = {e.id: e for e in lista}
    assert len(por_id) == len(lista), "ids colidiram entre eventos distintos"

    grande = max(lista, key=lambda e: len(e.clusters))
    titulos = " ".join(n.titulo for n in grande.noticias).lower()
    assert "seca severa" not in titulos or len(grande.clusters) == 1, (
        "a materia da seca entrou no evento da enchente por transitividade")


def test_tipos_diferentes_para_o_mesmo_fato_seguem_separados_por_projeto():
    """A limitação que sobra, declarada em vez de contornada.

    O agrupamento exige o mesmo tipo de evento. "BC intervem e liquida o Banco
    Master" cai em ``indefinido`` -- o vocabulário do classificador tem
    "liquidacao", não "liquida" --, então as duas redações do mesmo fato
    continuam em eventos separados. A lacuna é do classificador, e fechá-la
    alargando palavra solta trocaria um erro visível (evento a mais) por
    classificação errada em silêncio, que é o erro que este projeto persegue.
    """
    a = _macro("Banco Central decreta liquidacao extrajudicial do Banco Master",
               "www.reuters.com")
    b = _macro("BC intervem e liquida o Banco Master, segundo comunicado ao "
               "mercado", "valor.globo.com", horas=1)

    assert a.tipo_evento == "quebra_bancaria"
    assert b.tipo_evento == taxonomia.TIPO_INDEFINIDO.chave
    assert eventos._afinidade(eventos._tokens_tematicos(a),
                              eventos._tokens_tematicos(b)) >=         eventos.LIMIAR_AFINIDADE_TEMATICA, (
        "o par deixou de ser lexicalmente afim, e ai este teste nao mede mais "
        "o tipo como causa da separacao")
    assert len(eventos.agrupar(_clusters(a, b))) == 2
