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


def liquidez_para_decisao(declarada, observada,
                          meses_observados: int = 0) -> LiquidezDecisao:
    """Escolhe o numero que pode sustentar decisao de investimento."""
    dec = _positivo_ou_zero(declarada)
    obs = _positivo_ou_zero(observada)
    com_lastro = obs is not None and int(meses_observados or 0) >= MESES_MINIMOS

    if dec is None:
        if obs is None:
            return LiquidezDecisao(None, "ausente", "sem cadastro e sem fita")
        # Preenchimento de lacuna: o comportamento que ja existia.
        return LiquidezDecisao(obs, "fita_b3",
                               "cadastro sem liquidez; usada a fita oficial da B3")
    if not com_lastro:
        falta = "fita ausente" if obs is None else \
                f"fita com apenas {int(meses_observados or 0)} meses"
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
