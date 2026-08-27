# -*- coding: utf-8 -*-
"""A-155: DT_RECEB da CVM e a unica data que diz quando o mercado soube.

`first_seen_proxy` mede o dia em que o ETL rodou; se ele rodou hoje, o proxy
afirma que o balanco de 2019 ficou disponivel hoje. Isso nao sustenta backtest.
"""
from __future__ import annotations

import io
import zipfile
from datetime import date

import pytest

from data_pipeline.market.cvm_publicacao import Entrega, parse_cabecalho

_COLS = "CNPJ_CIA;DT_REFER;VERSAO;DENOM_CIA;CD_CVM;CATEG_DOC;ID_DOC;DT_RECEB;LINK_DOC"


def _pacote(linhas, year=2024, categoria="DFP"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{categoria.lower()}_cia_aberta_{year}.csv",
                   "\n".join([_COLS, *linhas]).encode("latin-1"))
    return buf.getvalue()


def _linha(cd="001023", refer="2024-12-31", receb="2025-02-19", versao="1"):
    return f"00.000.000/0001-91;{refer};{versao};BCO X;{cd};DFP;144874;{receb};http://x"


def _uma(linhas, **kw) -> Entrega:
    saida = parse_cabecalho(_pacote(linhas, **kw), kw.get("year", 2024),
                            kw.get("categoria", "DFP"))
    assert len(saida) == 1
    return saida[0]


def test_o_exercicio_vem_de_dt_refer_e_nao_do_ano_do_arquivo():
    """O ZIP de 2024 traz competencia 2024 entregue em 2025."""
    e = _uma([_linha(refer="2024-12-31", receb="2025-02-19")])
    assert e.exercicio == 2024
    assert e.disponivel_em == date(2025, 2, 19)


def test_reapresentacao_manda_na_disponibilidade():
    """O numero guardado hoje e o da versao mais recente; foi entao que ele
    ficou conhecivel, nao na primeira entrega."""
    e = _uma([_linha(receb="2025-03-24", versao="1"),
              _linha(receb="2025-08-11", versao="2")])
    assert e.disponivel_em == date(2025, 8, 11)
    assert e.primeira_entrega_em == date(2025, 3, 24)
    assert e.reapresentado and e.versoes == 2


def test_sem_reapresentacao_as_duas_datas_coincidem():
    e = _uma([_linha(receb="2025-02-19")])
    assert not e.reapresentado
    assert e.disponivel_em == e.primeira_entrega_em


def test_entrega_anterior_a_competencia_e_descartada():
    """Conhecer o exercicio antes de ele terminar e impossivel."""
    assert parse_cabecalho(
        _pacote([_linha(refer="2024-12-31", receb="2024-03-01")]), 2024) == []


def test_linha_sem_data_de_recebimento_nao_vira_data_inventada():
    assert parse_cabecalho(_pacote([_linha(receb="")]), 2024) == []


def test_codigo_cvm_zero_preenchido_vira_inteiro():
    assert _uma([_linha(cd="001023")]).codigo_cvm == 1023


def test_codigo_cvm_invalido_e_descartado():
    assert parse_cabecalho(_pacote([_linha(cd="")]), 2024) == []
    assert parse_cabecalho(_pacote([_linha(cd="N/A")]), 2024) == []


def test_companhias_distintas_nao_se_misturam():
    saida = parse_cabecalho(_pacote([_linha(cd="001023", receb="2025-02-19"),
                                     _linha(cd="002000", receb="2025-04-30")]), 2024)
    assert {(e.codigo_cvm, e.disponivel_em) for e in saida} == {
        (1023, date(2025, 2, 19)), (2000, date(2025, 4, 30))}


def test_exercicios_distintos_da_mesma_companhia_nao_se_misturam():
    """O pacote de um ano pode conter reapresentacao de exercicio anterior."""
    saida = parse_cabecalho(_pacote([_linha(refer="2024-12-31", receb="2025-02-19"),
                                     _linha(refer="2023-12-31", receb="2025-06-10")]), 2024)
    assert {(e.exercicio, e.disponivel_em) for e in saida} == {
        (2024, date(2025, 2, 19)), (2023, date(2025, 6, 10))}


def test_pacote_sem_o_cabecalho_esperado_nao_devolve_vazio_silencioso():
    """Formato mudou e um erro explicito; lista vazia seria 'nao ha entregas'."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("outro.csv", b"a;b")
    with pytest.raises(KeyError):
        parse_cabecalho(buf.getvalue(), 2024)
