"""Preferência visual isolada, durável e sem sobrescrita de memória dos chats."""
import pytest
from sqlalchemy import text
from streamlit.testing.v1 import AppTest

from core import user_preferences as preferences
from tests import test_multiuser as fixtures
from tests.test_multiuser import ADMIN, PASSWORD, accounts, login_as

postgres = fixtures.postgres
user_state = fixtures.user_state


def test_tema_isolado_e_preserva_outras_preferencias(postgres, user_state):
    login_as(user_state, ADMIN)
    other = accounts.create_user("Pessoa tema", "theme@example.test", PASSWORD)
    assert preferences.load_theme() == "dark"
    with postgres.begin() as conn:
        extra = accounts.locked_preferences(conn, ADMIN)
        extra["app4_private_chats_v2"] = {"synthetic": {"messages": []}}
        accounts.write_preferences(conn, ADMIN, extra)
    preferences.save_theme("light")
    assert preferences.load_theme() == "light"
    login_as(user_state, other)
    assert preferences.load_theme() == "dark"
    preferences.save_theme("dark")
    user_state.clear()
    login_as(user_state, ADMIN)
    assert preferences.load_theme() == "light"
    with postgres.connect() as conn:
        extra = conn.execute(text("SELECT extra_settings FROM user_settings WHERE user_id=:uid"),
                             {"uid": ADMIN}).scalar()
    assert extra["app4_private_chats_v2"] == {"synthetic": {"messages": []}}


def test_tema_rejeita_entrada_e_sessao_invalida(user_state):
    with pytest.raises(PermissionError):
        preferences.load_theme()
    with pytest.raises(PermissionError):
        preferences.save_theme("light")
    login_as(user_state, ADMIN)
    with pytest.raises(ValueError):
        preferences.save_theme("<style>untrusted</style>")


SCRIPT = '''
import time
import streamlit as st
from design.theme_selector import current_theme, render_theme_selector
from design.tema import aplicar_tema
st.session_state.setdefault('_app4_user', {'id': 'A', 'expires_at': time.time()+600})
aplicar_tema(current_theme())
render_theme_selector()
'''


def test_interface_salva_recarrega_e_nao_herda_tema_ao_trocar_usuario(monkeypatch):
    import design.theme_selector as selector
    from core.user_context import require_user
    stored = {"A": "light", "B": "dark"}
    monkeypatch.setattr(selector, "load_theme", lambda: stored[require_user()])
    monkeypatch.setattr(selector, "save_theme", lambda theme: stored.update({require_user(): theme}))
    app = AppTest.from_string(SCRIPT).run()
    assert not app.exception
    assert app.selectbox[0].value == "light"
    app.selectbox[0].select("dark").run()
    assert not app.exception
    assert stored["A"] == "dark"
    app.selectbox[0].select("light").run()
    fresh = AppTest.from_string(SCRIPT).run()
    assert fresh.selectbox[0].value == "light"
    user = dict(app.session_state["_app4_user"])
    user["id"] = "B"
    app.session_state["_app4_user"] = user
    app.run()
    assert not app.exception
    assert app.selectbox[0].value == "dark"
    assert stored == {"A": "light", "B": "dark"}


def test_falha_de_gravacao_nao_confirma_nem_muda_tema(monkeypatch):
    import design.theme_selector as selector
    monkeypatch.setattr(selector, "load_theme", lambda: "dark")

    def fail(theme):
        raise RuntimeError("sensitive details must never be rendered")

    monkeypatch.setattr(selector, "save_theme", fail)
    app = AppTest.from_string(SCRIPT).run()
    app.selectbox[0].select("light").run()
    assert not app.exception
    assert app.session_state["_app4_theme_preference"] == ("A", "dark")
    assert len(app.error) == 1
    assert "sensitive" not in app.error[0].value


def test_falha_de_leitura_bloqueia_gravacao_e_permite_retry(monkeypatch):
    import design.theme_selector as selector

    def fail():
        raise RuntimeError("sensitive")

    monkeypatch.setattr(selector, "load_theme", fail)
    app = AppTest.from_string(SCRIPT).run()
    assert not app.exception
    assert app.selectbox[0].disabled
    monkeypatch.setattr(selector, "load_theme", lambda: "light")
    app.run()
    assert not app.exception
    assert not app.selectbox[0].disabled
    assert app.selectbox[0].value == "light"
