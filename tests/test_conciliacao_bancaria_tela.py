"""A prévia do extrato mostra o par manual e confere o saldo.

Roda a seção de conciliação de verdade (``AppTest``), com o banco trocado por
um contexto fixo: o que se testa é a tela decidir o vínculo e a diferença.
"""
from __future__ import annotations

from streamlit.testing.v1 import AppTest


def _app():
    from datetime import date

    import streamlit as st

    import views.bank_statement_upload as bsu

    bsu.preparar_conciliacao = lambda rows, data_saldo=None: {
        "ok": True,
        "conta": {"id": "acc", "nome": "C6 Conta"},
        "ja_importados": set(),
        "vinculos": {0: {"transaction_id": "tx-1", "descricao": "Padaria", "data": date(2026, 10, 1),
                         "valor": -80.0, "categoria_id": "c", "dias": 0,
                         "similaridade": 0.8, "forca": "forte"}},
        "saldo_app": 1000.0,
    }
    rows = [
        {"data_movimento": date(2026, 10, 1), "valor": -80.0, "descricao_original": "Pix padaria",
         "categoria_id": "c"},
        {"data_movimento": date(2026, 10, 2), "valor": -30.0, "descricao_original": "Pix mercado",
         "categoria_id": "c"},
    ]
    parsed = {"saldos": {"final": {"data": date(2026, 10, 31), "valor": 970.0}}}
    escolhidos, saldo = bsu._render_conciliacao(rows, parsed)
    st.session_state["resultado"] = (escolhidos, saldo)


def test_par_marcado_e_saldo_que_fecha():
    at = AppTest.from_function(_app).run()
    assert not at.exception
    escolhidos, saldo = at.session_state["resultado"]
    assert escolhidos == {0: "tx-1"}
    assert saldo["valor"] == 970.0
    # 1000 no app − 30 do mercado (a padaria já está no app) = 970 do banco.
    assert any("fecha" in s.value for s in at.success)


def test_desmarcar_o_par_faz_a_linha_virar_lancamento_e_abre_diferenca():
    at = AppTest.from_function(_app).run()
    at.checkbox[0].uncheck().run()
    escolhidos, _ = at.session_state["resultado"]
    assert escolhidos == {}
    assert at.warning and "Diferença" in at.warning[0].value


def _app_sem_saldo():
    from datetime import date

    import streamlit as st

    import views.bank_statement_upload as bsu

    bsu.preparar_conciliacao = lambda rows, data_saldo=None: {
        "ok": True, "conta": {"id": "acc", "nome": "C6 Conta"},
        "ja_importados": {0}, "vinculos": {}, "saldo_app": None,
    }
    rows = [{"data_movimento": date(2026, 10, 1), "valor": -80.0,
             "descricao_original": "Pix padaria", "categoria_id": "c"}]
    st.session_state["resultado"] = bsu._render_conciliacao(rows, {"saldos": {"final": None}})


def test_extrato_sem_saldo_pede_o_numero_e_nao_inventa_conferencia():
    at = AppTest.from_function(_app_sem_saldo).run()
    assert not at.exception
    assert at.session_state["resultado"] == ({}, None)
    assert at.number_input[0].value is None
    assert any("já foram importadas" in i.value for i in at.info)
