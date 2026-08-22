"""Regressão do A-011: a aba "Análise de Empresa" precisa efetivamente
CHAMAR `_preco_atual_com_status` e `_dividendos_anuais_market_first`, não
apenas defini-las ao lado do caminho antigo (`_preco_atual`,
`_yf_dividendos_anuais`).

Contexto: numa rodada anterior essas funções foram criadas corretas e
testadas isoladamente, mas o call site real de `_tab_analise` continuou
usando as funções antigas — código morto ao lado da correção (achado A-011
reaberto em `artifacts/app4_professionalizacao/veredito_final_2026-08-19_rodada3.md`,
seção 3). Este teste amarra o comportamento observável na UI para que uma
futura reversão (voltar a chamar `_preco_atual`/`_yf_dividendos_anuais`
direto) quebre a suíte em vez de passar despercebida.
"""
from __future__ import annotations

import inspect

import views.empresas_b3 as b3

# ── Nível de código: os call sites reais chamam as funções novas ───────────

def test_tab_analise_chama_preco_atual_com_status_nao_o_wrapper_antigo():
    corpo = inspect.getsource(b3._tab_analise)
    assert "_preco_atual_com_status(" in corpo
    # a chamada direta ao wrapper antigo (que descarta o status) não pode
    # voltar a ser o call site real
    assert "_preco_atual(tk)" not in corpo


def test_tab_analise_chama_dividendos_market_first_nao_o_yfinance_puro():
    corpo = inspect.getsource(b3._tab_analise)
    assert "_dividendos_anuais_market_first(" in corpo
    # a chamada direta à função 100% yfinance (sem checar market.*) não pode
    # voltar a ser o call site real desta aba
    assert "_yf_dividendos_anuais(tk)" not in corpo


def test_tab_analise_le_load_error_do_historico_de_precos():
    corpo = inspect.getsource(b3._tab_analise)
    assert 'attrs.get("load_error")' in corpo
    assert "falha_rede" in corpo


def test_tab_analise_tem_legenda_de_fonte_no_grafico_de_preco():
    corpo = inspect.getsource(b3._tab_analise)
    idx_hdr = corpo.index('_sec_hdr("📉 Preço da Ação")')
    idx_chart = corpo.index("st.plotly_chart(fig_preco")
    trecho = corpo[idx_hdr:idx_chart]
    assert "st.caption(" in trecho


# ── Nível comportamental: renderização real via AppTest ────────────────────

def test_ui_distingue_falha_de_rede_de_ausencia_real_na_cotacao():
    """Com status='falha_rede', o rótulo ao lado da cotação não pode mais
    ser o texto estático antigo — precisa indicar problema de rede."""
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string(
        """
import pandas as pd
import streamlit as st
import core.b3_data as db
import views.empresas_b3 as view

# Isola do banco/rede reais: monkeypatch dos pontos de I/O.
view._preco_atual_com_status = lambda tk: (None, "falha_rede")
view._dividendos_anuais_market_first = lambda tk: (pd.DataFrame(), "yfinance")
view._yf_precos = lambda tk: pd.DataFrame()
db.market_active = lambda: True
db.load_demonstracoes = lambda *a, **k: pd.DataFrame()
db.load_multiplos_historico = lambda *a, **k: pd.DataFrame()
db.load_multiplos = lambda *a, **k: pd.Series(dtype=object)

st.session_state["b3_ticker_sel"] = "TESTE3"
view._tab_analise(pd.DataFrame())
"""
    ).run(timeout=60)

    assert not app.exception
    texto = "\n".join(item.value for item in app.markdown)
    assert "Falha de rede" in texto or "falha de rede" in texto.lower()
    assert "Cotação (yfinance)" not in texto or "rede" in texto.lower()


def test_ui_usa_fonte_market_first_para_dividendos_quando_disponivel():
    """Com `_dividendos_anuais_market_first` devolvendo market.dividends, a
    fonte precisa aparecer na UI (não pode silenciosamente continuar
    yfinance-only)."""
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string(
        """
import pandas as pd
import streamlit as st
import core.b3_data as db
import views.empresas_b3 as view

df_divs = pd.DataFrame({
    "Data": pd.to_datetime(["2023-12-31", "2024-12-31"]),
    "Dividendos": [1.5, 2.0],
})

view._preco_atual_com_status = lambda tk: (10.0, "ok")
view._dividendos_anuais_market_first = lambda tk: (df_divs, "market.dividends")
view._yf_precos = lambda tk: pd.DataFrame()
db.market_active = lambda: True
db.load_demonstracoes = lambda *a, **k: pd.DataFrame()
db.load_multiplos_historico = lambda *a, **k: pd.DataFrame()
db.load_multiplos = lambda *a, **k: pd.Series({"P/L": 10.0})

st.session_state["b3_ticker_sel"] = "TESTE3"
view._tab_analise(pd.DataFrame())
"""
    ).run(timeout=60)

    assert not app.exception
    texto = "\n".join(item.value for item in app.markdown)
    assert "market.dividends" in texto
