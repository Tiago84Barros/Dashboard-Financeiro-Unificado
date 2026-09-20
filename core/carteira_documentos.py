"""Evidência documental por classe da carteira, para o dossiê e o chat.

Cada classe tem um corpus diferente, e duas delas não têm corpus nenhum:

- **Ações**: documentos CVM/IPE já indexados em :mod:`core.rag_b3` (fato
  relevante, resultados, comunicados). É o mesmo acervo que a tela Empresas B3
  lê — nenhuma coleta nova acontece aqui.
- **FIIs e Exterior**: a vitrine de notícias (:mod:`core.noticias.vitrine`),
  que já vem agregada por ativo. Este módulo não recalcula nota de notícia;
  apenas cita o que está publicado.
- **Tesouro**: não há corpus por título. A ausência é declarada, não omitida.

A regra que o módulo inteiro serve: **falha de leitura sai em ``erro``, nunca
em lista vazia.** Lista vazia significa "a janela não trouxe nada", e quem lê o
contexto tem que poder distinguir isso de "a fonte não respondeu". Este projeto
já publicou "todos os 394 FIIs são inelegíveis" porque uma falha de leitura
passou por um quadro vazio sem levantar nada.

Nenhuma função levanta exceção para a camada de tela.
"""
from __future__ import annotations

#: Teto de ativos consultados. O RAG faz uma consulta por ticker; uma carteira
#: de 40 ações travaria a tela esperando documento que não cabe no prompt de
#: qualquer forma. O corte é pelo peso na classe, que a tela já ordena.
MAX_ATIVOS = 8

#: Orçamento de texto por ativo. O dossiê tem doze seções para caber junto.
MAX_CHARS_POR_ATIVO = 1800

_SEM_CORPUS_TESOURO = (
    "Títulos públicos federais não têm documentos por emissor no acervo: o "
    "emissor é o Tesouro Nacional e não publica fato relevante por título. "
    "Ausência de documento aqui é característica da classe, não lacuna de dado."
)


def _alvos(tickers) -> list[str]:
    vistos: dict[str, None] = {}
    for ticker in tickers or ():
        chave = str(ticker or "").strip().upper().replace(".SA", "")
        if chave:
            vistos.setdefault(chave, None)
        if len(vistos) >= MAX_ATIVOS:
            break
    return list(vistos)


def _documentos_acoes(alvos: list[str]) -> dict:
    """CVM/IPE via RAG. Falha de UM ticker não invalida os outros."""
    from core.rag_b3 import format_rag_context, retrieve_chunks

    itens: list[str] = []
    sem_corpus: list[str] = []
    falhas = 0
    for ticker in alvos:
        try:
            chunks, _stats = retrieve_chunks(
                ticker, top_k_total=12, per_topic_k=4, months_back=12)
        except Exception:  # noqa: BLE001 - falha por ticker, declarada abaixo
            falhas += 1
            continue
        if not chunks:
            sem_corpus.append(ticker)
            continue
        texto = format_rag_context(chunks, max_chars=MAX_CHARS_POR_ATIVO,
                                   max_por_doc=3)
        itens.append(f"{ticker} — documentos CVM/IPE dos últimos 12 meses:\n"
                     f"{texto}")
    if falhas and not itens:
        return {"fonte": "documentos CVM/IPE",
                "erro": "O acervo de documentos CVM/IPE não pôde ser lido agora.",
                "itens": [], "sem_corpus": []}
    return {"fonte": "documentos CVM/IPE (últimos 12 meses)",
            "itens": itens, "sem_corpus": sem_corpus, "erro": None}


def _documentos_noticias(alvos: list[str], engine) -> dict:
    """Vitrine de notícias já publicada, com a idade dela dita em voz alta."""
    from core.noticias.vitrine import VitrineIlegivel, ler

    if engine is None:
        return {"fonte": "vitrine de notícias",
                "erro": "Banco indisponível para ler a vitrine de notícias.",
                "itens": [], "sem_corpus": []}
    try:
        linhas, meta = ler(engine, alvos)
    except VitrineIlegivel as exc:
        return {"fonte": "vitrine de notícias",
                "erro": f"A vitrine de notícias não pôde ser lida ({exc}).",
                "itens": [], "sem_corpus": []}
    except Exception:  # noqa: BLE001
        return {"fonte": "vitrine de notícias",
                "erro": "A vitrine de notícias não pôde ser lida agora.",
                "itens": [], "sem_corpus": []}

    janela = int((meta or {}).get("janela_dias") or 0)
    gerada = (meta or {}).get("gerada_em")
    fonte = "vitrine de notícias"
    if janela:
        fonte += f" (janela de {janela} dias"
        fonte += f", gerada em {gerada:%d/%m/%Y})" if gerada is not None else ")"

    por_simbolo = {str(x.get("simbolo") or "").upper(): x for x in linhas}
    itens: list[str] = []
    sem_corpus: list[str] = []
    for ticker in alvos:
        linha = por_simbolo.get(ticker)
        manchetes = _manchetes(linha) if linha else []
        if not manchetes:
            sem_corpus.append(ticker)
            continue
        itens.append(f"{ticker}: " + " | ".join(manchetes))
    return {"fonte": fonte, "itens": itens, "sem_corpus": sem_corpus,
            "erro": None}


def _manchetes(linha) -> list[str]:
    itens = linha.get("itens") or []
    if isinstance(itens, str):
        import json

        try:
            itens = json.loads(itens)
        except ValueError:
            return []
    saida = []
    for item in itens:
        if not isinstance(item, dict):
            continue
        titulo = str(item.get("titulo") or "").strip()
        if not titulo:
            continue
        veiculo = str(item.get("veiculo") or "").strip()
        data = str(item.get("publicado_em") or "")[:10]
        sufixo = " (" + ", ".join(p for p in (veiculo, data) if p) + ")"
        saida.append(titulo + (sufixo if sufixo != " ()" else ""))
    return saida


def documentos_da_classe(classe: str, tickers, *, engine=None) -> dict:
    """``{"fonte", "itens", "sem_corpus", "erro"}`` para a classe pedida.

    ``itens`` são textos prontos para o contexto — quem monta o prompt não
    precisa saber de qual fonte vieram, só de qual fonte o campo ``fonte`` diz.
    """
    chave = str(classe or "").strip().lower()
    alvos = _alvos(tickers)
    if chave == "tesouro":
        return {"fonte": "sem corpus documental", "itens": [],
                "sem_corpus": [], "erro": None, "nota": _SEM_CORPUS_TESOURO}
    if not alvos:
        return {"fonte": "", "itens": [], "sem_corpus": [], "erro": None}
    if chave == "acoes":
        return _documentos_acoes(alvos)
    if chave in ("fiis", "exterior"):
        return _documentos_noticias(alvos, engine)
    return {"fonte": "", "itens": [], "sem_corpus": [], "erro": None}
