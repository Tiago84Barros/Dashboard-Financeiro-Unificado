"""Editores de lançamentos e de extratos: caminho claro e chaves sem colisão.

O ``st.data_editor`` pinta a grade num canvas cujas cores o Streamlit monta em JS
a partir do tema do config (escuro), e CSS não alcança — por isso cada editor
desta tela ganha uma versão com widgets nativos para o tema claro.

As abas do Streamlit desenham todas no mesmo run: dois editores que reusam o
mesmo desenho precisam de prefixos de chave distintos, senão o segundo estoura
com ``DuplicateWidgetID``. O teste renderiza os três editores no MESMO run
justamente para que a colisão apareça.
"""
import inspect
import textwrap

import views.bank_statement_upload as bsu
import views.controle_financeiro as cf

_SCRIPT = textwrap.dedent('''
    import datetime as dt
    import pandas as pd
    import streamlit as st
    from design.tema_canvas import registrar_tema

    registrar_tema("light")
    import views.bank_statement_upload as bsu
    import views.controle_financeiro as cf

    lanc = pd.DataFrame([{
        "ID": "a1", "Tipo": "saída", "Categoria": "Mercado",
        "Data": dt.date(2026, 1, 5), "Valor": 12.5,
        "Descrição": "pão", "Conta": "Nubank",
    }])
    # Os dois prefixos que a tela usa de verdade, no mesmo run.
    for prefixo in ("dash_lanc", "editor_tabelas"):
        saida = cf._editor_lancamentos_claro(
            lanc, range(1), ["entrada", "saída"], ["Mercado"], ["Nubank"],
            prefixo=prefixo)
        st.text(f"{prefixo}={saida.at[0, 'Descrição']}|{float(saida.at[0, 'Valor'])}")

    ext = pd.DataFrame([{
        "ID": "b2", "Data": "05/01/2026", "Banco": "Itaú", "Descrição": "tarifa",
        "Direção": "saida", "Categoria": "Pendente", "Status": "pendente",
        "Valor (R$)": 9.9,
    }])
    saida = cf._editor_extratos_claro(ext, range(1), ["Pendente", "Mercado"])
    st.text(f"extrato={saida.at[0, 'Direção']}|{float(saida.at[0, 'Valor (R$)'])}")

    previa = pd.DataFrame([{
        "Data": "05/01/2026", "Tipo banco": "DEB", "Descrição": "uber",
        "Direção": "saida", "Categoria": "Pendente", "Valor (R$)": 21.0,
    }])
    saida = bsu._editor_previa_claro(previa, ["Pendente", "Transporte"])
    st.text(f"previa={saida.at[0, 'Descrição']}|{float(saida.at[0, 'Valor (R$)'])}")
''')


def test_os_tres_editores_claros_convivem_no_mesmo_run():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string(_SCRIPT)
    app.run(timeout=120)

    assert not app.exception, app.exception[0].value if app.exception else ""
    textos = [t.value for t in app.text]
    assert "dash_lanc=pão|12.5" in textos
    assert "editor_tabelas=pão|12.5" in textos
    assert "extrato=saida|9.9" in textos
    assert "previa=uber|21.0" in textos
    # Três selects em lançamentos (×2), dois em extrato e dois na prévia.
    assert len(app.selectbox) == 3 * 2 + 2 + 2


def test_editor_de_lancamentos_das_tabelas_escolhe_a_grade_pelo_tema():
    fonte = inspect.getsource(cf._editor_lancamentos)
    assert "no_claro()" in fonte
    assert "_editor_lancamentos_claro" in fonte
    # O seletor de página fica FORA do form: dentro dele o Streamlit só leria a
    # troca no submit, e a página nunca mudaria.
    assert fonte.index("_pagina_lancamentos(") < fonte.index("with st.form(")


def test_editor_de_extratos_escolhe_a_grade_pelo_tema():
    fonte = inspect.getsource(cf._editor_extratos)
    assert "no_claro()" in fonte
    assert "_editor_extratos_claro" in fonte
    assert fonte.index("_pagina_lancamentos(") < fonte.index("with st.form(")


def test_previa_do_upload_escolhe_a_grade_pelo_tema():
    fonte = inspect.getsource(bsu._render_upload)
    assert "no_claro()" in fonte
    assert "_editor_previa_claro" in fonte


def test_editores_claros_nao_reusam_prefixo_de_chave():
    """Cada editor claro precisa de um espaço de chaves próprio."""
    prefixos = {
        "dash_lanc": inspect.getsource(cf._tab_dashboard),
        "editor_tabelas": inspect.getsource(cf._editor_lancamentos),
    }
    assert 'prefixo="dash_lanc"' not in prefixos["editor_tabelas"]
    assert "prefixo=editor_key" in prefixos["editor_tabelas"]
    assert 'prefixo="extrato"' in inspect.getsource(cf._editor_extratos)
