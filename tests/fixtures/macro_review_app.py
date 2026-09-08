"""Harness visual isolado: nenhum banco ou serviço externo é acessado."""
from unittest.mock import patch

import pandas as pd
import streamlit as st

from design.macro_portfolio import render_historical_macro_path

st.title('Validação macro — dados sintéticos')
weight = st.number_input('Peso sintético', value=.5)
mode = st.selectbox('Modo', ['moderate', 'fundamental'])
path = pd.DataFrame([dict(as_of='2025-12-31', symbol='SYNTH',
    weight_contextual=weight, weight_fundamental=weight, macro_impact=0., coverage=1.)])
with patch('core.macro_data.database.get_local_macro_engine', return_value=object()), patch(
    'core.macro_data.portfolio_context.historical_macro_weight_path', return_value=path
):
    render_historical_macro_path(asset_class='b3',
        holdings=pd.DataFrame([dict(symbol='SYNTH', sector='Teste', score=50., weight=weight)]),
        symbol_column='symbol', sector_column='sector', score_column='score',
        mode=mode, key='synthetic')
