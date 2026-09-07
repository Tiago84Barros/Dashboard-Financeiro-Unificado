"""Telas sintéticas em memória para validação visual das rotas de mercado.

Não importe views, loaders, bancos, arquivos de dados ou provedores externos
neste módulo.  O conteúdo é deliberadamente fictício e identificado como tal.
"""
from __future__ import annotations

import os

import streamlit as st

from core.app_test_mode import STATE_OPTIONS, state_from_value

_ROUTES = {
    "empresas_b3": ("🏢 Empresas B3", "B3 · Brasil", "B3-SINT-A"),
    "fiis": ("🏬 Seleção de FIIs", "FIIs · Brasil", "FII-SINT-A"),
    "empresas_americanas": ("🌎 Empresas Americanas", "EUA · USD", "US-SINT-A"),
    "portfolio_global": ("🌐 Portfólio Global", "Patrimônio consolidado", "GLB-SINT-A"),
}
_STATE_LABELS = {
    "loaded": "Carregado",
    "empty": "Vazio",
    "partial": "Parcial",
    "error": "Erro recuperável",
}


def _route_from_query() -> str:
    """Lê apenas a navegação já selecionada na sessão, sem acessar dados reais."""
    labels = {
        "🏢 Empresas B3": "empresas_b3",
        "🏬 Seleção de FIIs": "fiis",
        "🌎 Empresas Americanas": "empresas_americanas",
        "🌐 Portfólio Global": "portfolio_global",
    }
    return labels.get(st.session_state.get("app_main_navigation"), "empresas_b3")


def render() -> None:
    route = _route_from_query()
    title, market, symbol = _ROUTES[route]
    initial = state_from_value(os.getenv("APP_TEST_MODE_STATE"))
    state = st.radio(
        "Estado de validação sintético",
        options=STATE_OPTIONS,
        index=STATE_OPTIONS.index(initial),
        format_func=_STATE_LABELS.__getitem__,
        key=f"app_test_mode_state_{route}",
        horizontal=True,
    )

    st.markdown(f"## {title}")
    st.caption(
        f"Modo de validação sintético · {market} · fonte: memória · "
        "nenhum banco, rede, loader ou artefato financeiro foi acessado."
    )

    if state == "empty":
        st.info("Estado sintético vazio: não há itens de exemplo para exibir.")
        return
    if state == "error":
        st.error("Estado sintético de erro recuperável: a fonte fictícia está indisponível.")
        st.caption("Nenhuma tentativa de reconexão foi feita neste modo.")
        return

    if state == "partial":
        st.warning("Estado sintético parcial: parte dos campos de exemplo não está disponível.")

    st.success("Estado sintético carregado: dados exclusivamente fictícios em memória.")
    st.metric("Itens sintéticos", "3", help="Contagem de exemplos; não representa ativos reais.")
    rows = [
        {"Símbolo": symbol, "Status": "Disponível", "Cobertura": "Sintética"},
        {"Símbolo": f"{symbol}-B", "Status": "Parcial" if state == "partial" else "Disponível", "Cobertura": "Sintética"},
        {"Símbolo": f"{symbol}-C", "Status": "Disponível", "Cobertura": "Sintética"},
    ]
    st.dataframe(
        rows, hide_index=True, width="stretch",
        key=f"app_test_table_{route}_{state}",
    )
    st.caption("Período: demonstração sem data de mercado · unidade: itens · premissa: teste visual.")
