from views.fiis import _info_card_html, _scenario_cards_html, _selection_card_html


def test_info_and_scenario_cards_render_compact_css_blocks():
    info = _info_card_html("Regime", "Juros altos", accent="#123456")
    scenarios = _scenario_cards_html({"base": .06, "credito": -.20})
    assert 'class="fii-info-card"' in info
    assert "#123456" in info
    assert scenarios.count('class="fii-scenario ') == 2
    assert "pos" in scenarios and "neg" in scenarios
    assert "+6.0%" in scenarios and "-20.0%" in scenarios


def test_selection_card_preserves_details_and_escapes_content():
    html = _selection_card_html({
        "ticker": "TEST11<script>", "tipo": "tijolo", "weight": .10,
        "rank": 2, "peer_count": 100, "top_percent": 2,
        "strengths": ["score acima dos pares"],
        "role": "Renda <resiliente>",
        "caveats": ["WAULT ausente"],
    }, expanded=True)
    assert '<details class="fii-selection-card"' in html
    assert " open" in html
    assert "TEST11&lt;script&gt;" in html
    assert "Renda &lt;resiliente&gt;" in html
    assert "WAULT ausente" in html
