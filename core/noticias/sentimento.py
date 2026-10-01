"""Sentimento recalculado pelo APP4, para conferir o do provedor.

Léxico simples e auditável, não modelo. A escolha é deliberada: o valor deste
recálculo não está em ser melhor que o modelo da API -- não é --, está em ser
*independente* dele e explicável linha a linha. Quando os dois concordam, a
leitura ganha respaldo; quando discordam, isso vira um sinal de baixa confiança
que aparece na tela em vez de sumir.

Duas decisões que evitam modos de falha já vistos neste projeto:

* **Nenhum termo casado devolve ``None``, não ``0.0``.** Zero é "li e achei
  neutro"; ``None`` é "não consegui medir". Tratar os dois como a mesma coisa
  faria a notícia que o léxico não entende pesar como notícia neutra observada.
* **Negação inverte, não anula.** "não confirmou a fusão" com a fusão contando
  positivo daria um falso positivo; inverter o sinal do termo negado é o
  mínimo para o léxico não dizer o contrário do texto.

Na 1.2.0 a negação ganhou escopo (1.1.0 errava "não reduz dividendos" para
negativo, e "Banco não tem recomendação de venda" para venda):

* **A negação vale para o primeiro termo que vem depois dela**, em até três
  palavras, e é consumida ali. Termo de duas ou mais palavras também é negado.
* **Verbo que diminui inverte o termo seguinte** ("corta dividendos" é ruim,
  "reduz prejuízo" é bom). Sozinho ele não pontua: "reduz custos" e "corta a
  Selic" continuam sem medida, porque corte de custo ou de juros não é
  notícia ruim.
* **Negação antes do verbo que diminui cancela a inversão**: "não reduz
  dividendos" volta a ser dividendos, positivo.
* **Pontuação e conjunção fecham o escopo** ("não cai, mas lucro sobe" não
  nega o lucro; "reduz projeção, rebaixa Klabin" não inverte o rebaixamento).
* **Casa o termo mais longo**: "eleva preço-alvo" conta uma vez, não também
  como "eleva".
"""
from __future__ import annotations

import re

from core.noticias.modelos import Sentimento
from core.noticias.normalizacao import detectar_idioma, normalizar_texto

METODO = "lexico_app4_1.2.0"

# Pesos em -1..+1. Termos fortes (fraude, recuperação judicial) valem mais que
# termos de variação de preço, porque descrevem mudança de fundamento e não
# oscilação de mercado.
LEXICO_PT: dict[str, float] = {
    "lucro": 0.5, "lucros": 0.5, "alta": 0.4, "avanca": 0.4, "avanco": 0.4,
    "cresce": 0.5, "crescimento": 0.5, "recorde": 0.6, "supera": 0.6,
    "aprovacao": 0.4, "aprovado": 0.4, "dividendo": 0.4, "dividendos": 0.4,
    "expansao": 0.5, "aquisicao": 0.3, "contrato": 0.3, "acordo": 0.3,
    "melhora": 0.5, "elevacao": 0.4, "valorizacao": 0.5, "otimista": 0.4,
    "prejuizo": -0.7, "queda": -0.5, "cai": -0.4, "recuo": -0.4,
    "perda": -0.5, "perdas": -0.5, "fraude": -0.9, "investigacao": -0.5,
    "multa": -0.5, "rebaixamento": -0.7, "calote": -0.9, "inadimplencia": -0.6,
    "demissao": -0.4, "demissoes": -0.4, "vacancia": -0.5, "greve": -0.4,
    "recuperacao judicial": -0.95, "falencia": -0.95, "despencou": -0.7,
    "crise": -0.6, "risco": -0.3, "adiamento": -0.3, "suspensao": -0.5,
    "pessimista": -0.4, "desvalorizacao": -0.5, "escandalo": -0.8,
    # 1.1.0 -- vocabulário de mercado. Na 1.0.0 o léxico não casava nada em
    # 58% das notícias publicadas (801 de 1.377), quase todas de analista
    # ("eleva preço-alvo", "rebaixa para neutro") ou de pregão ("ações sobem").
    "sobe": 0.4, "sobem": 0.4, "subiu": 0.4, "subiram": 0.4, "dispara": 0.6,
    "disparam": 0.6, "salta": 0.5, "saltam": 0.5, "ganha": 0.3, "ganham": 0.3,
    "lucrou": 0.5, "superou": 0.6, "superam": 0.6, "positivo": 0.4,
    "positivos": 0.4, "forte": 0.3, "fortes": 0.3, "eleva": 0.4,
    "elevam": 0.4, "melhoram": 0.5, "melhorou": 0.5, "aprova": 0.4,
    "recompra": 0.5, "proventos": 0.4, "jcp": 0.4, "otimismo": 0.4,
    "recomendacao de compra": 0.6, "recomenda compra": 0.6,
    "recomendacao para compra": 0.6, "classificacao de compra": 0.6,
    "eleva preco alvo": 0.5, "aumenta preco alvo": 0.5,
    "eleva recomendacao": 0.6, "eleva classificacao": 0.6, "eleva rating": 0.6,
    "eleva a recomendacao": 0.6, "inicia cobertura com compra": 0.5,
    "acima do esperado": 0.5, "acima das estimativas": 0.5,
    "caem": -0.4, "caiu": -0.4, "cairam": -0.4, "recua": -0.4,
    "recuam": -0.4, "recuou": -0.4, "despenca": -0.7, "despencam": -0.7,
    "desaba": -0.7, "desabam": -0.7, "tomba": -0.6, "tombam": -0.6,
    "derrete": -0.7, "afunda": -0.6, "afundam": -0.6, "negativo": -0.4,
    "negativos": -0.4, "fraco": -0.4, "fracos": -0.4, "fraca": -0.4,
    "piora": -0.5, "piorou": -0.5, "rebaixa": -0.6, "rebaixam": -0.6,
    "rebaixou": -0.6, "endividamento": -0.3,
    "preocupacao": -0.4, "preocupacoes": -0.4,
    "pessimismo": -0.4, "prejuizos": -0.7, "deficit": -0.4,
    "recomendacao de venda": -0.6, "recomenda venda": -0.6,
    "recomenda vender": -0.6, "classificacao de venda": -0.6,
    "corta preco alvo": -0.5, "reduz preco alvo": -0.5,
    "rebaixa preco alvo": -0.5, "abaixo do esperado": -0.5,
    "abaixo das estimativas": -0.5, "rebaixa recomendacao": -0.6,
}

LEXICO_EN: dict[str, float] = {
    "profit": 0.5, "profits": 0.5, "beats": 0.6, "beat": 0.5, "surge": 0.6,
    "rises": 0.4, "rise": 0.4, "growth": 0.5, "record": 0.6, "upgrade": 0.6,
    "approval": 0.4, "approved": 0.4, "dividend": 0.4, "expansion": 0.5,
    "acquisition": 0.3, "deal": 0.3, "contract": 0.3, "improves": 0.5,
    "outperform": 0.6, "rally": 0.5, "optimistic": 0.4,
    "loss": -0.5, "losses": -0.5, "misses": -0.6, "miss": -0.5,
    "plunge": -0.7, "falls": -0.4, "fall": -0.4, "decline": -0.4,
    "fraud": -0.9, "probe": -0.5, "investigation": -0.5, "fine": -0.4,
    "downgrade": -0.7, "default": -0.9, "delinquency": -0.6, "layoffs": -0.4,
    "bankruptcy": -0.95, "vacancy": -0.5, "strike": -0.4, "lawsuit": -0.5,
    "crisis": -0.6, "risk": -0.3, "delay": -0.3, "suspension": -0.5,
    "scandal": -0.8, "warns": -0.5, "warning": -0.5,
    # 1.1.0 -- vocabulário de mercado (ver nota no LEXICO_PT).
    "soar": 0.6, "soars": 0.6, "soared": 0.6, "surges": 0.6, "surged": 0.6,
    "jumps": 0.5, "jumped": 0.5, "rose": 0.4, "gains": 0.4, "gained": 0.4,
    "climbs": 0.4, "rallies": 0.5, "beating": 0.5, "tops": 0.5,
    "upgrades": 0.6, "upgraded": 0.6, "raises": 0.4, "raised": 0.4,
    "lifts": 0.4, "boosts": 0.4, "strong": 0.3, "stronger": 0.4,
    "undervalued": 0.4, "buyback": 0.5, "repurchase": 0.4, "shines": 0.5,
    "outperforms": 0.6, "positive": 0.4, "bullish": 0.5,
    "buy rating": 0.5, "overweight": 0.5, "outperform rating": 0.6,
    "raises price target": 0.5, "raised price target": 0.5,
    "raises guidance": 0.6, "raises outlook": 0.6, "lifts outlook": 0.6,
    "raises forecast": 0.6, "price target raised": 0.5,
    "plunged": -0.7, "plunges": -0.7, "tumbles": -0.6, "tumbled": -0.6,
    "slumps": -0.6, "slumped": -0.6, "sinks": -0.5, "sank": -0.5,
    "drops": -0.4, "dropped": -0.4, "fell": -0.4, "slides": -0.4,
    "crashed": -0.8, "crash": -0.7, "downgrades": -0.7, "downgraded": -0.7,
    "weak": -0.4, "weaker": -0.4, "missed": -0.6, "negative": -0.4,
    "pessimistic": -0.4, "bearish": -0.5, "concerns": -0.4, "overvalued": -0.4,
    "sell rating": -0.5, "underweight": -0.5, "underperform": -0.6,
    "cuts price target": -0.5, "lowers price target": -0.5,
    "price target cut": -0.5, "cuts guidance": -0.6, "lowers guidance": -0.6,
    "cuts outlook": -0.6, "lowers outlook": -0.6, "cuts forecast": -0.6,
}

NEGACOES_PT = ("nao", "sem", "nunca", "nenhum", "nenhuma", "jamais")
NEGACOES_EN = ("no", "not", "never", "without", "fails", "failed")

# Verbos que diminuem o que vem depois. Ver docstring. "menor" e "lower"
# ficaram de fora: medidos no acervo, apareciam mais depois do substantivo ou
# como comparativo ("20% menor que", "moves lower as ...") do que antes dele.
DIMINUIDORES_PT = frozenset({
    "reduz", "reduziu", "reduzem", "reduzir", "corta", "cortou", "cortam",
    "cortar", "suspende", "suspendeu", "suspendem", "suspender", "cancela",
    "cancelou", "cancelar", "adia", "adiou", "adiar", "elimina", "eliminou",
    "eliminar", "interrompe", "interrompeu", "interromper",
})
DIMINUIDORES_EN = frozenset({
    "cut", "cuts", "slashes", "slashed", "trims", "trimmed", "suspend",
    "suspends", "suspended", "cancel", "cancels", "cancelled", "canceled",
    "reduce", "reduces", "reduced", "lowers", "lowered", "halt",
    "halts", "halted", "scraps", "scrapped", "eliminates",
})
FRONTEIRAS_PT = frozenset({"e", "mas", "porem", "enquanto", "contudo"})
FRONTEIRAS_EN = frozenset({"and", "but", "while", "whereas", "however"})

# Quantas palavras depois da negação (ou do verbo que diminui) o termo pode
# estar para ser alcançado.
_JANELA_NEGACAO = 3
# Separador de oração no texto original (a normalização apaga a pontuação).
# Vírgula entre dígitos é decimal e não separa.
_ORACAO = re.compile(r"(?<!\d),|,(?!\d)|[.;:!?()\[\]|\u2014\u2013]")


def _lexico(idioma: str | None):
    if idioma == "pt":
        return LEXICO_PT, NEGACOES_PT, DIMINUIDORES_PT, FRONTEIRAS_PT
    if idioma == "en":
        return LEXICO_EN, NEGACOES_EN, DIMINUIDORES_EN, FRONTEIRAS_EN
    return None


def _indexar(lexico: dict[str, float]) -> dict[str, list[tuple[tuple[str, ...], float]]]:
    """Primeira palavra -> termos que começam por ela, o mais longo primeiro."""
    indice: dict[str, list[tuple[tuple[str, ...], float]]] = {}
    for termo, peso in lexico.items():
        partes = tuple(termo.split())
        indice.setdefault(partes[0], []).append((partes, peso))
    for termos in indice.values():
        termos.sort(key=lambda t: -len(t[0]))
    return indice


def _casar(indice, palavras: list[str], i: int) -> tuple[int, float] | None:
    for partes, peso in indice.get(palavras[i], ()):
        if tuple(palavras[i:i + len(partes)]) == partes:
            return len(partes), peso
    return None


def calcular(texto: str | None, idioma: str | None = None) -> float | None:
    """Escore em -1..+1, ou ``None`` quando nenhum termo do léxico apareceu.

    Idioma desconhecido devolve ``None`` sem tentar: aplicar o léxico errado
    produziria um número parecendo medição, e este projeto já pagou caro por
    número plausível vindo da fonte errada.
    """
    normalizado = normalizar_texto(texto)
    if not normalizado:
        return None
    idioma = idioma or detectar_idioma(texto)
    escolhido = _lexico(idioma)
    if escolhido is None:
        return None
    lexico, negacoes, diminuidores, fronteiras = escolhido
    indice = _indexar(lexico)

    pesos: list[float] = []
    for oracao in _ORACAO.split(str(texto)):
        pesos.extend(_pesos_da_oracao(normalizar_texto(oracao).split(),
                                      indice, negacoes, diminuidores,
                                      fronteiras))

    if not pesos:
        return None
    media = sum(pesos) / len(pesos)
    return max(-1.0, min(1.0, media))


def _pesos_da_oracao(palavras, indice, negacoes, diminuidores,
                     fronteiras) -> list[float]:
    pesos: list[float] = []
    # Última posição que a negação / o verbo que diminui ainda alcança.
    negacao_ate = diminui_ate = -1
    diminui_inverte = False

    i = 0
    while i < len(palavras):
        casado = _casar(indice, palavras, i)
        if casado is not None:
            tamanho, peso = casado
            if i <= negacao_ate:
                peso, negacao_ate = -peso, -1
            if i <= diminui_ate:
                if diminui_inverte:
                    peso = -peso
                diminui_ate = -1
            pesos.append(peso)
            i += tamanho
            continue
        palavra = palavras[i]
        if palavra in diminuidores:
            # "não reduz": a negação cancela a diminuição e se consome nela.
            diminui_inverte = i > negacao_ate
            negacao_ate = -1
            diminui_ate = i + _JANELA_NEGACAO
        elif palavra in negacoes:
            negacao_ate = i + _JANELA_NEGACAO
        elif palavra in fronteiras:
            negacao_ate = diminui_ate = -1
        i += 1
    return pesos


def avaliar(titulo: str, resumo: str | None = None, *,
            idioma: str | None = None,
            sentimento_api: float | None = None,
            rotulo_api: str | None = None,
            escala_api: float = 1.0) -> Sentimento:
    """Junta o sentimento do provedor e o do APP4 num só registro.

    ``escala_api`` normaliza provedores cuja faixa não é -1..+1. O Alpha
    Vantage entrega aproximadamente -1..+1 e dispensa ajuste; deixar o
    parâmetro explícito evita que um provedor futuro com faixa 0..100 entre
    silenciosamente e desloque toda a base.
    """
    texto = f"{titulo or ''}. {resumo or ''}"
    idioma_final = idioma or detectar_idioma(texto)
    valor_app4 = calcular(texto, idioma_final)

    valor_api = None
    if sentimento_api is not None and escala_api:
        valor_api = max(-1.0, min(1.0, float(sentimento_api) / escala_api))

    return Sentimento(
        valor_api=valor_api,
        valor_app4=valor_app4,
        rotulo_api=rotulo_api,
        metodo_app4=METODO if valor_app4 is not None else None,
    )
