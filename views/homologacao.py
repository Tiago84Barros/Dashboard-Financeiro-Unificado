"""Homologação: fase corrente, as nove chaves e o que falta para avançar.

Por que esta tela é de leitura, e o interruptor mora na configuração
--------------------------------------------------------------------
O requisito pede *"tela de administração ou configuração segura"*. Aqui a
segunda opção é a segura: a fase e as flags vêm de ``APP4_FASE`` e
``APP4_FLAG_*``, lidas por :func:`core.config._get_secret`.

Um botão que liberasse o Modo Crise a partir da tela seria um botão de
liberação de decisão real ao alcance de qualquer sessão aberta -- inclusive uma
sessão esquecida numa máquina emprestada. O app **não tem** controle de acesso
por papel (não existe usuário administrador separado do usuário comum), então
não há como restringir esse botão a quem de direito. Enquanto isso for verdade,
a tela mostra e explica; quem muda é quem tem acesso aos secrets do deploy.

Isso está registrado como limitação em ``docs/homologacao_app4.md``, e não
disfarçado de decisão de produto.

Sinal visual sem depender de cor
--------------------------------
Cada chave traz um símbolo textual (``✓`` ligada, ``·`` desligada, ``⊘`` barrada
pela fase) além do badge. O requisito do Prompt 2 é explícito: a interface não
pode depender apenas de verde e vermelho.
"""
from __future__ import annotations

import json

import streamlit as st

from core.homologacao import criterios as C
from core.homologacao import flags as F
from design.componentes import (
    badge_status,
    card_metrica,
    container_pagina,
    secao_titulo,
)

SIMBOLO_LIGADA = "✓"
SIMBOLO_DESLIGADA = "·"
SIMBOLO_BARRADA = "⊘"


def _simbolo(estado: F.Estado, nome: str) -> tuple[str, str, str]:
    """(símbolo, rótulo textual, tipo do badge) para uma chave."""
    if estado.ativo(nome):
        return SIMBOLO_LIGADA, "ligada", "sucesso"
    if nome in estado.barradas_pela_fase:
        return SIMBOLO_BARRADA, "barrada pela fase", "alerta"
    return SIMBOLO_DESLIGADA, "desligada", "neutro"


def render_fase(estado: F.Estado) -> None:
    secao_titulo("Fase corrente", "🚦")
    col1, col2, col3 = st.columns(3)
    with col1:
        card_metrica("Fase", F.NOME_FASE[estado.fase].split("—")[0].strip(),
                     ajuda=F.DESCRICAO_FASE[estado.fase])
    with col2:
        card_metrica("Funcionalidades ativas",
                     f"{len(estado.ligadas)} de {len(F.CHAVES)}")
    with col3:
        card_metrica("Ligadas e barradas pela fase",
                     len(estado.barradas_pela_fase),
                     ajuda="alguém quis ligar e a fase não alcança")
    st.caption(F.DESCRICAO_FASE[estado.fase])
    st.caption(
        f"A fase vem da variável `{F.VARIAVEL_FASE}`. Valor ausente, ilegível "
        "ou fora de 1..4 cai na Fase 1 — o lado seguro.")


def render_chaves(estado: F.Estado) -> None:
    secao_titulo("As nove chaves", "🔑")
    st.caption(
        f"{SIMBOLO_LIGADA} ligada · {SIMBOLO_DESLIGADA} desligada · "
        f"{SIMBOLO_BARRADA} ligada na configuração, barrada pela fase. "
        "O símbolo acompanha a cor de propósito: a leitura não pode depender "
        "de distinguir verde de vermelho.")
    for nome, chave in F.CHAVES.items():
        simbolo, rotulo, tipo = _simbolo(estado, nome)
        with st.container(border=True):
            esq, dir_ = st.columns([4, 1])
            with esq:
                st.markdown(f"**{simbolo} {chave.rotulo}**")
                st.caption(f"Exige {chave.rotulo_fase}. Variável: "
                           f"`{chave.variavel}`.")
                motivo = estado.motivo(nome)
                st.caption(f"Quando desligada, {chave.efeito}."
                           + (f" Agora: {motivo}." if motivo else ""))
            with dir_:
                badge_status(rotulo, tipo)


def render_avanco(estado: F.Estado) -> None:
    secao_titulo("O que falta para avançar", "📋")
    alvo = estado.fase + 1
    if alvo not in C.EXIGIDO:
        st.caption("A Fase 4 é a última. Não há avanço a medir.")
        return

    st.caption(
        f"Para liberar **{F.NOME_FASE[alvo]}**, todos os critérios abaixo "
        "precisam estar **medidos** e atendidos. Critério não medido não "
        "avança a fase e também não reprova o sistema: ele diz que o teste "
        "ainda não foi feito.")
    for c in C.EXIGIDO[alvo]:
        alvo_txt = "≥" if c.sentido == C.MAIOR_MELHOR else "≤"
        with st.container(border=True):
            st.markdown(f"**{c.nome}** — exigido {alvo_txt} {c.limiar}"
                        f"{c.unidade}")
            st.caption(f"Por quê: {c.justificativa}.")
            st.caption("Situação: não medido nesta instalação.")
    st.caption(
        "A medição destes critérios ainda não está automatizada: ela sai dos "
        "testes e das rotinas de calibração, e é registrada manualmente antes "
        "de mudar a fase. Enquanto isso, a fase só muda por quem tem acesso "
        "aos secrets do deploy.")


def render_rollback(estado: F.Estado) -> None:
    secao_titulo("Rollback", "↩️")
    volta = C.rollback(estado)
    st.caption(
        f"Voltar para **{F.NOME_FASE[volta.fase]}** significa mudar "
        f"`{F.VARIAVEL_FASE}` para `{volta.fase}` e reiniciar o app. As flags "
        "não precisam ser mexidas: a fase menor já desliga o que ela não "
        "alcança, e reconfigurar nove chaves no pior momento possível é como "
        "um rollback vira um segundo incidente.")
    perdidas = tuple(n for n in estado.ligadas if not volta.ativo(n))
    if perdidas:
        st.caption("Sairiam do ar imediatamente: "
                   + ", ".join(F.CHAVES[n].rotulo for n in perdidas) + ".")
    else:
        st.caption("Nada sairia do ar — nesta fase não há funcionalidade "
                   "liberada que a fase anterior não alcance.")


def render_auditoria(estado: F.Estado) -> None:
    with st.expander("Área técnica — estado exato lido da configuração"):
        st.caption("Nenhum valor de secret aparece aqui: só o nome da variável "
                   "e o efeito do que foi lido.")
        st.code(json.dumps(estado.resumo_auditoria(), ensure_ascii=False,
                           indent=2), language="json")


def render() -> None:
    container_pagina(
        "Homologação e liberação gradual",
        "Em que fase o APP4 está, o que cada chave libera, e o que falta "
        "medir para avançar. Esta tela não liga nada — ela mostra.",
        icone="🚦")

    estado = F.carregar()
    render_fase(estado)
    render_chaves(estado)
    render_avanco(estado)
    render_rollback(estado)
    render_auditoria(estado)
