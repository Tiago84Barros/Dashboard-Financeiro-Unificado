"""Tabelas legíveis no tema claro.

``st.dataframe`` desenha a grade num canvas (glide-data-grid) com o tema do
``config.toml`` — escuro — e nenhum CSS a alcança. No tema claro a tabela é
reemitida como HTML, preservando rótulos, formatos de coluna e as cores por
célula que a tela aplicou pelo Styler.

O que o HTML não reproduz continua na grade nativa: seleção de linha, edição
(``st.data_editor``), ordenação por clique e tabelas grandes demais para virar
marcação. Perder a ordenação é o preço de ler a tabela no claro; por isso o
limite de linhas é baixo e o caminho nativo permanece intacto no escuro.
"""
from __future__ import annotations

import html as _html
import re

import pandas as pd
import streamlit as st

from design.tema_canvas import clarear_css

LIMITE_LINHAS = 400

# Injetado junto da camada clara (design/theme_light.py), e não aqui: o flag de
# sessão sobreviveria ao rerun e a segunda renderização sairia sem estilo.
CSS_TABELA = """
.tbl-clara-wrap {
 overflow:auto; border:1px solid var(--app-border); border-radius:10px;
 background:var(--app-surface);
}
.tbl-clara {border-collapse:collapse; width:100%; font-size:0.82rem;}
.tbl-clara thead th {
 position:sticky; top:0; z-index:1;
 background:var(--app-surface-raised); color:var(--app-muted);
 font-weight:600; font-size:0.72rem; text-transform:uppercase;
 letter-spacing:.04em; text-align:left; white-space:nowrap;
 padding:8px 10px; border-bottom:1px solid var(--app-border);
}
.tbl-clara tbody td, .tbl-clara tbody th {
 padding:6px 10px; border-bottom:1px solid var(--app-border);
 color:var(--app-text); white-space:nowrap;
}
.tbl-clara tbody tr:nth-child(even) td {background:rgba(23,32,51,.025);}
.tbl-clara tbody tr:hover td {background:rgba(23,94,172,.06);}
.tbl-clara td.num, .tbl-clara th.num {text-align:right; font-variant-numeric:tabular-nums;}
.tbl-clara .barra {
 display:block; height:8px; border-radius:4px; background:var(--app-border);
}
.tbl-clara .barra > i {display:block; height:100%; border-radius:4px; background:var(--app-primary);}
"""


# ───────────────────────────── formatos ─────────────────────────────
def _formatador(spec, tipo: str):
    """Traduz o ``format`` do column_config para uma função de texto."""
    if tipo == "progress":
        return None  # a barra é montada à parte
    if not isinstance(spec, str) or not spec:
        return None
    if spec == "percent":
        return lambda v: f"{v * 100:.1f}%"
    if spec in ("localized", "accounting"):
        return lambda v: f"{v:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")
    if spec == "plain":
        return str
    if spec == "dollar":
        return lambda v: f"$ {v:,.2f}"
    if spec == "euro":
        return lambda v: f"€ {v:,.2f}"
    if "%" in spec:
        return lambda v: spec % v
    if tipo == "date" and re.fullmatch(r"[DMY/.\- ]+", spec):
        padrao = spec.replace("DD", "%d").replace("MM", "%m").replace("YYYY", "%Y")
        return lambda v: pd.to_datetime(v).strftime(padrao)
    return None


def _config_da_coluna(config, nome):
    bruto = (config or {}).get(nome)
    if bruto is None:
        return None, None, None, {}
    if isinstance(bruto, str):
        return bruto, None, None, {}
    if not isinstance(bruto, dict):
        return None, None, None, {}
    tipo_config = bruto.get("type_config") or {}
    tipo = tipo_config.get("type") or ""
    return bruto.get("label"), tipo_config.get("format"), tipo, tipo_config


def _barra(valor, tipo_config) -> str:
    minimo = tipo_config.get("min_value") or 0
    maximo = tipo_config.get("max_value")
    try:
        atual = float(valor)
    except (TypeError, ValueError):
        return ""
    if maximo in (None, minimo):
        maximo = max(atual, minimo + 1)
    fracao = min(max((atual - minimo) / (maximo - minimo), 0.0), 1.0)
    rotulo = _formatador(tipo_config.get("format"), "number")
    texto = rotulo(atual) if rotulo else f"{atual:g}"
    return (f'<span class="barra"><i style="width:{fracao * 100:.0f}%"></i></span>'
            f'<small>{_html.escape(texto)}</small>')


# ───────────────────────────── montagem ─────────────────────────────
def _quadro(data):
    """Devolve (DataFrame, Styler|None) para o que o Streamlit aceitaria."""
    estilo = None
    if hasattr(data, "to_html") and hasattr(data, "data"):  # pandas Styler
        estilo, data = data, data.data
    if isinstance(data, pd.Series):
        data = data.to_frame()
    if not isinstance(data, pd.DataFrame):
        try:
            data = pd.DataFrame(data)
        except Exception:
            return None, None
    return data, estilo


def _html_do_styler(estilo, df, formatos, ocultar_indice) -> str:
    try:
        estilo = estilo.format(formatos) if formatos else estilo
        if ocultar_indice:
            estilo = estilo.hide(axis="index")
        estilo = estilo.set_table_attributes('class="tbl-clara"')
        return clarear_css(estilo.to_html())
    except Exception:
        return _html_simples(df, {}, formatos, ocultar_indice, {})


def _html_simples(df, rotulos, formatos, ocultar_indice, progressos) -> str:
    numericas = {c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])}
    cabecalho = [] if ocultar_indice else ['<th scope="col"></th>']
    for coluna in df.columns:
        classe = ' class="num"' if coluna in numericas else ""
        cabecalho.append(f"<th{classe}>{_html.escape(str(rotulos.get(coluna, coluna)))}</th>")

    linhas = []
    for indice, linha in df.iterrows():
        celulas = [] if ocultar_indice else [f"<th>{_html.escape(str(indice))}</th>"]
        for coluna in df.columns:
            valor = linha[coluna]
            if coluna in progressos:
                conteudo, classe = _barra(valor, progressos[coluna]), ""
            else:
                if pd.isna(valor):
                    texto = ""
                else:
                    formatar = formatos.get(coluna)
                    try:
                        texto = formatar(valor) if formatar else str(valor)
                    except (TypeError, ValueError):
                        texto = str(valor)
                conteudo = _html.escape(texto)
                classe = ' class="num"' if coluna in numericas else ""
            celulas.append(f"<td{classe}>{conteudo}</td>")
        linhas.append("<tr>" + "".join(celulas) + "</tr>")

    return ('<table class="tbl-clara"><thead><tr>' + "".join(cabecalho)
            + "</tr></thead><tbody>" + "".join(linhas) + "</tbody></table>")


def renderizar(dg, data, kwargs) -> bool:
    """Emite a tabela como HTML claro. Devolve False quando não dá conta."""
    # Interação que só a grade nativa tem.
    if any(kwargs.get(chave) for chave in ("on_select", "selection_mode", "key")):
        return False

    df, estilo = _quadro(data)
    if df is None or len(df) > LIMITE_LINHAS or df.empty:
        return False

    config = kwargs.get("column_config") or {}
    ordem = kwargs.get("column_order")
    if ordem:
        df = df[[c for c in ordem if c in df.columns]]
    escondidas = [c for c in df.columns if c in config and config[c] is None]
    if escondidas:
        df = df.drop(columns=escondidas)
    if estilo is not None and (ordem or escondidas):
        estilo = None  # o Styler não acompanha o recorte: cai no HTML simples

    rotulos, formatos, progressos = {}, {}, {}
    for coluna in df.columns:
        rotulo, spec, tipo, tipo_config = _config_da_coluna(config, coluna)
        if rotulo:
            rotulos[coluna] = rotulo
        if tipo == "progress":
            progressos[coluna] = tipo_config
        formatar = _formatador(spec, tipo)
        if formatar:
            formatos[coluna] = formatar

    ocultar_indice = kwargs.get("hide_index")
    if ocultar_indice is None:
        ocultar_indice = isinstance(df.index, pd.RangeIndex)

    if estilo is not None and not progressos:
        corpo = _html_do_styler(estilo, df, formatos, ocultar_indice)
        if rotulos:
            for coluna, rotulo in rotulos.items():
                corpo = corpo.replace(f">{_html.escape(str(coluna))}</th>",
                                      f">{_html.escape(str(rotulo))}</th>", 1)
    else:
        corpo = _html_simples(df, rotulos, formatos, ocultar_indice, progressos)

    altura = kwargs.get("height")
    limite = f"max-height:{int(altura)}px;" if isinstance(altura, (int, float)) else (
        "max-height:420px;" if len(df) > 14 else "")
    dg.markdown(f'<div class="tbl-clara-wrap" style="{limite}">{corpo}</div>',
                unsafe_allow_html=True)
    return True
