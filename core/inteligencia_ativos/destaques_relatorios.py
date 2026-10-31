"""
core/inteligencia_ativos/destaques_relatorios.py
O que os relatórios dizem, em vez do link para o relatório inteiro.

Pedido do usuário (30/09/2026): a caixa "Relatórios relevantes" deve dizer
quais são as informações relevantes de cada documento, não mandar o usuário
abrir o PDF. O texto dos documentos já está no corpus RAG
(``data/public/rag/chunks_*.parquet``, lido por ``core/rag_store.py``): aqui se
escolhem, em cada documento, as frases com fato e número (resultado, caixa,
dívida, dividendo, guidance, expansão...).

A escolha é determinística e cita o documento, sem paráfrase: a frase é do
emissor, nunca da LLM. Documento que é só metadado (``eh_stub``) não entra
no corpus de âncora e aparece na tela como "sem texto extraído".

Pedido seguinte (03/10/2026): a etapa 10 da análise detalhada não deve
listar documentos, e sim mostrar o que eles dizem de forma coerente para o
investidor. ``por_tema`` reagrupa as mesmas frases por assunto (resultado,
proventos, caixa e dívida, projeções, operação), do documento mais novo para
o mais velho, sem repetir o mesmo fato publicado em dois documentos.

``destaques(chunks)`` e ``por_tema`` são puros; ``ler(ticker)`` é a única
função com I/O.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass

logger = logging.getLogger(__name__)

N_DOCUMENTOS = 3
N_FRASES = 3
MESES = 12
LIMITE_CHUNKS = 60
MIN_CHARS, MAX_CHARS = 50, 280

# Palavra que indica fato de negócio. Sem acento: a comparação normaliza.
_FATOS = (
    "lucro", "prejuizo", "receita", "ebitda", "margem", "geracao de caixa",
    "caixa", "alavancagem", "divida", "dividend", "provento", "juros sobre",
    "jcp", "guidance", "projec", "crescimento", "cresceu", "aumento",
    "reducao", "queda", "recorde", "aquisic", "venda de", "inaugur",
    "expansao", "lojas", "investimento", "capex", "producao", "vendas",
    "resultado", "retorno", "roe", "custo", "despesa", "emissao", "debenture",
    "recompra", "rating", "inadimplencia", "carteira de credito", "ocupacao",
    "vacancia", "aluguel", "contrato",
)
# Verbo de fato: sem ele, a frase costuma ser rótulo de gráfico ou tabela.
_VERBOS = (" foi ", " foram ", " sera ", " serao ", " totaliz", " atingi",
           " alcanc", " registr", " aument", " reduz", " cresce", " cresceu",
           " caiu", " recuou", " subiu", " avanc", " encerr", " aprov",
           " celebr", " conclu", " anunci", " inaugur", " pagos", " pago ",
           " preve", " estima", " passa ", " passou", " somou", " somaram",
           " representa", " manteve", " mantem", " fechou", " obteve",
           " apresentou", " ficou", " ficaram", " teve ", " tiveram",
           " aumenta", " atinge", " alcanca", " totaliza", " recebe")
_INGLES = (" the ", " and ", " was ", " were ", " reached ", " million ",
           " compared ", " lower ", " higher ", " of the ", " in the ")
_NUMERO = re.compile(r"\d")
_VALOR = re.compile(r"(R\$|US\$|%|\bx\b|\d+,\d+x|milh|bilh|\bbps\b|p\.p\.)",
                    re.IGNORECASE)
_LETRAS_SOLTAS = re.compile(r"(?:\b\w\s){4,}")   # "R E S U L T A D O S"
# Documento que não fala do negócio (regimento, política de divulgação).
_IGNORAR = re.compile(r"politica de|regimento|codigo de conduta|estatuto")
_DIVISOR = re.compile(r"(?<=[.;!?])\s+(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ0-9])")


@dataclass(frozen=True)
class Destaque:
    titulo: str
    data: str | None        # AAAA-MM-DD
    tipo: str
    frases: tuple[str, ...]


class _SemAcento(dict):
    """Tabela de ``str.translate``: cada caractere vira a sua decomposição
    NFKD sem as marcas combinantes, calculada na primeira vez que aparece.

    Dá o mesmo que normalizar a frase inteira (a reordenação canônica do
    NFKD só mexe nas marcas, que saem), sem o laço em Python por caractere:
    era o grosso do custo da análise da carteira (05/10/2026)."""

    def __missing__(self, cod: int) -> str:
        saida = "".join(c for c in unicodedata.normalize("NFKD", chr(cod))
                        if not unicodedata.combining(c))
        self[cod] = saida
        return saida


_TABELA_SEM_ACENTO = _SemAcento()
_NAO_ASCII = re.compile(r"[^\x00-\x7f]")


def _trocar(m: re.Match) -> str:
    return _TABELA_SEM_ACENTO[ord(m.group())]


def _sem_acento(t: str) -> str:
    # Só o caractere não ASCII passa pela tabela: o ASCII sai igual, e a frase
    # em português tem poucos acentos. Metade do custo de ``str.translate``
    # com a tabela inteira (06/10/2026).
    t = t.lower()
    return t if t.isascii() else _NAO_ASCII.sub(_trocar, t)


def _limpar(texto: str) -> str:
    """O PDF extraído vem quebrado em fragmentos de linha: junta tudo numa
    linha só, sem espaços repetidos."""
    t = (texto or "").replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s{2,}", " ", t).strip()


def frases(texto: str) -> list[str]:
    return [f.strip() for f in _DIVISOR.split(_limpar(texto)) if f.strip()]


def _parece_prosa(frase: str, sem_acento: str | None = None) -> bool:
    """Frase de texto corrido em português, não linha de tabela, cabeçalho,
    tradução para o inglês ou fragmento cortado no meio da palavra.
    ``sem_acento`` é ``_sem_acento(frase)``, quando o chamador já o tem."""
    if not frase[:1].isupper() and not frase[:1].isdigit():
        return False                               # começa no meio da palavra
    if _LETRAS_SOLTAS.search(frase):
        return False
    letras = "".join(filter(str.isalpha, frase))
    if not letras or sum(map(str.isupper, letras)) > 0.5 * len(letras):
        return False                               # cabeçalho em caixa alta
    tokens = frase.split()
    numericos = sum(1 for t in tokens if any(map(str.isdigit, t)))
    palavras = sum(1 for t in tokens if len(t) >= 3 and t.isalpha())
    if numericos > 6 or palavras < 2 * numericos or palavras < 6:
        return False                               # linha de tabela
    base = f" {_sem_acento(frase) if sem_acento is None else sem_acento} "
    if sum(1 for w in _INGLES if w in base) >= 2:
        return False
    return any(w in base for w in _VERBOS)


def pontuar(frase: str, sem_acento: str | None = None) -> int:
    """0 = descarta. Frase boa é prosa com fato de negócio e número; valor
    monetário ou percentual pesa mais.

    Os filtros baratos (tamanho, número, fato) vêm antes de ``_parece_prosa``:
    as condições são todas obrigatórias, então a ordem não muda o resultado, e
    a maioria das frases cai neles. Era o grosso da análise fria da carteira
    (06/10/2026). ``sem_acento`` é ``_sem_acento(frase)``, quando o chamador
    já o tem."""
    if not MIN_CHARS <= len(frase) <= MAX_CHARS or not _NUMERO.search(frase):
        return 0
    base = _sem_acento(frase) if sem_acento is None else sem_acento
    fatos = sum(1 for p in _FATOS if p in base)
    if not fatos or not _parece_prosa(frase, base):
        return 0
    return fatos + 2 * len(_VALOR.findall(frase))


def _escolher(textos: list[str], n: int) -> tuple[str, ...]:
    vistas, candidatas = set(), []
    for pos, texto in enumerate(textos):
        for i, f in enumerate(frases(texto)):
            base = _sem_acento(f)
            chave = base[:80]
            if chave in vistas:
                continue
            vistas.add(chave)
            p = pontuar(f, base)
            if p:
                candidatas.append((p, pos, i, f))
    melhores = sorted(candidatas, key=lambda c: (-c[0], c[1], c[2]))[:n]
    # Na ordem em que aparecem no documento: o leitor segue o raciocínio.
    return tuple(c[3] for c in sorted(melhores, key=lambda c: (c[1], c[2])))


def destaques(chunks, n_docs: int = N_DOCUMENTOS,
              n_frases: int = N_FRASES) -> tuple[Destaque, ...]:
    """Chunks ``(chunk_text, data_doc, tipo_doc, titulo, doc_id)`` (ordem de
    ``rag_store.busca_ancora``: mais novo primeiro) → até ``n_docs``
    documentos com frases de fato. Documento sem frase aproveitável não
    entra. Puro."""
    por_doc: dict = {}
    for texto, data, tipo, titulo, doc_id in chunks or ():
        if _IGNORAR.search(_sem_acento(f"{tipo} {titulo}")):
            continue
        d = por_doc.setdefault(doc_id, {"data": data, "tipo": tipo or "",
                                        "titulo": titulo or "", "textos": []})
        d["textos"].append(texto or "")
    saida = []
    for d in por_doc.values():
        escolhidas = _escolher(d["textos"], n_frases)
        if escolhidas:
            saida.append(Destaque(d["titulo"] or d["tipo"] or "Documento",
                                  str(d["data"])[:10] if d["data"] else None,
                                  d["tipo"], escolhidas))
        if len(saida) >= n_docs:
            break
    return tuple(saida)


# Tema do investidor, na ordem de exibição. A frase vai para o tema de maior
# pontuação (cada termo vale o seu número de palavras: "custo de capital"
# vence "custo"); empate fica com o primeiro. Sem acento: a comparação normaliza.
TEMAS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("resultado", "Resultado", (
        "lucro", "prejuizo", "receita", "ebitda", "margem", "resultado",
        "custo", "despesa", "eficiencia", "retorno", "roe", "rentabilidade")),
    ("proventos", "Proventos e recompra", (
        "dividend", "provento", "juros sobre capital", "jcp", "payout",
        "recompra", "data base", "data de corte", "data-com", "ex-dividendo",
        "ex-juros", "remuneracao", "acionistas", "bonific")),
    ("divida", "Caixa e dívida", (
        "caixa", "divida", "alavancagem", "debenture", "rating",
        "financiamento", "liquidez")),
    ("projecoes", "Projeções e estratégia", (
        "guidance", "projec", "meta", "custo de capital", "estima", "preve",
        "estrategi", "plano de negocios")),
    ("operacao", "Operação e crescimento", (
        "producao", "vendas", "expansao", "lojas", "ocupacao", "vacancia",
        "carteira", "contrato", "aquisic", "capex", "investimento",
        "inaugur", "capacidade", "recorde", "export", "import")),
)
OUTROS = ("outros", "Outros fatos")
ROTULO_TEMA = {c: r for c, r, _ in TEMAS} | {OUTROS[0]: OUTROS[1]}
N_POR_TEMA = 3
N_DOCS_TEMA, N_FRASES_TEMA = 12, 4
_NUMEROS = re.compile(r"\d+(?:[.,]\d+)*")


@dataclass(frozen=True)
class Trecho:
    tema: str
    frase: str
    data: str | None        # AAAA-MM-DD
    titulo: str
    tipo: str = ""


def tema(frase: str) -> str:
    """Chave do tema do investidor para a frase. Puro."""
    base = _sem_acento(frase)
    melhor, pontos = OUTROS[0], 0
    for chave, _, palavras in TEMAS:
        n = sum(len(p.split()) for p in palavras if p in base)
        if n > pontos:
            melhor, pontos = chave, n
    return melhor


def _chave_fato(frase: str) -> str:
    """Mesmo fato em dois documentos (o relatório republicado, o release e a
    transcrição) tem os mesmos valores. Conta só número com decimal ou de
    três dígitos ou mais: "2T" e "12" variam de redação. Sem dois valores, o
    começo do texto."""
    nums = sorted({n for n in _NUMEROS.findall(frase)
                   if len(n) >= 3 or "," in n or "." in n})
    return "|".join(nums) if len(nums) >= 2 else _sem_acento(frase)[:80]


def por_tema(dest, n_por_tema: int = N_POR_TEMA) -> tuple[Trecho, ...]:
    """Destaques por documento (mais novo primeiro) → trechos agrupados por
    tema, na ordem de ``TEMAS``, até ``n_por_tema`` por tema, sem repetir
    fato. Puro."""
    vistos, grupos = set(), {}
    for d in dest or ():
        for f in d.frases:
            k = _chave_fato(f)
            if k in vistos:
                continue
            vistos.add(k)
            t = tema(f)
            lista = grupos.setdefault(t, [])
            if len(lista) < n_por_tema:
                lista.append(Trecho(t, f, d.data, d.titulo, d.tipo))
    ordem = [c for c, _, _ in TEMAS] + [OUTROS[0]]
    return tuple(x for c in ordem for x in grupos.get(c, ()))


def titulo_curto(titulo: str, tipo: str = "", n: int = 70) -> str:
    """Título da CVM pode ser a pauta inteira da assembleia, com "||". Puro."""
    t = (titulo or "").split("||")[0].strip() or tipo or "Documento"
    return t if len(t) <= n else t[:n - 1].rstrip() + "…"


def mesmo_documento(titulo: str, data: str | None, d: Destaque) -> bool:
    """O documento da lista de metadados é o mesmo que o do corpus?"""
    return (str(data or "")[:10] == (d.data or "")
            and _sem_acento(titulo or "")[:40] == _sem_acento(d.titulo)[:40])


def ler(ticker: str, n_docs: int = N_DOCUMENTOS,
        n_frases: int = N_FRASES) -> tuple[Destaque, ...]:
    """Destaques dos documentos de fato (resultado, fato relevante, guidance,
    dividendo...) dos últimos ``MESES`` meses. Sem corpus, vazio."""
    from core.inteligencia_ativos.informacoes import raiz_b3
    raiz = raiz_b3(ticker)
    if not raiz:                 # o corpus é de documentos da CVM
        return ()
    try:
        from core import rag_store
        limite = LIMITE_CHUNKS * max(1, n_docs // N_DOCUMENTOS)
        return destaques(rag_store.busca_ancora(raiz, limite, MESES),
                         n_docs, n_frases)
    except Exception:  # noqa: BLE001 — sem corpus a caixa cai na lista
        logger.warning("destaques de relatórios: corpus ilegível para %s",
                       ticker, exc_info=True)
        return ()


def _ler_trechos(ticker: str) -> tuple[Trecho, ...]:
    return por_tema(ler(ticker, N_DOCS_TEMA, N_FRASES_TEMA))


def _cache(fn):
    try:
        import streamlit as st
        return st.cache_data(ttl=3600, show_spinner=False)(fn)
    except Exception:  # pragma: no cover - contexto sem Streamlit
        return fn


_ler_trechos_cache = _cache(_ler_trechos)


def ler_trechos(ticker: str) -> tuple[Trecho, ...]:
    """Trechos por tema dos documentos recentes do ativo. Sem corpus,
    vazio.

    Com cache de 1 h por ativo: a análise da carteira pede os trechos de
    cada ação a cada recálculo, e o corpus só muda com um novo deploy."""
    return _ler_trechos_cache(str(ticker or "").upper())
