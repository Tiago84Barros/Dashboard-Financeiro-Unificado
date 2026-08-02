from design.tema import _CSS


def test_controle_financeiro_subnavigation_matches_floating_cards_visual_language():
    selector = '.st-key-cf_secao_ativa [data-baseweb="button-group"]'

    assert selector in _CSS
    assert '.st-key-cf_secao_ativa [data-testid="stButtonGroup"]' in _CSS
    assert 'grid-template-columns: repeat(4, minmax(0, 1fr));' in _CSS
    assert 'gap: 1rem;' in _CSS
    assert 'box-shadow: 0 10px 24px rgba(0, 0, 0, .18) !important;' in _CSS
    assert '> [data-testid="stBaseButton-segmented_controlActive"]' in _CSS
    assert 'background: #00C896 !important;' in _CSS
    assert 'border-color: #00C896 !important;' in _CSS


def test_controle_financeiro_subnavigation_is_responsive_and_reduced_motion_safe():
    assert '@media (max-width: 720px)' in _CSS
    assert 'grid-template-columns: repeat(2, minmax(0, 1fr));' in _CSS
    assert '@media (prefers-reduced-motion: reduce)' in _CSS
    assert 'transform: none;' in _CSS


def test_controle_financeiro_subnavigation_keeps_keyboard_focus_visible():
    selector = '.st-key-cf_secao_ativa [data-baseweb="button-group"] > button:focus-visible'

    assert selector in _CSS
    assert 'outline: 2px solid #4A9EFF !important;' in _CSS
    assert 'outline-offset: 3px;' in _CSS
