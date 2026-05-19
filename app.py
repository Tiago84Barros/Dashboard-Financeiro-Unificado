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
    "📊 Dashboard Geral":     "dashboard_geral",
    "💰 Controle Financeiro": "controle_financeiro",
    "📈 Investimentos":       "investimentos",
    "🏢 Empresas B3":         "empresas_b3",
    "⚙️ Configurações":       "configuracoes",
}


def _render_sidebar_pipeline_status() -> None:
    """Banner compacto na sidebar — mostra alerta apenas se dados estiverem desatualizados."""
    try:
        from data_pipeline.utils.db_utils import table_exists
        if not table_exists("data_freshness_status"):
            return
        from data_pipeline.orchestrator import get_outdated_sources
        outdated = get_outdated_sources()
        if not outdated:
            return
        st.divider()
        nomes = [s.get("source_name", "?") for s in outdated[:3]]
        label = f"{len(outdated)} fonte(s) desatualizada(s)"
        st.warning(f"🔴 {label}", icon=None)
        for n in nomes:
            st.caption(f"  · {n}")
        if len(outdated) > 3:
            st.caption(f"  · e mais {len(outdated) - 3}…")
        st.caption("Atualize em ⚙️ Configurações › Importação de Dados")
    except Exception:
        pass  # Banner é opcional — nunca quebra o app


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 📊 Dashboard Financeiro")
    st.caption("v0.7.0 · Reconciliação")
    st.divider()

    # Seção: Visão Geral
    st.markdown('<div class="nav-section">Visão Geral</div>', unsafe_allow_html=True)
    opcoes_visao = ["📊 Dashboard Geral"]

    # Seção: Finanças
    st.markdown('<div class="nav-section">Finanças</div>', unsafe_allow_html=True)
    opcoes_financas = ["💰 Controle Financeiro"]

    # Seção: Investimentos
    st.markdown('<div class="nav-section">Investimentos</div>', unsafe_allow_html=True)
    opcoes_invest = ["📈 Investimentos", "🏢 Empresas B3"]

    # Seção: Sistema
    st.markdown('<div class="nav-section">Sistema</div>', unsafe_allow_html=True)
    opcoes_sistema = ["⚙️ Configurações"]

    todas_opcoes = (
        opcoes_visao
        + opcoes_financas
        + opcoes_invest
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

    # Banner discreto de status do pipeline (só aparece se houver fontes desatualizadas)
    _render_sidebar_pipeline_status()


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
