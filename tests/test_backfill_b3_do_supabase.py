"""O backfill da B3 preenche buraco e nunca traz fóssil de revisão."""
from __future__ import annotations

import datetime as dt

import pandas as pd

from scripts.backfill_b3_precos_proventos_do_supabase import (
    precos_faltantes,
    proventos_faltantes,
)

D = dt.date


def test_preco_so_entra_onde_o_armazem_nao_tem_o_pregao():
    local = pd.DataFrame({"ticker": ["AAA3"], "date": [D(2026, 1, 2)]})
    nuvem = pd.DataFrame({
        "ticker": ["AAA3", "AAA3", "BBB3"],
        "date": [D(2026, 1, 2), D(2026, 1, 3), D(2026, 1, 2)],
        "close": [10.0, 11.0, 5.0],
    })
    falta = precos_faltantes(local, nuvem)
    assert sorted(zip(falta.ticker, falta.date)) == [
        ("AAA3", D(2026, 1, 3)), ("BBB3", D(2026, 1, 2))]


def test_provento_revisado_no_armazem_nao_volta_como_fossil():
    # O armazém tem o JCP de data-ex 26/12 já com o valor corrigido; a nuvem
    # guarda a versão antiga (outro valor) e um provento antigo sem par.
    local = pd.DataFrame({"ticker": ["BRSR6"], "ex_date": [D(2025, 12, 26)],
                          "payment_date": [D(2026, 1, 20)], "type": ["JCP"]})
    nuvem = pd.DataFrame({
        "ticker": ["BRSR6", "BRSR6"],
        "ex_date": [D(2025, 12, 26), D(1999, 4, 30)],
        "payment_date": [D(2026, 1, 20), D(1999, 5, 15)],
        "type": ["JCP", "DIVIDENDO"],
        "amount": [0.4625, 0.10],
    })
    falta = proventos_faltantes(local, nuvem)
    assert list(falta.ex_date) == [D(1999, 4, 30)]


def test_mesma_data_ex_com_outro_tipo_e_lacuna():
    local = pd.DataFrame({"ticker": ["X3"], "ex_date": [D(2024, 5, 1)],
                          "payment_date": [None], "type": ["JCP"]})
    nuvem = pd.DataFrame({"ticker": ["X3"], "ex_date": [D(2024, 5, 1)],
                          "payment_date": [None], "type": ["DIVIDENDO"],
                          "amount": [1.0]})
    assert len(proventos_faltantes(local, nuvem)) == 1


def test_nuvem_vazia_nao_quebra():
    vazio = pd.DataFrame(columns=["ticker", "date"])
    assert precos_faltantes(vazio, vazio).empty
    vaziod = pd.DataFrame(columns=["ticker", "ex_date", "payment_date", "type"])
    assert proventos_faltantes(vaziod, vaziod).empty


def test_provento_sem_data_ex_casa_pelo_pagamento():
    # Nulo não é par de nulo: o armazém tem um provento sem data-ex pago em
    # 10/03; a nuvem tem esse mesmo e outro, também sem data-ex, pago em
    # 10/06. Só o de junho é lacuna.
    local = pd.DataFrame({"ticker": ["Y3"], "ex_date": [None],
                          "payment_date": [D(2025, 3, 10)], "type": ["DIVIDENDO"]})
    nuvem = pd.DataFrame({
        "ticker": ["Y3", "Y3"],
        "ex_date": [None, None],
        "payment_date": [D(2025, 3, 10), D(2025, 6, 10)],
        "type": ["DIVIDENDO", "DIVIDENDO"],
        "amount": [0.5, 0.7],
    })
    falta = proventos_faltantes(local, nuvem)
    assert list(falta.payment_date) == [D(2025, 6, 10)]
    assert "_data_par" not in falta.columns


def test_provento_sem_data_ex_nao_some_quando_o_armazem_tem_outro_sem_data_ex():
    local = pd.DataFrame({"ticker": ["Y3"], "ex_date": [None],
                          "payment_date": [None], "type": ["JCP"]})
    nuvem = pd.DataFrame({"ticker": ["Y3"], "ex_date": [None],
                          "payment_date": [D(2025, 6, 10)], "type": ["JCP"],
                          "amount": [0.7]})
    assert len(proventos_faltantes(local, nuvem)) == 1
