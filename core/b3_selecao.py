"""Teste de seleção da B3: o excesso mensal sobre Pesos Iguais, com intervalo.

A aprovação de segmento olhava a margem de retorno composto sobre o 1/N
(um número só, sem incerteza) e, no modo econômico, na janela CHEIA — a mesma
em que os pesos foram calibrados. Um segmento podia passar por 3% de margem
acumulada com um excesso mensal cujo intervalo ia de -1% a +1,5% a.m.

Aqui o excesso mensal da VALIDAÇÃO (fora da amostra) vira média + intervalo t.
O veredito tem três estados, e só "contra" reprova por padrão: num mercado com
poucos anos de validação, exigir prova positiva reprova quase tudo — mas um
intervalo inteiro abaixo de zero é evidência de que a seleção DESTRÓI valor em
relação a comprar o segmento inteiro, e nenhum modo deveria aprovar isso.
"""
from __future__ import annotations

import math
from typing import Iterable

VANTAGEM = "vantagem"
CONTRA = "contra"
INCONCLUSIVO = "inconclusivo"
SEM_AMOSTRA = "sem amostra"

CONFIANCA_PADRAO = 0.90
MIN_MESES = 12


def intervalo_excesso(excesso: Iterable[float], confianca: float = CONFIANCA_PADRAO,
                      min_meses: int = MIN_MESES) -> dict:
    """Média e intervalo t bicaudal do excesso mensal (fração, 0.01 = 1% a.m.).

    ``veredito``: VANTAGEM se o limite inferior > 0, CONTRA se o superior < 0,
    INCONCLUSIVO se o intervalo cruza o zero, SEM_AMOSTRA com menos de
    ``min_meses`` observações finitas ou dispersão nula.
    """
    xs = [float(x) for x in excesso if x is not None and math.isfinite(float(x))]
    n = len(xs)
    vazio = {"media": None, "lo": None, "hi": None, "n": n,
             "confianca": confianca, "veredito": SEM_AMOSTRA}
    if n < max(2, min_meses):
        if n:
            vazio["media"] = sum(xs) / n
        return vazio
    media = sum(xs) / n
    var = sum((x - media) ** 2 for x in xs) / (n - 1)
    # Série constante: a soma em ponto flutuante deixa resíduo da ordem de
    # 1e-35 na variância, e o intervalo degenerado viraria veredito firme.
    if math.sqrt(var) <= 1e-12:
        vazio["media"] = media
        return vazio
    from scipy.stats import t

    meia = float(t.ppf(0.5 + confianca / 2, n - 1)) * math.sqrt(var / n)
    lo, hi = media - meia, media + meia
    if lo > 0:
        veredito = VANTAGEM
    elif hi < 0:
        veredito = CONTRA
    else:
        veredito = INCONCLUSIVO
    return {"media": media, "lo": lo, "hi": hi, "n": n,
            "confianca": confianca, "veredito": veredito}


def reprova(intervalo: dict | None, exigir_vantagem: bool = False) -> bool:
    """Regra única do portão de seleção, para todos os modos de aprovação.

    Sem amostra não reprova (a falta de validação é tratada pelos outros
    portões); CONTRA sempre reprova; com ``exigir_vantagem`` só VANTAGEM passa.
    """
    veredito = (intervalo or {}).get("veredito", SEM_AMOSTRA)
    if veredito == CONTRA:
        return True
    if exigir_vantagem:
        return veredito != VANTAGEM
    return False


def rotulo(intervalo: dict | None) -> str:
    """Texto curto para a tabela: '+0,42% [-0,10; +0,95]' em % a.m."""
    iv = intervalo or {}
    if iv.get("lo") is None or iv.get("media") is None:
        return "—"

    def _pct(x: float) -> str:
        return f"{x * 100:+.2f}".replace(".", ",")

    return f"{_pct(iv['media'])}% [{_pct(iv['lo'])}; {_pct(iv['hi'])}]"
