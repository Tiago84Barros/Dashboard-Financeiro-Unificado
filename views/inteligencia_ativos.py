"""
views/inteligencia_ativos.py
Aba "Inteligência dos Ativos" de Investimentos.

Depende da Estratégia de Investimentos (Configurações → Geral). Sem uma
estratégia concluída, a aba continua visível (com 🔒 no rótulo) e mostra o
caminho para liberá-la: o que falta, quanto já foi feito e um botão que leva
direto ao bloco da estratégia. Não é erro nem página vazia; é a etapa que
falta para a análise ser do usuário, e não genérica.

A decisão de liberar é de ``core/estrategia/portao.py``; a análise passa por
``core/inteligencia_ativos.py``, que pergunta ao portão de novo. Esta tela
não decide nada sozinha.

Coberto por tests/test_inteligencia_ativos_tela.py.
"""
from __future__ import annotations

from html import escape

import streamlit as st

from core import inteligencia_ativos as servico
from core.estrategia import politica as pol
from core.estrategia import portao

ROTULO = "Inteligência dos Ativos"
# Mesmos valores de app.py (menu) e views/configuracoes.py (flag lida lá).
NAVEGACAO_KEY = "app_main_navigation"
ROTA_CONFIGURACOES = "⚙️ Configurações"
VEIO_DA_ANALISE = "cfg_estrategia_veio_da_analise"

_BOTAO = {
    pol.NOT_STARTED: "Configurar minha estratégia",
    pol.IN_PROGRESS: "Continuar configuração",
    pol.NEEDS_REVIEW: "Revisar minha estratégia",
}


def rotulo_aba(liberacao: portao.Liberacao) -> str:
    icone = "🧠" if liberacao.disponivel else "🔒"
    return f"{icone}  {ROTULO}"


def render(liberacao: portao.Liberacao, carteira: dict | None = None) -> None:
    if liberacao.disponivel:
        _render_liberada(liberacao, carteira or {})
    else:
        _render_onboarding(liberacao)


# -- bloqueada: onboarding -----------------------------------------------------

def _titulo_e_texto(liberacao: portao.Liberacao) -> tuple[str, str]:
    if liberacao.motivo == portao.REVISAO_NECESSARIA:
        return ("Revise sua estratégia para liberar esta análise",
                "Sua estratégia de investimentos precisa de uma revisão antes "
                "de voltar a orientar a análise dos ativos. Confirme ou ajuste "
                "as respostas e conclua de novo.")
    return ("Configure sua estratégia para liberar esta análise",
            "Antes de analisar individualmente os ativos da sua carteira, "
            "precisamos entender o que você pretende construir com seus "
            "investimentos.<br><br>Essas informações permitem que a "
            "inteligência do sistema avalie cada ativo dentro do contexto da "
            "sua carteira, dos seus objetivos e do seu horizonte de "
            "investimento.")


def checklist(liberacao: portao.Liberacao) -> list[tuple[str, bool]]:
    """(campo mínimo, já respondido) na ordem do roteiro. Puro."""
    faltantes = set(liberacao.faltantes)
    return [(c, c not in faltantes) for c in pol.OBRIGATORIOS]


def cartao_onboarding(liberacao: portao.Liberacao) -> str:
    """HTML do cartão de onboarding, num bloco só. Puro."""
    titulo, texto = _titulo_e_texto(liberacao)
    caminho = " → ".join(f"<strong>{escape(p)}</strong>"
                         for p in portao.CAMINHO)
    itens = ""
    for chave, feito in checklist(liberacao):
        marca, cor = ("✓", "var(--app-primary)") if feito else ("○", "var(--app-muted)")
        itens += (f'<li style="list-style:none;margin:4px 0;color:{cor}">'
                  f'{marca} {escape(pol.POR_CHAVE[chave].rotulo)}</li>')
    return (
        '<div style="background:var(--app-surface);'
        'border:1px solid var(--app-border);border-radius:12px;'
        'padding:20px 22px;margin:8px 0 14px 0;">'
        '<div style="font-size:0.78rem;font-weight:600;letter-spacing:.04em;'
        'text-transform:uppercase;color:var(--app-info)">'
        'Falta uma etapa para personalizar suas análises</div>'
        '<div style="font-size:1.25rem;font-weight:800;color:var(--app-text);'
        f'margin:6px 0 10px 0">{escape(titulo)}</div>'
        '<div style="font-size:0.92rem;color:var(--app-muted);'
        f'line-height:1.5">{texto}</div>'
        '<div style="font-size:0.88rem;color:var(--app-text);'
        f'margin-top:14px">📍 Onde resolver: {caminho}</div>'
        '<div style="font-size:0.88rem;font-weight:600;color:var(--app-text);'
        'margin-top:14px">Para liberar a análise:</div>'
        f'<ul style="padding-left:4px;margin:6px 0 0 0">{itens}</ul>'
        '<div style="font-size:0.82rem;color:var(--app-subtle);'
        'margin-top:12px">O que será liberado: a leitura de cada ativo da '
        'carteira à luz do seu objetivo, horizonte, risco e alocação '
        'desejada.</div>'
        '</div>'
    )


def _ir_para_estrategia() -> None:
    """Callback do botão: troca a rota antes de o menu ser desenhado."""
    st.session_state[NAVEGACAO_KEY] = ROTA_CONFIGURACOES
    st.session_state[VEIO_DA_ANALISE] = True


def _render_onboarding(liberacao: portao.Liberacao) -> None:
    if liberacao.motivo == portao.ESTRATEGIA_INDISPONIVEL:
        st.info("Não foi possível ler sua estratégia de investimentos agora. "
                "A análise dos ativos depende dela; tente de novo em "
                "instantes. Se persistir, o administrador precisa conferir a "
                "tabela da estratégia (migration 076).")
        return
    st.markdown(cartao_onboarding(liberacao), unsafe_allow_html=True)
    st.progress(liberacao.pct / 100,
                text=f"Configuração da estratégia: {liberacao.pct:.0f}% concluída")
    st.button(_BOTAO.get(liberacao.status, _BOTAO[pol.NOT_STARTED]),
              key="ia_ir_para_estrategia", type="primary",
              on_click=_ir_para_estrategia)


# -- liberada ------------------------------------------------------------------

def _render_liberada(liberacao: portao.Liberacao, carteira: dict) -> None:
    versao = liberacao.politica.version
    st.success("Configuração concluída. A análise inteligente dos seus ativos "
               f"já está disponível. Premissa: estratégia versão {versao}.")
    posicoes = carteira.get("posicoes") or []
    if not posicoes:
        st.info("Nenhum ativo na carteira para analisar.")
        return
    opcoes = {f"{p['ticker']} · {p.get('nome') or p['ticker']}": p["ticker"]
              for p in posicoes}
    escolha = st.selectbox("Ativo", list(opcoes), key="ia_ativo")
    if st.button("Analisar ativo", key="ia_analisar"):
        resultado = servico.analisar_ativo(opcoes[escolha], carteira=carteira)
        _render_resultado(resultado)


def _render_resultado(resultado: dict) -> None:
    if not resultado.get("analysis_available"):
        # O portão fechou entre o desenho da aba e o clique (outra aba
        # concluiu uma edição, a política venceu). Ou o ativo saiu da carteira.
        if resultado.get("reason") == servico.ATIVO_FORA_DA_CARTEIRA:
            st.info("Este ativo não está mais na carteira.")
        else:
            st.info("Sua estratégia mudou de situação. Recarregue a página.")
        return
    if resultado.get("analysis") is None:
        st.info("A análise individual por LLM é a próxima etapa desta aba. "
                "Ela vai partir obrigatoriamente da sua estratégia, lida "
                "assim:")
        st.code(resultado["policy_context"], language=None)
