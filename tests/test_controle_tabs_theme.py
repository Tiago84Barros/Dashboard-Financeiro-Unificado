from design.tema import _CSS


def test_controle_financeiro_subnavigation_matches_tabs_visual_language():
    selector = '.st-key-cf_secao_ativa [data-baseweb="button-group"]'

    assert selector in _CSS
    assert '.st-key-cf_secao_ativa [data-testid="stButtonGroup"]' in _CSS
    assert 'border-bottom: 1px solid #2D3748;' in _CSS
    assert '> [data-testid="stBaseButton-segmented_controlActive"]' in _CSS
    assert 'border-bottom-color: #00C896 !important;' in _CSS
    assert 'overflow-x: auto;' in _CSS


def test_controle_financeiro_subnavigation_keeps_keyboard_focus_visible():
    selector = '.st-key-cf_secao_ativa [data-baseweb="button-group"] > button:focus-visible'

    assert selector in _CSS
    assert 'outline: 2px solid #4A9EFF !important;' in _CSS
