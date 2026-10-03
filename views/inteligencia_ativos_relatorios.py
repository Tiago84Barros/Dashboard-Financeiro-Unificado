"""
views/inteligencia_ativos_relatorios.py
Resumo por LLM dos relatórios, logo abaixo da etapa 10 da Análise detalhada.

A etapa 10 mostra as frases literais; aqui, sob demanda, a LLM diz o que
elas querem dizer (``core/inteligencia_ativos/leitura_relatorios.py``). A
chamada só acontece no botão: custa uma chamada por ativo, e LLM na
renderização trava a tela e os testes headless.

Cada cartão sai num st.markdown só e usa apenas var(--app-*).
Coberto por tests/test_leitura_relatorios.py.
"""
from __future__ import annotations

import hashlib
from html import escape

import streamlit as st

from core.inteligencia_ativos import informacoes as inf
from core.inteligencia_ativos import leitura_relatorios as lr
from core.inteligencia_ativos import modelos as m

_COR_STATUS = {lr.APROVADA: "primary", lr.COM_RESSALVAS: "warning",
               lr.REJEITADA: "danger"}

_SUB = ('<div style="font-size:0.7rem;font-weight:700;letter-spacing:.05em;'
        'text-transform:uppercase;color:var(--app-subtle);margin-top:10px">')


def chave_sessao(ticker: str, r: inf.Relatorios) -> str:
    """O resumo vale para estes trechos: chegou documento novo, pede de novo."""
    base = "\n".join(f"{t.data}|{t.titulo}|{t.frase}" for t in r.trechos)
    h = hashlib.sha256(base.encode("utf-8")).hexdigest()
    return f"ia_rel_{ticker}_{h[:12]}"


def _lista(itens) -> str:
    return ('<ul style="margin:2px 0 0 18px;padding:0">' + "".join(
        f'<li style="margin:3px 0;color:var(--app-text)">{escape(x)}</li>'
        for x in itens) + "</ul>")


def cartao(resumo: lr.Resumo) -> str:
    """O resumo, com a validação no rodapé. Puro."""
    cor = _COR_STATUS.get(resumo.status, "text")
    if resumo.status == lr.REJEITADA:
        corpo = (f'<div style="font-weight:700;color:var(--app-{cor})">'
                 'Resumo não gerado</div>' + _lista(resumo.problemas))
    else:
        corpo = ('<div style="color:var(--app-text);line-height:1.5">'
                 f'{escape(resumo.sintese)}</div>')
        atual = None
        for p in resumo.pontos:
            if p.tema != atual:
                if atual is not None:
                    corpo += "</ul>"
                atual = p.tema
                corpo += (f'{_SUB}{escape(p.rotulo)}</div>'
                          '<ul style="margin:2px 0 0 18px;padding:0">')
            corpo += (f'<li style="margin:3px 0;color:var(--app-text)">'
                      f'{escape(p.texto)}</li>')
        if atual is not None:
            corpo += "</ul>"
        if resumo.atencao:
            corpo += f'{_SUB}Acompanhar</div>' + _lista(resumo.atencao)
        if resumo.lacunas:
            corpo += f'{_SUB}O que os documentos não dizem</div>' + _lista(
                resumo.lacunas)
        validacao = ("Números conferidos com os trechos e o contexto enviado."
                     if resumo.status == lr.APROVADA else
                     "Aceito com ressalvas.")
        if resumo.numeros_sem_ancora:
            validacao += (" Números que não estão no contexto: "
                          + ", ".join(resumo.numeros_sem_ancora) + ".")
        if resumo.problemas:
            validacao += " " + " ".join(resumo.problemas)
        modelo = f" Modelo: {resumo.modelo}." if resumo.modelo else ""
        corpo += (
            f'<div style="font-size:0.76rem;color:var(--app-{cor});'
            f'margin-top:10px">{escape(validacao)}</div>'
            '<div style="font-size:0.76rem;color:var(--app-subtle);'
            'margin-top:2px">Leitura da IA sobre os trechos acima e o contexto '
            f'de mercado; não é recomendação.{escape(modelo)}</div>')
    return ('<div style="background:var(--app-surface);border:1px solid '
            'var(--app-border);border-left:4px solid var(--app-'
            f'{cor});border-radius:10px;padding:12px 16px;margin:6px 0">'
            '<div style="font-size:0.72rem;font-weight:700;letter-spacing:.05em;'
            'text-transform:uppercase;color:var(--app-subtle)">10 · O que os '
            f'relatórios mostram (IA)</div>{corpo}</div>')


def render(analise: m.AnaliseAtivo) -> lr.Resumo | None:
    """Botão e resumo. Nada aparece quando a etapa 10 não tem trechos."""
    from core.llm_b3 import llm_disponivel

    r = lr.relatorios_da_analise(analise)
    if r is None or not r.trechos:
        return None
    ticker = str(analise.ativo.ticker or "")
    chave = chave_sessao(ticker, r)
    if not llm_disponivel():
        st.caption("Resumo dos relatórios por IA indisponível: nenhum "
                   "provedor configurado.")
    else:
        # Resumo aceito fica na sessão; rejeitado pode ser pedido de novo.
        anterior = st.session_state.get(chave)
        rejeitado = (isinstance(anterior, lr.Resumo)
                     and anterior.status == lr.REJEITADA)
        if (anterior is None or rejeitado) and st.button(
                "Tentar de novo" if rejeitado else "Resumir os relatórios com IA",
                key=f"{chave}_botao"):
            with st.spinner("Lendo os relatórios…"):
                st.session_state[chave] = lr.gerar(analise, r)
    resumo = st.session_state.get(chave)
    if isinstance(resumo, lr.Resumo):
        st.markdown(cartao(resumo), unsafe_allow_html=True)
        return resumo
    return None
