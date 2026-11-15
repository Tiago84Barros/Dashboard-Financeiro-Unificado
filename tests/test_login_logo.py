"""A tela de login mostra o monograma TSB, o mesmo da aba e do menu lateral."""
from streamlit.testing.v1 import AppTest


def _tela_de_login():
    from core.auth import _renderizar_login

    _renderizar_login()


def test_login_mostra_o_monograma_tsb_no_lugar_do_emoji():
    at = AppTest.from_function(_tela_de_login).run(timeout=30)

    assert not at.exception
    blocos = [m.value for m in at.markdown]
    cabecalho = next(b for b in blocos if "Dashboard Financeiro" in b)
    assert 'class="login-logo"' in cabecalho
    assert "data:image/svg+xml;base64," in cabecalho
    assert "📊" not in cabecalho
    # Uma linha só: o st.markdown transforma HTML quebrado em bloco de código.
    assert "\n" not in cabecalho
