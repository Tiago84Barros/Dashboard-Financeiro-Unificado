"""Vocabulário estatístico explicado para quem usa o app — uma casa só.

O termo "Rank-IC" aparecia em seis telas (Empresas B3, Criação de Portfólio,
Safras, Carteira 20, Empresas Americanas, Documentação) sempre como sigla
solta: "+0,092", "Rank-IC ≥ −0,05", "poder preditivo (Rank-IC)". Quem não é
quant não sabe o que o número mede, em que escala vive, nem se 0,09 é muito
ou pouco — e a leitura errada mais comum é tomar 0,092 por "9,2% de retorno".

Cada tela redigia (ou não redigia) a própria explicação; aqui fica a única, e
as telas importam daqui. Puro: sem Streamlit, testável.
"""
from __future__ import annotations

import math

RANK_IC_NOME = "Rank-IC (coeficiente de informação por ranking)"

# Uma frase — cabe em tooltip, `help=` de coluna e legenda curta.
RANK_IC_CURTO = (
    "Rank-IC: mede se o score coloca as empresas na mesma ordem em que os "
    "retornos delas vieram depois. Vai de −1 a +1: +1 = acertou a ordem toda, "
    "0 = ordem aleatória, negativo = ordenou ao contrário. Não é retorno nem "
    "percentual."
)

# Faixas de leitura para o Rank-IC MÉDIO de vários períodos, em ações. São a
# referência usual da literatura de fatores (Grinold-Kahn): IC médio acima de
# 0,05 já é considerado sinal útil, acima de 0,10 é raro. Limite inferior
# inclusivo; percorridas em ordem.
FAIXAS_RANK_IC: tuple[tuple[float, str, str], ...] = (
    (0.10, "forte", "forte — raro em ações; vale desconfiar e conferir a amostra"),
    (0.05, "moderado", "moderado — patamar de um fator considerado útil"),
    (0.02, "fraco", "fraco — alguma ordenação, mas pequena"),
    (-0.02, "nulo", "praticamente nulo — a ordem do score não antecipa a dos retornos"),
)
_FAIXA_INVERTIDA = ("invertido", "negativo — o score tende a ordenar ao contrário")

RANK_IC_ESCALA = (
    "Como ler o valor médio: abaixo de −0,02, o score ordena ao contrário; "
    "de −0,02 a +0,02, praticamente nulo; de +0,02 a +0,05, fraco; "
    "de +0,05 a +0,10, moderado (já é um fator útil); acima de +0,10, forte "
    "e raro em ações. Valores pequenos são normais: retorno de ação tem muito "
    "ruído, e mesmo os melhores fatores conhecidos ficam perto de 0,05–0,10."
)

RANK_IC_COMO_CALCULA = (
    "Como é calculado: em cada data de decisão, as empresas são ordenadas duas "
    "vezes — pelo score que tinham naquele momento e pelo retorno que tiveram "
    "no período seguinte. O Rank-IC é a correlação entre essas duas ordens "
    "(correlação de Spearman). Repete-se a conta a cada período e tira-se a "
    "média; o valor-p diz se essa média poderia ter saído por acaso."
)

RANK_IC_NAO_E = (
    "O que ele NÃO diz: Rank-IC não é retorno. Um +0,09 não significa 9% a "
    "mais, e ordenar bem não garante que a carteira supere o mercado depois "
    "de custos — por isso a tela mostra o excesso de retorno separadamente."
)


def faixa_rank_ic(valor: float | None) -> tuple[str, str] | None:
    """``(rótulo curto, leitura)`` da faixa do valor; None se não finito."""
    if valor is None:
        return None
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    for limite, rotulo, leitura in FAIXAS_RANK_IC:
        if v >= limite:
            return rotulo, leitura
    return _FAIXA_INVERTIDA


def acerto_em_pares(valor: float | None) -> float | None:
    """Probabilidade aproximada de o score acertar qual de duas empresas rende mais.

    Tradução do Rank-IC (Spearman ρs) para a pergunta concreta "peguei duas
    empresas ao acaso; a de score maior rendeu mais?". Sob normalidade
    bivariada: r = 2·sen(π·ρs/6), τ de Kendall = (2/π)·arcsen(r), e a
    probabilidade de concordância de um par é (1 + τ)/2. É aproximação — por
    isso a tela diz "cerca de". 0 → 50% (moeda), +0,092 → ~53%.
    """
    if valor is None:
        return None
    try:
        rho = float(valor)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(rho) or not -1.0 <= rho <= 1.0:
        return None
    r = max(-1.0, min(1.0, 2.0 * math.sin(math.pi * rho / 6.0)))
    tau = (2.0 / math.pi) * math.asin(r)
    return (1.0 + tau) / 2.0


def leitura_rank_ic(valor: float | None, *, periodo: str = "no período seguinte") -> str:
    """Frase em português claro sobre UM valor de Rank-IC médio.

    Vazia quando o valor não é finito — a tela já mostra "—" nesse caso.
    """
    faixa = faixa_rank_ic(valor)
    if faixa is None:
        return ""
    v = float(valor)  # type: ignore[arg-type]
    texto = f"Rank-IC {v:+.3f} é {faixa[1]}."
    p = acerto_em_pares(v)
    if p is not None:
        texto += (f" Na prática: entre duas empresas escolhidas ao acaso, a de "
                  f"score maior rendeu mais {periodo} em cerca de "
                  f"{p * 100:.0f}% das vezes (50% seria cara ou coroa).")
    return texto


def ajuda_rank_ic(valor: float | None = None, *, periodo: str = "no período seguinte") -> str:
    """Texto de tooltip: definição + leitura do valor, quando houver."""
    leitura = leitura_rank_ic(valor, periodo=periodo)
    return f"{RANK_IC_CURTO} {leitura}".strip()


def explicacao_rank_ic(valor: float | None = None, *,
                       periodo: str = "no período seguinte") -> str:
    """Explicação completa em markdown, para o bloco expansível das telas."""
    def _secao(texto: str) -> str:
        titulo, corpo = texto.split(":", 1)
        corpo = corpo.strip()
        return f"**{titulo}.** {corpo[:1].upper()}{corpo[1:]}"

    partes = [
        _secao(RANK_IC_CURTO.replace("Rank-IC:", "O que é:", 1)),
        _secao(RANK_IC_COMO_CALCULA),
        _secao(RANK_IC_ESCALA),
        _secao(RANK_IC_NAO_E),
    ]
    leitura = leitura_rank_ic(valor, periodo=periodo)
    if leitura:
        partes.insert(0, f"**Este valor.** {leitura}")
    return "\n\n".join(partes)


# ── Intervalo de 95% ────────────────────────────────────────────────────────
# A sigla "IC 95%" (intervalo de confiança) colidia com "IC" de Rank-IC
# (coeficiente de informação) — na mesma tela de Criação de Portfólio, "IC"
# queria dizer duas coisas. As colunas passam a se chamar "Intervalo 95%" e o
# significado fica escrito ao lado delas.

INTERVALO_95_EXCESSO = (
    "Intervalo 95%: a faixa onde o excesso médio verdadeiro por safra deve "
    "estar, com 95% de confiança, dado o pouco histórico que existe. É "
    "calculado reamostrando as safras medidas (bootstrap). Se a faixa vai de "
    "um número negativo a um positivo — cruza o zero —, a medição não separa "
    "a carteira da referência: o ganho médio observado pode ter sido sorte. "
    "Faixa toda acima de zero = vantagem real nesta amostra; toda abaixo = "
    "a carteira perdeu."
)

INTERVALO_95_CORRELACAO = (
    "Intervalo 95%: a faixa onde a correlação verdadeira do par deve estar, "
    "com 95% de confiança, dado o número de meses observados. Quanto menos "
    "meses, mais larga a faixa. Se ela vai de um número negativo a um "
    "positivo — cruza o zero —, não dá para dizer que os dois ativos andam "
    "juntos (ou em sentidos opostos): a correlação medida pode ser acaso, e "
    "a proteção que ela sugere pode não existir."
)

INTERVALO_95_EXCESSO_IFIX = (
    "Excesso vs IFIX — intervalo 95%: a faixa onde deve estar, com 95% de "
    "confiança, quanto a carteira de FIIs rende por mês a mais (ou a menos) "
    "que o IFIX, calculada reamostrando os meses do teste histórico. Se a "
    "faixa cruza o zero (vai de negativo a positivo), a carteira não se "
    "distingue do IFIX nesta amostra — por isso aparece \"não significativo\"."
)
