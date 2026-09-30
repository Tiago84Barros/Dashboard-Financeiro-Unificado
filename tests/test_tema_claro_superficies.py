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
    from streamlit.elements.vega_charts import VegaChartsMixin

    from design.tema_canvas import instalar_adaptadores, registrar_tema

    capturado: list[tuple[dict, dict]] = []

    def espiao(self, data=None, spec=None, *args, **kwargs):
        alvo = spec if isinstance(spec, dict) else data
        capturado.append((alvo or {}, kwargs))
        return None

    original = VegaChartsMixin._vega_lite_chart
    VegaChartsMixin._vega_lite_chart = espiao
    try:
        instalar_adaptadores()  # embrulha o espião; idempotente por processo
        registrar_tema(tema)
        df = pd.DataFrame({"v": [1.0, 2.0, 3.0]})
        st.line_chart(df)
        st.bar_chart(df["v"])
        st.area_chart(df)
        st.scatter_chart(df)
    finally:
        VegaChartsMixin._vega_lite_chart = original
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
