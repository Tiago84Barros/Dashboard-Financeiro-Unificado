"""Deduplicacao exata e por semelhanca.

Cascata de tres niveis, do barato para o caro:

1. **URL canonica** -- mesma materia linkada com parametros de campanha
   diferentes.
2. **Hash de conteudo** -- mesmo titulo e resumo normalizados, publicados em
   URLs distintas (comum quando o veiculo troca o slug).
3. **Simhash + distancia de Hamming** -- reescrita leve da mesma materia.

**Duplicata nao e descartada: e absorvida.** O sobrevivente guarda as
duplicatas, e essa distincao importa mais adiante. Cinco portais republicando o
mesmo despacho de agencia parecem cinco confirmacoes independentes se voce so
contar dominios distintos -- e nao sao, sao um texto so. Contar confirmacao por
*cluster* de quase-duplicata, e nao por dominio, e o que impede uma unica
materia sindicalizada de atravessar o portao de confirmacao independente.

Todos os hashes vem de `hashlib`, nunca de `hash()`: o hash embutido do Python
varia por processo (PYTHONHASHSEED) e o cluster tem de ser o mesmo em duas
execucoes.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from core.noticias.modelos import Noticia
from core.noticias.normalizacao import normalizar_texto

BITS_SIMHASH = 64
#: Distancia maxima para considerar duas materias a mesma. O valor NAO e
#: arbitrario: foi medido sobre pares sinteticos, e os dois testes
#: ``test_o_limite_de_hamming_cobre_a_sindicalizacao_comum`` e
#: ``test_o_limite_de_hamming_nao_funde_materias_distintas`` mantem a medicao
#: viva -- mexer no valor quebra um dos dois lados da tabela abaixo.
#:
#:   devem colapsar                    nao podem colapsar
#:   prefixo do veiculo ........  6    Copom "sobe" x "mantem" ....  13
#:   titulo + 1 palavra ........  8    mesma empresa, outro tema ..  25
#:   sufixo de credito .........  9    fato oposto ................  29
#:   2 palavras no meio ........ 14    outras empresas ............  36
#:
#: Nao ha corte que separe os dois grupos -- 14 e 13 se cruzam. 8 cobre a
#: sindicalizacao comum (acrescimo de prefixo ou sufixo) e para 5 bits antes do
#: par distinto mais proximo. Subir alem disso comeca a fundir materias que
#: dizem coisas diferentes, e cluster errado apaga um evento inteiro.
LIMITE_HAMMING = 8

MOTIVO_URL = "url_identica"
MOTIVO_CONTEUDO = "conteudo_identico"
MOTIVO_SEMELHANCA = "quase_duplicata"


def sha256(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def hash_url(url_canonica: str) -> str:
    """Identificador de deduplicacao a partir da URL ja canonizada."""
    return sha256(url_canonica or "")


def hash_conteudo(titulo: str | None, resumo: str | None = None) -> str:
    """Hash do texto normalizado. Titulo pesa sempre; resumo entra se houver."""
    base = normalizar_texto(titulo)
    extra = normalizar_texto(resumo)
    if extra:
        base = f"{base} {extra}"
    return sha256(base)


def _tokens(texto: str) -> list[str]:
    """Bigramas de palavras. Unigrama sozinho confunde manchetes que
    compartilham vocabulario mas dizem coisas opostas."""
    palavras = normalizar_texto(texto).split()
    if len(palavras) < 2:
        return palavras
    return [f"{a} {b}" for a, b in zip(palavras, palavras[1:])]


def simhash(texto: str | None, bits: int = BITS_SIMHASH) -> int | None:
    """Simhash de ``bits`` bits, ou ``None`` para texto vazio demais.

    ``None`` em vez de ``0``: zero e um simhash valido e teria distancia pequena
    de outros valores esparsos, colapsando textos vazios com textos reais.
    """
    tokens = _tokens(texto or "")
    if not tokens:
        return None
    vetor = [0] * bits
    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        valor = int.from_bytes(digest, "big")
        for i in range(bits):
            vetor[i] += 1 if (valor >> i) & 1 else -1
    resultado = 0
    for i in range(bits):
        if vetor[i] > 0:
            resultado |= 1 << i
    return resultado


def distancia_hamming(a: int | None, b: int | None) -> int | None:
    """Bits diferentes entre dois simhashes, ou ``None`` se algum falta."""
    if a is None or b is None:
        return None
    return bin(a ^ b).count("1")


def quase_iguais(a: int | None, b: int | None,
                 limite: int = LIMITE_HAMMING) -> bool:
    dist = distancia_hamming(a, b)
    return dist is not None and dist <= limite


@dataclass
class Cluster:
    """Uma materia e as copias dela que foram absorvidas."""

    principal: Noticia
    duplicatas: list[Noticia] = field(default_factory=list)
    motivos: list[str] = field(default_factory=list)

    @property
    def dominios(self) -> tuple[str, ...]:
        vistos = []
        for n in [self.principal, *self.duplicatas]:
            d = n.fonte.dominio if n.fonte else ""
            if d and d not in vistos:
                vistos.append(d)
        return tuple(vistos)

    @property
    def replicado(self) -> bool:
        return bool(self.duplicatas)


def _melhor(a: Noticia, b: Noticia) -> tuple[Noticia, Noticia]:
    """Qual das duas fica como principal.

    Criterios, em ordem: fonte mais confiavel; publicacao mais antiga (quem
    noticiou primeiro e o original, o resto e replicacao); resumo presente; e
    por fim o ``id_dedup`` -- desempate arbitrario mas *estavel*, para o
    resultado nao depender da ordem em que os provedores responderam.
    """
    def chave(n: Noticia):
        pub = n.publicado_em
        return (
            -n.confiabilidade,
            pub.timestamp() if pub is not None else float("inf"),
            0 if n.resumo else 1,
            n.id_dedup,
        )

    return (a, b) if chave(a) <= chave(b) else (b, a)


def agrupar_duplicatas(noticias: list[Noticia],
                       limite: int = LIMITE_HAMMING) -> list[Cluster]:
    """Colapsa duplicatas exatas e quase-duplicatas em clusters.

    Comparacao par a par contra os principais ja formados. E O(n*k) com k =
    numero de clusters; para os lotes desta aplicacao (dezenas a poucas
    centenas por coleta) isso e mais barato e mais previsivel do que montar
    indice de bandas de simhash.
    """
    clusters: list[Cluster] = []
    por_url: dict[str, Cluster] = {}
    por_conteudo: dict[str, Cluster] = {}

    for noticia in noticias:
        alvo: Cluster | None = None
        motivo = ""

        if noticia.id_dedup and noticia.id_dedup in por_url:
            alvo, motivo = por_url[noticia.id_dedup], MOTIVO_URL
        elif noticia.hash_conteudo and noticia.hash_conteudo in por_conteudo:
            alvo, motivo = por_conteudo[noticia.hash_conteudo], MOTIVO_CONTEUDO
        else:
            for cluster in clusters:
                if quase_iguais(noticia.simhash, cluster.principal.simhash,
                                limite):
                    alvo, motivo = cluster, MOTIVO_SEMELHANCA
                    break

        if alvo is None:
            novo = Cluster(principal=noticia)
            clusters.append(novo)
            if noticia.id_dedup:
                por_url[noticia.id_dedup] = novo
            if noticia.hash_conteudo:
                por_conteudo[noticia.hash_conteudo] = novo
            continue

        principal, absorvida = _melhor(alvo.principal, noticia)
        if principal is not alvo.principal:
            alvo.duplicatas.append(alvo.principal)
            alvo.principal = principal
        else:
            alvo.duplicatas.append(absorvida)
        alvo.motivos.append(motivo)
        if noticia.id_dedup:
            por_url.setdefault(noticia.id_dedup, alvo)
        if noticia.hash_conteudo:
            por_conteudo.setdefault(noticia.hash_conteudo, alvo)

    return clusters


def deduplicar(noticias: list[Noticia],
               limite: int = LIMITE_HAMMING) -> list[Noticia]:
    """Atalho para quem so quer a lista sem repeticao."""
    return [c.principal for c in agrupar_duplicatas(noticias, limite)]
