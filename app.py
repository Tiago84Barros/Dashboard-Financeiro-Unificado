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
# Para a execucao se APP_PASSWORD estiver configurado e o usuario nao autenticado.
# Sem APP_PASSWORD configurado: libera acesso (modo dev local).
verificar_autenticacao()

# ── Mapeamento: label da sidebar → módulo em pages/ ──────────────────────────
_ROTAS: dict[str, str] = {
    "📊 Dashboard Geral":         "dashboard_geral",
    "💰 Controle Financeiro":     "controle_financeiro",
    "🎯 Metas":                   "metas",
    "🔔 Alertas":                 "alertas",
    "📈 Investimentos":           "investimentos",
    "💼 Carteira":                "carteira",
    "💵 Proventos":               "proventos",
    "🏢 Empresas B3":             "empresas_b3",
    "🌎 Empresas EUA":            "empresas_eua",
    "🌐 Cenário Macroeconômico":  "macro",
    "⚙️ Configurações":           "configuracoes",
}

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 📊 Dashboard Financeiro")
    st.caption("v0.4.9 · Fase 4")
    st.divider()

    # Seção: Visão Geral
    st.markdown('<div class="nav-section">Visão Geral</div>', unsafe_allow_html=True)
    opcoes_visao = ["📊 Dashboard Geral"]

    # Seção: Finanças
    st.markdown('<div class="nav-section">Finanças</div>', unsafe_allow_html=True)
    opcoes_financas = ["💰 Controle Financeiro", "🎯 Metas", "🔔 Alertas"]

    # Seção: Investimentos
    st.markdown('<div class="nav-section">Investimentos</div>', unsafe_allow_html=True)
    opcoes_invest = ["📈 Investimentos", "💼 Carteira", "💵 Proventos"]

    # Seção: Mercado
    st.markdown('<div class="nav-section">Mercado</div>', unsafe_allow_html=True)
    opcoes_mercado = ["🏢 Empresas B3", "🌎 Empresas EUA", "🌐 Cenário Macroeconômico"]

    # Seção: Sistema
    st.markdown('<div class="nav-section">Sistema</div>', unsafe_allow_html=True)
    opcoes_sistema = ["⚙️ Configurações"]

    todas_opcoes = (
        opcoes_visao
        + opcoes_financas
        + opcoes_invest
        + opcoes_mercado
        + opcoes_sistema
    )

    menu = st.radio(
        "Navegação",
        todas_opcoes,
        label_visibility="collapsed",
    )

    # Avisos de configuração (sem expor credenciais)
    avisos = settings.validate()
    if avisos:
        st.divider()
        for aviso in avisos:
            st.caption(f"⚠️ {aviso}")

# ── Roteamento ────────────────────────────────────────────────────────────────
modulo_nome = _ROTAS.get(menu)

if modulo_nome:
    try:
        modulo = importlib.import_module(f"pages.{modulo_nome}")
        modulo.render()
    except Exception as exc:
        mensagem_erro(
            f'Erro ao carregar o módulo "{menu}"',
            str(exc),
        )
else:
    st.warning(f'Rota não encontrada para "{menu}".')
