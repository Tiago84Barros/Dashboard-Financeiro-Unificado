"""Superfícies que o tema claro precisa alcançar — e as que não pode apagar."""
from __future__ import annotations

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]


def _classes_com_logo_inline() -> set[str]:
    """Classes que recebem a imagem do logo como ``background-image`` inline.

    Sai da estrutura, não de uma lista fixa: a assinatura de
    ``company_logo_html`` dá o padrão e cada chamada dá as variações.
    """
    fonte = (RAIZ / "design" / "market_companies.py").read_text(encoding="utf-8")
    classes = set(re.findall(r'css_class: str = "([\w-]+)"', fonte))
    for arquivo in list((RAIZ / "views").glob("*.py")) + list((RAIZ / "design").glob("*.py")):
        texto = arquivo.read_text(encoding="utf-8")
        if "company_logo_html" in texto:
            classes |= set(re.findall(r'css_class="([\w-]+)"', texto))
    assert classes, "nenhuma classe de logo encontrada — o padrão mudou"
    return classes


def test_tema_claro_nao_apaga_o_logo_com_o_atalho_background():
    """``background:...!important`` zera o ``background-image`` do estilo inline.

    Inline perde para ``!important``: a regra do tema claro pintava a placa e
    levava a imagem junto, e o card ficava com o quadrado vazio.
    """
    from design.theme_light import LIGHT_CSS

    classes = _classes_com_logo_inline()
    for regra in LIGHT_CSS.split("}"):
        if "{" not in regra:
            continue
        seletor, corpo = regra.split("{", 1)
        alvos = {c for c in classes if re.search(rf"\.{re.escape(c)}\b", seletor)}
        if not alvos:
            continue
        assert not re.search(r"(^|[;\s])background\s*:[^;]*!important", corpo), (
            f"{sorted(alvos)}: use background-color; o atalho apaga a imagem do logo"
        )


# Cromo do tema escuro que não pode sobrar em HTML da tela de controle: sobre a
# página clara vira o bloco escuro que o usuário enxerga.
_CROMO_ESCURO = ("#12151E", "#0E1117", "#1E2533", "#1A1F2E", "#2A1A12",
                 "#718096", "#4A5568", "#CBD5E0", "#E2E8F0", "#9CA3AF")


def test_controle_financeiro_nao_pinta_html_com_cromo_escuro():
    fonte = (RAIZ / "views" / "controle_financeiro.py").read_text(encoding="utf-8")
    sujas = [
        f"{n}: {linha.strip()}"
        for n, linha in enumerate(fonte.splitlines(), 1)
        if ("style=" in linha or "<hr" in linha)
        and any(cor.lower() in linha.lower() for cor in _CROMO_ESCURO)
    ]
    assert not sujas, "literais do tema escuro em HTML:\n" + "\n".join(sujas)


def _selectores_escuros_do_tema_base() -> dict[str, str]:
    """Seletores que ``design/tema.py`` pinta com cromo escuro fixo.

    Sai de quem escreve a cor, não de uma lista: o mapa de ``tema_canvas`` já
    enumera o cromo escuro do app, e qualquer regra nova cai aqui sozinha.
    """
    from design.tema_canvas import _CROMO

    escuros = {cor.lower() for cor in _CROMO}
    fonte = (RAIZ / "design" / "tema.py").read_text(encoding="utf-8")
    achados: dict[str, str] = {}
    for seletor, corpo in re.findall(r"([^{}]+)\{([^{}]*)\}", fonte):
        cor = re.search(r"background(?:-color)?\s*:\s*(#[0-9a-fA-F]{3,8})", corpo)
        if cor and cor.group(1).lower() in escuros:
            achados[seletor.strip().splitlines()[-1].strip()] = cor.group(1)
    assert achados, "nenhuma superfície escura encontrada — o padrão de tema.py mudou"
    return achados


def test_tema_claro_cobre_toda_superficie_escura_do_tema_base():
    """O trilho da barra de progresso ficava #2D3748 sob o preenchimento verde.

    O tema claro redefine tokens, mas cor literal cravada em ``tema.py`` não
    depende de token nenhum: só um seletor equivalente no claro a desfaz.
    """
    from design.theme_light import LIGHT_CSS

    faltando = [
        f"{seletor} ({cor})"
        for seletor, cor in _selectores_escuros_do_tema_base().items()
        if seletor not in LIGHT_CSS
    ]
    assert not faltando, f"sem override no tema claro: {faltando}"


def test_tema_claro_pinta_a_casca_interna_do_chat_e_o_rodape():
    """Faixa preta no rodapé: o fundo estava em divs que ninguém alcançava.

    Nem o ``stChatInput`` nem o ``stBottom`` carregam a cor — quem carrega é a
    div interna de cada um, sem ``data-testid`` para servir de alvo.
    """
    from design.theme_light import LIGHT_CSS

    for seletor in ('[data-testid="stBottom"] > div',
                    '[data-testid="stChatInput"] > div',
                    '[data-testid="stChatInputSubmitButton"]'):
        assert seletor in LIGHT_CSS, f"{seletor} sem regra no tema claro"


def test_tema_claro_alcanca_cabecalho_e_o_desbote_de_recalculo():
    """Duas coisas que o usuário lê como "layout quebrado" no claro.

    O cabeçalho (status "Stop", Share, ajuda, menu) vem branco do tema do
    config e some na barra clara. E o Streamlit desbota o render anterior a
    ``opacity:.33`` enquanto recalcula: no escuro passa, no branco a tela
    inteira parece apagada — e a Seleção de FIIs recalcula por minutos.
    """
    from design.theme_light import LIGHT_CSS

    for seletor in ('[data-testid="stHeader"] :is(button,span,svg,path,circle)',
                    '[data-testid="stToolbar"] :is(button,span,svg,path,circle)',
                    '[data-testid="stElementContainer"][data-stale="true"]'):
        assert seletor in LIGHT_CSS, f"{seletor} sem regra no tema claro"


def test_tema_claro_alcanca_a_tabela_markdown_da_resposta_da_llm():
    """Tabela da LLM saía branca no branco, dentro e fora da bolha do chat.

    Duas causas independentes, medidas no preview: ``stChatMessageContent``
    traz ``color:rgb(250,250,250)`` do tema do config e a tabela herdava (o
    parágrafo escapava porque a regra de ``stMarkdownContainer`` enumera ``p``
    e não alcança ``th``/``td``); e a grade nativa é ``rgba(250,250,250,.1)``,
    invisível sobre fundo claro mesmo com o texto legível.
    """
    from design.theme_light import LIGHT_CSS

    for seletor in ('[data-testid="stChatMessageContent"]',
                    '[data-testid="stMarkdownContainer"] :is(table,th,td)',
                    '[data-testid="stMarkdownContainer"] table :is(th,td)',
                    '[data-testid="stMarkdownContainer"] table thead th'):
        assert seletor in LIGHT_CSS, f"{seletor} sem regra no tema claro"


# ── Vega-Lite: os gráficos nativos (line/bar/area/scatter_chart) ──────────────
def _capturar_specs(tema: str) -> list[tuple[dict, dict]]:
    """Roda os gráficos nativos no tema dado e devolve (spec, kwargs) de cada um.

    Espia ``_vega_lite_chart``, que é por onde todo gráfico Vega passa — assim o
    teste mede o que chega ao front-end, não o que o adaptador pretendia fazer.
    """
    import pandas as pd
    import streamlit as st
    from streamlit.delta_generator import DeltaGenerator
    from streamlit.elements.vega_charts import VegaChartsMixin

    from design.tema_canvas import _INSTALADO, instalar_adaptadores, registrar_tema

    capturado: list[tuple[dict, dict]] = []

    def espiao(self, data=None, spec=None, *args, **kwargs):
        alvo = spec if isinstance(spec, dict) else data
        capturado.append((alvo or {}, kwargs))
        return None

    original = VegaChartsMixin._vega_lite_chart
    # `instalar_adaptadores` é idempotente por processo: se outro teste da mesma
    # sessão já instalou, ela sai na primeira linha e o espião ficaria **sem**
    # embrulho -- o teste passava sozinho e falhava na suíte. Zerar a marca faz
    # o embrulho acontecer de novo; o `finally` devolve tudo como estava.
    marca = getattr(st, _INSTALADO, False)
    antes = (DeltaGenerator.plotly_chart, DeltaGenerator.dataframe,
             st.plotly_chart, st.dataframe)
    setattr(st, _INSTALADO, False)
    VegaChartsMixin._vega_lite_chart = espiao
    try:
        instalar_adaptadores()
        registrar_tema(tema)
        df = pd.DataFrame({"v": [1.0, 2.0, 3.0]})
        st.line_chart(df)
        st.bar_chart(df["v"])
        st.area_chart(df)
        st.scatter_chart(df)
    finally:
        VegaChartsMixin._vega_lite_chart = original
        setattr(st, _INSTALADO, marca)
        (DeltaGenerator.plotly_chart, DeltaGenerator.dataframe,
         st.plotly_chart, st.dataframe) = antes
    return capturado


def test_grafico_nativo_no_claro_leva_a_moldura_clara_no_proprio_spec():
    """``theme=None`` não basta: o front-end preenche o que o spec deixa vazio.

    Medido no bundle do Streamlit 1.57 — quando o tema não é ``"streamlit"`` ele
    aplica os padrões do tema do app (que segue ``base="dark"``) sobre o spec.
    Quem não escreve fundo, eixo e rótulo no spec recebe o escuro de volta.
    """
    capturado = _capturar_specs("light")
    assert len(capturado) == 4, "os quatro gráficos nativos precisam passar pelo adaptador"
    for spec, kwargs in capturado:
        assert kwargs.get("theme") is None
        config = spec.get("config") or {}
        assert config.get("background") == "transparent"
        for chave in ("axis", "legend", "title", "header"):
            assert config.get(chave), f"config.{chave} vazio deixa o front-end escurecer"
        assert config["axis"]["labelColor"] == "#172033"
        assert config["axis"]["gridColor"] == "#e3e9f2"


def test_grafico_nativo_no_escuro_continua_com_o_tema_do_streamlit():
    for spec, kwargs in _capturar_specs("dark"):
        assert kwargs.get("theme") == "streamlit"
        assert "background" not in (spec.get("config") or {})


def test_moldura_clara_nao_sobrepoe_a_cor_escolhida_pela_tela():
    """O que a tela já definiu ganha do padrão — senão o adaptador viraria dono."""
    from design.tema_canvas import clarear_spec_vega

    spec = clarear_spec_vega({"mark": "bar", "config": {"axis": {"labelColor": "#123456"}}})
    assert spec["config"]["axis"]["labelColor"] == "#123456"
    assert spec["config"]["axis"]["gridColor"] == "#e3e9f2"


# ── data_editor: o canvas não obedece ao CSS, então precisa de caminho claro ───
def _chamadas_data_editor(fonte: str) -> list[tuple[int, bool]]:
    """Cada ``st.data_editor`` do módulo, com um sinal de se há guarda de tema.

    A guarda é procurada nos ancestrais do nó: serve o ``if no_claro():``, o
    ternário ``... if no_claro() else st.data_editor(...)`` e a função
    ``*_escuro``, que existe só para o ramo escuro e é escolhida no chamador.
    """
    import ast

    arvore = ast.parse(fonte)
    pai: dict[int, ast.AST] = {}
    for no in ast.walk(arvore):
        for filho in ast.iter_child_nodes(no):
            pai[id(filho)] = no

    def guardado(no: ast.AST) -> bool:
        atual = pai.get(id(no))
        while atual is not None:
            teste = getattr(atual, "test", None)
            if teste is not None and "no_claro" in ast.unparse(teste):
                return True
            if (isinstance(atual, ast.FunctionDef)
                    and atual.name.endswith("_escuro")):
                return True
            atual = pai.get(id(atual))
        return False

    achados = []
    for no in ast.walk(arvore):
        if (isinstance(no, ast.Call) and isinstance(no.func, ast.Attribute)
                and no.func.attr == "data_editor"):
            achados.append((no.lineno, guardado(no)))
    return achados


def test_todo_data_editor_tem_caminho_para_o_tema_claro():
    """A grade nativa pinta num canvas que o CSS não alcança.

    Medido injetando as ``--gdg-*`` no container do grid: elas passam a valer e o
    canvas não muda de cor (memória: canvas-do-data-editor-ignora-css). Quem não
    oferece outro caminho no claro entrega um bloco escuro na página branca.
    """
    sem_guarda = []
    for arquivo in sorted((RAIZ / "views").glob("*.py")):
        fonte = arquivo.read_text(encoding="utf-8")
        if "data_editor" not in fonte:
            continue
        sem_guarda += [
            f"{arquivo.name}:{linha}"
            for linha, tem_guarda in _chamadas_data_editor(fonte) if not tem_guarda
        ]
    assert not sem_guarda, (
        "st.data_editor sem alternativa no tema claro: " + ", ".join(sem_guarda)
    )


def test_tema_claro_clareia_o_bloco_de_codigo_com_texto_e_tokens():
    """``st.code`` é prosa no app, e ficava preto no claro.

    Medido em 29/09/2026 no bundle do Streamlit 1.57: o ``pre`` do ``stCode``
    recebe ``background: codeBackgroundColor`` e ``color: bodyText``, e cada
    ``.token.*`` sai de uma cor nomeada do tema — que é escuro no config. Fundo
    claro sem o texto e sem os tokens dá branco no branco, então as três coisas
    andam juntas. Confirmado no preview: ``pre`` rgb(238,242,247) sobre
    rgb(23,32,51), ``keyword`` rgb(27,79,160), ``comment`` rgb(70,86,110).
    """
    from design.theme_light import LIGHT_CSS

    assert "#0e1117" not in LIGHT_CSS.lower(), "o fundo preto do stCode voltou"
    for trecho in ('[data-testid="stCode"] pre',
                   ".token.comment",
                   ".token.keyword",
                   ".token.string",
                   '[data-testid="stMarkdownContainer"] :not(pre) > code'):
        assert trecho in LIGHT_CSS, f"{trecho} sem regra no tema claro"

    regra_pre = LIGHT_CSS.split('[data-testid="stCode"] pre {', 1)[1].split("}", 1)[0]
    assert "color:var(--app-text)" in regra_pre, (
        "fundo claro sem cor de texto: o código fica branco no branco"
    )


def test_tema_claro_alcanca_o_que_a_varredura_do_dom_achou_escuro():
    """Varredura no preview claro: luminância do fundo computado de cada nó.

    Sobraram quatro superfícies em 29/09/2026, todas fora do alcance dos
    tokens: a casca do menu do selectbox/multiselect (rgb(14,17,23) em volta
    das opções já brancas), a seta e o "x" do select (fill rgb(250,250,250)
    sumindo no campo branco), o delta do metric (rgb(92,228,136) e
    rgb(255,108,108), pálidos sobre branco) e o ``st.json``, que desenha com
    estilo inline. Depois da correção a varredura só acha o que é de propósito:
    o verde do checkbox marcado.
    """
    from design.theme_light import LIGHT_CSS

    for seletor in ('[data-testid="stSelectboxVirtualDropdown"]',
                    '[data-baseweb="select"] svg',
                    '[data-testid="stMetricDeltaIcon-Up"]',
                    '[data-testid="stMetricDeltaIcon-Down"]',
                    '[data-testid="stJson"] .react-json-view'):
        assert seletor in LIGHT_CSS, f"{seletor} sem regra no tema claro"

    # O tooltip usa o mesmo data-baseweb do menu e escuro ali é o desenho
    # normal: a regra do menu precisa se prender ao dropdown.
    for linha in LIGHT_CSS.splitlines():
        alvo = linha.strip().rstrip(",").rstrip(" {")
        if alvo.startswith('[data-baseweb="popover"]') and "stSelectbox" not in alvo:
            assert "listbox" in alvo or "[role=" in alvo, (
                f"{alvo} pinta todo popover, tooltip incluído"
            )


def test_escala_de_correlacao_clara_tem_meio_claro_e_numero_legivel():
    """``colorscale`` é o ponto cego do adaptador, e por decisão do módulo.

    Medido em 29/09/2026: ``_overrides`` percorre dicionários e listas, mas as
    paradas da escala são pares ``[posição, cor]`` — listas de escalares, que
    ele devolve intactas. Ou seja, o mapa de correlação chegava ao tema claro
    com o meio da escala em ``#0F172A``: célula quase preta no meio da página
    branca, e o número por cima já escurecido pelo adaptador. Clarear parada
    por parada quebraria a ordem do gradiente, então a saída é a que o próprio
    ``_tinta_clara`` prescreve: escolher a paleta clara na origem.
    """
    from design.tema_canvas import _luminancia, _rgba, escala_correlacao, registrar_tema

    def contraste(cor: str, texto: str) -> float:
        a, b = (_luminancia(_rgba(c)[:3]) for c in (cor, texto))
        return (max(a, b) + 0.05) / (min(a, b) + 0.05)

    registrar_tema("light")
    clara = escala_correlacao()
    registrar_tema("dark")
    escura = escala_correlacao()
    assert clara != escura, "a escala não acompanha o tema da sessão"

    posicoes = [p for p, _ in clara]
    assert posicoes == [p for p, _ in escura], "as paradas mudaram de posição"
    for posicao, cor in clara:
        lum = _luminancia(_rgba(cor)[:3])
        assert lum > 0.4, f"parada {posicao} ({cor}) escura demais para o claro"
        # O valor da correlação é escrito dentro da célula, em `--app-text`.
        assert contraste(cor, "#172033") >= 4.5, (
            f"parada {posicao} ({cor}) não deixa o número legível")


def test_mapa_de_correlacao_nao_crava_a_escala_na_tela():
    """Paleta cravada na view volta a ignorar o tema — a escolha é do tema."""
    fonte = (RAIZ / "views" / "investimentos.py").read_text(encoding="utf-8")
    assert "escala_correlacao()" in fonte, "a tela não pede a escala ao tema"
    assert "#0F172A" not in fonte.upper(), "parada escura cravada voltou à tela"
