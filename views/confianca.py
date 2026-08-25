# -*- coding: utf-8 -*-
"""Tela: Grau de Confiança — quanto o app confia em cada seção, e por quê.

Existe porque medir confiança sem lugar para lê-la é decoração: o índice de
confiança de dados ficou meses sem consumidor (A-125) e por isso não mudava
decisão nenhuma. Esta tela é a porta de entrada do relatório.

O que ela promete é limitado de propósito: informa a qualidade do DADO que
sustenta cada seção. Não é previsão, não é recomendação e não substitui
decisão humana nem aconselhamento profissional.
"""
from __future__ import annotations

import html

import streamlit as st

from core.confianca_secao import (
    FAIXA_ALTA,
    FAIXA_MEDIA,
    ConfiancaSecao,
    confianca_global,
    relatorio,
)

_COR = {"Alta": "#16a34a", "Media": "#d97706", "Baixa": "#dc2626",
        "Nao medido": "#64748b"}

_ROTULO_FAIXA = {"Alta": "Alta", "Media": "Média", "Baixa": "Baixa",
                 "Nao medido": "Não medido"}


def _pct(valor: float | None) -> str:
    return "—" if valor is None else f"{valor:.0f}%"


def _card(sec: ConfiancaSecao) -> str:
    """Todo o card sai num único bloco HTML. Abrir a div num st.markdown e
    fechá-la em outro produz moldura vazia com o conteúdo fora da borda."""
    cor = _COR.get(sec.faixa, "#64748b")
    linhas = []
    for c in sec.componentes:
        if c.medido:
            valor = f'<span style="color:{cor};font-weight:600">{c.pct:.0f}%</span>'
        else:
            # Não medido é cinza e nomeado. Exibi-lo como 0% acusaria um defeito
            # que não foi observado; omiti-lo fingiria cobertura que não houve.
            valor = '<span style="color:#64748b;font-style:italic">não medido</span>'
        linhas.append(
            '<div style="display:flex;justify-content:space-between;gap:12px;'
            'padding:4px 0;border-bottom:1px solid rgba(148,163,184,.18)">'
            f'<span style="flex:1">{html.escape(c.nome)}'
            f'<span style="color:#94a3b8;font-size:.78rem;display:block">'
            f'{html.escape(c.evidencia)}</span></span>{valor}</div>'
        )
    notas = "".join(
        f'<div style="color:#94a3b8;font-size:.8rem;margin-top:6px">⚠ '
        f'{html.escape(n)}</div>' for n in sec.notas)
    cobertura = ""
    if sec.cobertura_da_medicao < 1.0:
        cobertura = (
            f'<div style="color:#94a3b8;font-size:.8rem;margin-top:6px">'
            f'Percentual apoiado em {sec.cobertura_da_medicao * 100:.0f}% do peso '
            f'avaliado — o restante não pôde ser medido.</div>')
    return (
        '<div style="border:1px solid rgba(148,163,184,.25);border-radius:12px;'
        'padding:16px 18px;margin-bottom:14px;background:rgba(148,163,184,.06)">'
        '<div style="display:flex;justify-content:space-between;align-items:baseline">'
        f'<div style="font-weight:700;font-size:1.02rem">{html.escape(sec.secao)}</div>'
        f'<div style="font-weight:700;font-size:1.35rem;color:{cor}">'
        f'{_pct(sec.pct)}</div></div>'
        f'<div style="color:{cor};font-size:.82rem;margin-bottom:10px">'
        f'Confiança {_ROTULO_FAIXA.get(sec.faixa, sec.faixa)}</div>'
        + "".join(linhas) + cobertura + notas + '</div>'
    )


def render() -> None:
    st.title("🎯 Grau de Confiança")
    st.caption(
        "Qualidade do dado que sustenta cada seção. Apoio analítico — não é "
        "previsão, recomendação nem substituto de decisão humana."
    )

    with st.spinner("Medindo cada seção..."):
        secoes = relatorio()
    geral = confianca_global(secoes)

    cor_geral = _COR["Alta" if (geral or 0) >= FAIXA_ALTA else
                     "Media" if (geral or 0) >= FAIXA_MEDIA else "Baixa"]
    st.markdown(
        '<div style="border:1px solid rgba(148,163,184,.25);border-radius:14px;'
        'padding:20px;margin-bottom:20px;text-align:center;'
        'background:rgba(148,163,184,.08)">'
        '<div style="color:#94a3b8;font-size:.85rem;letter-spacing:.06em">'
        'CONFIANÇA GERAL DO APLICATIVO</div>'
        f'<div style="font-size:2.6rem;font-weight:800;color:{cor_geral}">'
        f'{_pct(geral)}</div>'
        '<div style="color:#94a3b8;font-size:.8rem">média das seções, ponderada '
        'pelo quanto de cada uma foi efetivamente medido</div></div>',
        unsafe_allow_html=True,
    )

    col_esq, col_dir = st.columns(2)
    for i, sec in enumerate(secoes):
        (col_esq if i % 2 == 0 else col_dir).markdown(
            _card(sec), unsafe_allow_html=True)

    st.markdown("### Como ler")
    st.markdown(
        f"- **Alta (≥ {FAIXA_ALTA:.0f}%)** — a seção sustenta decisão nos "
        "limites que ela própria declara.\n"
        f"- **Média ({FAIXA_MEDIA:.0f}–{FAIXA_ALTA:.0f}%)** — serve para "
        "estudar; confira a evidência do componente mais baixo antes de agir.\n"
        f"- **Baixa (< {FAIXA_MEDIA:.0f}%)** — trate como exploratório.\n\n"
        "**Abrangência** pesa pouco de propósito: uma seção pode ser muito "
        "confiável sobre uma fatia menor do mercado, e punir isso empurraria o "
        "app a inflar o universo com ativo ruim — o contrário do que se quer. "
        "Ativos sem dado suficiente são descartados do universo de decisão, "
        "não corrigidos no escuro."
    )
