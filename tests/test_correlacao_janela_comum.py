"""A-135: correlação sobre janela comum, e não sobre a história inteira de cada par.

O defeito era declarado e executado em desacordo. A legenda da seção sempre
disse "janela solicitada: 5y", mas os caminhos que leem do banco
(`asset_quotes` e `portfolio_position_snapshots`) passavam a série inteira para
`DataFrame.corr(min_periods=...)`, que é **pairwise**: cada par usava toda a
sobreposição que tivesse. Medido em produção, a mesma matriz misturava pares
com 32 meses e pares com 556 meses de história.

Duas correlações medidas em janelas diferentes não são comparáveis, e a matriz
convida exatamente a essa comparação — o usuário lê a linha do ativo e escolhe
o que diversifica. Uma correlação de 46 anos descreve um regime que não existe
mais; ao lado de uma de 2,7 anos, ela parece só "mais confiável".
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.correlation_analysis import (
    JANELA_CORR_MESES,
    MIN_CORR_MONTHS,
    calcular_correlacao_mensal,
)


def _precos(n_meses: int, semente: int, inicio="1980-01-31") -> pd.Series:
    idx = pd.date_range(inicio, periods=n_meses, freq="ME")
    rng = np.random.default_rng(semente)
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.05, n_meses))), index=idx)


def test_janela_limita_as_observacoes_usadas():
    """Série de 556 meses com janela de 60: no máximo 60 retornos entram."""
    a = _precos(556, 1)
    precos = pd.DataFrame({"A": a, "B": _precos(556, 2)})
    d = calcular_correlacao_mensal(precos)
    assert d["janela_meses"] == JANELA_CORR_MESES
    assert len(d["returns"]) <= JANELA_CORR_MESES
    assert int(d["overlap"].loc["A", "B"]) <= JANELA_CORR_MESES


def test_par_longo_e_par_curto_medem_a_mesma_janela():
    """O ponto do defeito: A/B com 46 anos e C com 3 anos entravam na MESMA
    matriz com sobreposições de ordem de grandeza diferente."""
    longa = pd.date_range("1980-01-31", periods=556, freq="ME")
    curta = longa[-36:]
    precos = pd.DataFrame({
        "A": _precos(556, 1), "B": _precos(556, 2),
        "C": pd.Series(_precos(36, 3).values, index=curta),
    })
    ov = calcular_correlacao_mensal(precos)["overlap"]
    assert int(ov.loc["A", "B"]) <= JANELA_CORR_MESES
    # Nenhum par pode ter mais sobreposicao que a janela: e isso que torna as
    # celulas comparaveis entre si.
    assert ov.values.max() <= JANELA_CORR_MESES


def test_janela_desligada_preserva_o_comportamento_antigo():
    """`janela_meses=None` continua usando a história inteira -- o backtest e a
    análise de regime precisam disso, a tela de carteira não."""
    precos = pd.DataFrame({"A": _precos(200, 1), "B": _precos(200, 2)})
    d = calcular_correlacao_mensal(precos, janela_meses=None)
    assert d["janela_meses"] is None
    assert int(d["overlap"].loc["A", "B"]) > JANELA_CORR_MESES


def test_par_sem_janela_comum_suficiente_sai_da_matriz():
    """Um ativo com 12 meses dentro da janela não empresta correlação de outro
    período: ele simplesmente não é exibido."""
    longa = pd.date_range("2010-01-31", periods=180, freq="ME")
    precos = pd.DataFrame({
        "A": pd.Series(_precos(180, 1).values, index=longa),
        "B": pd.Series(_precos(180, 2).values, index=longa),
        "NOVO": pd.Series(_precos(12, 9).values, index=longa[-12:]),
    })
    corr = calcular_correlacao_mensal(precos)["corr"]
    assert "NOVO" not in corr.columns, "12 meses < mínimo de 24 exigido"
    assert {"A", "B"} <= set(corr.columns)


def test_periodo_medido_e_declarado():
    """A legenda precisa poder dizer o que foi MEDIDO, não o que foi pedido."""
    longa = pd.date_range("2010-01-31", periods=180, freq="ME")
    precos = pd.DataFrame({"A": pd.Series(_precos(180, 1).values, index=longa),
                           "B": pd.Series(_precos(180, 2).values, index=longa)})
    d = calcular_correlacao_mensal(precos)
    assert d["periodo_medido"], "sem periodo medido a legenda volta a prometer"
    ini, fim = d["periodo_medido"]
    assert ini < fim
    assert (fim.year - ini.year) * 12 + (fim.month - ini.month) < JANELA_CORR_MESES


def test_minimo_continua_valendo_dentro_da_janela():
    precos = pd.DataFrame({"A": _precos(300, 1), "B": _precos(300, 2)})
    d = calcular_correlacao_mensal(precos, min_obs=MIN_CORR_MONTHS)
    assert d["min_obs"] == MIN_CORR_MONTHS
    assert not d["corr"].empty
