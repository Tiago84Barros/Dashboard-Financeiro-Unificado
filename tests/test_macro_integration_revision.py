from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

from core.macro_data.portfolio_context import (
    PortfolioMacroSnapshot,
    historical_macro_weight_path,
)
from core.macro_data.portfolio_tilt import apply_macro_tilt


def test_history_starts_from_fundamental_and_not_from_todays_tilt(monkeypatch):
    """A trajetória parte do peso fundamental, não do peso já inclinado.

    As três telas passam a tabela que está na tela, e a tabela da tela já
    passou pelo tilt de hoje. Reaplicar por cima compõe dois tilts: com
    impacto máximo, 0,500 vira 0,575 na tela e virava 0,639 na trajetória --
    +27,8% sobre o fundamental, com ``max_relative_weight_tilt`` em 0,15.
    Nenhuma chamada violava o teto; a composição o contornava.

    E a linha rotulada "fundamental" no gráfico recebia 0,575.
    """
    cutoff = datetime(2025, 12, 31, tzinfo=timezone.utc)
    monkeypatch.setattr(
        'core.macro_data.portfolio_context.load_portfolio_macro_snapshot',
        lambda *a, **k: PortfolioMacroSnapshot(
            {'A': 100, 'B': -100}, (), cutoff, 2, 2, 1))
    # Exatamente o que a tela entrega: peso já inclinado, com a origem junto.
    frame = pd.DataFrame({'symbol': ['A', 'B'], 'sector': ['X', 'Y'],
                          'score': [70., 70.], 'weight': [.575, .425],
                          'weight_before_macro': [.5, .5]})

    result = historical_macro_weight_path(
        object(), asset_class='b3', holdings=frame, symbol_column='symbol',
        sector_column='sector', score_column='score', cutoffs=[cutoff])

    assert result.weight_fundamental.tolist() == [.5, .5], (
        "a linha 'fundamental' recebeu o peso já inclinado de hoje")
    assert result.weight_contextual.tolist() == pytest.approx([.575, .425]), (
        "um tilt sobre o fundamental, não dois empilhados")


def test_history_does_not_exceed_the_configured_tilt_cap(monkeypatch):
    """O teto de 15% vale para o resultado, não só para cada chamada."""
    from core.macro_data.portfolio_tilt import MacroTiltConfig

    cutoff = datetime(2025, 12, 31, tzinfo=timezone.utc)
    monkeypatch.setattr(
        'core.macro_data.portfolio_context.load_portfolio_macro_snapshot',
        lambda *a, **k: PortfolioMacroSnapshot(
            {'A': 100, 'B': -100}, (), cutoff, 2, 2, 1))
    frame = pd.DataFrame({'symbol': ['A', 'B'], 'sector': ['X', 'Y'],
                          'score': [70., 70.], 'weight': [.575, .425],
                          'weight_before_macro': [.5, .5]})

    result = historical_macro_weight_path(
        object(), asset_class='b3', holdings=frame, symbol_column='symbol',
        sector_column='sector', score_column='score', cutoffs=[cutoff])

    # A referência é o 0,5 que o teste conhece, e não a coluna devolvida: o
    # defeito corrompia justamente `weight_fundamental`, e medir contra ela
    # dava 11,1% -- dentro do teto -- enquanto o peso real subia 27,8%. Uma
    # verificação que lê a fonte que o defeito estraga não pode falhar pelo
    # motivo que declara (memoria: medir-a-fonte-que-a-decisao-le).
    teto = MacroTiltConfig().max_relative_weight_tilt
    excesso = (result.weight_contextual / 0.5 - 1).abs()
    assert (excesso <= teto + 1e-9).all(), (
        f"tilt efetivo de {excesso.max():.1%} sobre um teto de {teto:.0%}")


def test_final_weight_guard_bounds_relative_moves_and_turnover():
    from core.macro_data.portfolio_tilt import bound_macro_weights
    base = pd.Series([.5, .3, .2])
    result = bound_macro_weights(base, pd.Series([.9, .05, .05]))
    assert result.sum() == pytest.approx(1)
    assert ((result-base).abs() <= base*.15 + 1e-10).all()
    assert .5*(result-base).abs().sum() <= .10


def test_signal_freshness_and_distinct_vintages():
    from core.macro_data.models import MacroObservation
    from core.macro_data.signals import evaluate_observation
    observations = [MacroObservation('test', 'test', date(2020, 1, 1), float(v),
                    datetime(2020, 2, i+1, tzinfo=timezone.utc)) for i, v in enumerate([1, 2, 3])]
    result = evaluate_observation(observations, desirability=1)
    assert result.direction == 'unknown'  # one period, three revisions


def test_stale_series_cannot_be_scored():
    from core.macro_data.models import MacroObservation
    from core.macro_data.signals import evaluate_observation
    obs = [MacroObservation('t', 't', date(2020, i, 1), float(i),
            datetime(2020, i, 2, tzinfo=timezone.utc)) for i in [1, 2]]
    result = evaluate_observation(obs, desirability=1, frequency='monthly',
                                 as_of=datetime(2025, 1, 1, tzinfo=timezone.utc))
    assert result.direction == 'unknown'
    assert 'vencida' in ' '.join(result.limitations)


def test_nan_and_infinite_weights_are_rejected():
    frame = pd.DataFrame({'symbol': ['A', 'B'], 'score': [70., 70.], 'weight': [np.inf, .5]})
    with pytest.raises(ValueError):
        apply_macro_tilt(frame, {}, symbol_column='symbol', score_column='score')
