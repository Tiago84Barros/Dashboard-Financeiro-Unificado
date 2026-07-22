"""
design/tema.py
CSS customizado para o Dashboard Financeiro Unificado.
Aplicado uma única vez em app.py via aplicar_tema().

Convenções de cor:
  #0E1117  background principal
  #1A1F2E  background secundário (cards, sidebar)
  #12161F  background sidebar (mais escuro)
  #2D3748  bordas e divisores
  #00C896  verde primário (positivo, destaque)
  #FC5C7D  vermelho (negativo, alerta)
  #F6C90E  amarelo (aviso)
  #4A9EFF  azul (info, neutro)
  #9CA3AF  texto secundário
"""
import streamlit as st

_CSS = """
<style>

/* ═══════════════════════════════════════════════
   SIDEBAR
═══════════════════════════════════════════════ */
[data-testid="stSidebar"] {
    background-color: #12161F;
    border-right: 1px solid #1E2533;
}
[data-testid="stSidebar"] hr {
    border-color: #2D3748 !important;
    margin: 8px 0;
}

/* Rótulos de seção da sidebar */
.nav-section {
    font-size: 0.68rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.10em;
    color: #4A5568;
    padding: 12px 4px 4px 4px;
}

/* Radio de navegação — esconde os bullets e estiliza como nav */
[data-testid="stSidebar"] .stRadio > div {
    gap: 2px;
}
[data-testid="stSidebar"] .stRadio > div > label {
    display: flex !important;
    align-items: center;
    padding: 8px 10px;
    border-radius: 8px;
    cursor: pointer;
    color: #9CA3AF;
    font-size: 0.9rem;
    transition: background 0.15s, color 0.15s;
    border: 1px solid transparent;
}
[data-testid="stSidebar"] .stRadio > div > label:hover {
    background: #1A2035;
    color: #E2E8F0;
}
[data-testid="stSidebar"] .stRadio > div > label:has(input:checked) {
    background: rgba(0, 200, 150, 0.10);
    color: #00C896;
    font-weight: 600;
    border-color: rgba(0, 200, 150, 0.20);
}
/* Esconde o círculo do radio */
[data-testid="stSidebar"] .stRadio > div > label > div:first-child {
    width: 0 !important;
    min-width: 0 !important;
    height: 0 !important;
    overflow: hidden;
    padding: 0 !important;
    margin: 0 !important;
}
/* Esconde label "Navegação" acima do radio */
[data-testid="stSidebar"] .stRadio > label {
    display: none;
}

/* Subnavegação persistente do Controle Financeiro.
   Mantém o estado do segmented_control, mas replica o visual de st.tabs
   usado em Investimentos: fundo transparente, divisor e aba ativa sublinhada. */
.st-key-cf_secao_ativa,
.st-key-cf_secao_ativa [data-testid="stButtonGroup"] {
    width: 100%;
}
.st-key-cf_secao_ativa [data-baseweb="button-group"] {
    display: flex;
    justify-content: flex-start;
    width: 100%;
    max-width: 100%;
    overflow-x: auto;
    flex-wrap: nowrap;
    gap: 0;
    border-bottom: 1px solid #2D3748;
}
.st-key-cf_secao_ativa [data-baseweb="button-group"] > button {
    flex: 0 0 auto;
    min-height: 2.5rem;
    margin: 0 1.25rem 0 0 !important;
    padding: 0.5rem 0.2rem 0.65rem !important;
    background: transparent !important;
    border: 0 !important;
    border-bottom: 3px solid transparent !important;
    border-radius: 0 !important;
    color: #E2E8F0 !important;
    box-shadow: none !important;
    font-weight: 500 !important;
    white-space: nowrap;
}
.st-key-cf_secao_ativa [data-baseweb="button-group"] > button:hover {
    color: #00C896 !important;
}
.st-key-cf_secao_ativa [data-baseweb="button-group"] > [data-testid="stBaseButton-segmented_controlActive"] {
    color: #00C896 !important;
    border-bottom-color: #00C896 !important;
}
.st-key-cf_secao_ativa [data-baseweb="button-group"] > button:focus-visible {
    outline: 2px solid #4A9EFF !important;
    outline-offset: -2px;
}

/* ═══════════════════════════════════════════════
   CARDS DE MÉTRICAS (st.metric)
═══════════════════════════════════════════════ */
[data-testid="metric-container"] {
    background: #1A1F2E;
    border: 1px solid #2D3748;
    border-radius: 12px;
    padding: 18px 20px 14px 20px;
    transition: border-color 0.2s;
}
[data-testid="metric-container"]:hover {
    border-color: #3D4F6B;
}
[data-testid="metric-container"] [data-testid="stMetricLabel"] {
    font-size: 0.78rem;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: #718096;
}
[data-testid="metric-container"] [data-testid="stMetricValue"] {
    font-size: 1.55rem;
    font-weight: 700;
    color: #F7FAFC;
    line-height: 1.2;
}
[data-testid="metric-container"] [data-testid="stMetricDelta"] {
    font-size: 0.82rem;
    font-weight: 600;
}

/* ═══════════════════════════════════════════════
   DIVISORES
═══════════════════════════════════════════════ */
hr {
    border-color: #2D3748 !important;
    margin: 16px 0 !important;
}

/* ═══════════════════════════════════════════════
   BOTÕES
═══════════════════════════════════════════════ */
.stButton > button[kind="primary"] {
    background-color: #00C896;
    color: #0A0F0C;
    font-weight: 700;
    border: none;
    border-radius: 8px;
}
.stButton > button[kind="primary"]:hover {
    background-color: #00A87E;
    color: #0A0F0C;
}

/* ═══════════════════════════════════════════════
   TABELAS / DATAFRAMES
═══════════════════════════════════════════════ */
[data-testid="stDataFrame"] {
    border: 1px solid #2D3748;
    border-radius: 8px;
    overflow: hidden;
}

/* ═══════════════════════════════════════════════
   BARRAS DE PROGRESSO
═══════════════════════════════════════════════ */
.stProgress > div > div {
    background-color: #00C896;
    border-radius: 4px;
}
.stProgress > div {
    background-color: #2D3748;
    border-radius: 4px;
}

/* ═══════════════════════════════════════════════
   ALERTAS / MENSAGENS
═══════════════════════════════════════════════ */
[data-testid="stAlert"] {
    border-radius: 10px;
}

/* ═══════════════════════════════════════════════
   TÍTULOS DE PÁGINA
═══════════════════════════════════════════════ */
.page-header {
    padding-bottom: 4px;
    border-bottom: 2px solid #00C896;
    margin-bottom: 4px;
    display: inline-block;
}

/* ═══════════════════════════════════════════════
   ESTADO VAZIO
═══════════════════════════════════════════════ */
.empty-state {
    text-align: center;
    padding: 48px 24px;
    color: #718096;
}
.empty-state .empty-icon {
    font-size: 2.8rem;
    margin-bottom: 12px;
}
.empty-state .empty-text {
    font-size: 0.95rem;
}

</style>
"""


def aplicar_tema() -> None:
    """
    Injeta o CSS customizado no app.
    Deve ser chamado uma única vez, no início de app.py,
    após st.set_page_config().
    """
    st.markdown(_CSS, unsafe_allow_html=True)
