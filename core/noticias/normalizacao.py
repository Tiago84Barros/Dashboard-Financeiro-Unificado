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


#: Par "lead + continuacao" do UTF-8 lido como latin-1: ``\u00c3\u00a7`` (c
#: cedilha), ``\u00c3\u00a3`` (a til), ``\u00e2\u0080\u0099`` (aspas curvas). Em portugues legitimo
#: esse par nao ocorre -- ``\u00c3`` e ``\u00c2`` maiusculos nao sao seguidos de
#: controle C1 nem de ``\u00a7``/``\u00a3``/``\u00a9``. Escrito em escapes de proposito: os
#: caracteres literais incluem controles invisiveis no editor.
_MOJIBAKE = re.compile("[\u00c2-\u00f4][\u0080-\u00bf]")
#: Uma sequencia UTF-8 completa vista como latin-1, para o conserto trecho a
#: trecho quando o texto mistura parte quebrada e parte boa.
_SEQ_MOJIBAKE = re.compile(
    "[\u00c2-\u00df][\u0080-\u00bf]|[\u00e0-\u00ef][\u0080-\u00bf]{2}"
    "|[\u00f0-\u00f4][\u0080-\u00bf]{3}")
#: O a com crase e ``C3 A0``; o ``A0`` vira espaco nao separavel, e o colapso
#: de espacos da coleta o troca por espaco comum. Sobra ``\u00c3`` + espaco --
#: lido como crase so quando o mesmo texto ja provou ser mojibake (~180 casos
#: no acervo). O espaco que sobra e ambiguo: "a crase" + espaco ("chega a
#: sexta") ou so o NBSP colapsado dentro da palavra ("as 20h10" vira
#: ``Ã s 20h10``). Medido na Exame: ``Ã s`` + fim de palavra apareceu 87
#: vezes, todas "as" com crase; as contracoes ``aquel``/``aquil`` seguem a
#: mesma regra. O resto mantem o espaco.
_A_CRASE_PERDIDO = re.compile(
    "\u00c3(?:[^\\S\u00a0](?=s\\b|qu[ei]l)|(?=[^\\S\u00a0]|$))")
#: ``Ã`` sozinho, entre espacos, nao e palavra em portugues -- e
#: ``Ã`` + espaco nao separavel colapsado, sem outro sinal no texto que
#: o prove. Medido no acervo: 9 titulos e 6 resumos da Exame ("chega ``Ã`` sexta
#: semifinal", "China doa ``Ã`` ONU") so tinham esse sinal. Palavra que
#: termina em ``Ã`` maiusculo ("IRM``Ã`` DE") nao casa: ha letra antes.
_A_CRASE_SOLTO = re.compile("(?<!\\w)\u00c3(?:[^\\S\u00a0](?=s\\b|qu[ei]l)|(?=[^\\S\u00a0]))")
_NBSP = "\u00a0"
_NBSP_SOLTO = re.compile("(?<!\u00c3)\u00a0")


def _trecho_utf8(achado: re.Match) -> str:
    trecho = achado.group(0)
    for codec in ("latin-1", "cp1252"):
        try:
            return trecho.encode(codec).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return trecho


def consertar_mojibake(texto: str | None) -> str:
    """Desfaz UTF-8 decodificado como latin-1 (``AutorizaÃ§Ã£o`` -> ``Autorização``).

    Medido no acervo local em 04/10/2026: 2.923 dos 3.377 itens da Exame
    (86,6%; 9,0% do acervo de 32.405) chegavam assim desde 06/09, porque o
    feed responde ``text/xml`` sem ``charset`` e o ``requests`` assume
    ISO-8859-1 para ``text/*``. A causa foi corrigida no transporte; isto
    conserta o que ja entrou e o que vier de outro feed com o mesmo defeito.

    Sem ``ftfy`` de proposito: nao esta no ``requirements.txt`` e o defeito e
    um so, de forma conhecida. A guarda e a propria decodificacao -- texto em
    latin-1 legitimo quase nunca e UTF-8 valido -- e ``Ã `` so vira ``à``
    quando o resto do texto ja provou ser mojibake. Texto sem o par lead +
    continuacao sai intacto; a funcao e idempotente.
    """
    if not texto:
        return ""
    s = str(texto)
    # Ate tres passadas: 1 item do acervo (easybourse) veio decodificado errado DUAS
    # vezes (``d\xc3\x83\xc2\xa9sordre``), e uma passada so deixaria a funcao nao
    # idempotente -- o conserto de hoje viraria o conserto de amanha.
    for _ in range(3):
        novo = _uma_passada(s)
        if novo == s:
            break
        s = novo
    return s


def _uma_passada(s: str) -> str:
    if not _MOJIBAKE.search(s):
        return _A_CRASE_SOLTO.sub("\u00e0", s)
    candidato = _A_CRASE_PERDIDO.sub("\u00c3" + _NBSP, s)
    for codec in ("latin-1", "cp1252"):
        try:
            # O NBSP que sobra vira espaço, salvo colado num ``Ã``: aí ele é
            # a 2a camada de um ``à`` com mojibake duplo, e a próxima passada
            # o consome (1 item da easybourse, medido no acervo). NBSP também
            # saiu do ``\s`` dos padrões da crase pelo mesmo motivo.
            return _NBSP_SOLTO.sub(" ", candidato.encode(codec).decode("utf-8"))
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    # Texto misto (parte ja correta, parte quebrada): trecho a trecho.
    consertado = _SEQ_MOJIBAKE.sub(_trecho_utf8, s)
    if consertado == s:
        return s
    return _A_CRASE_PERDIDO.sub("\u00e0", consertado)


def limpar_html(texto: str | None) -> str:
    """Tira marcacao e entidades. RSS entrega descricao como HTML.

    Conserta mojibake antes de colapsar espacos: o ``à`` quebrado (``Ã`` +
    espaco nao separavel) ainda tem conserto exato neste ponto.
    """
    if not texto:
        return ""
    limpo = _TAGS.sub(" ", consertar_mojibake(str(texto)))
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
