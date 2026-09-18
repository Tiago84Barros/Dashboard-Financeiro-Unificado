# -*- coding: utf-8 -*-
"""Fonte única do que é um ticker de FII neste projeto (A-136).

A regra vivia copiada em oito lugares -- seis fragmentos SQL, um
``re.fullmatch`` na ingestão e um ``re.findall`` no contexto do LLM -- todos
escrevendo ``[A-Z]{4}11``. Copiada, ela não ficou igual a si mesma: a versão do
LLM usa fronteiras de palavra, as do SQL usam âncoras, e nenhuma delas podia ser
corrigida sem que as outras sete continuassem erradas.

E estavam erradas. O código de negociação da B3 tem quatro caracteres
alfanuméricos antes do sufixo, e o quarto pode ser dígito quando o emissor já
gastou as combinações de letras. Medido em 18/09/2026 contra o cadastro local
(1.068 tickers) e a fita oficial (779), seis fundos têm essa forma -- BGS111,
BPG111, CSH411, INC111, PLO411 e SPG211 -- e um deles negocia: SPG211 tem 51
pregões entre 18/03/2025 e 27/05/2026, preço de R$ 11,99 e giro médio de
R$ 284 mil. Ele não entrava na vitrine, e não por política: por ortografia.

O que o padrão continua barrando é deliberado e é a razão de ele não ser
simplesmente ``[A-Z0-9]{4}11``:

* ``ABCP12``, ``ALZR12`` e outros 133 -- recibo de subscrição, não cota;
* ``ALMI11B``, ``BBFI11B`` e outros 60 -- balcão organizado, outro mercado;
* ``0AFQ11``, ``00DX11`` e outros 459 -- códigos de cadastro que começam com
  dígito e nunca foram código de negociação.

Exigir três letras à esquerda separa os dois grupos sem lista de exceção.
"""
from __future__ import annotations

import re

#: Núcleo sem âncoras -- três letras, um alfanumérico, o sufixo de cota.
NUCLEO_TICKER_FII = r"[A-Z]{3}[A-Z0-9]11"

#: Padrão ancorado. Serve tanto ao ``re`` do Python quanto ao ``~`` do Postgres,
#: que é POSIX e entende esta sintaxe sem tradução.
PADRAO_TICKER_FII = rf"^{NUCLEO_TICKER_FII}$"

_RX = re.compile(PADRAO_TICKER_FII)
_RX_CITADO = re.compile(rf"\b{NUCLEO_TICKER_FII}\b")


def e_ticker_fii(valor: object) -> bool:
    """`True` se `valor` é um código de negociação de cota de FII."""
    return bool(_RX.match(str(valor or "").strip().upper()))


def tickers_citados(texto: object) -> list[str]:
    """Tickers de FII mencionados em texto livre, na ordem, sem repetir."""
    achados = _RX_CITADO.findall(str(texto or "").upper())
    return list(dict.fromkeys(achados))


def sql_ticker_fii(coluna: str) -> str:
    """Predicado SQL para `coluna`, pronto para interpolar num WHERE.

    A coluna é nome de coluna escrito no próprio código -- nunca entrada de
    usuário --, e o padrão é constante; não há concatenação de dado aqui.
    """
    return f"{coluna} ~ '{PADRAO_TICKER_FII}'"
