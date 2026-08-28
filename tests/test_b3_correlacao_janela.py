"""A-135 (segunda frente): a matriz da Criação de Portfólio B3 tem teto de janela.

`monthly_returns_for` alimentava `correlation_matrix`, que é pairwise, com o
quadro inteiro de `_batch_yf_precos_mensais(period="10y")`. Um par saía medido
em 120 meses e o vizinho em 18, e ambos eram confrontados com o mesmo limiar de
0,65 e somados na mesma média — que é o que decide a SUBSTITUIÇÃO de um ativo.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.b3_correlation_diversification import (
    JANELA_CORR_MESES,
    correlation_coverage,
    monthly_returns_for,
)


def _quadro(n_meses: int, tickers: list[str]) -> pd.DataFrame:
    idx = pd.date_range("2010-01-31", periods=n_meses, freq="ME")
    rng = np.random.default_rng(7)
    return pd.DataFrame(
        {tk: 100 * np.exp(np.cumsum(rng.normal(0, 0.06, n_meses))) for tk in tickers},
        index=idx)


def test_janela_limita_a_sobreposicao_do_par_longo():
    rets = monthly_returns_for(_quadro(120, ["AAAA3", "BBBB3"]), ["AAAA3", "BBBB3"])
    assert len(rets) <= JANELA_CORR_MESES
    ok, total = correlation_coverage(rets)
    assert (ok, total) == (1, 1)
    assert rets[["AAAA3", "BBBB3"]].dropna().shape[0] <= JANELA_CORR_MESES


def test_janela_desligada_preserva_o_quadro_inteiro():
    """Quem precisa de história completa continua podendo pedi-la."""
    rets = monthly_returns_for(_quadro(120, ["AAAA3", "BBBB3"]),
                               ["AAAA3", "BBBB3"], janela_meses=None)
    assert len(rets) > JANELA_CORR_MESES


def test_ativo_curto_nao_e_descartado_pela_janela():
    """O teto encurta a janela; não expulsa candidato. Expulsar seria pior aqui:
    a tela precisa dos candidatos para poder substituir."""
    df = _quadro(120, ["AAAA3", "BBBB3"])
    df["NOVO3"] = np.nan
    df.iloc[-30:, df.columns.get_loc("NOVO3")] = _quadro(30, ["X"])["X"].values
    rets = monthly_returns_for(df, ["AAAA3", "BBBB3", "NOVO3"])
    assert "NOVO3" in rets.columns
    assert rets["NOVO3"].notna().sum() >= 18, "ainda cumpre o piso MIN_OBS"


def test_janela_e_a_mesma_de_correlation_analysis():
    """As duas telas medem correlação; medir a mesma coisa em janelas diferentes
    é o defeito que esta correção existe para fechar."""
    from core.correlation_analysis import JANELA_CORR_MESES as janela_investimentos
    assert JANELA_CORR_MESES == janela_investimentos
