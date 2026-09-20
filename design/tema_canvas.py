"""Superfícies desenhadas em canvas/SVG seguindo o tema da sessão.

O CSS não alcança o Plotly nem o Vega: as cores viram atributo da figura, e
por isso os gráficos continuavam escuros dentro da página clara. Este módulo
reescreve **apenas a moldura** — fundo, grade, eixos, legenda e fontes de
rótulo — quando a sessão está no tema claro. As cores dos dados (fatias,
barras, linhas) continuam as mesmas; só são escurecidas quando não teriam
contraste suficiente sobre o branco.

O tema é lido do ``session_state`` **na hora da chamada**, e não no momento em
que o adaptador é instalado: o patch é global ao processo e o servidor atende
várias sessões ao mesmo tempo, então quem está no escuro não pode herdar a
decisão de quem está no claro.
"""
from __future__ import annotations

import re

import streamlit as st

_SESSAO = "_app4_tema_canvas"
_INSTALADO = "_app4_adaptadores_canvas"

# Cromo escuro do app (fundo de página, cartões, bordas). Aparece tanto no
# layout quanto em contornos de fatia (`marker.line.color`), e no claro vira a
# superfície correspondente.
_CROMO = {
    "#0e1117": "#ffffff",
    "#1a1f2e": "#ffffff",
    "#12161f": "#ffffff",
    "#1e2533": "#e3e9f2",
    "#2d3748": "#dbe3ee",
    "#4a5568": "#c2ccda",
}

# Tons de texto do tema escuro -> tons legíveis sobre branco.
_TEXTO = {
    "#fafafa": "#172033",
    "#ffffff": "#172033",
    "#f7fafc": "#172033",
    "#e2e8f0": "#172033",
    "#edf2f7": "#172033",
    "#cbd5e0": "#2b3a52",
    "#c4cbd5": "#2b3a52",
    "#a0aec0": "#46566e",
    "#94a3b8": "#46566e",
    "#9ca3af": "#46566e",
    "#718096": "#46566e",
    "#4a5568": "#46566e",
}

_GRADE = "#e3e9f2"
_EIXO = "#b9c4d4"
_TEXTO_PADRAO = "#172033"
_SUPERFICIE = "#ffffff"

_CHAVES_FUNDO = {"bgcolor", "paper_bgcolor", "plot_bgcolor"}
_CHAVES_GRADE = {"gridcolor", "zerolinecolor"}
_CHAVES_EIXO = {"linecolor", "tickcolor", "bordercolor", "outlinecolor", "spikecolor"}
_PAIS_FONTE = {
    "font", "tickfont", "titlefont", "textfont", "insidetextfont",
    "outsidetextfont", "hoverlabel", "labelfont", "rangefont",
}
_PAIS_CONTORNO = {"line", "marker"}


# ─────────────────────────── cores ───────────────────────────
def _rgba(cor: str):
    """Devolve (r, g, b, a) para hex/rgb()/rgba(); None para o que não souber."""
    if not isinstance(cor, str):
        return None
    texto = cor.strip().lower()
    if texto.startswith("#"):
        digitos = texto[1:]
        if len(digitos) == 3:
            digitos = "".join(c * 2 for c in digitos)
        if len(digitos) == 8:
            partes = [int(digitos[i:i + 2], 16) for i in range(0, 8, 2)]
            return (partes[0], partes[1], partes[2], partes[3] / 255)
        if len(digitos) != 6:
            return None
        try:
            return tuple(int(digitos[i:i + 2], 16) for i in range(0, 6, 2)) + (1.0,)
        except ValueError:
            return None
    achado = re.match(r"rgba?\(([^)]*)\)", texto)
    if not achado:
        return None
    campos = [p.strip() for p in achado.group(1).split(",")]
    if len(campos) < 3:
        return None
    try:
        r, g, b = (float(campos[i]) for i in range(3))
        a = float(campos[3]) if len(campos) > 3 else 1.0
    except ValueError:
        return None
    return (r, g, b, a)


def _hex(cor: str) -> str | None:
    """Normaliza para ``#rrggbb`` quando a cor é opaca; None caso contrário."""
    partes = _rgba(cor)
    if not partes or partes[3] < 0.999:
        return None
    return "#%02x%02x%02x" % tuple(int(round(c)) for c in partes[:3])


def _luminancia(partes) -> float:
    def canal(v: float) -> float:
        v = v / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (canal(c) for c in partes[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contraste_no_branco(partes) -> float:
    return 1.05 / (_luminancia(partes) + 0.05)


def escurecer_para_o_claro(cor: str, minimo: float = 2.6) -> str:
    """Escurece a cor só o bastante para ela aparecer sobre o branco.

    Mantém o matiz: é a mesma cor da série no escuro, um pouco mais fechada.
    """
    partes = _rgba(cor)
    if not partes:
        return cor
    if _contraste_no_branco(partes) >= minimo:
        return cor
    r, g, b, a = partes
    for _ in range(24):
        r, g, b = (c * 0.92 for c in (r, g, b))
        if _contraste_no_branco((r, g, b)) >= minimo:
            break
    if a < 0.999:
        return "rgba(%d,%d,%d,%s)" % (round(r), round(g), round(b), a)
    return "#%02x%02x%02x" % (round(r), round(g), round(b))


def _fundo_claro(cor: str) -> str | None:
    partes = _rgba(cor)
    if not partes:
        return None
    if partes[3] < 0.35:  # transparente ou quase: a página clara aparece
        return None
    chave = _hex(cor)
    if chave in _CROMO:
        return _CROMO[chave]
    return _SUPERFICIE if _luminancia(partes) < 0.35 else None


def _grade_clara(cor: str) -> str | None:
    partes = _rgba(cor)
    if not partes or _luminancia(partes) > 0.6:
        return None
    return _GRADE


def _eixo_claro(cor: str) -> str | None:
    partes = _rgba(cor)
    if not partes or _luminancia(partes) > 0.6:
        return None
    return _EIXO


def _texto_claro(cor: str) -> str | None:
    chave = _hex(cor)
    if chave in _TEXTO:
        return _TEXTO[chave]
    partes = _rgba(cor)
    if not partes:
        return None
    if _contraste_no_branco(partes) < 2.6:
        # Cinza claro sem matiz vira o texto padrão; cor semântica só fecha.
        r, g, b = partes[:3]
        if max(r, g, b) - min(r, g, b) < 12:
            return _TEXTO_PADRAO
        return escurecer_para_o_claro(cor)
    return None


_LUM_FUNDO_OK = 0.62


def _tinta_clara(cor: str) -> str:
    """Fundo de célula legível sobre o branco, sem trocar o matiz.

    Primeiro compõe a cor sobre o branco: o padrão das telas é tinta com alpha
    baixo (``rgba(0,200,150,.15)``), que já nasce pálida e não precisa de mais
    nada. Só o que continua escuro depois disso é lavado.

    Limitação aceita: um colormap contínuo (``background_gradient``) perde a
    ordem, porque a ponta escura clareia e a clara fica onde está. Clarear o
    escuro e preservar o claro são objetivos opostos, e não existe função
    monótona que faça os dois. As telas do app pintam faixa discreta, não
    gradiente -- se alguma passar a usar gradiente, ele precisa ser escolhido
    já na paleta clara, e não corrigido aqui.
    """
    partes = _rgba(cor)
    if not partes:
        return cor
    alfa = partes[3]
    r, g, b = (c + (255 - c) * (1 - alfa) for c in partes[:3])
    if _luminancia((r, g, b)) < _LUM_FUNDO_OK:
        r, g, b = (c + (255 - c) * 0.84 for c in (r, g, b))
    return "#%02x%02x%02x" % (round(r), round(g), round(b))


def _texto_de_tabela(cor: str) -> str:
    """Fecha o tom do texto; cinza sem matiz vira o texto padrão do tema.

    Escurecer um cinza só até bater o contraste devolve outro cinza -- legível
    no teste e apagado na tela, ao lado das colunas em ``--app-text``.
    """
    partes = _rgba(cor)
    if partes and max(partes[:3]) - min(partes[:3]) < 12:
        return _TEXTO_PADRAO
    return escurecer_para_o_claro(cor, 4.0)


_CSS_CORES = re.compile(r"(background-color|background|color)\s*:\s*([^;}\n]+)")


def clarear_css(marcacao: str) -> str:
    """Adapta as cores inline de um HTML (Styler) ao fundo claro.

    As telas guardam a paleta do tema escuro; sobre o branco, o texto precisa
    fechar o tom e o fundo precisa lavar — sem trocar o matiz, que é o que
    carrega o significado (verde positivo, vermelho negativo).
    """
    def troca(achado: re.Match) -> str:
        propriedade, valor = achado.group(1), achado.group(2).strip()
        if not _rgba(valor):
            return achado.group(0)
        if propriedade == "color":
            # Texto de tabela é pequeno: exige mais contraste que um rótulo
            # de gráfico, por isso o alvo aqui é maior que o de `_texto_claro`.
            nova = _TEXTO.get(_hex(valor) or "") or _texto_de_tabela(valor)
        else:
            nova = _tinta_clara(valor)
        return f"{propriedade}: {nova}"
    return _CSS_CORES.sub(troca, marcacao)


def _contorno_claro(cor: str) -> str | None:
    """Contorno de fatia/barra: só troca quando é o cromo escuro do app."""
    return _CROMO.get(_hex(cor) or "")


# ─────────────────────── travessia da figura ───────────────────────
def _overrides(no, chave_pai: str | None = None, em_fonte: bool = False):
    """Percorre o JSON e devolve só o que muda — nunca o objeto inteiro."""
    if isinstance(no, list):
        # O Plotly casa listas elemento a elemento; ``{}`` deixa o item intacto.
        itens = [_overrides(v, chave_pai, em_fonte) or {} for v in no]
        return itens if any(itens) else None
    if not isinstance(no, dict):
        return None

    mudancas: dict = {}
    for chave, valor in no.items():
        if isinstance(valor, (dict, list)):
            filho = _overrides(valor, chave, em_fonte or chave in _PAIS_FONTE)
            if filho:
                mudancas[chave] = filho
            continue
        if not isinstance(valor, str):
            continue
        nova = None
        if chave in _CHAVES_FUNDO:
            nova = _fundo_claro(valor)
        elif chave in _CHAVES_GRADE:
            nova = _grade_clara(valor)
        elif chave in _CHAVES_EIXO:
            nova = _eixo_claro(valor)
        elif chave == "color":
            if em_fonte or chave_pai in _PAIS_FONTE:
                nova = _texto_claro(valor)
            elif chave_pai in _PAIS_CONTORNO:
                nova = _contorno_claro(valor)
        if nova and nova != valor:
            mudancas[chave] = nova
    return mudancas or None


def clarear_figura(fig):
    """Adapta a moldura da figura ao tema claro, no lugar (idempotente)."""
    try:
        bruto = fig.to_plotly_json()
    except Exception:
        return fig

    layout = _overrides(bruto.get("layout") or {}) or {}
    layout.setdefault("template", "plotly_white")
    layout.setdefault("paper_bgcolor", "rgba(0,0,0,0)")
    layout.setdefault("plot_bgcolor", "rgba(0,0,0,0)")
    layout.setdefault("font", {}).setdefault("color", _TEXTO_PADRAO)
    try:
        fig.update_layout(**layout)
    except Exception:
        pass

    for traco, dados in zip(fig.data, bruto.get("data") or []):
        ajuste = _overrides(dados)
        if not ajuste:
            continue
        ajuste.pop("type", None)
        try:
            traco.update(ajuste)
        except Exception:
            continue
    return fig


# ─────────────────────── instalação dos adaptadores ───────────────────────
def registrar_tema(theme: str) -> None:
    """Guarda o tema da sessão corrente para os adaptadores consultarem."""
    try:
        st.session_state[_SESSAO] = "light" if theme == "light" else "dark"
    except Exception:
        pass


def tema_da_sessao() -> str:
    try:
        return st.session_state.get(_SESSAO, "dark")
    except Exception:
        return "dark"


def no_claro() -> bool:
    """Tema claro ativo nesta sessao.

    Publica porque quem desenha em canvas (data_editor, por exemplo) precisa
    escolher outro caminho de renderizacao, e uma so definicao evita que a
    checagem seja reescrita em cada tela.
    """
    return tema_da_sessao() == "light"


def instalar_adaptadores() -> None:
    """Embrulha os elementos que desenham em canvas. Roda uma vez por processo."""
    if getattr(st, _INSTALADO, False):
        return

    from streamlit.delta_generator import DeltaGenerator
    from streamlit.elements.vega_charts import VegaChartsMixin

    plotly_original = DeltaGenerator.plotly_chart

    def plotly_chart(self, figure_or_data, *args, **kwargs):
        if no_claro():
            if hasattr(figure_or_data, "update_layout"):
                clarear_figura(figure_or_data)
            # Sem isto o Streamlit reaplica o template escuro por cima.
            kwargs.setdefault("theme", None)
        return plotly_original(self, figure_or_data, *args, **kwargs)

    dataframe_original = DeltaGenerator.dataframe

    def dataframe(self, data=None, *args, **kwargs):
        if no_claro() and not args:
            from design.tabela_clara import renderizar
            try:
                if renderizar(self, data, kwargs):
                    return None
            except Exception:
                pass  # qualquer tropeço na marcação cai na grade nativa
        return dataframe_original(self, data, *args, **kwargs)

    altair_original = VegaChartsMixin._altair_chart

    def _altair_chart(self, *args, **kwargs):
        if no_claro():
            kwargs["theme"] = None
        return altair_original(self, *args, **kwargs)

    DeltaGenerator.plotly_chart = plotly_chart
    DeltaGenerator.dataframe = dataframe
    VegaChartsMixin._altair_chart = _altair_chart
    # ``st.plotly_chart`` e ``st.dataframe`` são métodos já vinculados ao
    # DeltaGenerator raiz: trocar só na classe não alcança quem chama pelo
    # módulo, que é a forma usada em praticamente todas as telas.
    st.plotly_chart = st._main.plotly_chart
    st.dataframe = st._main.dataframe
    setattr(st, _INSTALADO, True)
