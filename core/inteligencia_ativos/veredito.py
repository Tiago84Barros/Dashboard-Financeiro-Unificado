"""
core/inteligencia_ativos/veredito.py
A avaliação por regras da Inteligência dos Ativos, fora dela, para as LLMs
da Empresas B3 (chat e relatório da análise de portfólio).

Motivo (DIRR3, 02/10/2026): o chat da Empresas B3 dizia "comprar mais" com
Dív/PL de 1,27x enquanto a Inteligência dizia "Vender" por um alerta
eliminatório de dívida. A régua de dívida já é uma só (``avaliacao``); faltava
a LLM ler o mesmo veredito. Este módulo devolve, por ticker, a mesma
``Avaliacao`` que a Inteligência calcula (mesmos leitores, mesma régua) e o
limite que ela impõe à recomendação.

O que fica de fora, de propósito: a decisão por peso (``resumida.decisao``)
depende da política e da carteira do usuário, que a carteira modelo da
Empresas B3 não tem. O limite aqui é só o que vem do ativo em si: alerta
eliminatório e qualidade frágil, os mesmos passos de
``resumida.com_avaliacao``.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass

from core.inteligencia_ativos import avaliacao as av_
from core.inteligencia_ativos import secoes
from core.inteligencia_ativos.modelos import InfoBasica, Secao

logger = logging.getLogger(__name__)

# Recomendação máxima que a avaliação permite à LLM.
TROCAR, NAO_APORTAR, LIVRE = "trocar", "nao_aportar", "livre"

# Chat com muitos tickers citados não pode ler o banco para todos.
MAX_TICKERS = 15

REGRA_VEREDITO = (
    "COERÊNCIA COM A INTELIGÊNCIA DOS ATIVOS (obrigatória): o bloco "
    "'AVALIAÇÃO POR REGRAS' traz, por ação, o mesmo veredito que a aba "
    "Inteligência dos Ativos mostra ao usuário, com a mesma dívida e a mesma "
    "régua setorial. Sua recomendação não pode contradizê-lo:\n"
    "- LIMITE = avaliar troca: há alerta eliminatório. É proibido recomendar "
    "comprar, comprar mais, aumentar ou manter sem ressalva; cite o alerta "
    "com o número.\n"
    "- LIMITE = não aportar: qualidade frágil. É proibido recomendar comprar, "
    "comprar mais ou aumentar; manter só com a ressalva de não reforçar.\n"
    "- LIMITE = livre: siga os critérios do bloco.\n"
    "Se os dados do contexto parecerem contradizer o veredito, diga qual "
    "número diverge e de qual fonte vem cada um, sem trocar o veredito. Use "
    "os números do bloco para dívida (dívida líquida/EBITDA, dívida "
    "bruta/patrimônio): são os do balanço da base."
)


@dataclass(frozen=True)
class _Insumos:
    """O que ``avaliacao.avaliar`` lê de uma ``AnaliseAtivo``."""
    ativo: InfoBasica
    fundamentos: Secao
    valuation: Secao
    pares: Secao
    noticias: Secao


def info_acao_b3(ticker: str, nome: str | None = None,
                 setor: str | None = None) -> InfoBasica:
    """A ``InfoBasica`` que a Inteligência monta para uma ação da B3: sem
    subclasse (``calculos.subclasse`` não dá uma a ``Ações BR``)."""
    tk = str(ticker or "").strip().upper().replace(".SA", "")
    return InfoBasica(ticker=tk, nome=nome or tk, classe="Ações BR",
                      classe_politica="acoes_br", subclasse=None,
                      setor=setor or None, moeda="BRL", valor_investido=None,
                      valor_mercado=None, peso_atual=0.0)


def avaliar_acao_b3(ticker: str, nome: str | None = None,
                    setor: str | None = None, *, ler_fundamentos=None,
                    ler_valuation=None, ler_informacoes=None
                    ) -> av_.Avaliacao:
    """A ``Avaliacao`` da Inteligência para a ação, pelos mesmos provedores.
    Os leitores são injetáveis (testes); o padrão é o de ``secoes``."""
    info = info_acao_b3(ticker, nome, setor)
    vp = (ler_valuation or secoes._ler_valuation_e_pares)(info)
    infos = (ler_informacoes or secoes._ler_informacoes)(info)
    insumos = _Insumos(
        ativo=info,
        fundamentos=secoes.provedor_fundamentos(info, None,
                                                leitor=ler_fundamentos),
        valuation=secoes.provedor_valuation(info, None, leitor=lambda _: vp),
        pares=secoes.provedor_pares(info, None, leitor=lambda _: vp),
        noticias=secoes.provedor_noticias(info, None,
                                          leitor=lambda _: infos),
    )
    return av_.avaliar(insumos)


def limite(av: av_.Avaliacao) -> tuple[str, str]:
    """(limite, motivo): a recomendação máxima que a avaliação permite.

    Mesmos passos de ``resumida.com_avaliacao``: alerta eliminatório e
    qualidade frágil com preço caro ou mercado negativo viram "avaliar
    troca"; qualidade frágil sozinha barra o aporte.
    """
    if av.criticos:
        return TROCAR, f"alerta eliminatório: {av.criticos[0].texto}"
    if av.qualidade == av_.FRAGIL:
        piora = [t for t, ok in (("preço acima do normal",
                                  av.preco == av_.CARO),
                                 ("mercado e notícias negativos",
                                  av.mercado == av_.NEGATIVO)) if ok]
        if piora:
            return TROCAR, f"qualidade frágil e {' e '.join(piora)}"
        return NAO_APORTAR, "qualidade frágil"
    return LIVRE, ""


ROTULO_LIMITE = {TROCAR: "avaliar troca", NAO_APORTAR: "não aportar",
                 LIVRE: "livre"}


def texto_ticker(av: av_.Avaliacao, ticker: str) -> str:
    cod, motivo = limite(av)
    cab = f"LIMITE da recomendação para {ticker}: {ROTULO_LIMITE[cod]}"
    if motivo:
        cab += f" ({motivo})"
    return cab + "\n" + av_.texto(av, ticker)


def bloco_para_llm(itens, *, avaliador=None, max_tickers: int = MAX_TICKERS
                   ) -> tuple[str, dict[str, av_.Avaliacao]]:
    """Bloco 'AVALIAÇÃO POR REGRAS' para os tickers de ``itens`` (dicts com
    ``ticker`` e, se houver, ``nome`` e ``setor``) e as avaliações por
    ticker. Ticker que falha é nomeado no bloco, nunca some."""
    avaliador = avaliador or avaliar_acao_b3
    vistos: list[dict] = []
    for it in itens or ():
        tk = str((it or {}).get("ticker") or "").strip().upper()
        if tk and tk not in {v["ticker"] for v in vistos}:
            vistos.append({**it, "ticker": tk})
    partes = ["=== AVALIAÇÃO POR REGRAS (Inteligência dos Ativos) ==="]
    avs: dict[str, av_.Avaliacao] = {}
    for it in vistos[:max_tickers]:
        tk = it["ticker"]
        try:
            av = avaliador(tk, it.get("nome"), it.get("setor"))
        except Exception as exc:  # fonte que falha é nomeada
            logger.warning("Avaliação por regras de %s falhou: %s", tk, exc)
            partes.append(f"{tk}: avaliação por regras indisponível agora "
                          f"({type(exc).__name__}); não conclua sem ela.")
            continue
        avs[tk] = av
        partes.append(texto_ticker(av, tk))
    fora = [v["ticker"] for v in vistos[max_tickers:]]
    if fora:
        partes.append("Sem avaliação neste turno (limite de "
                      f"{max_tickers} tickers): {', '.join(fora)}.")
    if len(partes) == 1:
        return "", avs
    return "\n\n".join(partes), avs


_ORDEM_PERSPECTIVA = {"fraca": 0, "moderada": 1, "forte": 2}


def coerente(analise: dict, av: av_.Avaliacao | None) -> dict:
    """Limita a ``perspectiva`` do relatório da LLM ao que a avaliação
    permite: alerta eliminatório (ou "avaliar troca") → no máximo "fraca";
    qualidade frágil → no máximo "moderada". O resumo diz por quê. Puro."""
    if av is None or not isinstance(analise, dict):
        return analise
    cod, motivo = limite(av)
    teto = {TROCAR: "fraca", NAO_APORTAR: "moderada"}.get(cod)
    atual = analise.get("perspectiva")
    if teto is None or atual not in _ORDEM_PERSPECTIVA \
            or _ORDEM_PERSPECTIVA[atual] <= _ORDEM_PERSPECTIVA[teto]:
        return analise
    nota = (f"Perspectiva limitada a '{teto}' pela avaliação por regras da "
            f"Inteligência dos Ativos ({motivo}); a LLM indicou '{atual}'.")
    novo = {**analise, "perspectiva": teto}
    for chave in ("resumo", "tese_final"):
        if chave in analise or chave == "resumo":
            texto = str(analise.get(chave) or "").strip()
            novo[chave] = f"{nota} {texto}".strip()
    return novo


def regra_relatorio(av: av_.Avaliacao | None, ticker: str) -> str:
    """Trecho para o contexto do relatório por empresa: o veredito da
    Inteligência e o teto de perspectiva que ``coerente`` vai aplicar."""
    if av is None:
        return ""
    cod, _ = limite(av)
    teto = {TROCAR: "fraca", NAO_APORTAR: "moderada"}.get(cod)
    regra = (f" A perspectiva não pode passar de '{teto}'; explique pelo "
             "alerta, com o número." if teto else "")
    return ("\n\n" + texto_ticker(av, ticker) + "\nEste é o veredito que a "
            "Inteligência dos Ativos mostra ao usuário; a nota não pode "
            "contradizê-lo." + regra)


# ─────────────────────────────────────────────────────────────────────────────
# Conferência pós-resposta do chat
# ─────────────────────────────────────────────────────────────────────────────
# O chat é texto livre: a regra no prompt não garante nada. A conferência lê a
# resposta e procura recomendação acima do limite por ticker. É heurística
# (padrões de verbo e negação): não pega toda paráfrase e pode marcar falso
# positivo. O que ela garante é que nenhuma contradição detectada sai sem
# reescrita ou sem aviso visível.

_TICKER_RE = re.compile(r"\b([A-Z]{4}\d{1,2})\b")
_CHARTS_RE = re.compile(r"```charts.*?```", re.DOTALL | re.IGNORECASE)
_OBJETO = r"(?=[^.;\n]{0,25}?\b(posicao|peso|exposicao|participacao|alocacao|fatia|[a-z]{4}\d{1,2})\b)"
_COMPRA_RE = re.compile(
    r"\b(comprar|compre|comprando|compraria|aportar|aporte|aportes|aportando|"
    r"acumular|acumule|acumulando|sobreponderar|sobrepondere|overweight)\b"
    r"|recomenda\w*\s*(de|:)?\s*compra\b"
    r"|\b(aumentar|aumente|aumentando|elevar|eleve|reforcar|reforce|"
    r"adicionar|adicione)\b" + _OBJETO)
_MANTER_RE = re.compile(r"\b(manter|mantenha|mantenho|manteria|manutencao)\b")
_NEGACAO_RE = re.compile(
    r"\b(nao|nem|evite|evitar|sem|proibido|jamais|nunca|antes de|deixar de|"
    r"em vez de|ao inves de|so|desaconselh\w*)\b")
_RESSALVA_RE = re.compile(
    r"alerta|eliminatori|troca|trocar|vender|venda|reduzir|reduza|sair|saida")


@dataclass(frozen=True)
class Violacao:
    ticker: str
    acao: str          # "comprar/aumentar" ou "manter sem ressalva"
    trecho: str
    limite: str
    motivo: str


def _norm(texto: str) -> str:
    """Minúsculas sem acento, com o mesmo comprimento do original (os
    índices dos tickers achados no original valem no normalizado)."""
    saida = []
    for ch in texto:
        n = "".join(c for c in unicodedata.normalize("NFKD", ch)
                    if not unicodedata.combining(c)).lower()
        saida.append(n if len(n) == 1 else ch)
    return "".join(saida)


def _trechos(texto: str, ticker: str) -> list[tuple[int, int]]:
    """Janelas do texto que falam de ``ticker``: da menção (ou do começo da
    linha, se não houver outro ticker antes) até o próximo ticker, o fim do
    parágrafo ou 400 caracteres. Ticker em título estende ao parágrafo
    seguinte."""
    mencoes = [(m.start(), m.end(), m.group(1))
               for m in _TICKER_RE.finditer(texto)]
    janelas = []
    for i, (ini, fim, tk) in enumerate(mencoes):
        if tk != ticker:
            continue
        inicio = texto.rfind("\n", 0, ini) + 1
        outros_antes = [f for s, f, t in mencoes[:i] if t != ticker]
        if outros_antes:
            inicio = max(inicio, outros_antes[-1])
        final = min(len(texto), fim + 400)
        outros_depois = [s for s, f, t in mencoes[i + 1:] if t != ticker]
        if outros_depois:
            final = min(final, outros_depois[0])
        par = texto.find("\n\n", fim)
        if par != -1 and par - fim < 40:
            par = texto.find("\n\n", par + 2)
        if par != -1:
            final = min(final, par)
        janelas.append((inicio, final))
    return janelas


def _sem_negacao(norm: str, m: re.Match, inicio: int) -> bool:
    antes = norm[max(inicio, m.start() - 30):m.start()]
    antes = re.split(r"[.;:!?\n]", antes)[-1]  # só a oração do verbo
    return not _NEGACAO_RE.search(antes)


def conferir_resposta(texto: str, avaliacoes: dict | None) -> list[Violacao]:
    """Contradições da resposta com o limite de cada ticker avaliado: compra
    ou aumento com limite "avaliar troca" ou "não aportar"; manter sem
    ressalva com "avaliar troca". Uma violação por ticker, no máximo."""
    if not texto or not avaliacoes:
        return []
    corpo = _CHARTS_RE.sub(lambda m: " " * len(m.group(0)), texto)
    norm = _norm(corpo)
    achadas: list[Violacao] = []
    for tk, av in avaliacoes.items():
        cod, motivo = limite(av)
        if cod == LIVRE:
            continue
        for ini, fim in _trechos(corpo, tk):
            seg = norm[ini:fim]
            acao = None
            for m in _COMPRA_RE.finditer(norm, ini, fim):
                if _sem_negacao(norm, m, ini):
                    acao = "comprar/aumentar"
                    break
            if acao is None and cod == TROCAR and not _RESSALVA_RE.search(seg):
                for m in _MANTER_RE.finditer(norm, ini, fim):
                    if _sem_negacao(norm, m, ini):
                        acao = "manter sem ressalva"
                        break
            if acao:
                trecho = " ".join(corpo[ini:fim].split())[:200]
                achadas.append(Violacao(tk, acao, trecho, cod, motivo))
                break
    return achadas


def _linha(v: Violacao) -> str:
    return (f"{v.ticker}: a resposta indica {v.acao} (\u201c{v.trecho}\u201d), "
            f"mas o limite é '{ROTULO_LIMITE[v.limite]}' ({v.motivo})")


def pedido_de_correcao(violacoes: list[Violacao]) -> str:
    """Mensagem que pede à LLM a resposta inteira de novo, sem a contradição."""
    linhas = "\n".join(f"- {_linha(v)}." for v in violacoes)
    return (
        "CONFERÊNCIA AUTOMÁTICA: sua resposta anterior contradiz a avaliação "
        "por regras da Inteligência dos Ativos:\n" + linhas + "\n"
        "Reescreva a resposta INTEIRA à pergunta anterior do usuário "
        "respeitando REGRA_VEREDITO: com 'avaliar troca', não recomende "
        "comprar, aumentar nem manter sem ressalva, e cite o alerta com o "
        "número; com 'não aportar', não recomende comprar nem aumentar. "
        "Mantenha os números, as seções e o bloco ```charts```, se houver. "
        "Entregue só a resposta corrigida, sem comentar esta conferência."
    )


def com_aviso(texto: str, violacoes: list[Violacao]) -> str:
    """A resposta com um aviso no topo, para a contradição que sobrou."""
    if not violacoes:
        return texto
    linhas = "\n".join(f"> - {_linha(v)}." for v in violacoes)
    return ("> ⚠️ **Conferência automática:** esta resposta contradiz a "
            "avaliação por regras da Inteligência dos Ativos. Vale o veredito "
            "da Inteligência.\n" + linhas + "\n\n" + (texto or ""))
