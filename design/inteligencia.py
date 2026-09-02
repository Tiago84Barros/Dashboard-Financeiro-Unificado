"""Componentes visuais da Inteligência de Mercado.

Cor nunca é o único canal
-------------------------
Todo estado desta área -- qualidade do dado, frescor, situação do ativo, nível
de crise, canal do alerta -- chega à tela por **três** canais simultâneos:
ícone, texto e cor. Quem não distingue verde de vermelho lê o mesmo que os
demais, e quem lê num print em escala de cinza também.

O vocabulário não é redefinido aqui: ícone, rótulo e cor vêm de
``core.inteligencia.qualificacao.APARENCIA`` e vizinhos. Se este módulo
escolhesse os próprios ícones, a tela e a explicação da LLM passariam a falar
línguas diferentes sobre o mesmo dado.

Card sai num ``st.markdown`` só
-------------------------------
Div aberta num bloco e fechada em outro vira moldura vazia com o conteúdo caindo
fora da borda. Todo card daqui é montado como string única.
"""
from __future__ import annotations

import datetime as dt
from html import escape

import streamlit as st

from core.inteligencia import alertas as al
from core.inteligencia import painel as P
from core.inteligencia import qualificacao as qz

__all__ = [
    "selo_qualidade", "linha_valor", "card_valor", "grade_valores",
    "selo_frescor", "barra_frescor", "selo_provedor", "cabecalho_bloco",
    "bloco_completo", "selo_situacao", "cartao_noticia", "cartao_alerta",
    "area_tecnica", "aviso_sem_garantia",
]

AVISO_SEM_GARANTIA = (
    "Nada aqui é recomendação de compra ou venda, nem garantia de retorno. "
    "Estimativas são publicadas em faixa e podem estar erradas."
)


def _selo(icone: str, rotulo: str, cor: str, ajuda: str = "") -> str:
    ajuda_attr = f' title="{escape(ajuda, quote=True)}"' if ajuda else ""
    return (f'<span class="app-status-badge"{ajuda_attr} '
            f'style="--badge-color:{cor};--badge-bg:rgba(0,0,0,0.06)">'
            f'<span aria-hidden="true">{escape(icone)}</span> '
            f'{escape(rotulo)}</span>')


def selo_qualidade(qualidade: str) -> str:
    """Fato, hipótese, estimativa ou não medido -- em ícone, texto e cor."""
    ap = qz.APARENCIA[qualidade]
    return _selo(ap["icone"], ap["rotulo"], ap["cor"], ap.get("ajuda", ""))


def selo_frescor(frescor: qz.Frescor, agora: dt.datetime | None = None) -> str:
    ap = frescor.aparencia(agora)
    return _selo(ap["icone"], ap["rotulo"], ap["cor"], frescor.descrever(agora))


def selo_situacao(situacao: str) -> str:
    ap = P.APARENCIA_SITUACAO[situacao]
    return _selo(ap["icone"], ap["rotulo"], ap["cor"])


def selo_provedor(provedor: qz.Provedor) -> str:
    cor = "#00C896" if provedor.disponivel else "#FC5C7D"
    icone = "●" if provedor.disponivel else "✕"
    rotulo = f"{provedor.nome}: {'no ar' if provedor.disponivel else 'fora do ar'}"
    return _selo(icone, rotulo, cor, provedor.descrever())


def linha_valor(valor: qz.Valor) -> str:
    """Um valor em uma linha: rótulo, número, selo de qualidade e contexto."""
    extras: list[str] = []
    if valor.confianca:
        extras.append(f"confiança {valor.confianca}")
    if valor.horizonte:
        extras.append(f"horizonte {valor.horizonte}")
    if valor.fonte:
        extras.append(f"fonte: {valor.fonte}")
    if valor.observacao:
        extras.append(valor.observacao)
    rodape = (f'<div class="app-kpi-delta" style="color:#9CA3AF">'
              f'{escape(" · ".join(extras))}</div>' if extras else "")
    return (
        '<div class="app-kpi-card" style="--app-kpi-accent:'
        f'{valor.aparencia["cor"]}">'
        f'<div class="app-kpi-label">{escape(valor.rotulo)}</div>'
        f'<div class="app-kpi-value">{escape(valor.texto)}</div>'
        f'{selo_qualidade(valor.qualidade)}{rodape}</div>')


def card_valor(valor: qz.Valor) -> None:
    st.markdown(linha_valor(valor), unsafe_allow_html=True)


def grade_valores(valores, colunas: int = 3) -> None:
    """Grade de cards. Não medidos vão junto, e não no fim escondidos.

    Separar os ausentes numa gaveta faria a tela parecer completa. Eles ficam na
    mesma grade, com o selo "Não medido", porque a lacuna é informação.
    """
    valores = list(valores)
    if not valores:
        return
    for inicio in range(0, len(valores), colunas):
        faixa = valores[inicio:inicio + colunas]
        cols = st.columns(len(faixa))
        for col, valor in zip(cols, faixa):
            with col:
                card_valor(valor)


def barra_frescor(pn: P.Painel) -> None:
    """Última atualização, fontes vencidas e estado dos provedores."""
    ultima = pn.ultima_atualizacao
    texto = (ultima.strftime("%d/%m/%Y %H:%M UTC") if ultima
             else "nenhuma fonte informou data de atualização")
    selos = "".join(selo_frescor(f, pn.gerado_em) for f in pn.frescor)
    selos += "".join(selo_provedor(p) for p in pn.provedores)
    st.markdown(
        '<div class="app-kpi-card" style="--app-kpi-accent:#4A9EFF">'
        '<div class="app-kpi-label">Última atualização (fonte mais antiga)'
        '</div>'
        f'<div class="app-kpi-value">{escape(texto)}</div>{selos}</div>',
        unsafe_allow_html=True)

    if pn.desatualizados or pn.provedores_fora:
        nomes = [f.rotulo for f in pn.desatualizados]
        nomes += [p.nome for p in pn.provedores_fora]
        st.warning(
            "**Dados desatualizados ou indisponíveis:** " + ", ".join(nomes)
            + ". A ausência de informação aqui não significa ausência de risco.")


def cabecalho_bloco(bloco: qz.Bloco, agora: dt.datetime | None = None) -> None:
    partes = [f'<span class="app-section-title">{escape(bloco.titulo)}</span>']
    partes.append(_selo("◑", f"cobertura {bloco.cobertura:.0%}", "#4A9EFF",
                        "fração dos componentes que foi possível medir"))
    if bloco.frescor is not None:
        partes.append(selo_frescor(bloco.frescor, agora))
    st.markdown('<div class="app-section-heading">' + "".join(partes) + "</div>",
                unsafe_allow_html=True)
    if bloco.explicacao_simples:
        st.caption(bloco.explicacao_simples)


def area_tecnica(bloco: qz.Bloco, chave: str = "") -> None:
    """A área que se expande. O simples fica fora; o técnico, dentro."""
    if not bloco.detalhe_tecnico and not bloco.limitacoes:
        return
    with st.expander(f"Detalhe técnico — {bloco.titulo}", expanded=False):
        if bloco.detalhe_tecnico:
            st.markdown("**Como este resultado foi obtido**")
            for linha in bloco.detalhe_tecnico:
                st.markdown(f"- {linha}")
        if bloco.limitacoes:
            st.markdown("**Limitações declaradas**")
            for linha in bloco.limitacoes:
                st.markdown(f"- {linha}")
        if bloco.nao_medidos:
            st.markdown("**Não medido nesta execução**")
            for v in bloco.nao_medidos:
                st.markdown(f"- {v.rotulo}: {v.observacao or 'sem fonte'}")


def bloco_completo(bloco: qz.Bloco, *, colunas: int = 3,
                   agora: dt.datetime | None = None) -> None:
    cabecalho_bloco(bloco, agora)
    grade_valores(bloco.valores, colunas=colunas)
    if bloco.limitacoes:
        st.caption("Limitações: " + " · ".join(bloco.limitacoes))
    area_tecnica(bloco)


def cartao_noticia(item: P.ItemNoticia) -> None:
    """Fonte, data e hora sempre visíveis -- e o estado de verificação junto."""
    selo = selo_qualidade(item.qualidade_conteudo)
    verif = _selo("✓" if item.confirmado else "?",
                  "Confirmado" if item.confirmado else "Não confirmado",
                  "#00C896" if item.confirmado else "#E8B84B",
                  item.estado_verificacao)
    marcas = ""
    for v in item.valores():
        marcas += (f'<div class="app-kpi-delta" style="color:#9CA3AF">'
                   f'{escape(v.descrever())}</div>')
    link = (f'<div class="app-kpi-delta"><a href="{escape(item.url, quote=True)}"'
            ' target="_blank" rel="noopener">abrir na fonte</a></div>'
            if item.url else "")
    st.markdown(
        '<div class="app-kpi-card" style="--app-kpi-accent:#4A9EFF">'
        f'<div class="app-kpi-value">{escape(item.titulo)}</div>'
        f'<div class="app-kpi-label">{escape(item.carimbo)}</div>'
        f'{selo}{verif}{marcas}{link}</div>',
        unsafe_allow_html=True)


def cartao_alerta(alerta: al.Alerta) -> None:
    ap = alerta.aparencia
    cor = {0: "#9CA3AF", 1: "#4A9EFF", 2: "#E8B84B",
           3: "#FC5C7D", 4: "#FC5C7D"}.get(alerta.nivel_codigo, "#9CA3AF")
    st.markdown(
        f'<div class="app-kpi-card" style="--app-kpi-accent:{cor}">'
        f'<div class="app-kpi-value">{escape(alerta.titulo)}</div>'
        f'<div class="app-kpi-label">{escape(alerta.corpo)}</div>'
        + _selo(ap["icone"], ap["rotulo"], cor, alerta.motivo_canal)
        + f'<div class="app-kpi-delta" style="color:#9CA3AF">'
          f'{escape(alerta.motivo_canal)}</div></div>',
        unsafe_allow_html=True)


def aviso_sem_garantia() -> None:
    st.caption(AVISO_SEM_GARANTIA)
