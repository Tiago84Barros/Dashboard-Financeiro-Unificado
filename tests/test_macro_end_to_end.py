from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from core.macro_data.portfolio_context import (
    PortfolioMacroSnapshot,
    historical_macro_weight_path,
)
from core.macro_data.portfolio_tilt import apply_macro_tilt


def test_history_replays_identical_kernel_and_base(monkeypatch):
    cutoff = datetime(2025, 12, 31, tzinfo=timezone.utc)
    snapshot = PortfolioMacroSnapshot({'A': 100., 'B': -100.}, (), cutoff, 2, 2, 1)
    monkeypatch.setattr('core.macro_data.portfolio_context.load_portfolio_macro_snapshot', lambda *a, **k: snapshot)
    frame = pd.DataFrame({'symbol': ['A', 'B'], 'sector': ['X', 'Y'],
                          'score': [70., 70.], 'weight': [.575, .425], 'peso_fundamental': [.5, .5]})
    calls = []
    def rebuild(base, impacts, mode):
        calls.append(base.weight.tolist())
        return apply_macro_tilt(base, impacts, symbol_column='symbol', score_column='score', mode=mode)
    path = historical_macro_weight_path(object(), asset_class='b3', holdings=frame,
        symbol_column='symbol', sector_column='sector', score_column='score', cutoffs=[cutoff], rebuild=rebuild)
    assert calls == [[.5, .5]]
    assert path.weight_contextual.tolist() == pytest.approx([.575, .425])
    assert frame.weight.tolist() == [.575, .425]


def test_fii_final_overlay_stays_near_fundamental():
    from core.fii_portfolio_v4 import (
        MacroScenario,
        PortfolioPolicy,
        optimize_diligence_portfolio,
    )
    from tests.test_fii_portfolio_v4 import _candidate
    rows = [_candidate(i, t) for i, t in enumerate(['tijolo', 'papel', 'fof', 'hibrido'] * 3)]
    policy = PortfolioPolicy(max_assets=12, max_asset=.15)
    base = optimize_diligence_portfolio(rows, MacroScenario(selic=12, ipca=5), policy=policy)
    result = optimize_diligence_portfolio(rows, MacroScenario(selic=12, ipca=5), policy=policy,
        macro_impacts={r['ticker']: 100 if i % 2 else -100 for i, r in enumerate(rows)}, macro_mode='scenario')
    assert result['items'] and not result['constraint_violations']
    fundamental = {r['ticker']: r['weight'] for r in base['items']}
    for row in result['items']:
        assert row['weight_before_macro'] == pytest.approx(fundamental[row['ticker']], abs=1e-6)
        assert abs(row['weight'] - row['weight_before_macro']) <= .15 * row['weight_before_macro'] + 1e-8
    assert result['macro_turnover'] <= .10


def test_global_only_applies_change_since_saved_snapshot(monkeypatch):
    from core.macro_data.global_context import load_global_macro_context
    now = datetime.now(timezone.utc)
    snap = PortfolioMacroSnapshot({'A': 20., 'B': 30.}, (), now, 2, 2, 1)
    monkeypatch.setattr('core.macro_data.global_context.load_portfolio_macro_snapshot', lambda *a, **k: snap)
    positions = pd.DataFrame([
        {'asset_class': 'b3', 'symbol': 'A', 'sector_raw': 'X', 'payload': {'assumptions': {'params': {
            'macro_mode': 'moderate', 'macro_snapshot': {'impacts': {'A': 20.}}}}}},
        {'asset_class': 'b3', 'symbol': 'B', 'sector_raw': 'Y', 'payload': {}},
    ])
    _, changes, limitations = load_global_macro_context(object(), positions)
    assert changes == {'A': 0.}
    assert any('B:' in line for line in limitations)


def test_global_macro_keeps_cost_guard():
    from core.global_portfolio.advisor import recomendar
    from tests.test_global_advisor import _HOJE, _SEMPRE_REBALANCEAR, _linha, _sinal
    positions = pd.DataFrame([_linha('A', peso=.5), _linha('B', peso=.5)])
    signals = [_sinal('quality', s, 0., 'metrics') for s in ['A', 'B']]
    result = recomendar(positions, signals, alvos={}, politica=_SEMPRE_REBALANCEAR,
        custos={}, patrimonio_total=10000, data_atual=_HOJE, macro_impacts={'A': 100., 'B': -100.})
    assert all(a.acao == 'manter' for a in result)
    assert result[0].peso_sugerido == pytest.approx(.575)
    assert result[0].macro_delta == 100


def test_calibration_requires_strict_and_oos_evidence():
    from core.macro_data.calibration import calibrate_sector_factor
    rng = np.random.default_rng(42)
    dates = pd.date_range('2010-01-31', periods=96, freq='ME', tz='UTC')
    signal = rng.uniform(-1, 1, len(dates))
    frame = pd.DataFrame({'cutoff': dates, 'known_at': dates,
        'return_end': dates + pd.offsets.MonthEnd(1), 'signal': signal,
        'forward_return': signal * .04 + rng.normal(0, .001, len(dates))})
    result = calibrate_sector_factor(frame, prior=.3)
    assert result.status == 'candidate_for_review' and result.oos_r2 > 0
    assert calibrate_sector_factor(frame, prior=.3, knowledge_mode='reconstructed').status == 'initial_prior'
    frame['known_at'] = frame['return_end']
    assert calibrate_sector_factor(frame, prior=.3).status == 'initial_prior'


def test_snapshot_roundtrip_preserves_evidence_and_identity():
    from core.macro_data.portfolio_context import format_saved_macro_context
    snapshot = PortfolioMacroSnapshot({'A': 10}, ({'symbol': 'A', 'provider': 'synthetic',
        'provider_code': 'RATE', 'reference_period': '2025-01-31', 'value': 2.0,
        'intensity': 20., 'confidence': 60., 'direction': 'positive', 'channel': 'test',
        'unit': '%', 'source_url': 'https://example.org/synthetic'},),
        datetime(2025, 2, 1, tzinfo=timezone.utc), 1, 1, 1)
    context = format_saved_macro_context(snapshot.to_payload())
    assert snapshot.snapshot_id in context
    assert 'synthetic.RATE' in context and '2025-01-31' in context
    from core.llm_context_fii import build_fii_chat_context
    saved_context = build_fii_chat_context(user_question='Contexto macro?',
        selected_items=[], scored_rows=[], methodology_rows=[], scenario=None,
        portfolio_result={'params_json': {'macro_snapshot': snapshot.to_payload()}})
    assert snapshot.snapshot_id in saved_context


def test_global_invalid_saved_impact_is_context_only(monkeypatch):
    from core.macro_data.global_context import load_global_macro_context
    snap = PortfolioMacroSnapshot({'SYNTH': 20.}, (), datetime.now(timezone.utc), 1, 1, 1)
    monkeypatch.setattr('core.macro_data.global_context.load_portfolio_macro_snapshot', lambda *a, **k: snap)
    for invalid in ['invalid', float('nan'), float('inf'), {}]:
        positions = pd.DataFrame([{'asset_class': 'fii', 'symbol': 'SYNTH', 'sector': 'tijolo',
            'payload': {'assumptions': {'params': {'macro_snapshot': {'impacts': {'SYNTH': invalid}}}}}}])
        _, changes, limitations = load_global_macro_context(object(), positions)
        assert changes == {}
        assert any('SYNTH' in line for line in limitations)


def test_global_uses_saved_macro_sector(monkeypatch):
    from core.macro_data.global_context import load_global_macro_context
    captured = {}
    def snapshot(*args, **kwargs):
        captured.update(kwargs['assets'])
        return PortfolioMacroSnapshot({}, (), datetime.now(timezone.utc), 1, 0, 0)
    monkeypatch.setattr('core.macro_data.global_context.load_portfolio_macro_snapshot', snapshot)
    positions = pd.DataFrame([{'asset_class': 'fii', 'symbol': 'SYNTH', 'sector_raw': 'Logístico',
        'payload': {'assumptions': {'params': {'macro_snapshot': {
            'details': [{'symbol': 'SYNTH', 'sector': 'tijolo'}]}}}}}])
    load_global_macro_context(object(), positions)
    assert captured == {'SYNTH': 'tijolo'}
