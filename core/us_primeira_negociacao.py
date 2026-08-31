# -*- coding: utf-8 -*-
"""Quando o papel comecou a ser negociavel -- e o que fazer quando nao se sabe.

`data_pipeline/us/scoring_history.py` ja tinha o portao que exclui a empresa
das safras anteriores a sua estreia. Ele nunca disparou: `market_us.assets`
tem 7.654 linhas e ZERO `first_trade_date` -- nenhum passo do projeto escreve
nessa coluna. Portao cujo insumo nao tem escritor e codigo morto que parece
protecao.

O custo medido nas 16 safras da versao 0.7.1: 2.695 das 23.522 linhas --
11,5% -- eram empresa que ainda nao havia negociado na data. Na safra de 2010,
20 de 163; na de 2025, 205 de 2.354. Elas entram
porque a demonstracao anual de um ano pre-IPO chega ao EDGAR junto do S-1, e
a linha sem `filed_at` cai na regra antiga (fim do periodo mais folga). Nada
disso era publico na data, e o papel nem existia para ser comprado -- e como
o score e por RANK, cada intrusa desloca a posicao de todas as outras.

A data derivada aqui e a primeira negociacao OBSERVADA, nao a estreia legal:
o piso e o que a serie mensal registra. As duas divergem se a serie for
truncada; no armazem ela nao e -- os primeiros meses se espalham de 1962 em
diante, sem empilhar num limite comum. Onde nao ha preco nenhum, o resultado
e `None`, e ausencia NAO exclui ninguem: nao saber quando o papel estreou nao
e evidencia de que ele nao existia.
"""
from __future__ import annotations

from datetime import date


def primeira_negociacao(barras) -> date | None:
    """Menor data com negociacao observada, ou None quando nao ha serie."""
    if not barras:
        return None
    datas = [d for d in barras if d is not None]
    return min(datas) if datas else None


def ja_negociava(primeira: date | None, as_of: date) -> bool:
    """O papel era comprável em `as_of`?

    `None` responde True: a duvida nao pode excluir. O portao existe para
    tirar da amostra quem comprovadamente ainda nao existia, e nao para
    exigir prova de existencia de quem nao tem serie de preco.
    """
    return primeira is None or primeira <= as_of
