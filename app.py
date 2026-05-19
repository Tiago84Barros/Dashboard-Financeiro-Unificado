"""
app.py — Dashboard Financeiro Unificado
Ponto de entrada principal. Configura a página, aplica o tema e roteia para os módulos.

Roteamento: lazy imports manuais por branch.
Motivo: isolamento de erros por módulo e compatibilidade futura com autenticação.
"""
import importlib

import streamlit as st
from core.auth import verificar_autenticacao
from core.config import settings
from design.componentes import mensagem_erro
from design.tema import aplicar_tema

st.set_page_config(
    page_title="Dashboard Financeiro Unificado",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Tema visual ───────────────────────────────────────────────────────────────
aplicar_tema()

# ── Autenticacao ──────────────────────────────────────────────────────────────
verificar_autenticacao()

# ── Mapeamento: label da sidebar → módulo em views/ ──────────────────────────
_ROTAS: dict[str, str] = {
    "📊 Dashboard Geral":     "dashboard_geral",
    "💰 Controle Financeiro": "controle_financeiro",
    "📈 Investimentos":       "investimentos",
    "🏢 Empresas B3":         "empresas_b3",
    "⚙️ Configurações":       "configuracoes",
}

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 📊 Dashboard Financeiro")
    st.caption("v0.7.0 · Reconciliação")
    st.divider()

    st.markdown('<div class="nav-section">Visão Geral</div>', unsafe_allow_html=True)
    opcoes_visao = ["📊 Dashboard Geral"]

    st.markdown('<div class="nav-section">Finanças</div>', unsafe_allow_html=True)
    opcoes_financas = ["💰 Controle Financeiro"]

    st.markdown('<div class="nav-section">Investimentos</div>', unsafe_allow_html=True)
    opcoes_invest = ["📈 Investimentos", "🏢 Empresas B3"]

    st.markdown('<div class="nav-section">Sistema</div>', unsafe_allow_html=True)
    opcoes_sistema = ["⚙️ Configurações"]

    menu = st.radio(
        "Navegação",
        opcoes_visao + opcoes_financas + opcoes_invest + opcoes_sistema,
        label_visibility="collapsed",
    )

    avisos = settings.validate()
    if avisos:
        st.divider()
        for aviso in avisos:
            st.caption(f"⚠️ {aviso}")

# ── Roteamento ────────────────────────────────────────────────────────────────
modulo_nome = _ROTAS.get(menu)

if modulo_nome:
    try:
        modulo = importlib.import_module(f"views.{modulo_nome}")
        modulo.render()
    except Exception as exc:
        mensagem_erro(
            f'Erro ao carregar o módulo "{menu}"',
            str(exc),
        )
else:
    st.warning(f'Rota não encontrada para "{menu}".')
