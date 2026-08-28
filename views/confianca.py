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


_SIMBOLO = {True: ("✓", "#16a34a"), False: ("✗", "#dc2626"),
            None: ("—", "#64748b")}


@st.cache_data(ttl=900, show_spinner=False)
def _rigor() -> dict:
    """As três notas na mesma lista de perguntas (A-162).

    Cacheado porque cada motor consulta o banco; o dado muda quando uma safra
    ou um certificado é republicado, não a cada clique.
    """
    from core.validacao_motor import DIMENSOES, comparacao_de_rigor
    comp = comparacao_de_rigor()
    return {"dimensoes": list(DIMENSOES),
            "motores": {classe: {d: (None if p is None else (p.ok, p.detalhe))
                                 for d, p in dims.items()}
                        for classe, dims in comp.items()}}


def _tabela_rigor() -> None:
    """Por que as três notas não são comparáveis entre si.

    A casca visual é a mesma nas três abas, e isso sugere que 80 no FII vale o
    mesmo que 80 nos EUA. Não vale: cada motor venceu um conjunto diferente de
    condições. Até 28/08/2026 cada um declarava só as perguntas que respondia,
    e o que menos perguntava marcava a melhor nota de metodologia.
    """
    try:
        dados = _rigor()
    except Exception:  # noqa: BLE001
        return
    motores = dados.get("motores") or {}
    if not motores:
        return
    st.markdown("### Rigor dos três motores de score")
    st.caption(
        "As notas de FII, Empresas B3 e Empresas Americanas saem de motores "
        "independentes e **não são comparáveis entre si**: 80 num não é o 80 "
        "do outro. Abaixo, as mesmas perguntas feitas aos três — ✓ vencida, "
        "✗ reprovada, — não apurada."
    )
    cabecalho = "".join(
        f'<th style="text-align:center;padding:8px 10px;font-weight:600;'
        f'font-size:.82rem">{html.escape(c)}</th>' for c in motores)
    linhas = []
    for dim in dados["dimensoes"]:
        celulas = []
        for classe in motores:
            item = motores[classe].get(dim)
            ok, detalhe = (None, "não declarada") if item is None else item
            simbolo, cor = _SIMBOLO[ok]
            celulas.append(
                f'<td style="text-align:center;padding:8px 10px;vertical-align:top">'
                f'<div style="color:{cor};font-weight:700;font-size:1.1rem">{simbolo}</div>'
                f'<div style="color:#94a3b8;font-size:.72rem;line-height:1.25">'
                f'{html.escape(str(detalhe)[:150])}</div></td>')
        linhas.append(
            '<tr style="border-top:1px solid rgba(148,163,184,.18)">'
            f'<td style="padding:8px 10px;font-weight:600;font-size:.85rem">'
            f'{html.escape(dim)}</td>' + "".join(celulas) + '</tr>')
    st.markdown(
        '<div style="border:1px solid rgba(148,163,184,.25);border-radius:12px;'
        'padding:8px 10px;background:rgba(148,163,184,.06);overflow-x:auto">'
        '<table style="width:100%;border-collapse:collapse">'
        f'<tr><th style="text-align:left;padding:8px 10px"></th>{cabecalho}</tr>'
        + "".join(linhas) + '</table></div>',
        unsafe_allow_html=True)
    st.caption(
        "Uma pergunta **não apurada** não conta como vencida nem como "
        "reprovada — ela sai da média e continua escrita. Foi o contrário disso "
        "que inflou a nota do motor de FIIs: enquanto ele declarava uma "
        "pergunta e os outros dois declaravam duas, medir menos rendia nota "
        "maior."
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

    _tabela_rigor()

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
