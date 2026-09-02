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
"""
from __future__ import annotations

import re

from core.noticias.modelos import Sentimento
from core.noticias.normalizacao import detectar_idioma, normalizar_texto

METODO = "lexico_app4_1.0.0"

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
}

NEGACOES_PT = ("nao", "sem", "nunca", "nenhum", "nenhuma", "jamais")
NEGACOES_EN = ("no", "not", "never", "without", "fails", "failed")

_JANELA_NEGACAO = 3


def _lexico(idioma: str | None) -> tuple[dict[str, float], tuple[str, ...]] | None:
    if idioma == "pt":
        return LEXICO_PT, NEGACOES_PT
    if idioma == "en":
        return LEXICO_EN, NEGACOES_EN
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
    lexico, negacoes = escolhido

    palavras = normalizado.split()
    pesos: list[float] = []

    for termo, peso in lexico.items():
        if " " in termo:
            if re.search(rf"(?<![a-z0-9]){re.escape(termo)}(?![a-z0-9])",
                         normalizado):
                pesos.append(peso)
            continue
        for i, palavra in enumerate(palavras):
            if palavra != termo:
                continue
            janela = palavras[max(0, i - _JANELA_NEGACAO):i]
            negado = any(p in negacoes for p in janela)
            pesos.append(-peso if negado else peso)

    if not pesos:
        return None
    media = sum(pesos) / len(pesos)
    return max(-1.0, min(1.0, media))


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
