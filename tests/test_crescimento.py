"""CAGR por regressão e retorno anual (core/crescimento), fonte das telas B3 e EUA.

Antes, anos com valor ≤ 0 eram filtrados e a regressão corria sobre o que
sobrava; o ano em curso entrava no retorno anual como se estivesse fechado.
"""
from __future__ import annotations

import pandas as pd
import pytest

from core.crescimento import (
    Crescimento,
    anos_de_datas,
    cagr_regressao,
    retornos_anuais,
    sem_ano_em_curso,
    ultimo_preco_nao_positivo,
)


def test_serie_positiva_reproduz_a_regressao_log():
    c = cagr_regressao([2020, 2021, 2022], [100.0, 110.0, 121.0])
    assert c.taxa == pytest.approx(0.10)
    assert (c.inicio, c.fim, c.truncado, c.motivo) == (2020, 2022, False, None)
    assert c.legenda() == "Regressão log · 2020–2022"


def test_prejuizo_no_meio_corta_a_janela_em_vez_de_sumir():
    # 100 → −50 → −30 → 200 → 220: antes regredia 100, 200, 220 como contínuo.
    c = cagr_regressao([2019, 2020, 2021, 2022, 2023], [100, -50, -30, 200, 220])
    assert c.taxa == pytest.approx(0.10)
    assert (c.inicio, c.fim, c.truncado) == (2022, 2023, True)
    assert "desde o último ano ≤ 0" in c.legenda()


def test_ultimo_ano_com_prejuizo_nao_tem_cagr():
    c = cagr_regressao([2021, 2022, 2023], [100, 120, -10])
    assert c.taxa is None and c.motivo == "ultimo_nao_positivo"
    assert "não se aplica" in c.legenda()


def test_zero_tambem_interrompe():
    c = cagr_regressao([2021, 2022, 2023], [100, 0, 50])
    assert c.taxa is None and c.motivo == "historico_curto"


def test_ausente_nao_e_zero():
    c = cagr_regressao([2021, 2022, 2023], [100, None, 121])
    assert c.taxa == pytest.approx(0.10)
    assert c.truncado is False


def test_sem_dado():
    assert cagr_regressao([], []).motivo == "sem_dado"
    assert cagr_regressao([2020], [float("nan")]).legenda() == "Sem histórico"


def test_anos_de_datas_preserva_a_inclinacao():
    datas = pd.to_datetime(["2020-12-31", "2021-12-31", "2022-12-31"])
    c = cagr_regressao(anos_de_datas(datas), [100.0, 110.0, 121.0])
    assert c.taxa == pytest.approx(0.10, abs=1e-3)
    assert (c.inicio, c.fim) == (2020, 2022)


def test_sem_ano_em_curso_numerico_e_data():
    anual = pd.DataFrame({"fiscal_year": [2024, 2025, 2026], "v": [1, 2, 0.4]})
    assert sem_ano_em_curso(anual, "fiscal_year", hoje="2026-10-29")["fiscal_year"].tolist() == [2024, 2025]
    datas = pd.DataFrame({"Data": pd.to_datetime(["2025-12-31", "2026-12-31"]), "v": [1, 2]})
    assert len(sem_ano_em_curso(datas, "Data", hoje="2026-10-29")) == 1
    assert sem_ano_em_curso(pd.DataFrame(), "Data").empty


def test_retorno_do_ano_aberto_e_rotulado():
    datas = ["2024-12-30", "2025-12-30", "2026-10-28"]
    out = retornos_anuais(datas, [10.0, 12.0, 15.0])
    assert out["Ano"].tolist() == [2025, 2026]
    assert out["Retorno"].tolist() == pytest.approx([20.0, 25.0])
    assert out["Rotulo"].tolist() == ["2025", "2026 (até 28/10)"]


def test_retorno_sem_dado():
    assert retornos_anuais([], []).columns.tolist() == ["Ano", "Retorno", "Rotulo"]


def test_preco_ajustado_negativo_corta_o_historico_anterior():
    # Forma de uma série ajustada do yfinance em que um provento maior que o
    # preço do dia deixou o fator negativo: o gráfico mostrava retornos abaixo de −100%.
    datas = ["2004-12-30", "2005-12-29", "2006-12-28", "2007-04-30",
             "2007-12-28", "2008-12-30", "2009-12-30"]
    precos = [4.00, 4.00, -0.30, -0.20, 30.00, 20.00, 8.00]
    out = retornos_anuais(datas, precos)
    assert out["Ano"].tolist() == [2008, 2009]
    assert out["Retorno"].tolist() == pytest.approx([20.00 / 30.00 * 100 - 100,
                                                      8.00 / 20.00 * 100 - 100])
    assert (out["Retorno"] > -100).all()
    assert out.attrs["corte"] == pd.Timestamp("2007-04-30")


def test_serie_toda_positiva_nao_tem_corte():
    datas = ["2024-12-30", "2025-12-30"]
    assert ultimo_preco_nao_positivo(datas, [10.0, 12.0]) is None
    assert retornos_anuais(datas, [10.0, 12.0]).attrs["corte"] is None


def test_ultimo_preco_nao_positivo_devolve_o_mais_recente():
    datas = ["2006-01-02", "2006-06-01", "2007-01-02", "2007-02-01"]
    assert ultimo_preco_nao_positivo(datas, [1.0, 0.0, -2.0, 3.0]) == pd.Timestamp("2007-01-02")
    # Preço ≤ 0 no último dia: não sobra trecho coerente.
    out = retornos_anuais(datas, [1.0, 2.0, 3.0, -1.0])
    assert out.empty and out.attrs["corte"] == pd.Timestamp("2007-02-01")


def test_legenda_sem_taxa_com_motivo_desconhecido():
    assert Crescimento(None, motivo="x").legenda() == "Sem histórico"
