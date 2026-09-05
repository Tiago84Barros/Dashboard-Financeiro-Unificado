"""
app.py — Dashboard Financeiro Unificado
Ponto de entrada principal. Configura a página, aplica o tema e roteia para os módulos.

Roteamento: lazy imports manuais por branch.
Motivo: isolamento de erros por módulo e compatibilidade futura com autenticação.
"""
import importlib
import logging

import streamlit as st

from core.app_test_mode import is_app_test_mode, module_for_route

logger = logging.getLogger(__name__)

MSG_ERRO_GENERICO_AO_CARREGAR_MODULO = (
    "Não foi possível carregar este módulo. Tente novamente em instantes."
)

st.set_page_config(
    page_title="Dashboard Financeiro Unificado",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

_APP_TEST_MODE = is_app_test_mode()

# O modo sintético é resolvido antes de importar configuração/autenticação ou
# qualquer view financeira. Assim a validação visual não lê .env, banco, rede ou
# artefatos de dados reais. O caminho normal permanece inalterado.
if not _APP_TEST_MODE:
    from core.auth import verificar_autenticacao
    from core.config import settings
    from design.componentes import mensagem_erro
    from design.tema import aplicar_tema

    aplicar_tema()
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
    "🌐 Portfólio Global":    "portfolio_global",
    "🧭 Inteligência de Mercado": "inteligencia_mercado",
    "🌍 Macro Internacional": "macro_internacional",
    "🎯 Grau de Confiança":  "confianca",
    "🚦 Homologação":         "homologacao",
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

    st.markdown('<div class="nav-section">Navegação</div>', unsafe_allow_html=True)
    if _APP_TEST_MODE:
        # Essas são as únicas rotas permitidas: todas resolvem para a view em
        # memória ``views.app_test_mode`` antes que qualquer view real seja
        # importada.
        opcoes_menu = [
            "🏢 Empresas B3",
            "🌎 Empresas Americanas",
            "🏬 Seleção de FIIs",
            "🌐 Portfólio Global",
        ]
    else:
        opcoes_visao = ["📊 Dashboard Geral"]
        opcoes_financas = ["💰 Controle Financeiro"]
        opcoes_invest = ["📈 Investimentos", "🏢 Empresas B3",
                         "🌎 Empresas Americanas",
                         "🏬 Seleção de FIIs",
                         "🌐 Portfólio Global",
                         "🧭 Inteligência de Mercado", "🌍 Macro Internacional"]
        # "Grau de Confiança" e "Homologação" tinham rota em ``_ROTAS`` e não
        # tinham entrada aqui: nasceram inalcançáveis pela sidebar, cada uma
        # no próprio commit que a criou. Motor de diagnóstico que ninguém
        # consulta é decoração (``memoria: diagnostico-precisa-porta-de-entrada``),
        # e é a tela de Homologação que diz em que fase o APP4 está --
        # exatamente o que não podia ficar escondido.
        opcoes_sistema = ["🎯 Grau de Confiança", "🚦 Homologação",
                          "📚 Documentação", "⚙️ Configurações"]
        opcoes_menu = opcoes_visao + opcoes_financas + opcoes_invest + opcoes_sistema

    menu = st.radio(
        "Navegação",
        opcoes_menu,
        label_visibility="collapsed",
        key="app_main_navigation",
    )

    avisos = [] if _APP_TEST_MODE else settings.validate()
    if avisos:
        st.divider()
        st.markdown('<div class="nav-section">Ambiente</div>', unsafe_allow_html=True)
        for aviso in avisos:
            st.caption(f"⚠️ {aviso}")

# ── Roteamento ────────────────────────────────────────────────────────────────
modulo_nome = _ROTAS.get(menu)

if modulo_nome:
    try:
        modulo = importlib.import_module(f"views.{module_for_route(modulo_nome)}")
        modulo.render()
    except Exception as exc:  # noqa: BLE001 - fronteira de isolamento entre rotas
        if _APP_TEST_MODE:
            st.error(f'Erro ao carregar o modo sintético "{menu}": {exc}')
        else:
            # O detalhe tecnico completo (driver, host, porta, stack) vai so
            # para o log — nao para a tela. A pessoa usuaria ve uma mensagem
            # amigavel; quem opera o app le o log para diagnosticar. Ver
            # achado A-013 (vazamento de excecao crua ao usuario final).
            logger.exception('Erro ao carregar o modulo "%s"', menu)
            mensagem_erro(
                f'Erro ao carregar o módulo "{menu}"',
                MSG_ERRO_GENERICO_AO_CARREGAR_MODULO,
            )
else:
    st.warning(f'Rota não encontrada para "{menu}".')
