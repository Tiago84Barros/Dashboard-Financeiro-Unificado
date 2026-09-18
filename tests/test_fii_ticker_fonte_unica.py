# -*- coding: utf-8 -*-
"""A-136: o que e um ticker de FII passa a ter um dono so.

A regra estava escrita oito vezes -- seis fragmentos SQL, um `re.fullmatch` na
ingestao e um `re.findall` no contexto do LLM -- e todas as oito diziam
`[A-Z]{4}11`. Copia nao se corrige junto: era preciso acertar oito arquivos
para acertar uma regra, e o padrao `[[guarda-duplicada-diverge]]` ja custou
caro neste projeto (tres copias, duas divergencias).

E a regra estava errada. O codigo de negociacao da B3 tem quatro caracteres
alfanumericos antes do sufixo, e o quarto pode ser digito. Medido em 18/09/2026
contra o cadastro local (1.068 tickers) e a fita oficial (779): seis fundos tem
essa forma e um deles negocia -- SPG211, 51 pregoes entre 18/03/2025 e
27/05/2026, preco de R$ 11,99, giro medio de R$ 284 mil/dia. Ficava fora da
vitrine por ortografia, nao por politica.

O teste de unicidade e por AST/texto, nao por comportamento: duas copias que
concordam hoje passam em qualquer teste de comportamento e divergem amanha.
"""
import ast
import re
from pathlib import Path

import pytest

from core.fii_ticker import (
    PADRAO_TICKER_FII,
    e_ticker_fii,
    sql_ticker_fii,
    tickers_citados,
)

RAIZ = Path(__file__).resolve().parents[1]


# --- a regra -----------------------------------------------------------------

@pytest.mark.parametrize("ticker", ["HGLG11", "SPG211", "BGS111", "PLO411"])
def test_cota_de_fii_e_ticker(ticker):
    assert e_ticker_fii(ticker)


@pytest.mark.parametrize("ticker,porque", [
    ("ABCP12", "recibo de subscricao, nao cota"),
    ("ALMI11B", "balcao organizado, outro mercado"),
    ("SPG215", "sufixo que nao e o da cota"),
    ("0AFQ11", "codigo de cadastro, nunca foi de negociacao"),
    ("00DX11", "idem, dois digitos"),
    ("PETR4", "nao e FII"),
    ("", "vazio"),
    (None, "ausente"),
])
def test_o_que_continua_de_fora(ticker, porque):
    assert not e_ticker_fii(ticker), porque


def test_tres_letras_e_o_que_separa_cadastro_de_negociacao():
    """A tentacao era `[A-Z0-9]{4}11`, e ela admitiria os 461 codigos de
    cadastro que comecam com digito. Exigir tres letras a esquerda separa os
    dois grupos sem lista de excecao."""
    assert e_ticker_fii("SPG211")
    assert not e_ticker_fii("0SPG11")
    assert not e_ticker_fii("00SP11")


def test_texto_livre_cita_ticker_com_digito():
    citados = tickers_citados("comparar o SPG211 com HGLG11 e de novo SPG211")
    assert citados == ["SPG211", "HGLG11"]


def test_predicado_sql_carrega_o_mesmo_padrao():
    assert sql_ticker_fii("f.ticker") == f"f.ticker ~ '{PADRAO_TICKER_FII}'"


# --- a unicidade -------------------------------------------------------------

_COPIA = re.compile(r"\[A-Z[0-9]*\]\{\d\}(\[[A-Z0-9]+\])?1?1")
_FONTE = RAIZ / "core" / "fii_ticker.py"


def _arquivos_de_producao():
    for pasta in ("core", "data_pipeline", "views", "scripts"):
        for arq in (RAIZ / pasta).rglob("*.py"):
            if arq != _FONTE:
                yield arq


def test_nenhum_outro_arquivo_reescreve_o_padrao():
    """Unicidade se testa pelo texto, nao pelo comportamento: duas copias que
    concordam hoje passam em qualquer teste de comportamento."""
    reincidentes = {}
    for arq in _arquivos_de_producao():
        try:
            texto = arq.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        achados = [linha.strip() for linha in texto.splitlines()
                   if _COPIA.search(linha)]
        if achados:
            reincidentes[str(arq.relative_to(RAIZ))] = achados[:3]
    assert not reincidentes, (
        "padrao de ticker reescrito fora de core/fii_ticker.py: "
        f"{reincidentes}")


def test_quem_le_a_vitrine_consome_a_fonte_unica():
    """Guarda de fiacao: importar o modulo e nao usa-lo deixaria o SQL antigo
    de pe sem nenhum teste de comportamento reclamar."""
    for rel in ("core/market_read.py", "data_pipeline/market/fii_ingest.py"):
        arvore = ast.parse((RAIZ / rel).read_text(encoding="utf-8"))
        chamadas = [
            no for no in ast.walk(arvore)
            if isinstance(no, ast.Call) and isinstance(no.func, ast.Name)
            and no.func.id.endswith("sql_ticker_fii")
        ]
        assert chamadas, f"{rel} deixou de derivar o filtro da fonte unica"
