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

/* ═══════════════════════════════════════════════
   DESIGN SYSTEM GLOBAL — FINTECH ANALÍTICA V4
═══════════════════════════════════════════════ */
:root {
    --app-bg: #0E1117;
    --app-surface: #121722;
    --app-surface-raised: #171D2B;
    --app-border: rgba(148, 163, 184, 0.14);
    --app-border-strong: rgba(148, 163, 184, 0.24);
    --app-text: #F8FAFC;
    --app-muted: #94A3B8;
    --app-subtle: #64748B;
    --app-primary: #00C896;
    --app-info: #4A9EFF;
    --app-danger: #FC5C7D;
    --app-warning: #F6C90E;
    --app-radius-sm: 10px;
    --app-radius-md: 14px;
    --app-radius-lg: 20px;
    --app-shadow: 0 12px 34px rgba(0, 0, 0, 0.22);
}

.stApp {
    background:
        radial-gradient(circle at 78% -12%, rgba(74,158,255,.055), transparent 29rem),
        var(--app-bg);
}
[data-testid="stMainBlockContainer"] {
    max-width: 1560px;
    padding-top: 2rem;
    padding-bottom: 4rem;
}

/* Marca e navegação */
.app-brand {
    position: relative;
    overflow: hidden;
    padding: 15px 14px;
    border: 1px solid var(--app-border);
    border-radius: 15px;
    background:
        radial-gradient(circle at 95% 0%, rgba(74,158,255,.16), transparent 48%),
        linear-gradient(145deg, rgba(25,31,47,.96), rgba(16,20,30,.96));
    box-shadow: 0 10px 28px rgba(0,0,0,.24);
}
.app-brand-mark {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 34px;
    height: 34px;
    border: 1px solid rgba(0,200,150,.28);
    border-radius: 10px;
    background: rgba(0,200,150,.10);
    margin-bottom: 10px;
}
.app-brand-title {
    color: var(--app-text);
    font-size: .96rem;
    font-weight: 780;
    letter-spacing: -.015em;
}
.app-brand-subtitle {
    color: var(--app-subtle);
    font-size: .69rem;
    line-height: 1.45;
    margin-top: 3px;
}
[data-testid="stSidebar"] {
    background:
        linear-gradient(180deg, rgba(17,22,32,.99), rgba(13,17,25,.99));
    border-right-color: var(--app-border);
}
[data-testid="stSidebar"] [data-testid="stSidebarContent"] {
    padding-top: 1.1rem;
}
[data-testid="stSidebar"] .stRadio > div > label {
    min-height: 42px;
    padding: 9px 11px;
    border-radius: 10px;
    color: #A8B3C5;
    font-size: .84rem;
}
[data-testid="stSidebar"] .stRadio > div > label:hover {
    transform: translateX(2px);
    background: rgba(74,158,255,.065);
}
[data-testid="stSidebar"] .stRadio > div > label:has(input:checked) {
    background: linear-gradient(90deg, rgba(0,200,150,.13), rgba(0,200,150,.055));
    color: #D9FFF4;
    border-color: rgba(0,200,150,.22);
    box-shadow: inset 3px 0 0 var(--app-primary);
}

/* Cabeçalho padrão de páginas */
.app-page-hero {
    position: relative;
    overflow: hidden;
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 1.4rem;
    padding: 1.55rem 1.7rem;
    margin: 0 0 1.25rem;
    border: 1px solid var(--app-border);
    border-radius: var(--app-radius-lg);
    background:
        radial-gradient(circle at 90% 5%, rgba(74,158,255,.17), transparent 35%),
        radial-gradient(circle at 7% 115%, rgba(0,200,150,.10), transparent 34%),
        linear-gradient(145deg, #171C2A, #10141E 72%);
    box-shadow: var(--app-shadow), inset 0 1px 0 rgba(255,255,255,.04);
}
.app-page-hero::after {
    content: "";
    position: absolute;
    right: -90px;
    bottom: -135px;
    width: 210px;
    height: 210px;
    border: 1px solid rgba(74,158,255,.18);
    border-radius: 50%;
}
.app-page-copy { position: relative; z-index: 1; min-width: 0; }
.app-page-eyebrow {
    color: #60A5FA;
    font-size: .63rem;
    font-weight: 800;
    letter-spacing: .16em;
    text-transform: uppercase;
    margin-bottom: .48rem;
}
.app-page-title-row {
    display: flex;
    align-items: center;
    gap: .72rem;
}
.app-page-title-row h1 {
    color: var(--app-text);
    font-size: clamp(1.62rem, 2.5vw, 2.18rem);
    font-weight: 820;
    letter-spacing: -.04em;
    line-height: 1.06;
    padding: 0;
    margin: 0;
}
.app-page-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 40px;
    height: 40px;
    flex: 0 0 auto;
    border: 1px solid rgba(74,158,255,.25);
    border-radius: 12px;
    background: rgba(74,158,255,.09);
    font-size: 1.15rem;
}
.app-page-subtitle {
    max-width: 760px;
    color: var(--app-muted);
    font-size: .82rem;
    line-height: 1.55;
    margin: .6rem 0 0;
}
.app-page-meta-group {
    position: relative;
    z-index: 1;
    display: flex;
    justify-content: flex-end;
    flex-wrap: wrap;
    gap: .45rem;
}
.app-page-meta {
    display: inline-flex;
    flex-direction: column;
    gap: .08rem;
    min-width: 112px;
    padding: .5rem .7rem;
    border: 1px solid var(--app-border);
    border-radius: 11px;
    background: rgba(15,23,42,.62);
    color: #D7E0ED;
    font-size: .74rem;
    font-weight: 720;
}
.app-page-meta small {
    color: var(--app-subtle);
    font-size: .55rem;
    font-weight: 760;
    letter-spacing: .08em;
    text-transform: uppercase;
}

/* Títulos de seção */
.app-section-heading {
    display: flex;
    align-items: center;
    gap: .7rem;
    margin: 1.5rem 0 .8rem;
}
.app-section-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    flex: 0 0 auto;
    border: 1px solid rgba(74,158,255,.21);
    border-radius: 10px;
    background: rgba(74,158,255,.075);
}
.app-section-title {
    color: #E7EDF6;
    font-size: .98rem;
    font-weight: 760;
    letter-spacing: -.015em;
}
.app-section-subtitle {
    color: var(--app-subtle);
    font-size: .72rem;
    margin-top: .1rem;
}
.app-kpi-card {
    position: relative;
    overflow: hidden;
    min-height: 100px;
    padding: 14px 16px;
    margin-bottom: 7px;
    border: 1px solid var(--app-border);
    border-radius: 14px;
    background: linear-gradient(155deg, var(--app-surface-raised), var(--app-surface));
    box-shadow: 0 8px 24px rgba(0,0,0,.15);
}
.app-kpi-card::before {
    content: "";
    position: absolute;
    left: 0;
    top: 14px;
    bottom: 14px;
    width: 3px;
    border-radius: 0 3px 3px 0;
    background: var(--app-kpi-accent);
}
.app-kpi-label {
    color: var(--app-muted);
    font-size: .64rem;
    font-weight: 760;
    letter-spacing: .085em;
    text-transform: uppercase;
    margin-bottom: 7px;
}
.app-kpi-value {
    overflow-wrap: anywhere;
    color: var(--app-text);
    font-size: clamp(1.28rem, 2vw, 1.58rem);
    font-weight: 820;
    letter-spacing: -.03em;
    line-height: 1.08;
}
.app-kpi-delta {
    font-size: .7rem;
    font-weight: 720;
    margin-top: 6px;
}
.app-status-badge {
    display: inline-flex;
    align-items: center;
    min-height: 28px;
    padding: 3px 10px;
    border: 1px solid color-mix(in srgb, var(--badge-color) 62%, transparent);
    border-radius: 999px;
    background: var(--badge-bg);
    color: var(--badge-color);
    font-size: .7rem;
    font-weight: 720;
    white-space: nowrap;
}

/* Containers, métricas e gráficos */
[data-testid="stVerticalBlockBorderWrapper"] {
    border-color: var(--app-border) !important;
    border-radius: var(--app-radius-md) !important;
    background: linear-gradient(155deg, rgba(22,27,41,.86), rgba(15,19,28,.88));
    box-shadow: 0 8px 26px rgba(0,0,0,.14);
}
[data-testid="metric-container"] {
    position: relative;
    overflow: hidden;
    background: linear-gradient(155deg, var(--app-surface-raised), var(--app-surface));
    border-color: var(--app-border);
    border-radius: var(--app-radius-md);
    padding: 16px 17px 14px;
    box-shadow: 0 8px 24px rgba(0,0,0,.16);
}
[data-testid="metric-container"]:hover {
    border-color: rgba(74,158,255,.30);
    transform: translateY(-1px);
}
[data-testid="metric-container"] [data-testid="stMetricLabel"] {
    color: var(--app-muted);
    font-size: .67rem;
    font-weight: 760;
    letter-spacing: .08em;
}
[data-testid="metric-container"] [data-testid="stMetricValue"] {
    color: var(--app-text);
    font-size: clamp(1.28rem, 2vw, 1.62rem);
    font-weight: 820;
    letter-spacing: -.035em;
}
[data-testid="stPlotlyChart"] {
    overflow: hidden;
    border-radius: 12px;
}
[data-testid="stDataFrame"] {
    border-color: var(--app-border);
    border-radius: 12px;
    background: rgba(15,19,28,.75);
    box-shadow: 0 6px 20px rgba(0,0,0,.12);
}

/* Tabs e subnavegações */
.stTabs [data-baseweb="tab-list"] {
    gap: .25rem;
    overflow-x: auto;
    border-bottom: 1px solid var(--app-border);
}
.stTabs [data-baseweb="tab"] {
    min-height: 42px;
    padding: .62rem .9rem;
    border-radius: 9px 9px 0 0;
    color: var(--app-muted);
    font-size: .78rem;
    font-weight: 650;
    white-space: nowrap;
}
.stTabs [data-baseweb="tab"]:hover {
    color: #DCE6F4;
    background: rgba(74,158,255,.05);
}
.stTabs [aria-selected="true"] {
    color: var(--app-primary) !important;
    background: rgba(0,200,150,.055) !important;
}
.stTabs [data-baseweb="tab-highlight"] { background-color: var(--app-primary); }

/* ═══════════════════════════════════════════════
   SUB-NAVEGAÇÃO DE SEÇÕES (design.componentes.abas_secao)
   ───────────────────────────────────────────────
   Um único contrato visual para as abas internas do app: o mesmo trilho
   sublinhado das abas nativas (.stTabs, usadas em Investimentos), aplicado ao
   segmented_control — que, ao contrário de st.tabs, aceita `key` (a aba ativa
   sobrevive a reruns) e `on_change` (permite rolar ao topo na troca).
   O seletor casa por prefixo de key: qualquer widget criado por abas_secao
   nasce com a classe st-key-appnav_<nome>, sem CSS novo por tela.
═══════════════════════════════════════════════ */
[class*="st-key-appnav_"],
[class*="st-key-appnav_"] [data-testid="stButtonGroup"] {
    width: 100%;
}
[class*="st-key-appnav_"] [data-baseweb="button-group"] {
    display: flex !important;
    flex-wrap: nowrap;
    gap: .25rem;
    width: 100%;
    max-width: 100%;
    margin: 0 0 1rem;
    padding: 0 !important;
    overflow-x: auto;
    border: 0 !important;
    border-bottom: 1px solid var(--app-border) !important;
    border-radius: 0 !important;
    background: transparent !important;
}
[class*="st-key-appnav_"] [data-baseweb="button-group"] > button {
    flex: 0 0 auto;
    width: auto !important;
    min-width: 0 !important;
    min-height: 42px;
    margin: 0 !important;
    padding: .62rem .9rem !important;
    border: 0 !important;
    border-radius: 9px 9px 0 0 !important;
    background: transparent !important;
    color: var(--app-muted) !important;
    box-shadow: none !important;
    font-size: .78rem;
    font-weight: 650 !important;
    line-height: 1.25;
    white-space: nowrap;
    transition: color .16s ease, background .16s ease;
}
[class*="st-key-appnav_"] [data-baseweb="button-group"] > button:hover {
    color: #DCE6F4 !important;
    background: rgba(74, 158, 255, .05) !important;
}
[class*="st-key-appnav_"] [data-baseweb="button-group"] > [data-testid="stBaseButton-segmented_controlActive"] {
    color: var(--app-primary) !important;
    background: rgba(0, 200, 150, .055) !important;
    box-shadow: inset 0 -2px 0 var(--app-primary) !important;
}
[class*="st-key-appnav_"] [data-baseweb="button-group"] > button:focus-visible {
    outline: 2px solid #4A9EFF !important;
    outline-offset: -2px;
}
/* O iframe de 0px que reposiciona a página no topo não pode abrir espaço. */
[class*="st-key-appnav_"] + div [data-testid="stIFrame"],
[data-testid="stIFrame"][height="0"] {
    display: block;
    height: 0 !important;
    border: 0;
}
@media (max-width: 760px) {
    [class*="st-key-appnav_"] [data-baseweb="button-group"] > button {
        padding-inline: .68rem !important;
    }
}
@media (prefers-reduced-motion: reduce) {
    [class*="st-key-appnav_"] [data-baseweb="button-group"] > button {
        transition: none;
    }
}

/* Formulários e ações */
.stButton > button {
    min-height: 38px;
    border-color: var(--app-border-strong);
    border-radius: 10px;
    background: rgba(23,29,43,.82);
    color: #DCE5F2;
    font-weight: 680;
    transition: transform .15s ease, border-color .15s ease, background .15s ease;
}
.stButton > button:hover {
    border-color: rgba(74,158,255,.42);
    background: rgba(74,158,255,.09);
    color: #F8FAFC;
    transform: translateY(-1px);
}
.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #00C896, #00A982);
    color: #061C15;
    box-shadow: 0 7px 18px rgba(0,200,150,.16);
}
.stButton > button:focus-visible,
[data-baseweb="input"]:focus-within,
[data-baseweb="select"]:focus-within {
    outline: 2px solid var(--app-info) !important;
    outline-offset: 2px;
}
[data-baseweb="input"],
[data-baseweb="select"] > div,
[data-baseweb="textarea"] {
    border-color: var(--app-border) !important;
    border-radius: 10px !important;
    background-color: rgba(18,23,34,.88) !important;
}
[data-testid="stFileUploaderDropzone"] {
    border-color: var(--app-border-strong);
    border-radius: 14px;
    background: rgba(18,23,34,.72);
}

/* Feedback e conteúdo expansível */
[data-testid="stAlert"] {
    border: 1px solid var(--app-border);
    border-radius: 12px;
    background: rgba(18,23,34,.82);
}
[data-testid="stExpander"] {
    overflow: hidden;
    border-color: var(--app-border) !important;
    border-radius: 12px !important;
    background: rgba(18,23,34,.62);
}
[data-testid="stChatMessage"] {
    border: 1px solid var(--app-border);
    border-radius: 14px;
    background: rgba(18,23,34,.68);
}
[data-testid="stCaptionContainer"] { color: var(--app-subtle); }
hr { border-color: var(--app-border) !important; }

@media (max-width: 760px) {
    [data-testid="stMainBlockContainer"] {
        padding-top: 1.1rem;
        padding-left: 1rem;
        padding-right: 1rem;
    }
    .app-page-hero {
        align-items: flex-start;
        flex-direction: column;
        padding: 1.2rem 1.1rem;
        border-radius: 16px;
    }
    .app-page-title-row { align-items: flex-start; }
    .app-page-meta-group { justify-content: flex-start; }
    .app-page-meta { min-width: 100px; }
    .stTabs [data-baseweb="tab"] { padding-inline: .68rem; }
}

@media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
        scroll-behavior: auto !important;
        transition-duration: .01ms !important;
        animation-duration: .01ms !important;
    }
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
