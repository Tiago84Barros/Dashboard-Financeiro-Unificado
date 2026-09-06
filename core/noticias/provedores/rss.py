"""Provedor de RSS/Atom, sem chave.

Existe por três motivos concretos:

1. **Cobertura do Brasil.** Alpha Vantage e Marketaux são fracos em B3 e
   praticamente cegos para FII. O feed do veículo brasileiro é a única fonte
   gratuita que cobre isso de verdade.
2. **Prova de que a abstração vale.** Um provedor sem chave, em XML, com outro
   modelo de erro, exercita o contrato de um jeito que dois clientes de API
   REST parecidos não exercitariam.
3. **Chão de coleta.** Quando a cota diária das APIs acaba, o RSS continua.

Uma instância por feed, de propósito: cada feed tem disponibilidade própria, e
o motor precisa poder dizer "InfoMoney respondeu, Valor não" em vez de "RSS
falhou". A cota é contada pela família (``rss``), não pela instância.

**Cuidado com XML de terceiro.** O corpo é rejeitado se trouxer ``DOCTYPE`` ou
se passar do teto de tamanho: é a mitigação de expansão de entidade sem
acrescentar dependência nova ao ``requirements.txt``. Feed é conteúdo remoto
não confiável, e o parser da biblioteca padrão não protege sozinho.
"""
from __future__ import annotations

import re
from xml.etree import ElementTree

from core.noticias.normalizacao import limpar_html
from core.noticias.provedores.base import (
    Consulta,
    ItemBruto,
    ProvedorBase,
    RespostaInvalida,
    _texto,
)
from core.noticias.transporte import Resposta

TETO_BYTES = 4 * 1024 * 1024
_DOCTYPE = re.compile(r"<!DOCTYPE", re.IGNORECASE)

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "dc": "http://purl.org/dc/elements/1.1/",
    "content": "http://purl.org/rss/1.0/modules/content/",
}


def _slug(texto: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", texto.lower()).strip("_") or "feed"


class ProvedorRSS(ProvedorBase):
    """Um feed RSS 2.0 ou Atom."""

    nao_suporta = ("tickers", "temas", "paises", "idiomas")

    def __init__(self, transporte, *, feed: str, rotulo: str | None = None,
                 idioma: str | None = None, pais: str | None = None, **kwargs):
        super().__init__(transporte, **kwargs)
        self._feed = feed
        self._rotulo = rotulo or feed
        self._idioma = idioma
        self._pais = pais
        self.nome = f"rss:{_slug(self._rotulo)}"

    @property
    def rotulo(self) -> str:
        return self._rotulo

    def disponivel(self) -> bool:
        return bool(self._feed)

    def _requisicao(self, consulta: Consulta) -> tuple[str, dict[str, object]]:
        return self._feed, {}

    def _carregar_json(self, resposta: Resposta) -> object:
        """Converte o XML num dicionário simples, serializável para o cache.

        O cache é JSON; devolver a árvore do ElementTree faria a gravação
        falhar em silêncio e o provedor voltar à rede a cada chamada, gastando
        justamente o que o cache existe para poupar.
        """
        texto = resposta.texto or ""
        if len(texto.encode("utf-8", "ignore")) > TETO_BYTES:
            raise RespostaInvalida(self.nome, "feed acima do teto de tamanho")
        if _DOCTYPE.search(texto):
            raise RespostaInvalida(self.nome, "feed com DOCTYPE recusado")
        try:
            raiz = ElementTree.fromstring(texto)
        except ElementTree.ParseError as exc:
            raise RespostaInvalida(self.nome, f"XML malformado: {exc}") from exc

        entradas = raiz.findall(".//item") or raiz.findall(".//atom:entry", _NS)
        itens = []
        for entrada in entradas:
            itens.append({
                "titulo": self._campo(entrada, ("title", "atom:title")),
                "url": self._link(entrada),
                "resumo": self._campo(entrada, ("description", "atom:summary",
                                                "content:encoded")),
                "autor": self._campo(entrada, ("author", "dc:creator",
                                               "atom:author/atom:name")),
                "data": self._campo(entrada, ("pubDate", "atom:published",
                                              "atom:updated", "dc:date")),
                "categorias": [c.text.strip() for c in entrada.findall("category")
                               if (c.text or "").strip()],
            })
        return {"feed": self._feed, "rotulo": self._rotulo, "itens": itens}

    @staticmethod
    def _campo(no, caminhos: tuple[str, ...]) -> str | None:
        for caminho in caminhos:
            achado = no.find(caminho, _NS)
            if achado is not None and (achado.text or "").strip():
                return achado.text.strip()
        return None

    @staticmethod
    def _link(no) -> str | None:
        achado = no.find("link")
        if achado is not None:
            # RSS põe a URL no texto; Atom põe no atributo href.
            if (achado.text or "").strip():
                return achado.text.strip()
            href = achado.get("href")
            if href:
                return href.strip()
        for alternativo in no.findall("atom:link", _NS):
            rel = alternativo.get("rel") or "alternate"
            href = alternativo.get("href")
            if rel == "alternate" and href:
                return href.strip()
        return _texto(no.findtext("guid"))

    def _extrair(self, carga: object) -> list[ItemBruto]:
        if not isinstance(carga, dict) or not isinstance(carga.get("itens"), list):
            raise RespostaInvalida(self.nome, "carga de feed inesperada")
        itens: list[ItemBruto] = []
        for cru in carga["itens"]:
            if not isinstance(cru, dict):
                continue
            titulo = _texto(cru.get("titulo"))
            url = _texto(cru.get("url"))
            if not titulo or not url:
                continue
            categorias = cru.get("categorias")
            itens.append(ItemBruto(
                titulo=limpar_html(titulo),
                url=url,
                resumo=limpar_html(cru.get("resumo")) or None,
                veiculo=_texto(carga.get("rotulo")) or self._rotulo,
                autor=_texto(cru.get("autor")),
                publicado_em=_texto(cru.get("data")),
                idioma=self._idioma,
                pais=self._pais,
                categorias=tuple(str(c) for c in categorias
                                 if str(c).strip()) if isinstance(categorias, list) else (),
                # RSS não traz sentimento nem relevância. Ficam ``None``, e não
                # zero: o motor precisa distinguir "o provedor não mediu" de "o
                # provedor mediu e deu neutro".
                sentimento_api=None,
                relevancia_api=None,
                bruto={"feed": _texto(carga.get("feed"))},
            ))
        return itens


#: Feeds públicos usados como cobertura de base do mercado brasileiro. São
#: veículos já catalogados em ``core/noticias/fontes.py``; acrescentar um feed
#: sem catalogar o domínio faz a fonte cair no piso de confiabilidade.
#: Feeds padrão. Todos foram medidos pelo pipeline real em 06/09/2026 antes de
#: entrar: os doze responderam, com **100% dos itens datados**, e nenhum caiu em
#: ``CLASSE_DESCONHECIDA`` -- ``suno.com.br``, ``seudinheiro.com`` e
#: ``investors.com`` (que aparece via Yahoo) foram catalogados em
#: :mod:`core.noticias.fontes` no mesmo commit, porque feed sem domínio
#: catalogado entra pelo piso de confiabilidade e o piso é silencioso.
#:
#: Recusados na medição, e por quê: Clube FII devolve 403 ao nosso User-Agent,
#: o feed da Reuters não resolve DNS e o da Nasdaq estoura o tempo limite.
FEEDS_PADRAO: tuple[dict[str, str], ...] = (
    {"feed": "https://www.infomoney.com.br/feed/",
     "rotulo": "InfoMoney", "idioma": "pt", "pais": "BR"},
    {"feed": "https://www.moneytimes.com.br/feed/",
     "rotulo": "Money Times", "idioma": "pt", "pais": "BR"},
    {"feed": "https://braziljournal.com/feed/",
     "rotulo": "Brazil Journal", "idioma": "pt", "pais": "BR"},
    {"feed": "https://neofeed.com.br/feed/",
     "rotulo": "NeoFeed", "idioma": "pt", "pais": "BR"},
    {"feed": "https://valorinveste.globo.com/rss/valorinveste/",
     "rotulo": "Valor Investe", "idioma": "pt", "pais": "BR"},
    {"feed": "https://exame.com/feed/",
     "rotulo": "Exame", "idioma": "pt", "pais": "BR"},
    {"feed": "https://br.investing.com/rss/news.rss",
     "rotulo": "Investing BR", "idioma": "pt", "pais": "BR"},
    {"feed": "https://www.suno.com.br/noticias/feed/",
     "rotulo": "Suno Noticias", "idioma": "pt", "pais": "BR"},
    {"feed": "https://www.seudinheiro.com/feed/",
     "rotulo": "Seu Dinheiro", "idioma": "pt", "pais": "BR"},
    # Cobertura dos EUA. Entra aqui, e não por mais chamada de API, porque RSS
    # não tem cota: é a única ampliação que não disputa o teto diário do Alpha
    # Vantage nem o de itens por resposta do Marketaux.
    {"feed": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
     "rotulo": "CNBC", "idioma": "en", "pais": "US"},
    {"feed": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
     "rotulo": "MarketWatch", "idioma": "en", "pais": "US"},
    {"feed": "https://finance.yahoo.com/news/rssindex",
     "rotulo": "Yahoo Finance", "idioma": "en", "pais": "US"},
)


def feeds_padrao(transporte, **kwargs) -> list[ProvedorRSS]:
    """Instancia um provedor por feed da lista padrão."""
    return [ProvedorRSS(transporte, **{**cfg, **kwargs}) for cfg in FEEDS_PADRAO]
