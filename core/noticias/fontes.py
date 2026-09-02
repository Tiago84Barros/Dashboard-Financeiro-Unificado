"""Classificação e confiabilidade das fontes de notícia.

A confiabilidade é do VEÍCULO, não da matéria. Um comunicado da CVM e um post de
blog dizendo a mesma coisa não são a mesma evidência, e o índice de relevância
precisa dessa diferença explicitamente -- senão ele mede volume de publicação,
que é justamente o que agregador infla.

**Fonte desconhecida recebe confiabilidade baixa, não ausente.** É a única
assimetria deliberada deste módulo: em quase todo o resto do projeto a ausência
de dado vira ``None`` para não punir quem não foi medido. Aqui não dá: a
confiabilidade da fonte é a defesa contra conteúdo plantado, e tratar um domínio
que nunca vimos como "não sei, então não conta" faria o desconhecido pesar
exatamente como a CVM depois da renormalização. Quem não se identifica não ganha
o benefício da dúvida -- ganha o piso, e o piso aparece no texto.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

# Classes de fonte, da mais forte para a mais fraca.
CLASSE_PRIMARIA = "primaria"        # a própria companhia / o próprio regulador
CLASSE_REGULADOR = "regulador"      # CVM, SEC, BCB, B3
CLASSE_AGENCIA = "agencia"          # Reuters, AP, Bloomberg, AFP
CLASSE_ESPECIALIZADA = "imprensa_especializada"
CLASSE_GERAL = "imprensa_geral"
CLASSE_AGREGADOR = "agregador"
CLASSE_BLOG = "blog"
CLASSE_DESCONHECIDA = "desconhecida"

CLASSES = (
    CLASSE_PRIMARIA, CLASSE_REGULADOR, CLASSE_AGENCIA, CLASSE_ESPECIALIZADA,
    CLASSE_GERAL, CLASSE_AGREGADOR, CLASSE_BLOG, CLASSE_DESCONHECIDA,
)

# Confiabilidade por classe, em 0..1.
CONFIABILIDADE_POR_CLASSE: dict[str, float] = {
    CLASSE_PRIMARIA: 1.00,
    CLASSE_REGULADOR: 1.00,
    CLASSE_AGENCIA: 0.90,
    CLASSE_ESPECIALIZADA: 0.75,
    CLASSE_GERAL: 0.60,
    CLASSE_AGREGADOR: 0.40,
    CLASSE_BLOG: 0.25,
    CLASSE_DESCONHECIDA: 0.20,
}

# Só estas contam como "fonte primária" para o portão de aporte.
CLASSES_PRIMARIAS = frozenset({CLASSE_PRIMARIA, CLASSE_REGULADOR})


@dataclass(frozen=True)
class Fonte:
    """Identidade e reputação de um veículo."""

    dominio: str
    veiculo: str
    classe: str
    confiabilidade: float
    pais: str | None = None
    idioma: str | None = None

    @property
    def primaria(self) -> bool:
        return self.classe in CLASSES_PRIMARIAS


def _f(dominio, veiculo, classe, pais=None, idioma=None) -> Fonte:
    return Fonte(dominio, veiculo, classe, CONFIABILIDADE_POR_CLASSE[classe],
                 pais, idioma)


# Catálogo curado. Não pretende ser exaustivo: é o conjunto que o projeto já
# consome (CVM, SEC, BCB) mais os veículos que os provedores gratuitos mais
# devolvem. Domínio fora daqui cai em `CLASSE_DESCONHECIDA` e o texto do selo
# diz isso.
_CATALOGO: tuple[Fonte, ...] = (
    # Reguladores e fontes primárias
    _f("cvm.gov.br", "CVM", CLASSE_REGULADOR, "BR", "pt"),
    _f("dados.cvm.gov.br", "CVM Dados Abertos", CLASSE_REGULADOR, "BR", "pt"),
    _f("rad.cvm.gov.br", "CVM / ENET", CLASSE_REGULADOR, "BR", "pt"),
    _f("sec.gov", "SEC", CLASSE_REGULADOR, "US", "en"),
    _f("bcb.gov.br", "Banco Central do Brasil", CLASSE_REGULADOR, "BR", "pt"),
    _f("b3.com.br", "B3", CLASSE_REGULADOR, "BR", "pt"),
    _f("ibge.gov.br", "IBGE", CLASSE_REGULADOR, "BR", "pt"),
    _f("gov.br", "Governo Federal", CLASSE_REGULADOR, "BR", "pt"),
    _f("federalreserve.gov", "Federal Reserve", CLASSE_REGULADOR, "US", "en"),
    _f("businesswire.com", "Business Wire (release)", CLASSE_PRIMARIA, "US", "en"),
    _f("prnewswire.com", "PR Newswire (release)", CLASSE_PRIMARIA, "US", "en"),
    _f("globenewswire.com", "GlobeNewswire (release)", CLASSE_PRIMARIA, "US", "en"),
    # Agências
    _f("reuters.com", "Reuters", CLASSE_AGENCIA, "GB", "en"),
    _f("bloomberg.com", "Bloomberg", CLASSE_AGENCIA, "US", "en"),
    _f("apnews.com", "Associated Press", CLASSE_AGENCIA, "US", "en"),
    _f("afp.com", "AFP", CLASSE_AGENCIA, "FR", "fr"),
    _f("ft.com", "Financial Times", CLASSE_AGENCIA, "GB", "en"),
    _f("wsj.com", "Wall Street Journal", CLASSE_AGENCIA, "US", "en"),
    # Imprensa especializada
    _f("valor.globo.com", "Valor Econômico", CLASSE_ESPECIALIZADA, "BR", "pt"),
    _f("valorinveste.globo.com", "Valor Investe", CLASSE_ESPECIALIZADA, "BR", "pt"),
    _f("infomoney.com.br", "InfoMoney", CLASSE_ESPECIALIZADA, "BR", "pt"),
    _f("moneytimes.com.br", "Money Times", CLASSE_ESPECIALIZADA, "BR", "pt"),
    _f("braziljournal.com", "Brazil Journal", CLASSE_ESPECIALIZADA, "BR", "pt"),
    _f("neofeed.com.br", "NeoFeed", CLASSE_ESPECIALIZADA, "BR", "pt"),
    _f("exame.com", "Exame", CLASSE_ESPECIALIZADA, "BR", "pt"),
    _f("investing.com", "Investing.com", CLASSE_ESPECIALIZADA, None, None),
    _f("cnbc.com", "CNBC", CLASSE_ESPECIALIZADA, "US", "en"),
    _f("marketwatch.com", "MarketWatch", CLASSE_ESPECIALIZADA, "US", "en"),
    _f("barrons.com", "Barrons", CLASSE_ESPECIALIZADA, "US", "en"),
    _f("fundsexplorer.com.br", "Funds Explorer", CLASSE_ESPECIALIZADA, "BR", "pt"),
    _f("clubefii.com.br", "Clube FII", CLASSE_ESPECIALIZADA, "BR", "pt"),
    # Imprensa geral
    _f("g1.globo.com", "G1", CLASSE_GERAL, "BR", "pt"),
    _f("folha.uol.com.br", "Folha de S.Paulo", CLASSE_GERAL, "BR", "pt"),
    _f("estadao.com.br", "O Estado de S. Paulo", CLASSE_GERAL, "BR", "pt"),
    _f("uol.com.br", "UOL", CLASSE_GERAL, "BR", "pt"),
    _f("cnnbrasil.com.br", "CNN Brasil", CLASSE_GERAL, "BR", "pt"),
    _f("bbc.com", "BBC", CLASSE_GERAL, "GB", "en"),
    _f("nytimes.com", "The New York Times", CLASSE_GERAL, "US", "en"),
    # Agregadores e portais de conteúdo enviado
    _f("finance.yahoo.com", "Yahoo Finance", CLASSE_AGREGADOR, "US", "en"),
    _f("yahoo.com", "Yahoo", CLASSE_AGREGADOR, "US", "en"),
    _f("msn.com", "MSN", CLASSE_AGREGADOR, None, None),
    _f("news.google.com", "Google Notícias", CLASSE_AGREGADOR, None, None),
    _f("benzinga.com", "Benzinga", CLASSE_AGREGADOR, "US", "en"),
    _f("zacks.com", "Zacks", CLASSE_AGREGADOR, "US", "en"),
    _f("seekingalpha.com", "Seeking Alpha", CLASSE_BLOG, "US", "en"),
    _f("fool.com", "Motley Fool", CLASSE_BLOG, "US", "en"),
)

POR_DOMINIO: dict[str, Fonte] = {f.dominio: f for f in _CATALOGO}

DESCONHECIDA = Fonte(
    dominio="",
    veiculo="Fonte não catalogada",
    classe=CLASSE_DESCONHECIDA,
    confiabilidade=CONFIABILIDADE_POR_CLASSE[CLASSE_DESCONHECIDA],
)

_LIMPA_PORTA = re.compile(r":\d+$")


def dominio_de(url: str | None) -> str:
    """Domínio do host, em minúsculas e sem ``www``.

    Guarda o host inteiro em vez de recortar pelo sufixo público: para
    ``valor.globo.com`` o veículo é o Valor, e um recorte por sufixo devolveria
    ``globo.com`` e classificaria o Valor como imprensa geral.
    """
    if not url:
        return ""
    texto = str(url).strip()
    if "://" not in texto:
        texto = "//" + texto
    try:
        host = (urlsplit(texto).hostname or "").lower()
    except ValueError:
        return ""
    host = _LIMPA_PORTA.sub("", host)
    return host[4:] if host.startswith("www.") else host


def classificar(url: str | None, veiculo: str | None = None) -> Fonte:
    """Classifica pelo domínio da URL, subindo na hierarquia de host.

    ``news.infomoney.com.br`` cai em ``infomoney.com.br`` sem precisar de
    entrada própria. Quando nada bate, devolve a fonte desconhecida carregando o
    domínio e o nome que o provedor informou -- perder o nome faria a tela dizer
    "Fonte não catalogada" sem dizer qual.
    """
    host = dominio_de(url)
    if host:
        partes = host.split(".")
        for i in range(len(partes) - 1):
            achado = POR_DOMINIO.get(".".join(partes[i:]))
            if achado is not None:
                return achado
    nome = (veiculo or "").strip() or (host or DESCONHECIDA.veiculo)
    return Fonte(
        dominio=host,
        veiculo=nome,
        classe=CLASSE_DESCONHECIDA,
        confiabilidade=DESCONHECIDA.confiabilidade,
    )


def confiavel(fonte: Fonte, piso: float = 0.40) -> bool:
    """Se a fonte passa do piso de confiabilidade usado pelos portões."""
    return fonte.confiabilidade >= piso
