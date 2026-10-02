"""
core/inteligencia_ativos/veredito.py
A avaliação por regras da Inteligência dos Ativos, fora dela, para as LLMs
do app: Empresas B3, Empresas Americanas, Seleção de FIIs, Portfólio Global,
chat por ativo e os chats e dossiês da carteira em Investimentos.

Motivo (DIRR3, 02/10/2026): o chat da Empresas B3 dizia "comprar mais" com
Dív/PL de 1,27x enquanto a Inteligência dizia "Vender" por um alerta
eliminatório de dívida. A régua de dívida já é uma só (``avaliacao``); faltava
a LLM ler o mesmo veredito. Este módulo devolve, por ticker, a mesma
``Avaliacao`` que a Inteligência calcula (mesmos leitores, mesma régua) e o
limite que ela impõe à recomendação — para ação da B3, ação americana e FII.

Duas profundidades:

* Carteiras modelo (Empresas B3/EUA, FIIs, Global) e chat por ativo: só o
  limite que vem do ativo em si — alerta eliminatório e qualidade frágil, os
  mesmos passos de ``resumida.com_avaliacao``. Não há política nem peso do
  usuário para decidir mais que isso.
* Carteira do usuário (Investimentos): a decisão inteira da Inteligência
  (``resumida.decisao``: peso contra a % devida + avaliação), em
  ``Veredito``. Só existe com a Estratégia liberada; sem ela, vale o limite
  do ativo.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Callable

from core.inteligencia_ativos import avaliacao as av_
from core.inteligencia_ativos import secoes
from core.inteligencia_ativos.modelos import InfoBasica, Secao

logger = logging.getLogger(__name__)

# Recomendação máxima que a avaliação permite à LLM. REDUZIR só sai da
# decisão por peso (``Veredito``): o ativo está acima da % devida.
TROCAR, REDUZIR, NAO_APORTAR, LIVRE = "trocar", "reduzir", "nao_aportar", "livre"

# Chat com muitos tickers citados não pode ler o banco para todos.
MAX_TICKERS = 15

# mercado → (rótulo de classe da carteira, moeda). Os mesmos rótulos que a
# Inteligência recebe da carteira; ``calculos.classe_politica`` faz o resto.
MERCADOS = {"b3": ("Ações BR", "BRL"), "us": ("Ações EUA", "USD"),
            "fii": ("FII", "BRL")}

REGRA_VEREDITO = (
    "COERÊNCIA COM A INTELIGÊNCIA DOS ATIVOS (obrigatória): o bloco "
    "'AVALIAÇÃO POR REGRAS' traz, por ativo (ação da B3, ação americana ou "
    "FII), o mesmo veredito que a aba Inteligência dos Ativos mostra ao "
    "usuário, com a mesma dívida e a mesma régua setorial. Sua recomendação "
    "não pode contradizê-lo:\n"
    "- LIMITE = avaliar troca: há alerta eliminatório (ou a decisão é avaliar "
    "troca). É proibido recomendar comprar, comprar mais, aumentar ou manter "
    "sem ressalva; cite o alerta com o número.\n"
    "- LIMITE = reduzir: a posição está acima da % devida pela estratégia do "
    "usuário. É proibido recomendar comprar ou aumentar; manter só com a "
    "ressalva de reduzir (ou de parar de aportar até o peso voltar).\n"
    "- LIMITE = não aportar: qualidade frágil ou posição já na % devida. É "
    "proibido recomendar comprar, comprar mais ou aumentar; manter só com a "
    "ressalva de não reforçar.\n"
    "- LIMITE = livre: siga os critérios do bloco.\n"
    "Se houver linha 'DECISÃO da Inteligência', ela é a decisão que o "
    "usuário vê na aba (Manter, Comprar ou Vender, com o porquê): repita-a, "
    "não a troque por outra. Se os dados do contexto parecerem contradizer o "
    "veredito, diga qual número diverge e de qual fonte vem cada um, sem "
    "trocar o veredito. Use os números do bloco para dívida (dívida "
    "líquida/EBITDA, dívida bruta/patrimônio): são os do balanço da base. "
    "Ativo citado sem avaliação no bloco não tem veredito: não invente um."
)


@dataclass(frozen=True)
class _Insumos:
    """O que ``avaliacao.avaliar`` lê de uma ``AnaliseAtivo``."""
    ativo: InfoBasica
    fundamentos: Secao
    valuation: Secao
    pares: Secao
    noticias: Secao


def info_ativo(ticker: str, nome: str | None = None,
               setor: str | None = None, *, mercado: str = "b3"
               ) -> InfoBasica:
    """A ``InfoBasica`` que a Inteligência monta para o ativo do ``mercado``
    (b3, us, fii), sem subclasse (``calculos.subclasse`` não dá uma a
    ``Ações BR``, ``Ações EUA`` nem ``FII``)."""
    from core.inteligencia_ativos import calculos
    classe, moeda = MERCADOS.get(mercado, MERCADOS["b3"])
    tk = str(ticker or "").strip().upper()
    if moeda == "BRL":
        tk = tk.replace(".SA", "")
    return InfoBasica(ticker=tk, nome=nome or tk, classe=classe,
                      classe_politica=calculos.classe_politica(
                          {"classe": classe, "moeda": moeda}),
                      subclasse=None, setor=setor or None, moeda=moeda,
                      valor_investido=None, valor_mercado=None,
                      peso_atual=0.0)


def info_acao_b3(ticker: str, nome: str | None = None,
                 setor: str | None = None) -> InfoBasica:
    return info_ativo(ticker, nome, setor, mercado="b3")


def avaliar_ativo(ticker: str, nome: str | None = None,
                  setor: str | None = None, *, mercado: str = "b3",
                  ler_fundamentos=None, ler_valuation=None,
                  ler_informacoes=None) -> av_.Avaliacao:
    """A ``Avaliacao`` da Inteligência para o ativo, pelos mesmos provedores.
    Os leitores são injetáveis (testes); o padrão é o de ``secoes``, que
    despacha por classe (B3, EUA, FII)."""
    info = info_ativo(ticker, nome, setor, mercado=mercado)
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


def avaliar_acao_b3(ticker: str, nome: str | None = None,
                    setor: str | None = None, **leitores) -> av_.Avaliacao:
    return avaliar_ativo(ticker, nome, setor, mercado="b3", **leitores)


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


ROTULO_LIMITE = {TROCAR: "avaliar troca", REDUZIR: "reduzir",
                 NAO_APORTAR: "não aportar", LIVRE: "livre"}


@dataclass(frozen=True)
class Veredito:
    """A decisão inteira da Inteligência para um ativo da carteira do
    usuário (``resumida.decisao``) e o limite que ela impõe à LLM."""
    ticker: str
    limite: str
    motivo: str
    decisao: str          # rótulo: Manter | Comprar | Vender
    detalhe: str          # o porquê curto que a aba mostra
    avaliacao: av_.Avaliacao


def veredito_de_decisao(ticker: str, d) -> Veredito:
    """``resumida.Decisao`` → ``Veredito``. Vender que pede avaliar (troca
    ou venda: alerta, tese em dúvida, alternativa) vira "avaliar troca";
    vender parte pelo peso vira "reduzir"; Manter barra o aporte; Comprar
    fica livre (com os critérios da avaliação)."""
    from core.inteligencia_ativos import resumida as rs
    av = d.avaliacao
    cod_av, motivo_av = limite(av) if av is not None else (LIVRE, "")
    detalhe = str(d.detalhe or "")
    if d.codigo == rs.VENDER:
        cod = TROCAR if detalhe.startswith("avaliar") else REDUZIR
    elif d.codigo == rs.MANTER:
        cod = NAO_APORTAR
    else:
        cod = LIVRE
    if cod == TROCAR and cod_av == TROCAR:
        motivo = motivo_av
    else:
        motivo = "; ".join(x for x in (detalhe, motivo_av if cod_av != LIVRE
                                       and motivo_av not in detalhe else "")
                           if x)
    return Veredito(ticker, cod, motivo if cod != LIVRE else "", d.rotulo,
                    detalhe, av)


def _limite_de(x) -> tuple[str, str]:
    """(limite, motivo) de uma ``Avaliacao`` ou de um ``Veredito``."""
    if isinstance(x, Veredito):
        return x.limite, x.motivo
    return limite(x)


def _avaliacao_de(x) -> av_.Avaliacao | None:
    return x.avaliacao if isinstance(x, Veredito) else x


def texto_ticker(x, ticker: str) -> str:
    cod, motivo = _limite_de(x)
    cab = f"LIMITE da recomendação para {ticker}: {ROTULO_LIMITE[cod]}"
    if motivo:
        cab += f" ({motivo})"
    if isinstance(x, Veredito):
        cab += (f"\nDECISÃO da Inteligência dos Ativos para {ticker} (pela "
                f"estratégia e pelo peso na carteira do usuário): "
                f"{x.decisao} — {x.detalhe}")
    av = _avaliacao_de(x)
    return cab + ("\n" + av_.texto(av, ticker) if av is not None else "")


def vereditos_da_carteira(resultado: dict | None
                          ) -> dict[str, Veredito]:
    """Veredito por ticker da carteira do usuário, com a mesma decisão que
    a página resumida da Inteligência mostra (``blocos`` → ``alvos`` →
    ``decisao``). ``resultado`` é o de ``servico.analisar_carteira``; sem
    análise disponível (Estratégia não liberada), ``{}``. Reserva e renda
    fixa ficam de fora, como na página."""
    from core.inteligencia_ativos import resumida as rs
    if not resultado or not resultado.get("analysis_available"):
        return {}
    ctx, analises = resultado.get("contexto"), resultado.get("analises") or ()
    saida: dict[str, Veredito] = {}
    for b in rs.blocos(analises, ctx):
        if b.chave in rs.GRUPOS_EM_LISTA:
            continue
        alvos = b.alvos
        for a in b.analises:
            tk = str(a.ativo.ticker or "").strip().upper()
            try:
                av = av_.avaliar(a)
                d = rs.decisao(a, alvos.get(a.ativo.ticker), av)
            except Exception as exc:  # um ativo não derruba os outros
                logger.warning("Decisão de %s falhou: %s", tk, exc)
                continue
            saida[tk] = veredito_de_decisao(tk, d)
    return saida


def bloco_para_llm(itens, *, avaliador=None, mercado: str = "b3",
                   decisoes: dict | None = None,
                   max_tickers: int = MAX_TICKERS
                   ) -> tuple[str, dict]:
    """Bloco 'AVALIAÇÃO POR REGRAS' para os tickers de ``itens`` (dicts com
    ``ticker`` e, se houver, ``nome``, ``setor`` e ``mercado``) e as
    avaliações por ticker. ``mercado`` é o padrão dos itens sem um.
    ``decisoes`` (ticker → ``Veredito``) vale no lugar do avaliador: é a
    decisão da carteira do usuário. Ticker que falha é nomeado no bloco,
    nunca some."""
    decisoes = {str(k).upper(): v for k, v in (decisoes or {}).items()}
    vistos: list[dict] = []
    for it in itens or ():
        tk = str((it or {}).get("ticker") or "").strip().upper()
        if tk and tk not in {v["ticker"] for v in vistos}:
            vistos.append({**it, "ticker": tk})
    partes = ["=== AVALIAÇÃO POR REGRAS (Inteligência dos Ativos) ==="]
    avs: dict = {}
    for it in vistos[:max_tickers]:
        tk = it["ticker"]
        if tk in decisoes:
            avs[tk] = decisoes[tk]
            partes.append(texto_ticker(decisoes[tk], tk))
            continue
        merc = it.get("mercado") or mercado
        try:
            if avaliador is not None:
                av = avaliador(tk, it.get("nome"), it.get("setor"))
            else:
                av = avaliar_ativo(tk, it.get("nome"), it.get("setor"),
                                   mercado=merc)
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


def bloco_seguro(itens, **kw) -> tuple[str, dict]:
    """``bloco_para_llm`` que não derruba o chat: a falha geral vira um
    bloco que a nomeia (a LLM não conclui sem o veredito)."""
    try:
        return bloco_para_llm(itens, **kw)
    except Exception as exc:
        logger.warning("Bloco de veredito falhou: %s", exc)
        return ("=== AVALIAÇÃO POR REGRAS (Inteligência dos Ativos) ===\n"
                f"Indisponível agora ({type(exc).__name__}): não recomende "
                "comprar nem aumentar sem ela."), {}


def anexar(contexto: str, itens, mencionados=(), **kw) -> tuple[str, dict]:
    """``contexto`` + o bloco das posições e dos tickers citados, e as
    avaliações por ticker para ``responder_coerente``. Ponto único dos chats:
    cada tela só diz quais ativos e de que mercado."""
    alvo = list(itens or []) + [{"ticker": t} for t in (mencionados or ()) if t]
    bloco, avs = bloco_seguro(alvo, **kw)
    return (f"{contexto}\n\n{bloco}" if bloco else contexto), avs


def mercado_da_posicao(posicao: dict) -> str | None:
    """Mercado (b3, us, fii) em que a posição da carteira do usuário tem
    avaliação por regras, ou None (renda fixa, ETF, BDR, cripto)."""
    classe = str(posicao.get("classe") or "")
    moeda = str(posicao.get("moeda") or "BRL").upper()
    if classe == "FII":
        return "fii"
    if classe == "Ações BR" and moeda in ("", "BRL"):
        return "b3"
    if moeda not in ("", "BRL") and "ETF" not in classe.upper():
        return "us"
    return None


def bloco_da_carteira(posicoes, decisoes: dict | None = None, pergunta: str = "",
                      ) -> tuple[str, dict]:
    """Bloco de veredito da carteira do usuário: a DECISÃO da Inteligência
    (``decisoes``, de ``vereditos_da_carteira``) onde houver, e o limite do
    ativo nas posições sem decisão e nas ações da B3 citadas na pergunta.
    Maiores posições primeiro; decisão pronta não conta para o teto, que
    existe para não avaliar ativo demais por turno."""
    decisoes = {str(k).upper(): v for k, v in (decisoes or {}).items()}

    def valor(p):
        try:
            return float(p.get("valor_mercado") or 0)
        except (TypeError, ValueError):
            return 0.0
    com, sem = [], []
    for p in sorted(posicoes or (), key=valor, reverse=True):
        tk = str(p.get("ticker") or "").strip().upper()
        if tk in decisoes:
            com.append({"ticker": tk})
        elif (merc := mercado_da_posicao(p)) is not None:
            sem.append({"ticker": tk, "nome": p.get("nome"),
                        "setor": p.get("setor"), "mercado": merc})
    citados = [{"ticker": t, "mercado": "b3"}
               for t in dict.fromkeys(_TICKER_RE.findall(str(pergunta or "")))]
    return bloco_seguro(citados + com + sem, decisoes=decisoes,
                        max_tickers=MAX_TICKERS + len(com))


_ORDEM_PERSPECTIVA = {"fraca": 0, "moderada": 1, "forte": 2}
_TETO = {TROCAR: "fraca", REDUZIR: "moderada", NAO_APORTAR: "moderada"}


def coerente(analise: dict, av: av_.Avaliacao | None) -> dict:
    """Limita a ``perspectiva`` do relatório da LLM ao que a avaliação
    permite: alerta eliminatório (ou "avaliar troca") → no máximo "fraca";
    qualidade frágil → no máximo "moderada". O resumo diz por quê. Puro."""
    if av is None or not isinstance(analise, dict):
        return analise
    cod, motivo = _limite_de(av)
    teto = _TETO.get(cod)
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
    cod, _ = _limite_de(av)
    teto = _TETO.get(cod)
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
# Ticker citado vira "§" no texto normalizado: o verbo "aumentar AAPL" tem
# objeto, como "aumentar PETR4".
_MASCARA = "§"
_OBJETO = (r"(?=[^.;\n]{0,25}?(?:\b(posicao|peso|exposicao|participacao|"
           r"alocacao|fatia|cotas|[a-z]{4}\d{1,2})\b|" + _MASCARA + "))")
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

# Ticker americano curto colide com palavra em maiúscula ("A dívida", "O
# fundo", "DE"): esses não são procurados no texto (o bloco ainda os avalia).
_PALAVRAS = frozenset({"A", "E", "O", "AS", "OS", "AO", "DA", "DE", "DO",
                       "EM", "NA", "NO", "UM", "OU", "SE", "IR", "PL", "DY",
                       "EUA", "ROE", "LPA", "VPA", "FII", "IPO", "PIB"})


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


def _mencoes_re(conhecidos=()) -> re.Pattern:
    """Tickers no texto: o padrão da B3 (4 letras + dígitos, que cobre FII)
    e os tickers avaliados (os americanos não têm padrão próprio), como
    palavra inteira, sem pegar "AAPL" dentro de "AAPLX" nem ".SA"."""
    extra = sorted({str(t).upper() for t in conhecidos
                    if t and len(str(t)) >= 2
                    and str(t).upper() not in _PALAVRAS
                    and not _TICKER_RE.fullmatch(str(t).upper())},
                   key=len, reverse=True)
    alternativas = [r"[A-Z]{4}\d{1,2}"] + [re.escape(t) for t in extra]
    return re.compile(r"(?<![A-Za-z0-9])(" + "|".join(alternativas)
                      + r")(?![A-Za-z0-9])")


def _trechos(texto: str, ticker: str, mencoes_re: re.Pattern | None = None
             ) -> list[tuple[int, int]]:
    """Janelas do texto que falam de ``ticker``: da menção (ou do começo da
    linha, se não houver outro ticker antes) até o próximo ticker, o fim do
    parágrafo ou 400 caracteres. Ticker em título estende ao parágrafo
    seguinte."""
    mencoes = [(m.start(), m.end(), m.group(1))
               for m in (mencoes_re or _mencoes_re((ticker,))).finditer(texto)]
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
    """Contradições da resposta com o limite de cada ticker avaliado
    (``Avaliacao`` ou ``Veredito``): compra ou aumento com limite "avaliar
    troca", "reduzir" ou "não aportar"; manter sem ressalva com "avaliar
    troca" ou "reduzir". Uma violação por ticker, no máximo."""
    if not texto or not avaliacoes:
        return []
    corpo = _CHARTS_RE.sub(lambda m: " " * len(m.group(0)), texto)
    mre = _mencoes_re(avaliacoes.keys())
    norm = list(_norm(corpo))
    for m in mre.finditer(corpo):
        norm[m.start():m.end()] = _MASCARA * (m.end() - m.start())
    norm = "".join(norm)
    achadas: list[Violacao] = []
    for tk, x in avaliacoes.items():
        cod, motivo = _limite_de(x)
        if cod == LIVRE:
            continue
        for ini, fim in _trechos(corpo, str(tk).upper(), mre):
            seg = norm[ini:fim]
            acao = None
            for m in _COMPRA_RE.finditer(norm, ini, fim):
                if _sem_negacao(norm, m, ini):
                    acao = "comprar/aumentar"
                    break
            if acao is None and cod in (TROCAR, REDUZIR) \
                    and not _RESSALVA_RE.search(seg):
                for m in _MANTER_RE.finditer(norm, ini, fim):
                    if _sem_negacao(norm, m, ini):
                        acao = "manter sem ressalva"
                        break
            if acao:
                trecho = " ".join(corpo[ini:fim].split())[:200]
                achadas.append(Violacao(str(tk).upper(), acao, trecho, cod,
                                        motivo))
                break
    return achadas


def _linha(v: Violacao) -> str:
    return (f"{v.ticker}: a resposta indica {v.acao} (“{v.trecho}”), "
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
        "número; com 'reduzir', não recomende comprar nem aumentar, e manter "
        "só com a ressalva de reduzir; com 'não aportar', não recomende "
        "comprar nem aumentar. "
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


def responder_coerente(chat: Callable[[list, str], str], history: list,
                       user_message: str, avaliacoes: dict | None) -> str:
    """Conferência pós-resposta para qualquer chat: ``chat(history, msg)``
    devolve a resposta. Se ela recomenda acima do limite de algum ticker,
    pede uma reescrita; se a reescrita falhar ou ainda contradisser, a
    resposta sai com aviso no topo — nunca em silêncio. Custa uma chamada a
    mais só quando há contradição."""
    history = list(history or [])
    resposta = chat(history, user_message)
    violacoes = conferir_resposta(resposta, avaliacoes)
    if not violacoes:
        return resposta
    logger.info("Chat contradiz o veredito em %s; pedindo reescrita.",
                ", ".join(v.ticker for v in violacoes))
    turnos = [*history, {"role": "user", "content": user_message},
              {"role": "assistant", "content": resposta}]
    try:
        nova = chat(turnos, pedido_de_correcao(violacoes))
    except Exception as exc:
        logger.warning("Reescrita do chat falhou: %s", exc)
        return com_aviso(resposta, violacoes)
    if not str(nova or "").strip():
        return com_aviso(resposta, violacoes)
    return com_aviso(nova, conferir_resposta(nova, avaliacoes))


# ─────────────────────────────────────────────────────────────────────────────
# Portão da seleção (Criação de Portfólio B3, Empresas Americanas, FIIs)
# ─────────────────────────────────────────────────────────────────────────────
# A carteira criada é compra: um nome que a Inteligência barra ("avaliar
# troca" ou "não aportar") não pode entrar nela, ou a mesma tela que monta a
# carteira contradiz a aba que a avalia.

VETA_SELECAO = (TROCAR, NAO_APORTAR)


def novo_log_selecao() -> dict:
    return {"vetados": [], "substituicoes": [], "vagas_vazias": [],
            "indisponiveis": [], "persistentes": []}


def filtrar_selecao(selecionados, ranked, *, avaliador, pesos: dict,
                    seg_label: str, log: dict, exclui=None,
                    max_substitutos: int = 3) -> list[str]:
    """Os ``selecionados`` do segmento sem os que a Inteligência barra. O
    vetado cede a vaga e o peso ao próximo de ``ranked`` (pares (ticker,
    score) do mesmo segmento, na ordem do score) que ela não barra; até
    ``max_substitutos`` candidatos por vaga. Sem substituto, a vaga fica
    vazia e vai para o log.

    ``avaliador(ticker) -> Avaliacao``. Falha de avaliação não veta
    (fail-open) mas é nomeada em ``log["indisponiveis"]``. ``exclui(ticker)``
    é a regra de exclusão que a montagem já aplica (Score de Entrada).
    Altera ``pesos`` e ``log``; não toca em score."""
    exclui = exclui or (lambda _tk: False)
    selecionados = [str(t) for t in selecionados]
    julgados: dict[str, tuple[str, str] | None] = {}

    def barrado(tk: str) -> tuple[str, str] | None:
        if tk not in julgados:
            try:
                cod, motivo = limite(avaliador(tk))
                julgados[tk] = (cod, motivo) if cod in VETA_SELECAO else None
            except Exception as exc:  # fail-open, mas nomeado
                logger.warning("Inteligência indisponível para %s: %s", tk, exc)
                log["indisponiveis"].append(
                    {"tk": tk, "segmento": seg_label,
                     "erro": type(exc).__name__})
                julgados[tk] = None
        return julgados[tk]

    def vetar(tk: str, j: tuple[str, str]) -> None:
        log["vetados"].append({"tk": tk, "segmento": seg_label,
                               "limite": ROTULO_LIMITE[j[0]], "motivo": j[1]})

    finais: list[str] = []
    for tk in selecionados:
        j = barrado(tk)
        if j is None:
            finais.append(tk)
            continue
        vetar(tk, j)
        substituto, tentados = None, 0
        for cand, _sc in ranked or ():
            cand = str(cand)
            if cand in finais or cand in selecionados or cand in julgados \
                    and julgados[cand] is not None or exclui(cand):
                continue
            tentados += 1
            jc = barrado(cand)
            if jc is None:
                substituto = cand
                break
            vetar(cand, jc)
            if tentados >= max_substitutos:
                break
        if substituto:
            finais.append(substituto)
            pesos[substituto] = pesos.get(substituto) or pesos.get(tk, 0.0)
            log["substituicoes"].append({"entra": substituto, "sai": tk,
                                         "segmento": seg_label})
        else:
            log["vagas_vazias"].append({"sai": tk, "segmento": seg_label})
    return finais


def reotimizar_sem_vetados(montar: Callable[[frozenset], dict], *,
                           avaliador, itens_de, log: dict,
                           max_rodadas: int = 6) -> tuple[dict, frozenset]:
    """O portão para carteira montada por otimizador (Seleção de FIIs), que
    não tem vaga por segmento para um substituto herdar.

    ``montar(excluidos) -> resultado`` monta a carteira sem os tickers de
    ``excluidos``; ``itens_de(resultado) -> [(ticker, grupo)]`` lista o que
    entrou. Nome barrado ("avaliar troca" ou "não aportar") vai para os
    excluídos e a carteira é montada de novo: quem entra no lugar é escolha
    do otimizador, sob as mesmas restrições. Repete até nenhum entrar
    barrado ou até ``max_rodadas``; o que ainda restar barrado vai para
    ``log["persistentes"]`` (nunca sai calado).

    Mesma regra de ``filtrar_selecao``: avaliação que falha não veta, mas é
    nomeada em ``log["indisponiveis"]``. Devolve (resultado, excluídos)."""
    julgados: dict[str, tuple[str, str] | None] = {}
    excluidos: frozenset = frozenset()
    resultado = montar(excluidos)
    anteriores = {tk for tk, _ in itens_de(resultado)}
    for rodada in range(max_rodadas):
        barrados = []
        for tk, grupo in itens_de(resultado):
            if tk not in julgados:
                try:
                    cod, motivo = limite(avaliador(tk))
                    julgados[tk] = ((cod, motivo) if cod in VETA_SELECAO
                                    else None)
                except Exception as exc:  # fail-open, mas nomeado
                    logger.warning("Inteligência indisponível para %s: %s",
                                   tk, exc)
                    log["indisponiveis"].append(
                        {"tk": tk, "segmento": grupo,
                         "erro": type(exc).__name__})
                    julgados[tk] = None
            if julgados[tk] is not None:
                barrados.append((tk, grupo))
        if not barrados:
            break
        if rodada == max_rodadas - 1:
            log.setdefault("persistentes", []).extend(
                {"tk": tk, "segmento": g,
                 "limite": ROTULO_LIMITE[julgados[tk][0]],
                 "motivo": julgados[tk][1]} for tk, g in barrados)
            break
        for tk, grupo in barrados:
            cod, motivo = julgados[tk]
            log["vetados"].append({"tk": tk, "segmento": grupo,
                                   "limite": ROTULO_LIMITE[cod],
                                   "motivo": motivo})
        excluidos = excluidos | {tk for tk, _ in barrados}
        resultado = montar(excluidos)
        sai = ", ".join(tk for tk, _ in barrados)
        atuais = itens_de(resultado)
        for tk, grupo in atuais:
            if tk not in anteriores:
                log["substituicoes"].append({"entra": tk, "sai": sai,
                                             "segmento": grupo})
        anteriores = {tk for tk, _ in atuais}
    # Quem entrou numa rodada e foi barrado na seguinte não substituiu ninguém.
    log["substituicoes"] = [s for s in log["substituicoes"]
                            if s["entra"] not in excluidos]
    return resultado, excluidos
