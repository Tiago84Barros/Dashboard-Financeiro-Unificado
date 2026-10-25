import copy
from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import core.carteira20 as c20
import views.carteira20_b3 as vc

RAIZ = Path(__file__).parents[1]

_SCRIPT = """
import time
from datetime import date
import streamlit as st
st.session_state.setdefault("_app4_user",
    {"id": "11111111-1111-1111-1111-111111111111", "expires_at": time.time() + 600})
from views import carteira20_b3
carteira20_b3.render(show_header=False, hoje=date.fromisoformat(st.session_state["_hoje"]))
"""


@pytest.fixture
def registros_publicados():
    regs = c20.ler(c20.REGISTRO)
    if not c20.revisoes(regs):
        pytest.skip("registro sem revisão publicada")
    return regs


def _rodar(monkeypatch, regs, hoje="2026-10-25"):
    monkeypatch.chdir(RAIZ)
    monkeypatch.setattr(vc, "carregar", lambda: regs)
    monkeypatch.setattr(vc, "aviso_lacuna", lambda msg, *, codigo, **kw: None)
    app = AppTest.from_string(_SCRIPT)
    app.session_state["_hoje"] = hoje
    return app.run(timeout=60)


def _texto(app) -> str:
    partes = [m.value for m in app.markdown] + [c.value for c in app.caption]
    return "\n".join(str(p) for p in partes)


def test_mostra_carteira_vigente_e_cadeia_integra(monkeypatch, registros_publicados):
    app = _rodar(monkeypatch, registros_publicados)
    assert not app.exception
    txt = _texto(app)
    assert "Cadeia íntegra" in txt
    assert "o primeiro fecha em 30/11/2026" in txt
    carteira = app.dataframe[0].value
    corpo = c20.vigente(registros_publicados)["corpo"]
    assert list(carteira["Ticker"]) == [c["ticker"] for c in corpo["carteira"]]
    assert len(carteira) == c20.N_ACOES
    historico = app.dataframe[-1].value
    assert list(historico["Hash"]) == [r["hash"] for r in registros_publicados]


def test_cadeia_adulterada_aparece_quebrada(monkeypatch, registros_publicados):
    regs = copy.deepcopy(registros_publicados)
    regs[1]["corpo"]["carteira"][0]["ticker"] = "XXXX3"
    app = _rodar(monkeypatch, regs)
    assert not app.exception
    assert "Cadeia QUEBRADA" in _texto(app)


def test_mes_fechado_sem_medicao_nao_finge_estar_em_dia(monkeypatch, registros_publicados):
    app = _rodar(monkeypatch, registros_publicados, hoje="2026-12-05")
    assert not app.exception
    txt = _texto(app)
    assert "a medição ainda não foi publicada" in txt
    assert "Nenhum mês fechado" not in txt


def test_registro_ausente_mostra_estado_vazio(monkeypatch):
    app = _rodar(monkeypatch, None)
    assert not app.exception
    assert "ainda não tem registro publicado" in _texto(app)


def test_proxima_revisao_e_primeiro_fechamento(registros_publicados):
    prox = vc.proxima_revisao(registros_publicados, date(2026, 10, 25))
    assert prox["data_formacao"] == "2027-04-30"
    assert vc.primeiro_fechamento(registros_publicados) == date(2026, 11, 30)


def test_aba_despachada_na_vitrine_b3():
    import design.market_companies as mc
    assert mc.NAV_ITEMS_B3[6] == ("📒", "Carteira 20")
    assert ("📒", "Carteira 20") not in mc.NAV_ITEMS
    b3 = (RAIZ / "views" / "empresas_b3.py").read_text(encoding="utf-8")
    assert "carteira20_b3.render" in b3
