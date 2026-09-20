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

# Preâmbulo único dos scripts de AppTest.
#
# Isolar o banco nomeando uma função de leitura por vez é lista branca: a
# porta não prevista continua aberta. Foi o que aconteceu aqui — os três
# `db.load_*` abaixo estavam neutralizados, mas `_b3_peer_scores` chama
# `db.load_multiplos_todos`, que varre a tabela inteira no Supabase; os dois
# testes de UI estouravam o timeout de 60 s sem nenhuma asserção falhar.
#
# O corte agora é no ponto de estrangulamento: `core.market_read._q` já sabe
# devolver quadro vazio com `load_error="database_unavailable"` quando não há
# engine. Zerar o engine fecha todas as leituras de `market.*` de uma vez,
# inclusive as que ainda não existem.
#
# Os `db.load_*` continuam aqui porque fixam o CONTEÚDO que cada teste quer
# exercitar (uma Series com P/L, um quadro vazio), não porque sejam a defesa.
_PREAMBULO = """
import time

import pandas as pd
import streamlit as st
import core.database as cdb
import core.market_read as mr
import core.b3_data as db
import core.chat_repository as chat_repo
import views.empresas_b3 as view

# Corta o banco no ponto de estrangulamento (ver comentário no teste).
cdb.get_engine = lambda *a, **k: None
mr._engine = lambda: None
# Marcador de que o corte pegou, verificado pelo teste: se alguma leitura de
# market.* voltar a alcançar a rede, este marcador some junto.
st.markdown("BANCO_CORTADO=" + str(mr._q("SELECT 1").attrs.get("load_error")))

# O chat do ativo exige pessoa autenticada (core.user_context.require_user).
# Identidade sintetica + repositorio de chat neutralizado, mesmo padrao de
# tests/fii_rich_preview.py: sem isso a aba inteira morre com PermissionError.
st.session_state["_app4_user"] = {
    "id": "11111111-1111-1111-1111-111111111111",
    "expires_at": time.time() + 600,
}
chat_repo.load = lambda *a, **k: []
chat_repo.save = lambda *a, **k: None
chat_repo.clear = lambda *a, **k: None

view._yf_precos = lambda tk: pd.DataFrame()
# Ticker fictício: neutraliza a guarda de universo, que consulta o banco.
view._universo_b3_tickers = lambda: ()
db.market_active = lambda: True
db.load_demonstracoes = lambda *a, **k: pd.DataFrame()
db.load_multiplos_historico = lambda *a, **k: pd.DataFrame()
"""

_EPILOGO = """
st.session_state["b3_ticker_sel"] = "TESTE3"
view._tab_analise(pd.DataFrame())
"""


def _rodar(corpo: str):
    """Roda a aba de análise isolada do banco e devolve (app, texto)."""
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string(_PREAMBULO + corpo + _EPILOGO).run(timeout=60)
    assert not app.exception
    texto = "\n".join(item.value for item in app.markdown)
    # O isolamento é afirmado pelo comportamento, não pela leitura do
    # preâmbulo: sem esta linha o teste mediu outra coisa.
    assert "BANCO_CORTADO=database_unavailable" in texto
    return app, texto


def test_ui_distingue_falha_de_rede_de_ausencia_real_na_cotacao():
    """Com status='falha_rede', o rótulo ao lado da cotação não pode mais
    ser o texto estático antigo — precisa indicar problema de rede."""
    _, texto = _rodar("""
view._preco_atual_com_status = lambda tk: (None, "falha_rede")
view._dividendos_anuais_market_first = lambda tk: (pd.DataFrame(), "yfinance")
db.load_multiplos = lambda *a, **k: pd.Series(dtype=object)
""")

    assert "Falha de rede" in texto or "falha de rede" in texto.lower()
    assert "Cotação (yfinance)" not in texto or "rede" in texto.lower()


def test_ui_usa_fonte_market_first_para_dividendos_quando_disponivel():
    """Com `_dividendos_anuais_market_first` devolvendo market.dividends, a
    fonte precisa aparecer na UI (não pode silenciosamente continuar
    yfinance-only)."""
    _, texto = _rodar("""
df_divs = pd.DataFrame({
    "Data": pd.to_datetime(["2023-12-31", "2024-12-31"]),
    "Dividendos": [1.5, 2.0],
})

view._preco_atual_com_status = lambda tk: (10.0, "ok")
view._dividendos_anuais_market_first = lambda tk: (df_divs, "market.dividends")
db.load_multiplos = lambda *a, **k: pd.Series({"P/L": 10.0})
""")

    assert "market.dividends" in texto
