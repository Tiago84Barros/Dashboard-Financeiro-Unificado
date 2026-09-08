"""Regressão de estado Streamlit com dados exclusivamente sintéticos."""
import pandas as pd
from streamlit.testing.v1 import AppTest


def test_history_invalidates_after_composition_mode_or_snapshot_change(monkeypatch):
    monkeypatch.setattr('core.macro_data.database.get_local_macro_engine', lambda: object())
    def history(*args, **kwargs):
        return pd.DataFrame([dict(as_of='2025-12-31', symbol='SYNTH',
            weight_contextual=.5, weight_fundamental=.5, macro_impact=0., coverage=1.)])
    monkeypatch.setattr('core.macro_data.portfolio_context.historical_macro_weight_path', history)
    app = AppTest.from_string('''
import pandas as pd
import streamlit as st
from design.macro_portfolio import render_historical_macro_path
weight = st.number_input('Peso sintético', value=0.5, key='weight')
mode = st.selectbox('Modo', ['moderate', 'fundamental'], key='mode')
version = st.number_input('Versão sintética', value=1, key='version')
render_historical_macro_path(asset_class='b3',
    holdings=pd.DataFrame([dict(symbol='SYNTH',sector='Teste',score=50.,weight=weight)]),
    symbol_column='symbol',sector_column='sector',score_column='score',
    mode=mode,key='synthetic',signature_context={'snapshot':version})
''').run()
    assert not app.exception
    for widget, value in [('weight', .6), ('mode', 'fundamental'), ('version', 2)]:
        app.button(key='synthetic_calculate').click().run()
        assert not app.exception and len(app.dataframe) == 1
        if widget == 'mode':
            app.selectbox(key=widget).set_value(value).run()
        else:
            app.number_input(key=widget).set_value(value).run()
        assert not app.exception and len(app.dataframe) == 0
