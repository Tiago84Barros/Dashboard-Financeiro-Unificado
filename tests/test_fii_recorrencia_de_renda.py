"""Achado R1/R2/R3: ``income_recurrence`` contava como "pagamento zero" os
meses anteriores à existência do fundo.

Medido em 13/09/2026 sobre as 396 linhas reais do armazém local: o divisor era
36 fixo, então um fundo com 16 meses de vida entrava com 20 zeros artificiais.
Isso ataca os dois fatores da fórmula ao mesmo tempo — ``positive_share`` cai e
o coeficiente de variação sobe. RBVA11 pagou em todos os 16 meses em que
existiu e era pontuado como 21% recorrente; HFOF11, 21%; RBFM11, 7,7%. 128 dos
396 fundos tinham a recorrência subestimada pelo artefato e 89 caíam abaixo de
0,45, faixa em que o piso de 8% de renda recorrente (``dy_12m *
income_recurrence``) reprova qualquer DY típico de FII.

Ausência de histórico não é pagamento zero — e também não é prova de
recorrência. Por isso a correção tem duas metades: contar só a partir do
primeiro provento observado E exigir um mínimo de meses para a métrica
existir. Sem a segunda metade, RBFM11 com 8 meses viraria 0,994.
"""
from __future__ import annotations

import json
from datetime import date

from core.fii_methodology import (
    INCOME_RECURRENCE_FORMULA,
    INCOME_RECURRENCE_MIN_MONTHS,
    INCOME_RECURRENCE_WINDOW_MONTHS,
    income_recurrence,
)


def _meses(fim: date, quantidade: int) -> list[date]:
    """Os ``quantidade`` meses que terminam em ``fim``, do mais antigo ao mais
    recente."""
    saida: list[date] = []
    for offset in reversed(range(quantidade)):
        ano, mes = fim.year, fim.month - offset
        while mes <= 0:
            ano -= 1
            mes += 12
        saida.append(date(ano, mes, 1))
    return saida


def _renda(fim: date, meses: int, valor: float = 100.0) -> dict[date, float]:
    return {chave: valor for chave in _meses(fim, meses)}


FIM = date(2026, 9, 1)


def test_meses_antes_do_primeiro_provento_nao_entram_no_divisor():
    """O caso RBVA11: pagou em todos os 16 meses em que existiu. A janela de 36
    fixos o pontuava em 0,210 porque 20 meses de inexistência entravam como
    pagamento zero."""
    resultado = income_recurrence(_renda(FIM, 16), FIM)
    assert resultado is not None
    assert resultado > .99, f"vida integralmente paga deveria dar ~1,0, deu {resultado}"


def test_historico_curto_demais_devolve_ausencia_e_nao_nota_alta():
    """RBFM11 tem 8 meses de vida. Contar só a vida o levaria a 0,994 — trocar
    um viés por outro. Abaixo do mínimo a métrica não existe, e o caminho de
    métrica crítica ausente já sabe lidar com isso."""
    assert income_recurrence(_renda(FIM, 8), FIM) is None
    assert income_recurrence({}, FIM) is None


def test_minimo_e_o_ciclo_anual_completo():
    """O mínimo é 12: é o ciclo anual que ``dy_12m`` — o outro fator de
    ``dy_recorrente`` — também cobre, e é o piso que o walk-forward PIT já
    exigia. Custa 20 dos 381 fundos com provento (5,2%)."""
    assert INCOME_RECURRENCE_MIN_MONTHS == 12
    assert income_recurrence(_renda(FIM, INCOME_RECURRENCE_MIN_MONTHS), FIM) is not None
    assert income_recurrence(_renda(FIM, INCOME_RECURRENCE_MIN_MONTHS - 1), FIM) is None


def test_fundo_que_parou_de_pagar_e_punido_e_nao_absolvido():
    """A causa oposta: silêncio DEPOIS do primeiro provento é evidência de
    quebra de recorrência, não ausência de histórico. O recorte começa no
    primeiro provento de toda a série — não no primeiro provento da janela —,
    senão um fundo que passou 24 meses mudo e voltou a pagar há 12 sairia com
    nota perfeita."""
    serie = _renda(date(2024, 9, 1), 24)          # pagou até set/2024
    serie.update({chave: 0.0 for chave in _meses(FIM, 12)})   # calado desde então
    parado = income_recurrence(serie, FIM)
    assert parado is not None, "o fundo existe na janela inteira; a métrica é medível"
    assert parado < .70, f"12 meses mudos não podem passar por recorrência ({parado})"
    assert parado < income_recurrence(_renda(FIM, 16), FIM)


def test_janela_nao_passa_de_trinta_e_seis_meses():
    """Vida longa não amplia a janela: a medida continua sendo dos 36 meses
    mais recentes, como a de crescimento."""
    assert INCOME_RECURRENCE_WINDOW_MONTHS == 36
    longo = _renda(FIM, 120)
    for chave in _meses(date(2020, 1, 1), 24):    # buraco antigo, fora da janela
        longo[chave] = 0.0
    assert income_recurrence(longo, FIM) == income_recurrence(_renda(FIM, 36), FIM)


def test_pagamento_irregular_pontua_abaixo_do_regular():
    """A recorrência mede regularidade: mesma renda total, concentrada em
    poucos meses, vale menos."""
    regular = income_recurrence(_renda(FIM, 24, 100.0), FIM)
    irregular = dict.fromkeys(_meses(FIM, 24), 0.0)
    for indice, chave in enumerate(sorted(irregular)):
        if indice % 3 == 0:
            irregular[chave] = 300.0
    assert income_recurrence(irregular, FIM) < regular


def test_renda_nula_em_toda_a_janela_nao_produz_numero():
    serie = dict.fromkeys(_meses(FIM, 24), 0.0)
    assert income_recurrence(serie, FIM) is None


def test_ingestao_publica_a_recorrencia_da_vida_observada():
    """O caminho de produção (``derived/dividends``) é o que grava a métrica em
    ``market.fii_metric_observations``. Sem ele, a correção fica na função
    pura e o banco continua com 0,210."""
    from data_pipeline.market import fii_v2

    linhas = fii_v2.income_metrics_from_monthly(
        {"RBVA11": _renda(FIM, 16), "RBFM11": _renda(FIM, 8)}, as_of=date(2026, 9, 20))
    por_chave = {(linha["ticker"], linha["metric_name"]): linha for linha in linhas}
    assert por_chave[("RBVA11", "income_recurrence")]["value_numeric"] > .99
    assert ("RBFM11", "income_recurrence") not in por_chave, (
        "8 meses de vida não sustentam a métrica; a observação não deve existir")
    metadados = json.loads(por_chave[("RBVA11", "income_recurrence")]["metadata_json"])
    assert metadados["formula"] == INCOME_RECURRENCE_FORMULA
    # A fórmula publicada tem de dizer que a janela é recortada: o consumidor
    # que ler `positive_share/(1+cv),36m` numa linha nova pensaria que o
    # divisor ainda é 36 fixo.
    assert "36m" != INCOME_RECURRENCE_FORMULA.split(",")[-1]


def test_meses_povoados_contam_a_janela_e_nao_a_vida_inteira():
    """``populated_months`` é a evidência que acompanha a observação. Ele
    sempre significou "meses com pagamento dentro da janela de 36"; contar a
    vida inteira mudaria o sentido de um campo já publicado sem avisar
    ninguém — um fundo de dez anos passaria de 36 para 120."""
    from data_pipeline.market import fii_v2

    linhas = fii_v2.income_metrics_from_monthly(
        {"DECADA11": _renda(FIM, 120)}, as_of=date(2026, 9, 20))
    metadados = json.loads(next(
        linha for linha in linhas
        if linha["metric_name"] == "income_recurrence")["metadata_json"])
    assert metadados["populated_months"] == INCOME_RECURRENCE_WINDOW_MONTHS
