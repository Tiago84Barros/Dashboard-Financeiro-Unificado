# -*- coding: utf-8 -*-
"""A liquidez que sustenta decisao, quando as fontes discordam (A-133).

O cadastro (``market.fiis.liquidez_diaria``, vindo da brapi) e a fita oficial
da B3 (volume financeiro diario em ``market.fii_b3_security_history``, resumido
por ``data_pipeline.market.fii.liquidez_diaria_b3``) nem sempre contam a mesma
historia. Medido em 26/08/2026, o desacordo chega a ser grosseiro:

    SHPP11  declara 2.794.163/dia  |  fita: 721/dia   -> 3.874x
    VVRI11  declara    68.561/dia  |  fita: 541/dia   ->   127x

Ate aqui o estimador da fita so era chamado quando o cadastro vinha VAZIO. Com
numero preenchido, ninguem conferia -- e o piso de liquidez da politica, que
funciona, era derrotado pela entrada em vez de pela regra.

Quando as duas discordam alem de ``FATOR_CONTRADICAO``, esta funcao prefere a
fita. A razao nao e desconfianca do agregador: e que uma das fontes registra o
negocio e a outra o interpreta. A preferencia exige lastro -- sem
``MESES_MINIMOS`` de observacao, fita curta e lacuna de carga, nao desmentido.

A regra e simetrica de proposito. Declarar liquidez alta demais engana o
investidor sobre a propria saida; declarar baixa demais exclui do universo um
fundo que negocia. O segundo erro e mais barato, nao e inexistente.
"""
from __future__ import annotations

from dataclasses import dataclass

# Fator a partir do qual as duas fontes deixam de ser ruido e viram conflito.
# Dez vezes e folgado por escolha: agregador e bolsa divergem de rotina por
# janela, ajuste e criterio de leilao, e nao se quer trocar de fonte por isso.
FATOR_CONTRADICAO = 10.0

# Meses fechados de fita necessarios para ela poder desmentir o cadastro. O
# proprio estimador ja trabalha com seis meses e descarta o mes incompleto;
# tres e o piso em que a observacao para de ser anedota.
MESES_MINIMOS = 3


@dataclass(frozen=True)
class LiquidezDecisao:
    valor: float | None
    origem: str      # 'declarada' | 'fita_b3' | 'ausente'
    motivo: str
    razao: float | None = None


def _positivo_ou_zero(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f < 0:  # NaN ou negativo nao e liquidez
        return None
    return f


def _meses_validos(v) -> int:
    """Contagem de meses de fita, tolerante ao que o merge do pandas entrega.

    Ticker que nao aparece na fita sai do merge como NaN -- e NaN e truthy,
    entao `int(v or 0)` estourava. Ausencia de contagem e ausencia de lastro.
    """
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return 0
    return max(n, 0)


def liquidez_para_decisao(declarada, observada,
                          meses_observados: int = 0) -> LiquidezDecisao:
    """Escolhe o numero que pode sustentar decisao de investimento."""
    dec = _positivo_ou_zero(declarada)
    obs = _positivo_ou_zero(observada)
    meses = _meses_validos(meses_observados)
    com_lastro = obs is not None and meses >= MESES_MINIMOS

    if dec is None:
        if obs is None:
            return LiquidezDecisao(None, "ausente", "sem cadastro e sem fita")
        # Preenchimento de lacuna: o comportamento que ja existia.
        return LiquidezDecisao(obs, "fita_b3",
                               "cadastro sem liquidez; usada a fita oficial da B3")
    if not com_lastro:
        falta = "fita ausente" if obs is None else \
                f"fita com apenas {meses} meses"
        return LiquidezDecisao(dec, "declarada",
                               f"mantida a declarada ({falta}, sem lastro para desmentir)")

    # Razao sempre >= 1, para nao depender de qual lado e maior.
    if obs == 0:
        razao = float("inf") if dec > 0 else 1.0
    else:
        razao = max(dec / obs, obs / dec) if dec > 0 else float("inf")

    if razao >= FATOR_CONTRADICAO:
        return LiquidezDecisao(
            obs, "fita_b3",
            f"declarada contradiz a fita oficial da B3 em {razao:,.0f}x"
            .replace(",", "."),
            razao=razao)
    return LiquidezDecisao(dec, "declarada",
                           "cadastro e fita concordam dentro da tolerancia",
                           razao=razao)


# Qualidade de fonte: a fita oficial da B3 registra o negocio; o agregador o
# le. A diferenca de 0.95 para 0.80 e a mesma que ja existia no codigo antes
# do A-133 -- o que mudou e QUANDO cada uma se aplica.
QUALIDADE_FITA = 0.95
QUALIDADE_CADASTRO = 0.80


def procedencia_liquidez(origem: str, fonte_fita, fita_available_at,
                         cadastro_available_at) -> dict:
    """Procedencia do numero que a arbitragem escolheu.

    Antes do A-133 so tinha fita quem nao tinha cadastro, entao "existe fita"
    e "a fita foi usada" eram a mesma coisa e ler um campo pelo outro nao doia.
    Ao medir todo ticker isso deixou de valer: 348 de 394 linhas publicadas
    afirmaram origem B3 sem terem trocado de fonte. Aqui a procedencia segue a
    DECISAO -- `origem` -- e nunca a mera disponibilidade do dado alternativo.
    """
    if origem == "fita_b3":
        return {
            "source": str(fonte_fita or "b3_cotahist_monthly_median_div_21_v1"),
            "source_quality": QUALIDADE_FITA,
            "available_at": str(fita_available_at or cadastro_available_at),
        }
    if origem == "declarada":
        return {
            "source": "brapi",
            "source_quality": QUALIDADE_CADASTRO,
            "available_at": str(cadastro_available_at),
        }
    # Sem liquidez nenhuma nao ha o que qualificar. Zero e honesto; herdar a
    # qualidade do cadastro seria afirmar confianca sobre um vazio.
    return {"source": "indisponivel", "source_quality": 0.0,
            "available_at": str(cadastro_available_at)}
