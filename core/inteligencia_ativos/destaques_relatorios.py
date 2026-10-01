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

``destaques(chunks)`` é puro; ``ler(ticker)`` é a única função com I/O.
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


def _sem_acento(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", t.lower())
                   if not unicodedata.combining(c))


def _limpar(texto: str) -> str:
    """O PDF extraído vem quebrado em fragmentos de linha: junta tudo numa
    linha só, sem espaços repetidos."""
    t = (texto or "").replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s{2,}", " ", t).strip()


def frases(texto: str) -> list[str]:
    return [f.strip() for f in _DIVISOR.split(_limpar(texto)) if f.strip()]


def _parece_prosa(frase: str) -> bool:
    """Frase de texto corrido em português, não linha de tabela, cabeçalho,
    tradução para o inglês ou fragmento cortado no meio da palavra."""
    if not frase[:1].isupper() and not frase[:1].isdigit():
        return False                               # começa no meio da palavra
    if _LETRAS_SOLTAS.search(frase):
        return False
    letras = [c for c in frase if c.isalpha()]
    if not letras or sum(c.isupper() for c in letras) > 0.5 * len(letras):
        return False                               # cabeçalho em caixa alta
    tokens = frase.split()
    numericos = sum(1 for t in tokens if any(c.isdigit() for c in t))
    palavras = sum(1 for t in tokens if len(t) >= 3 and t.isalpha())
    if numericos > 6 or palavras < 2 * numericos or palavras < 6:
        return False                               # linha de tabela
    base = f" {_sem_acento(frase)} "
    if sum(1 for w in _INGLES if w in base) >= 2:
        return False
    return any(w in base for w in _VERBOS)


def pontuar(frase: str) -> int:
    """0 = descarta. Frase boa é prosa com fato de negócio e número; valor
    monetário ou percentual pesa mais."""
    if not MIN_CHARS <= len(frase) <= MAX_CHARS or not _parece_prosa(frase):
        return 0
    base = _sem_acento(frase)
    fatos = sum(1 for p in _FATOS if p in base)
    if not fatos or not _NUMERO.search(frase):
        return 0
    return fatos + 2 * len(_VALOR.findall(frase))


def _escolher(textos: list[str], n: int) -> tuple[str, ...]:
    vistas, candidatas = set(), []
    for pos, texto in enumerate(textos):
        for i, f in enumerate(frases(texto)):
            chave = _sem_acento(f)[:80]
            if chave in vistas:
                continue
            vistas.add(chave)
            p = pontuar(f)
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


def mesmo_documento(titulo: str, data: str | None, d: Destaque) -> bool:
    """O documento da lista de metadados é o mesmo que o do corpus?"""
    return (str(data or "")[:10] == (d.data or "")
            and _sem_acento(titulo or "")[:40] == _sem_acento(d.titulo)[:40])


def ler(ticker: str) -> tuple[Destaque, ...]:
    """Destaques dos documentos de fato (resultado, fato relevante, guidance,
    dividendo...) dos últimos ``MESES`` meses. Sem corpus, vazio."""
    from core.inteligencia_ativos.informacoes import raiz_b3
    raiz = raiz_b3(ticker)
    if not raiz:                 # o corpus é de documentos da CVM
        return ()
    try:
        from core import rag_store
        return destaques(rag_store.busca_ancora(raiz, LIMITE_CHUNKS, MESES))
    except Exception:  # noqa: BLE001 — sem corpus a caixa cai na lista
        logger.warning("destaques de relatórios: corpus ilegível para %s",
                       ticker, exc_info=True)
        return ()
