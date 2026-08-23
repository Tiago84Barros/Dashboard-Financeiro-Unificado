import pandas as pd
import pytest

import design.market_companies as market_companies
from views.fiis import (
    _fii_available_card_html,
    _fii_logo_url,
    _fii_score_class,
    _info_card_html,
    _scenario_cards_html,
    _selection_card_html,
)


@pytest.fixture(autouse=True)
def _sem_rede(monkeypatch):
    """company_logo_html faz HEAD na CDN para decidir logo vs. inicial. Sem
    isto o teste sai à rede e passa a depender de estar online."""
    monkeypatch.setattr(
        market_companies, "_logo_disponivel_cached", lambda url: False,
    )


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


def test_fii_score_class_segue_os_limiares_de_empresas_b3():
    assert _fii_score_class(70.0) == "fii-sc-high"
    assert _fii_score_class(40.0) == "fii-sc-mid"
    assert _fii_score_class(39.9) == "fii-sc-low"


def test_fii_available_card_html_escapa_e_mostra_metricas():
    row = pd.Series({
        "Ticker": "TEST11<script>", "Nome": "Fundo Teste <script>",
        "Segmento": "Multicategoria", "Tipo": "papel",
        "Score": 82.0, "DY_12m": 0.1234, "P/VP": 0.97,
        "Liquidez_Diaria": 1_500_000.0,
    })
    card = _fii_available_card_html(row)
    assert "TEST11&lt;script&gt;" in card
    assert "Fundo Teste &lt;script&gt;" in card
    assert "<script>" not in card
    assert "fii-sc-high" in card
    assert "Multicategoria · Papel/CRI" in card
    assert "12.3%" in card
    assert "0.97" in card
    assert "R$ 1500k" in card


def test_card_do_fii_sai_num_bloco_unico_com_as_tags_fechadas():
    """O Streamlit fecha tag pendente a cada bloco de markdown: abrir a div do
    card num bloco e fechá-la em outro renderizava a moldura vazia, com o
    conteúdo caindo fora dela."""
    card = _fii_available_card_html(pd.Series({
        "Ticker": "HGLG11", "Nome": "CSHG Logistica", "Segmento": "Logistica",
        "Tipo": "tijolo", "Score": 91.0, "DY_12m": .089,
        "P/VP": 1.02, "Liquidez_Diaria": 4_100_000.0,
    }))
    assert card.count("<div") == card.count("</div>")
    assert card.startswith('<div class="fii-card"') and card.endswith("</div>")
    # Logo dentro do card e sem <img>: st.image não cabe num bloco de HTML, e
    # a tag <img> é justamente o que o achado A-012 proíbe.
    assert 'class="fii-logo"' in card
    assert "<img" not in card


def test_fii_logo_url_usa_cdn_b3_e_remove_sufixo_sa():
    assert _fii_logo_url("PETR4.SA") == (
        "https://raw.githubusercontent.com/thefintz/icones-b3/main/icones/PETR4.png"
    )
