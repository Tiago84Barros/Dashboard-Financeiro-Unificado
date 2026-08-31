# -*- coding: utf-8 -*-
"""Registrante que para de arquivar não é empresa que morreu.

`refuta_saida` procura relatório anual posterior sob o MESMO CIK — e na
reorganização societária ele nunca aparece: o registrante antigo para de
arquivar para sempre e um CIK novo assume. Medido no armazém em 31/08/2026,
das 60 saídas nomeadas que tinham cotação, 60 seguiam negociando (BlackRock,
Bunge, Ferguson, Noble, Apollo) e uma única estava marcada como refutada. Sem
esta segunda porta, o backtest gravaria BlackRock como perda de 2025.

O que NÃO pode acontecer: ticker reciclado por outra empresa anos depois
derrubar a saída verdadeira do dono anterior. Por isso a prova exige
continuidade em volta da data, e não só negociação posterior.
"""
from __future__ import annotations

from datetime import date

from core.us_saidas_sec import refuta_por_continuidade


def _mensal(inicio: date, fim: date) -> list[date]:
    saida, ano, mes = [], inicio.year, inicio.month
    while date(ano, mes, 28) <= fim:
        saida.append(date(ano, mes, 28))
        ano, mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
    return saida


def test_papel_que_seguiu_negociando_derruba_a_saida():
    """O caso BlackRock: CIK 1364742 morre, o papel BLK não."""
    v = refuta_por_continuidade(date(2025, 12, 31),
                                _mensal(date(2020, 1, 1), date(2026, 8, 28)))
    assert v is not None
    assert v["motivo"] == "ticker_negociado_apos_saida"


def test_ticker_reciclado_nao_derruba_a_saida_do_dono_anterior():
    """Só cotação recente, com buraco em volta da saída: não é o mesmo papel."""
    assert refuta_por_continuidade(
        date(2013, 12, 31), _mensal(date(2024, 1, 1), date(2026, 8, 28))) is None


def test_papel_que_parou_na_saida_continua_sendo_saida():
    assert refuta_por_continuidade(
        date(2024, 12, 31), _mensal(date(2020, 1, 1), date(2024, 12, 28))) is None


def test_folga_de_meses_nao_conta_como_vida():
    """Última barra mensal logo depois da data não prova que o papel viveu."""
    assert refuta_por_continuidade(
        date(2024, 12, 31),
        _mensal(date(2023, 1, 1), date(2024, 12, 28)) + [date(2025, 1, 28)]
    ) is None


def test_sem_cotacao_a_saida_nao_e_confirmada_nem_refutada():
    """Assimetria: o armazém só tem preço de quem sobreviveu.

    Ausência de cotação aqui é ausência de dado — devolver "não refutada" é o
    certo, mas ninguém pode ler isso como confirmação da morte.
    """
    assert refuta_por_continuidade(date(2024, 12, 31), []) is None
    assert refuta_por_continuidade(None, [date(2026, 1, 28)]) is None
