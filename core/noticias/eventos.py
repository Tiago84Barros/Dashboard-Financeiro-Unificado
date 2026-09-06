"""Classificação do tipo de evento e agrupamento de matérias sobre o mesmo fato.

Duas etapas distintas e propositalmente separadas:

``classificar``  -- que tipo de fato é este, dentro do vocabulário fechado de
                    ``taxonomia``.
``agrupar``      -- quais matérias falam do mesmo fato.

A contagem de fontes independentes é a parte que mais engana e por isso está
explicitada aqui: ela conta **clusters de quase-duplicata com domínios
distintos**, não matérias e não domínios. Cinco portais republicando o mesmo
despacho de agência são um cluster e uma fonte -- contá-los como cinco faria uma
única matéria sindicalizada atravessar sozinha o portão de confirmação
independente, que é justamente o portão que existe para impedir isso.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from core.noticias import taxonomia
from core.noticias.dedup import Cluster
from core.noticias.modelos import Entidades, Noticia
from core.noticias.normalizacao import normalizar_texto

JANELA_PADRAO_H = 48.0

# Tópicos e setores devolvidos pelas APIs, mapeados para o vocabulário fechado.
# O que não estiver aqui cai em `indefinido` sem derrubar a coleta.
MAPA_CATEGORIA: dict[str, str] = {
    "earnings": "resultado_trimestral",
    "financial_markets": "indefinido",
    "mergers_and_acquisitions": "fusao_aquisicao",
    "ipo": "emissao_capital",
    "economy_monetary": "juros_politica_monetaria",
    "economy_fiscal": "fiscal_politico",
    "economy_macro": "atividade_emprego",
    "energy_transportation": "commodity",
    "real_estate": "vacancia_locacao",
    "retail_wholesale": "operacional",
    "manufacturing": "operacional",
    "technology": "operacional",
    "life_sciences": "operacional",
    "blockchain": "indefinido",
    "finance": "indefinido",
}

# Palavras-chave por tipo. Ordem importa: o primeiro tipo que casar vence, e a
# lista está ordenada da consequência mais grave para a mais leve. Uma matéria
# que fala de fraude E de resultado é, para efeito de decisão, sobre a fraude.
PALAVRAS_POR_TIPO: tuple[tuple[str, tuple[str, ...]], ...] = (
    # Antes de `recuperacao_judicial` de propósito: "falencia do banco" casaria
    # com "falencia" e a matéria viraria um evento de um ativo só. Quebra de
    # banco não é a falência de uma empresa qualquer -- é o tipo de fato que o
    # Motor de Eventos Extremos precisa ver como candidato a contágio.
    ("quebra_bancaria", ("quebra do banco", "quebra de banco", "bank failure",
                         "liquidacao extrajudicial", "intervencao do banco central",
                         "regime de resolucao", "socorro ao banco", "bailout",
                         "fdic", "banco quebrou", "insolvencia do banco")),
    ("recuperacao_judicial", ("recuperacao judicial", "falencia", "bankruptcy",
                              "chapter 11", "liquidacao")),
    ("fraude_governanca", ("fraude", "fraud", "escandalo", "scandal",
                           "manipulacao", "irregularidade", "delacao")),
    ("crise_sistemica", ("crise sistemica", "contagio", "pane no sistema",
                         "corrida bancaria", "bank run", "credit crunch")),
    ("pandemia", ("pandemia", "epidemia", "surto de", "covid", "coronavirus",
                  "quarentena", "lockdown", "emergencia sanitaria",
                  "emergencia de saude publica", "outbreak", "gripe aviaria")),
    ("deslistagem", ("deslistagem", "delisting", "fechamento de capital",
                     "opa de fechamento")),
    ("fusao_aquisicao", ("fusao", "aquisicao", "incorporacao", "merger",
                         "acquisition", "takeover", "compra da")),
    ("divida_rating", ("rating", "rebaixamento", "downgrade", "upgrade de nota",
                       "moody", "fitch", "standard poor", "debenture",
                       "default", "calote")),
    ("litigio_regulatorio", ("processo", "acao judicial", "lawsuit", "multa",
                             "cade", "antitruste", "investigacao", "probe",
                             "liminar")),
    ("emissao_capital", ("follow on", "oferta subsequente", "emissao de cotas",
                         "ipo", "subscricao", "aumento de capital")),
    ("mudanca_gestao", ("novo presidente", "novo ceo", "renuncia", "demissao do",
                        "troca de comando", "steps down", "resigns")),
    ("dividendo", ("dividendo", "jcp", "juros sobre capital", "provento",
                   "dividend", "payout", "rendimento mensal")),
    ("guidance", ("guidance", "projecao", "perspectiva para", "outlook",
                  "revisa estimativa")),
    # Antes de `resultado_trimestral` porque "balanco anual" casa com "balanco"
    # e a materia viraria um trimestre que nao existiu. A lista e curta de
    # proposito: cada palavra aqui e uma que o trimestral perde.
    ("resultado_anual", ("balanco anual", "resultado anual", "resultado do ano",
                         "resultado do exercicio", "exercicio social",
                         "demonstracoes financeiras padronizadas", "dfp",
                         "lucro do ano", "annual results", "full year results",
                         "fiscal year results")),
    ("resultado_trimestral", ("balanco", "resultado do", "trimestre",
                              "quarterly", "earnings", "lucro liquido",
                              "receita liquida", "ebitda")),
    ("vacancia_locacao", ("vacancia", "locacao", "inquilino", "vacancy",
                          "contrato de aluguel", "abl")),
    ("regulacao_setorial", ("resolucao", "marco regulatorio", "aneel", "anatel",
                            "ans", "regulacao", "leilao de")),
    ("juros_politica_monetaria", ("selic", "copom", "taxa de juros", "fomc",
                                  "federal reserve", "banco central",
                                  "politica monetaria")),
    ("inflacao", ("inflacao", "ipca", "igp m", "cpi", "deflacao")),
    ("cambio", ("dolar", "cambio", "real se", "exchange rate", "moeda")),
    ("fiscal_politico", ("arcabouco fiscal", "orcamento", "reforma tributaria",
                         "deficit primario", "eleicao", "congresso aprova")),
    ("atividade_emprego", ("pib", "desemprego", "payroll", "atividade economica",
                           "caged")),
    # Antes de `commodity`: uma seca que quebra a safra é, para efeito de
    # decisão, o desastre -- e não a variação de preço que ele produziu. A
    # ordem inversa transformaria todo evento climático agrícola em "preço de
    # commodity" e apagaria a causa do registro.
    ("evento_climatico", ("enchente", "inundacao", "seca severa", "estiagem",
                          "furacao", "hurricane", "tufao", "terremoto",
                          "earthquake", "desastre natural", "incendio florestal",
                          "wildfire", "queimada", "geada", "el nino", "la nina",
                          "evento climatico", "catastrofe natural")),
    ("commodity", ("petroleo", "brent", "minerio de ferro", "commodity",
                   "safra", "opep")),
    ("geopolitica", ("guerra", "sancao", "tarifa", "conflito", "embargo",
                     "geopolitic")),
    ("concorrencia", ("concorrente", "market share", "nova entrante",
                      "perda de contrato")),
    ("operacional", ("producao", "fabrica", "planta", "acidente", "parada",
                     "manutencao", "output")),
)


def classificar(titulo: str, resumo: str | None = None,
                categorias=()) -> str:
    """Tipo de evento, sempre uma chave válida da taxonomia.

    Palavra-chave do texto tem prioridade sobre a categoria do provedor: a
    categoria é ampla demais (``finance``, ``technology``) e diria pouco sobre a
    materialidade, que é o que a relevância usa depois.
    """
    texto = normalizar_texto(f"{titulo or ''} {resumo or ''}")
    if texto:
        for chave, termos in PALAVRAS_POR_TIPO:
            if any(termo in texto for termo in termos):
                return chave

    for categoria in categorias or ():
        alvo = MAPA_CATEGORIA.get(str(categoria).strip().lower())
        if alvo and alvo != "indefinido":
            return alvo

    return taxonomia.TIPO_INDEFINIDO.chave


def _chave_de_agrupamento(noticia: Noticia) -> tuple[str, ...]:
    """O que precisa coincidir para duas matérias tratarem do mesmo fato."""
    ent = noticia.entidades
    if ent.tickers:
        return tuple(sorted(ent.tickers))
    if ent.empresas:
        return tuple(sorted(normalizar_texto(e) for e in ent.empresas))
    if ent.setores:
        return tuple(sorted(normalizar_texto(s) for s in ent.setores))
    if ent.ativos:
        return tuple(sorted(ent.ativos))
    if ent.paises:
        return tuple(sorted(ent.paises))
    return ()


# ── Agrupamento temático: o fato macro que não carrega entidade (A-145) ──────
#
# A revisão de 02/09 mediu o efeito: guerra, quebra de banco e evento climático
# produziram **2 eventos a partir de 2 matérias do mesmo fato**, porque a chave
# acima depende de ticker, empresa, setor, ativo ou país -- e a notícia macro
# não carrega nenhum deles. O efeito não para no agrupamento: com um cluster por
# evento, ``n_fontes_independentes`` vale 1 e o portão de confirmação reprova um
# fato que teve duas agências.
#
# Chave por tipo, sozinha, seria pior do que o defeito. Duas quebras de banco
# distintas na mesma janela viram um evento com dois domínios, e o portão de
# confirmação abre para uma confirmação que nunca existiu -- fabricar
# confirmação é o erro que abre a porta, e o atual só fecha portas.
#
# Por isso a chave temática exige afinidade lexical medida. A tabela abaixo foi
# medida com ``_afinidade`` sobre pares realistas (o script está no corpo do
# commit); ``overlap`` é ``|A ∩ B| / min(|A|, |B|)``:
#
#   devem agrupar (mesmo fato, 2 agências)      não podem agrupar (mesmo tipo)
#   enchente no RS (duas redações) ...... 0,75  Copom x Federal Reserve .. 0,00
#   OMS / Organização Mundial da Saúde .. 0,67  enchente x furacão ....... 0,00
#   BC liquida o Master (duas redações) . 0,67  OMS x lockdown local ..... 0,00
#   Copom (duas redações) ............... 0,40  BC x banco regional EUA .. 0,17
#   BC, redação mais distante ........... 0,33
#
# A margem entre 0,33 e 0,17 é estreita, e o limiar fica em 0,30 encostado no
# lado seguro: **na dúvida os eventos ficam separados**. Sub-agrupar reprova um
# fato verdadeiro -- custo já conhecido e visível; super-agrupar aprova um fato
# que ninguém confirmou. Só o segundo produz ação.
#
# Limitação declarada, e não contornada: o agrupamento exige **o mesmo tipo de
# evento**, então duas redações do mesmo fato que caiam em tipos diferentes
# continuam em eventos separados -- "BC intervem e liquida o Banco Master" cai
# em ``indefinido`` porque o vocabulário tem "liquidacao", não "liquida". Isso é
# lacuna de vocabulário do classificador, não do agrupamento, e alargar palavra
# solta ("cheia", "liquida") para fechá-la trocaria um erro visível por
# classificações erradas silenciosas.
#
# Nenhuma dessas matérias é vista como duplicata pela camada anterior: as
# distâncias de simhash dos quatro pares que devem agrupar são 22, 26, 28 e 27,
# muito acima do limite 8 de ``dedup``. Redação independente do mesmo fato não
# é quase-duplicata -- e é exatamente por isso que o agrupamento por evento
# existe como camada separada.
LIMIAR_AFINIDADE_TEMATICA = 0.30

#: Palavras que aparecem em qualquer manchete e não distinguem fato nenhum.
#: Lista curta de propósito: quanto mais se remove, mais duas matérias
#: diferentes se parecem.
_PALAVRAS_VAZIAS: frozenset[str] = frozenset("""
ate ao aos apos com como contra das dos ela ele eles entre foi for isso mais
mas nao nas nos numa para pela pelo pelos por que sem ser seu sua suas seus
sao sobre tem uma uns umas vai vao ver diz dizem segundo apos ainda
""".split())

#: Escopos em que a chave temática vale. Notícia de escopo ``ativo`` sem
#: entidade nenhuma continua abrindo evento próprio: se nem o sujeito foi
#: resolvido, agrupar por semelhança de manchete estaria adivinhando de quem é
#: o fato -- e o projeto já pagou caro por atribuir notícia a empresa que ela
#: não citou.
_ESCOPOS_TEMATICOS = frozenset({taxonomia.ESCOPO_MACRO, taxonomia.ESCOPO_SETOR})


def _tokens_tematicos(noticia: Noticia) -> frozenset[str]:
    """Palavras de conteúdo do título (o resumo não entra).

    O resumo varia muito de tamanho entre veículos, e um resumo longo dilui a
    interseção justamente nos pares que deveriam casar.
    """
    return frozenset(
        p for p in normalizar_texto(noticia.titulo or "").split()
        if len(p) >= 3 and p not in _PALAVRAS_VAZIAS)


def _afinidade(a: frozenset[str], b: frozenset[str]) -> float:
    """``|A ∩ B| / min(|A|, |B|)``, ou 0,0 se algum lado estiver vazio.

    A divisão pelo menor conjunto, e não pela união, é deliberada: manchete
    curta de agência e manchete longa de portal cobrindo o mesmo fato têm união
    grande e interseção do tamanho da curta. Jaccard puro puniria o par certo
    por diferença de comprimento.
    """
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _chave_tematica(noticia: Noticia) -> tuple[str, ...]:
    """Sentinela de agrupamento para o fato sem entidade, ou ``()``."""
    if taxonomia.tipo(noticia.tipo_evento).escopo not in _ESCOPOS_TEMATICOS:
        return ()
    if not _tokens_tematicos(noticia):
        return ()
    return ("tema", noticia.tipo_evento)


@dataclass
class Evento:
    """Um fato e todas as matérias que o cobrem."""

    id: str
    tipo: str
    chave: tuple[str, ...]
    clusters: list[Cluster] = field(default_factory=list)
    #: Palavras de conteúdo da matéria que abriu o evento. Só é usada quando a
    #: chave é temática, e é a **âncora**: toda candidata é comparada com a
    #: primeira, nunca com a união acumulada. Comparar com a união deixaria o
    #: evento derivar por transitividade -- A parecido com B, B parecido com C,
    #: e C acaba dentro de um evento com que não se parece.
    tokens_tema: frozenset[str] = field(default_factory=frozenset)

    @property
    def noticias(self) -> list[Noticia]:
        return [c.principal for c in self.clusters]

    @property
    def principal(self) -> Noticia:
        """A matéria que representa o evento: a de fonte mais confiável."""
        return max(self.noticias, key=lambda n: (n.confiabilidade,
                                                 -(n.publicado_em.timestamp()
                                                   if n.publicado_em else 0)))

    @property
    def dominios(self) -> tuple[str, ...]:
        vistos: list[str] = []
        for cluster in self.clusters:
            dominio = (cluster.principal.fonte.dominio
                       if cluster.principal.fonte else "")
            if dominio and dominio not in vistos:
                vistos.append(dominio)
        return tuple(vistos)

    @property
    def n_fontes_independentes(self) -> int:
        """Veículos distintos com apuração própria.

        Conta domínios distintos entre os PRINCIPAIS dos clusters. As cópias
        absorvidas por semelhança não entram: elas são o mesmo texto, e texto
        replicado não confirma nada.
        """
        return max(1, len(self.dominios))

    @property
    def confirmado_por_primaria(self) -> bool:
        return any(n.fonte is not None and n.fonte.primaria
                   for n in self.noticias)

    @property
    def entidades(self) -> Entidades:
        """União das entidades de todas as matérias do evento."""
        def junta(campo: str) -> tuple[str, ...]:
            vistos: list[str] = []
            for noticia in self.noticias:
                for valor in getattr(noticia.entidades, campo):
                    if valor not in vistos:
                        vistos.append(valor)
            return tuple(vistos)

        return Entidades(tickers=junta("tickers"), empresas=junta("empresas"),
                         setores=junta("setores"), paises=junta("paises"),
                         moedas=junta("moedas"), ativos=junta("ativos"))

    @property
    def primeiro_em(self) -> datetime | None:
        datas = [n.publicado_em for n in self.noticias if n.publicado_em]
        return min(datas) if datas else None

    @property
    def ultimo_em(self) -> datetime | None:
        datas = [n.publicado_em for n in self.noticias if n.publicado_em]
        return max(datas) if datas else None

    @property
    def estado_verificacao(self) -> str:
        if self.confirmado_por_primaria:
            return taxonomia.VERIF_FONTE_PRIMARIA
        if self.n_fontes_independentes >= 2:
            return taxonomia.VERIF_INDEPENDENTE
        return taxonomia.VERIF_NAO_VERIFICADA


def _id_evento(tipo: str, chave: tuple[str, ...], quando: datetime | None,
               tokens: frozenset[str] = frozenset()) -> str:
    """Identificador estável do evento.

    ``tokens`` entra no hash porque a chave temática é a **mesma sentinela**
    para todo evento daquele tipo: sem discriminador, duas quebras de banco
    distintas no mesmo dia receberiam o mesmo ``evento_id`` sem serem o mesmo
    evento -- e um id que colide não é id, é um rótulo que faz duas coisas
    passarem por uma.
    """
    dia = quando.strftime("%Y%m%d") if quando else "sem_data"
    tema = "+".join(sorted(tokens))
    bruto = f"{tipo}|{'+'.join(chave)}|{dia}|{tema}"
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()[:16]


def agrupar(clusters: list[Cluster],
            janela_horas: float = JANELA_PADRAO_H) -> list[Evento]:
    """Agrupa clusters de duplicata em eventos.

    Coincidem quando têm o mesmo tipo, a mesma chave de entidade e caem dentro
    da janela. Matéria sem data **não** entra em nenhum evento existente por
    proximidade temporal -- abre o próprio. Encaixar por ausência de data já
    produziu, neste projeto, centenas de encerramentos que nunca aconteceram.
    """
    eventos: list[Evento] = []
    janela = timedelta(hours=max(0.0, janela_horas))

    for cluster in clusters:
        noticia = cluster.principal
        chave = _chave_de_agrupamento(noticia)
        tokens: frozenset[str] = frozenset()
        if not chave:
            chave = _chave_tematica(noticia)
            if chave:
                tokens = _tokens_tematicos(noticia)
        alvo: Evento | None = None

        if chave:
            for evento in eventos:
                if evento.tipo != noticia.tipo_evento or evento.chave != chave:
                    continue
                if noticia.publicado_em is None or evento.ultimo_em is None:
                    continue
                if abs(noticia.publicado_em - evento.ultimo_em) > janela:
                    continue
                # Chave temática só junta com afinidade lexical medida; chave de
                # entidade não passa por aqui, porque a entidade já é a prova.
                if tokens and _afinidade(tokens, evento.tokens_tema) <                         LIMIAR_AFINIDADE_TEMATICA:
                    continue
                alvo = evento
                break

        if alvo is None:
            eventos.append(Evento(
                id=_id_evento(noticia.tipo_evento, chave, noticia.publicado_em,
                              tokens),
                tipo=noticia.tipo_evento,
                chave=chave,
                clusters=[cluster],
                tokens_tema=tokens,
            ))
        else:
            alvo.clusters.append(cluster)

    return eventos
