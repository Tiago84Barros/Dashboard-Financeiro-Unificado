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
    # 🌎 e não 🇺🇸: Windows não renderiza emoji de bandeira (vira as letras "US")
    "🌎 Empresas Americanas": "empresas_americanas",
    "🏬 Seleção de FIIs":      "fiis",
    "📚 Documentação":        "documentacao",
    "⚙️ Configurações":       "configuracoes",
}

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(
        '<div class="app-brand">'
        '<div class="app-brand-mark" aria-hidden="true">📊</div>'
        '<div class="app-brand-title">Dashboard Financeiro</div>'
        '<div class="app-brand-subtitle">Visão unificada do seu caixa e dos investimentos</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    opcoes_visao = ["📊 Dashboard Geral"]
    opcoes_financas = ["💰 Controle Financeiro"]
    opcoes_invest = ["📈 Investimentos", "🏢 Empresas B3",
                     "🌎 Empresas Americanas",
                     "🏬 Seleção de FIIs"]
    opcoes_sistema = ["📚 Documentação", "⚙️ Configurações"]

    st.markdown('<div class="nav-section">Navegação</div>', unsafe_allow_html=True)
    menu = st.radio(
        "Navegação",
        opcoes_visao + opcoes_financas + opcoes_invest + opcoes_sistema,
        label_visibility="collapsed",
        key="app_main_navigation",
    )

    avisos = settings.validate()
    if avisos:
        st.divider()
        st.markdown('<div class="nav-section">Ambiente</div>', unsafe_allow_html=True)
        for aviso in avisos:
            st.caption(f"⚠️ {aviso}")

# ── Roteamento ────────────────────────────────────────────────────────────────
modulo_nome = _ROTAS.get(menu)

if modulo_nome:
    try:
        modulo = importlib.import_module(f"views.{modulo_nome}")
        modulo.render()
    except Exception as exc:  # noqa: BLE001 - fronteira de isolamento entre rotas
        mensagem_erro(
            f'Erro ao carregar o módulo "{menu}"',
            str(exc),
        )
else:
    st.warning(f'Rota não encontrada para "{menu}".')
