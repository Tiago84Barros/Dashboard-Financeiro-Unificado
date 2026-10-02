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
