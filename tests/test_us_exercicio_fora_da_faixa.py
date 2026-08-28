# -*- coding: utf-8 -*-
"""A-141: um exercicio fiscal absurdo nao pode derrubar a empresa inteira.

O parser replicava a faixa 1990..2100 do CHECK das tabelas de demonstracoes,
mas nunca a aplicava a linha emitida. Um fato XBRL com `end` em 1980 -- SRPT
tem um, sem nenhum valor financeiro -- passava pelo parser e era recusado pelo
banco; como o lote e gravado numa transacao so, NG, SRPT e TENX ficaram sem
nenhum fundamento ingerido.
"""
from __future__ import annotations

import logging

from data_pipeline.us import edgar_facts as ef


def _companyfacts(periodos: list[tuple[str, str, str, float]]) -> dict:
    """companyfacts minimo: (start, end, filed, valor) por exercicio anual."""
    return {"facts": {"us-gaap": {"Assets": {"units": {"USD": [
        {"start": ini, "end": fim, "filed": arq, "val": val, "form": "10-K"}
        for ini, fim, arq, val in periodos
    ]}}}}}


def test_periodo_de_1980_nao_entra_e_o_resto_da_empresa_entra():
    cf = _companyfacts([
        ("1979-07-22", "1980-07-21", "2013-03-15", 0.0),
        ("2023-01-01", "2023-12-31", "2024-02-20", 5_000_000.0),
    ])
    linhas = ef.build_balance_rows(cf, "SRPT")
    anos = {ln["fiscal_year"] for ln in linhas}
    assert 1980 not in anos, "ano fora do CHECK do banco nao pode ser emitido"
    assert 2023 in anos, "descartar o ruido nao pode descartar a empresa"


def test_descarte_e_registrado_em_warning(caplog):
    """Filtro silencioso viraria 'a empresa nao tem esse ano' na leitura."""
    cf = _companyfacts([("1979-07-22", "1980-07-21", "2013-03-15", 0.0)])
    with caplog.at_level(logging.WARNING, logger=ef.__name__):
        ef.build_balance_rows(cf, "SRPT")
    assert "SRPT" in caplog.text and "1980" in caplog.text, caplog.text


def test_toda_linha_emitida_cabe_no_check_das_tabelas():
    """A faixa do parser e a do banco sao a mesma afirmacao; fixa-las juntas."""
    cf = _companyfacts([
        ("1979-07-22", "1980-07-21", "2013-03-15", 1.0),
        ("2199-01-01", "2199-12-31", "2200-02-01", 1.0),
        ("2020-01-01", "2020-12-31", "2021-02-20", 1.0),
    ])
    for construtor in (ef.build_balance_rows, ef.build_income_rows,
                       ef.build_cashflow_rows):
        for linha in construtor(cf, "X"):
            assert ef._FY_MIN <= linha["fiscal_year"] <= ef._FY_MAX
