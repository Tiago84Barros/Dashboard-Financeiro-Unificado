"""Normalizacao de URL, data, texto e idioma.

Regra dura do modulo: **data sempre em UTC timezone-aware, ou `None`**. Nunca
naive, nunca "hoje" como fallback. Uma data inventada aqui vira, tres camadas
adiante, uma noticia de 2019 exibida como se fosse de agora -- que e exatamente
o que o requisito proibe.
"""
from __future__ import annotations

import html
import re
import unicodedata
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Parametros de rastreamento: nao mudam o conteudo, so a atribuicao de campanha.
# Duas URLs que so diferem nisso sao a mesma materia, e mante-los faria o dedup
# por hash falhar exatamente nos agregadores, que sao quem mais os usa.
_PARAMS_DESCARTE = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name", "utm_reader", "utm_brand", "utm_social",
    "fbclid", "gclid", "dclid", "msclkid", "igshid", "mc_cid", "mc_eid",
    "ref", "referrer", "source", "src", "cmpid", "smid", "partner",
    "yptr", "guccounter", "guce_referrer", "guce_referrer_sig",
    "__twitter_impression", "spm", "share", "amp",
})

_FORMATOS_DATA = (
    "%Y%m%dT%H%M%S",     # Alpha Vantage NEWS_SENTIMENT
    "%Y%m%dT%H%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
)

_TAGS = re.compile(r"<[^>]+>")
_ESPACOS = re.compile(r"\s+")
_NAO_PALAVRA = re.compile(r"[^\w\s]", re.UNICODE)


def url_canonica(url: str | None) -> str:
    """Forma canonica da URL, para servir de chave de deduplicacao.

    Minusculiza esquema e host, remove ``www``, descarta parametros de
    rastreamento, ordena os que sobram, joga fora o fragmento e a barra final.
    O caminho preserva a caixa: em varios CMS o slug e sensivel a maiusculas e
    minusculizar geraria uma chave para uma URL que nao existe.
    """
    if not url:
        return ""
    texto = str(url).strip()
    if not texto:
        return ""
    if "://" not in texto:
        texto = "https://" + texto
    try:
        partes = urlsplit(texto)
    except ValueError:
        return texto

    host = (partes.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if partes.port and partes.port not in (80, 443):
        host = f"{host}:{partes.port}"

    consulta = [
        (k, v) for k, v in parse_qsl(partes.query, keep_blank_values=False)
        if k.lower() not in _PARAMS_DESCARTE
    ]
    consulta.sort()

    caminho = partes.path or "/"
    if len(caminho) > 1 and caminho.endswith("/"):
        caminho = caminho[:-1]

    return urlunsplit((
        (partes.scheme or "https").lower(),
        host,
        caminho,
        urlencode(consulta),
        "",
    ))


def para_utc(valor) -> datetime | None:
    """Converte o que o provedor mandou para ``datetime`` UTC aware.

    Aceita ``datetime``, epoch numerico, ISO 8601 (com ``Z`` ou deslocamento),
    RFC 2822 (RSS) e os formatos compactos das APIs. Devolve ``None`` para
    qualquer coisa que nao seja reconhecida com seguranca.

    **Datetime naive e tratado como UTC.** E uma suposicao, e ela esta
    registrada nas limitacoes: os provedores usados publicam em UTC, mas nenhum
    deles declara o fuso no payload. Chutar o fuso local seria pior -- deslocaria
    toda a base pelo fuso da maquina que coletou.
    """
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return (valor.replace(tzinfo=timezone.utc) if valor.tzinfo is None
                else valor.astimezone(timezone.utc))
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        # Epoch em segundos; milissegundos aparecem em algumas APIs.
        segundos = float(valor)
        if segundos > 1e11:
            segundos /= 1000.0
        try:
            return datetime.fromtimestamp(segundos, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    texto = str(valor).strip()
    if not texto:
        return None

    try:
        achado = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError:
        achado = None
    if achado is not None:
        return para_utc(achado)

    for formato in _FORMATOS_DATA:
        try:
            return para_utc(datetime.strptime(texto, formato))
        except ValueError:
            continue

    try:
        return para_utc(parsedate_to_datetime(texto))
    except (TypeError, ValueError, IndexError):
        return None


def limpar_html(texto: str | None) -> str:
    """Tira marcacao e entidades. RSS entrega descricao como HTML."""
    if not texto:
        return ""
    limpo = _TAGS.sub(" ", str(texto))
    return _ESPACOS.sub(" ", html.unescape(limpo)).strip()


#: Rodape que varios CMS anexam a descricao do item no RSS: "The post <titulo>
#: appeared first on <veiculo>." Nao e conteudo -- e assinatura de plugin.
#:
#: Sai porque o texto do item alimenta a resolucao de entidades, e o cadastro
#: americano tem uma empresa chamada Post (POST, Post Holdings). Na coleta de
#: 05/09/2026 esse rodape sozinho atribuiu POST a 30 dos 48 itens do acervo,
#: quase todos sobre assunto nenhum ligado a empresa. Cortar aqui, na entrada,
#: e melhor que filtrar POST na saida: o problema nao e a empresa, e o ruido.
_RODAPE_FEED = re.compile(
    r"\s*the\s+post\s+.*?\s+appeared\s+first\s+on\b.*$",
    re.IGNORECASE | re.DOTALL)


def sem_rodape_de_feed(texto: str | None) -> str:
    """Remove a assinatura de plugin do fim da descricao de um item RSS."""
    if not texto:
        return ""
    return _ESPACOS.sub(" ", _RODAPE_FEED.sub("", str(texto))).strip()


def normalizar_texto(texto: str | None) -> str:
    """Minuscula, sem acento, sem pontuacao, espacos colapsados.

    E a forma usada para hash de conteudo e para simhash. Manter acento faria
    ``Petrobras eleva producao`` e ``Petrobras eleva produção`` -- a mesma
    manchete reescrita por dois veiculos -- virarem duas noticias.
    """
    if not texto:
        return ""
    base = unicodedata.normalize("NFKD", str(texto))
    base = "".join(c for c in base if not unicodedata.combining(c))
    base = _NAO_PALAVRA.sub(" ", base.lower())
    return _ESPACOS.sub(" ", base).strip()


_STOPWORDS_PT = frozenset({
    "de", "da", "do", "das", "dos", "que", "para", "com", "uma", "nao", "por",
    "mais", "como", "mas", "sobre", "apos", "ate", "pelo", "pela", "sao",
})
_STOPWORDS_EN = frozenset({
    "the", "of", "and", "for", "with", "that", "from", "after", "over", "will",
    "has", "have", "its", "into", "amid", "says", "than", "their",
})


def detectar_idioma(texto: str | None) -> str | None:
    """Heuristica de idioma por palavras funcionais. ``None`` no empate.

    Nao pretende ser um classificador: serve para separar pt de en, que e a
    unica distincao que o motor usa (o lexico de sentimento e por idioma).
    Empate devolve ``None`` e o lexico neutro nao pontua -- melhor sem nota do
    que com nota do lexico errado.
    """
    palavras = set(normalizar_texto(texto).split())
    if not palavras:
        return None
    pt = len(palavras & _STOPWORDS_PT)
    en = len(palavras & _STOPWORDS_EN)
    if pt == en:
        return None
    return "pt" if pt > en else "en"
