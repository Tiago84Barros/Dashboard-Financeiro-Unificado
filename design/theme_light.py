"""Camada clara por página/sessão; não muda config global do Streamlit.

O que é desenhado em canvas não obedece a CSS: gráficos e tabelas passam pelos
adaptadores de ``design/tema_canvas.py`` (Plotly/Vega) e
``design/tabela_clara.py`` (``st.dataframe`` reemitido como HTML). Segue
escuro só o que depende da grade nativa: ``st.data_editor`` e as tabelas que o
HTML não dá conta (seleção, tamanho).
"""
from design.tabela_clara import CSS_TABELA

LIGHT_CSS = """
<style>
:root {
 --app-bg:#f5f7fb; --app-surface:#ffffff; --app-surface-raised:#eef2f7;
 --app-border:#d1d9e4; --app-border-strong:#a5b4c7;
 --app-text:#172033; --app-muted:#46566e; --app-subtle:#52627a;
 --app-primary:#007e60; --app-info:#175eac; --app-danger:#b42342;
 --app-warning:#875e00; --app-accent:#6d28d9; --app-shadow:0 8px 24px rgba(30,45,70,.08);
 color-scheme:light;
}
.stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
 background:var(--app-bg)!important; color:var(--app-text)!important;
}
[data-testid="stHeader"], [data-testid="stBottom"],
[data-testid="stBottomBlockContainer"] {
 background:var(--app-bg)!important; color:var(--app-text)!important;
}
/* Ícones e rótulos do cabeçalho (status "Stop", Share, ajuda, menu) chegam
   em branco do tema do config e somem na barra clara. `currentColor` cobre
   o SVG que herda a cor; quem já vem preto não muda. */
[data-testid="stHeader"] :is(button,span,svg,path,circle),
[data-testid="stToolbar"] :is(button,span,svg,path,circle) {
 color:var(--app-text)!important;
}
[data-testid="stHeader"] svg, [data-testid="stToolbar"] svg {fill:currentColor!important;}
/* Enquanto um recálculo longo roda, o Streamlit desbota o render anterior
   (`opacity:.33`). No escuro mal aparece; no branco a tela inteira parece
   apagada e o usuário lê isso como defeito. Desbotar menos mantém o aviso —
   quem diz que está rodando é o status do cabeçalho, agora visível. */
[data-testid="stElementContainer"][data-stale="true"] {opacity:.88!important;}
[data-testid="stSidebar"], [data-testid="stSidebarContent"] {
 background:#edf2f8!important; color:var(--app-text)!important;
}
[data-testid="stMarkdownContainer"] :is(p,li,h1,h2,h3,h4,h5,h6),
[data-testid="stWidgetLabel"], [data-testid="stWidgetLabel"] p,
[data-testid="stMetricValue"], [data-testid="stMetricLabel"],
[data-testid="stExpander"] summary, [data-testid="stCaptionContainer"] {
 color:var(--app-text)!important;
}
[data-testid="stCaptionContainer"] p {color:var(--app-muted)!important;}
[data-testid="stCaption"], [data-testid="stCaption"] p {color:var(--app-muted)!important;}
/* "200MB per file" herda rgba(250,250,250,.6) e some no dropzone branco. */
[data-testid="stFileUploaderDropzoneInstructions"] :is(span,small) {
 color:var(--app-muted)!important;
}
[data-testid="stExpander"] :is(details,summary) {
 background:var(--app-surface)!important; color:var(--app-text)!important;
}
[data-testid="stTextInputRootElement"], [data-testid="stTextInputField"],
[data-testid="stNumberInput"] input,
[data-testid="stSelectbox"] [role="group"],
[data-testid="stSelectbox"] [role="combobox"],
[data-testid="stSelectbox"] button, [role="listbox"], [role="option"] {
 background:#fff!important; color:var(--app-text)!important;
 border-color:var(--app-border)!important;
}
/* Entre `stBottom` e `stBottomBlockContainer` ha uma div sem testid que fica
   com o fundo do config (escuro): era uma faixa preta atravessando o rodape
   das telas com chat. A seta de enviar herda o branco translucido e some. */
[data-testid="stBottom"] > div {background:var(--app-bg)!important;}
[data-testid="stChatInputSubmitButton"] {color:var(--app-muted)!important;}
/* A casca interna do campo de chat tem fundo proprio (escuro) e sobrevivia
   mesmo com o `stChatInput` pintado de branco: era uma caixa preta no rodape. */
[data-testid="stChatInput"] > div {
 background-color:var(--app-surface)!important; border-color:var(--app-border)!important;
}
[data-testid="stSidebarCollapseButton"] button {color:var(--app-text)!important;}
[data-testid="stSidebar"] .stRadio > div > label {
 color:var(--app-muted)!important;
}
[data-testid="stSidebar"] .stRadio > div > label:has(input:checked) {
 color:#005c46!important; background:#d8f3e9!important;
}
[data-testid="stMetric"], [data-testid="metric-container"],
[data-testid="stExpander"], [data-testid="stChatMessage"],
[data-testid="stFileUploaderDropzone"], [data-testid="stAlert"],
[data-testid="stForm"] {
 background:var(--app-surface)!important; border-color:var(--app-border)!important;
 color:var(--app-text)!important;
}
[data-baseweb="input"], [data-baseweb="input"] input,
[data-baseweb="base-input"],
[data-baseweb="textarea"], textarea, [data-baseweb="select"] > div,
[data-baseweb="select"] input, [data-baseweb="popover"] [role="listbox"],
[data-baseweb="popover"] [role="option"], [data-baseweb="menu"],
[data-baseweb="calendar"], [data-testid="stChatInput"] {
 background:#fff!important; color:var(--app-text)!important;
 border-color:var(--app-border)!important; caret-color:var(--app-text)!important;
}
input::placeholder, textarea::placeholder {color:#64748b!important;}
[data-baseweb="tab"] {color:var(--app-muted)!important;}
[data-baseweb="tab"][aria-selected="true"] {color:#00694f!important;}
/* ``^=`` e não ``=``: o botão de um ``st.form`` chega como
   kind="secondaryFormSubmit" / "primaryFormSubmit", e a igualdade exata deixava
   "Alterar minha senha" e "Cadastrar usuário" com fundo escuro e texto escuro
   em cima da página clara -- ilegível. */
button[kind^="secondary"], button[kind^="tertiary"],
[data-testid="stPopoverButton"] {
 background:#fff!important; color:var(--app-text)!important;
 border-color:var(--app-border)!important;
}
button[kind^="secondary"] p, button[kind^="tertiary"] p {color:var(--app-text)!important;}
button[kind^="primary"] {background:#007e60!important;color:#fff!important;}
/* `st.link_button` sai como <a>, nao como <button>: as regras por `kind^=`
   acima nao o alcancam e ele ficava preto com texto branco na pagina clara. */
a[data-testid="stBaseLinkButton-secondary"],
a[data-testid="stBaseLinkButton-tertiary"] {
 background:#fff!important; color:var(--app-text)!important;
 border-color:var(--app-border)!important;
}
/* Caixa, bolinha, chave e barra nativas sao desenhadas com o tema do config
   (escuro): na pagina branca viram quadrado e ponto pretos, chave invisivel e
   trilho preto. Sempre `background-color`, nunca o atalho `background`: a
   marca de "marcado" chega como background-image e o atalho a apagaria. */
[data-baseweb="checkbox"] > span:first-child {
 background-color:#fff!important; border-color:var(--app-border-strong)!important;
}
[data-baseweb="checkbox"]:has(input:checked) > span:first-child {
 background-color:var(--app-primary)!important; border-color:var(--app-primary)!important;
}
[data-baseweb="checkbox"] > div:first-child {
 background-color:var(--app-border-strong)!important;
}
[data-baseweb="checkbox"]:has(input:checked) > div:first-child {
 background-color:var(--app-primary)!important;
}
[data-baseweb="radio"] > div:first-child > div {background-color:#fff!important;}
[data-baseweb="radio"]:has(input:checked) > div:first-child {
 background-color:var(--app-primary)!important;
}
[data-baseweb="radio"]:has(input:checked) > div:first-child > div {
 background-color:#fff!important;
}
[data-testid="stNumberInputStepUp"], [data-testid="stNumberInputStepDown"] {
 background-color:var(--app-surface-raised)!important; color:var(--app-text)!important;
}
[data-baseweb="progress-bar"], [data-baseweb="progress-bar"] > div > div {
 background-color:var(--app-surface-raised)!important;
}
/* `design/tema.py` pinta o trilho da barra com #2D3748 fixo; no claro sobra
   uma faixa escura sob o preenchimento verde. */
.stProgress > div {background-color:var(--app-surface-raised)!important;}
button[kind^="primary"] p {color:#fff!important;}
/* Olho de "mostrar senha": ícone herda o branco do tema escuro e some no campo
   branco. */
[data-testid="stTextInputRootElement"] button,
[data-baseweb="input"] button {
 background:transparent!important; color:var(--app-muted)!important;
}
button:disabled {opacity:.55;}
:is(input,button,textarea,[role="combobox"]):focus-visible {
 outline:2px solid #175eac!important; outline-offset:2px;
}
.app-brand, .app-page-hero, .app-page-meta,
.b3-card, .apb3-kpi, .apb3-macro, .apb3-logo-item,
.fii-selection-card {
 background:var(--app-surface)!important; border-color:var(--app-border)!important;
 box-shadow:var(--app-shadow); color:var(--app-text)!important;
}
/* A placa do logo recebe a imagem como `background-image` inline (ver
   design/market_companies.py::company_logo_html). O atalho `background` com
   !important zera essa imagem — estilo inline perde para !important — e a
   placa ficava vazia no claro. Aqui muda só a cor de fundo. */
.b3-card-logo-wrap {
 background-color:var(--app-surface-raised)!important;
 color:var(--app-muted)!important;
}
.app-brand-title, .app-page-title-row h1, .b3-card-ticker,
.apb3-kpi-val, .apb3-macro-val, .apb3-logo-ticker {
 color:var(--app-text)!important;
}
.app-brand-subtitle, .app-page-subtitle, .nav-section, .b3-card-nome,
.b3-card-tag, .apb3-kpi-label, .apb3-kpi-sub, .apb3-macro-lbl,
.apb3-logo-weight {color:var(--app-muted)!important;}
.apb3-macro-up {color:#087548!important;}
.apb3-macro-dn {color:#b42342!important;}
/* Gráficos: as cores dos dados vêm de design/tema_canvas.py. O fundo fica
   transparente de propósito -- muitos já moram dentro de um card, e uma
   segunda caixa branca com borda viraria moldura dentro de moldura. */
[data-testid="stPlotlyChart"], [data-testid="stVegaLiteChart"] {
 background:transparent;
}
/* A grade do dataframe/editor é canvas: o Streamlit a desenha com o tema do
   config (escuro). A moldura clara evita a borda preta solta na página. */
[data-testid="stDataFrame"], [data-testid="stDataEditor"] {
 background:var(--app-surface); border:1px solid var(--app-border);
 border-radius:10px; padding:4px;
}
[data-testid="stCode"] {background:#0e1117;border-radius:10px;}
</style>
"""

LIGHT_CSS = LIGHT_CSS.replace("</style>", CSS_TABELA + "</style>")
