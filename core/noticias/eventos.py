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
    ("recuperacao_judicial", ("recuperacao judicial", "falencia", "bankruptcy",
                              "chapter 11", "liquidacao")),
    ("fraude_governanca", ("fraude", "fraud", "escandalo", "scandal",
                           "manipulacao", "irregularidade", "delacao")),
    ("crise_sistemica", ("crise sistemica", "contagio", "pane no sistema",
                         "corrida bancaria", "bank run", "credit crunch")),
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


@dataclass
class Evento:
    """Um fato e todas as matérias que o cobrem."""

    id: str
    tipo: str
    chave: tuple[str, ...]
    clusters: list[Cluster] = field(default_factory=list)

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


def _id_evento(tipo: str, chave: tuple[str, ...], quando: datetime | None) -> str:
    dia = quando.strftime("%Y%m%d") if quando else "sem_data"
    bruto = f"{tipo}|{'+'.join(chave)}|{dia}"
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
        alvo: Evento | None = None

        if chave:
            for evento in eventos:
                if evento.tipo != noticia.tipo_evento or evento.chave != chave:
                    continue
                if noticia.publicado_em is None or evento.ultimo_em is None:
                    continue
                if abs(noticia.publicado_em - evento.ultimo_em) <= janela:
                    alvo = evento
                    break

        if alvo is None:
            eventos.append(Evento(
                id=_id_evento(noticia.tipo_evento, chave, noticia.publicado_em),
                tipo=noticia.tipo_evento,
                chave=chave,
                clusters=[cluster],
            ))
        else:
            alvo.clusters.append(cluster)

    return eventos
