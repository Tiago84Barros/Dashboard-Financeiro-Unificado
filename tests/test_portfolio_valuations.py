import math

import pytest

from core.portfolio_valuations import aggregate_valuations


def test_weighted_means_and_total_portfolio_coverage():
    positions = [{'ticker': 'A', 'valor_mercado': 100},
                 {'ticker': 'B', 'valor_mercado': 300},
                 {'ticker': 'TESOURO', 'valor_mercado': 600}]
    result = aggregate_valuations(positions, {'A': {'dy': 0., 'pl': 10},
                                            'B': {'dy': 8., 'pl': 20}})
    assert result['dy']['value'] == 6.
    assert result['pl']['value'] == 17.5
    assert result['dy']['coverage'] == .4
    assert result['pvp']['value'] is None


@pytest.mark.parametrize('invalid', [None, 'bad', math.nan, math.inf, -3, 0])
def test_invalid_multiple_not_zero_filled(invalid):
    result = aggregate_valuations([{'ticker': 'A', 'valor_mercado': 100}],
                                  {'A': {'pl': invalid}})
    assert result['pl']['value'] is None
    assert result['pl']['coverage'] == 0


def test_lots_share_fundamentals_but_count_ticker_once():
    result = aggregate_valuations([{'ticker': 'A', 'valor_mercado': 100},
                                  {'ticker': 'A', 'valor_mercado': 200},
                                  {'ticker': 'B', 'valor_mercado': None}],
                                 {'A': {'pvp': 2}, 'B': {'pvp': 8}})
    assert result['pvp']['value'] == 2
    assert result['pvp']['assets'] == 1


def test_empty_portfolio():
    assert aggregate_valuations([], {})['dy']['value'] is None


def test_panel_with_synthetic_data(monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr('design.portfolio_valuations._load',
        lambda *args: ({'SYNTH3': {'dy': 8., 'pl': 12., 'pvp': 1.2}}, [], 'sintética'))
    app = AppTest.from_string('''
from design.portfolio_valuations import render_portfolio_valuations
render_portfolio_valuations([
    {'ticker': 'SYNTH3F', 'classe': 'Ações', 'valor_mercado': 200},
    {'ticker': 'TESOURO', 'classe': 'Tesouro Direto', 'valor_mercado': 800}])
''').run()
    assert not app.exception
    cards = [m.value for m in app.markdown if '<div style="background:' in m.value]
    assert len(cards) == 7
    assert [c for c in cards if '8.00%' in c] and [c for c in cards if '12.00x' in c]
    assert [c for c in cards if '1.20x' in c]
    # A cobertura vive DENTRO do card, e o card sai num bloco só: div aberta num
    # `st.markdown` e fechada em outro já produziu moldura vazia neste projeto.
    dy = next(c for c in cards if '8.00%' in c)
    assert '20.0% do valor da carteira' in dy
    assert dy.count('<div') == dy.count('</div>')


def test_class_failure_does_not_erase_other_source(monkeypatch):
    from core.portfolio_valuations import load_valuation_fundamentals

    def unavailable(*args):
        raise RuntimeError('synthetic failure')
    monkeypatch.setattr('core.data_reconciliacao.batch_fund_fmt', unavailable)
    monkeypatch.setattr('core.fundamentus.batch_fiis', lambda *args: {'FII11': {'dy': 9}})
    data, failures = load_valuation_fundamentals(('SYNTH3',), ('FII11',))
    assert data == {'FII11': {'dy': 9}}
    assert failures == ['Ações B3']
