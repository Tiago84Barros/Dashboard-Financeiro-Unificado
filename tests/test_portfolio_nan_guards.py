"""Guards de NaN no pipeline de pesos/backtest do portfólio B3.

Regressão: um score NaN num ano histórico virava peso NaN -> val_est NaN ->
alpha_selic NaN -> alpha_selic_medio NaN, quebrando o INSERT do portfólio modelo.
"""
import numpy as np

from views.empresas_b3 import _weights_from_scores
from views.portfolio_b3 import _margem_pct


def test_weights_from_scores_ignora_nan():
    w = _weights_from_scores(["A", "B", "C"], {"A": 80.0, "B": float("nan"), "C": 60.0})
    assert all(np.isfinite(v) for v in w.values())   # nenhum peso NaN
    assert abs(sum(w.values()) - 1.0) < 1e-9         # normalizado
    assert w["A"] >= w["C"] >= w["B"]                # NaN tratado como pior (0)


def test_weights_from_scores_score_ausente_vira_zero():
    w = _weights_from_scores(["A", "B"], {"A": 70.0})  # B sem score
    assert all(np.isfinite(v) for v in w.values())
    assert abs(sum(w.values()) - 1.0) < 1e-9


def test_margem_pct_guarda_nao_finitos():
    assert _margem_pct(float("nan"), 100.0) == 0.0
    assert _margem_pct(100.0, float("nan")) == 0.0
    assert _margem_pct(110.0, 0.0) == 0.0            # ref<=0
    assert _margem_pct(float("inf"), 100.0) == 0.0
    assert abs(_margem_pct(110.0, 100.0) - 10.0) < 1e-9   # caso normal intacto
