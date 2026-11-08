"""Gravação da carteira-modelo EUA/FII: grava no clique e diz o que está no banco.

Defeito de 08/11/2026: carteiras "salvas" não apareciam no Dashboard Geral nem
no Portfólio Global. Ver design/gravacao_carteira.py.
"""
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from design.gravacao_carteira import resumo_da_carteira_salva

ROOT = Path(__file__).resolve().parents[1]


def test_resumo_sem_carteira_salva_avisa_que_a_classe_fica_de_fora():
    texto = resumo_da_carteira_salva({}, ["NVDA"])
    assert "Nenhuma carteira padrão salva" in texto
    assert "Portfólio Global" in texto


def test_resumo_diz_quando_a_carteira_do_banco_difere_da_tela():
    ativa = {
        "created_at": datetime(2026, 9, 15, 23, 18, tzinfo=timezone.utc),
        "items": [{"symbol": "NVDA"}, {"symbol": "ADBE"}, {"symbol": "EAT"}],
    }
    texto = resumo_da_carteira_salva(ativa, ["EAT", "NUTX", "HRMY", "LNG"])
    assert "3 ativos" in texto
    assert "15/09/2026" in texto
    assert "Difere da carteira acima" in texto
    assert "3 entram, 2 saem" in texto


def test_resumo_reconhece_a_mesma_carteira_por_ticker_ou_symbol():
    ativa = {"created_at": None, "items": [{"ticker": "mxrf11"}, {"ticker": "HGLG11"}]}
    texto = resumo_da_carteira_salva(ativa, ["HGLG11", "MXRF11"], unidade="FIIs")
    assert "2 FIIs" in texto
    assert "Mesmos ativos" in texto
    assert "Difere" not in texto


_SCRIPT = """
import streamlit as st
from design import gravacao_carteira as g

def gravar():
    if st.session_state.get("falhar"):
        raise RuntimeError("host=db.interno senha=segredo")
    st.session_state["gravacoes"] = st.session_state.get("gravacoes", 0) + 1
    return "abcdef1234567890"

st.button("Salvar", key="salvar", on_click=g.ao_clicar_gravar(
    "salvar", gravar, sucesso="Salva. ID: {id}", falha="Não foi possível salvar."))
g.mostrar_resultado("salvar")
"""


def test_gravacao_roda_no_clique_e_mostra_o_desfecho_uma_vez():
    app = AppTest.from_string(_SCRIPT).run()
    assert "gravacoes" not in app.session_state
    app.button(key="salvar").click().run()
    assert not app.exception
    assert app.session_state["gravacoes"] == 1
    assert [s.value for s in app.success] == ["Salva. ID: abcdef12"]
    # O desfecho é consumido: um rerun qualquer não repete a mensagem.
    app.run()
    assert not app.success
    assert app.session_state["gravacoes"] == 1


def test_falha_de_gravacao_vira_mensagem_amigavel_sem_texto_da_excecao():
    app = AppTest.from_string(_SCRIPT)
    app.session_state["falhar"] = True
    with patch("design.gravacao_carteira.registrar_excecao") as registrar:
        app.run()
        app.button(key="salvar").click().run()
    assert not app.exception
    erros = "\n".join(e.value for e in app.error)
    assert "Não foi possível salvar." in erros
    assert "segredo" not in erros
    assert registrar.call_count == 1


def test_eua_tem_um_botao_so_e_ele_grava_no_clique():
    source = (ROOT / "views" / "empresas_americanas.py").read_text(encoding="utf-8")
    # O botão de sessão escrevia numa chave que nenhuma tela lia.
    assert 'st.session_state["us_portfolio_model"]' not in source
    assert "key=\"us_create_save_model\"" not in source
    assert 'key="us_create_persist_model"' in source
    assert "on_click=_gravacao.ao_clicar_gravar(" in source
    assert "resumo_da_carteira_salva(" in source


def test_nenhuma_tela_le_a_chave_de_sessao_do_botao_removido():
    for path in (ROOT / "views").glob("*.py"):
        assert '"us_portfolio_model"' not in path.read_text(encoding="utf-8"), path
