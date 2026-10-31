"""design/relatorio_analista.py — o relatório de analista do ativo na tela.

Só monta HTML: a conclusão, a tese, os riscos, as faixas e os gatilhos vêm
prontos de ``core.inteligencia_ativos.relatorio_analista``, que usa a mesma
avaliação e o mesmo limite da aba Inteligência dos Ativos. A tela não refaz
conta nenhuma.

As limitações do relatório não vão para a tela (regra de 05/10/2026): vão
para Configurações → Restrições. Por isso o gatilho sem dado para conferir não
aparece aqui (ele está nas limitações), e a amostra das faixas, a régua
setorial e a procedência de cada número ficam só no texto da LLM (como na
aba Inteligência resumida, que registra a régua como detalhe técnico). Cada card sai numa string só, para um único
``st.markdown``: abrir a ``<div>`` num bloco e fechar em outro já produziu
moldura vazia neste projeto.
"""
from __future__ import annotations

import time

import streamlit as st

from core.inteligencia_ativos import avaliacao as av_
from core.inteligencia_ativos import relatorio_analista as ra
from core.inteligencia_ativos import veredito as vd
from core.lacunas import registrar_limitacoes
from design.componentes import _linha, cor_token
from design.lacunas import falha_de_acao

MODULO = "design/relatorio_analista.py"

#: O relatório lê notícias e vitrines que mudam no dia: uma hora de sessão
#: poupa o rerun sem segurar uma leitura velha.
TTL_CACHE_S = 3600
_CHAVE_CACHE = "relatorio_analista_cache"

# Fundo, borda e texto como token: estilo inline só muda de tema pelo token.
_FUNDO = "var(--app-surface, #12151E)"
_ELEVADO = "var(--app-surface-raised, #1A1F2B)"
_BORDA = "var(--app-border, #1E2533)"
_TEXTO = "var(--app-text, #E2E8F0)"
_MUDO = "var(--app-muted, #9CA3AF)"

POSITIVO = cor_token("#00C896")
NEGATIVO = cor_token("#FC5C7D")
ALERTA = cor_token("#F6C90E")
INFO = cor_token("#4A9EFF")
NEUTRO = cor_token("#9CA3AF")

#: Símbolo, cor e frase de cada limite: a cor nunca é o único canal.
APARENCIA_LIMITE: dict[str, tuple[str, str, str]] = {
    vd.TROCAR: ("⊘", NEGATIVO, "as regras da avaliação pedem rever a posição"),
    vd.REDUZIR: ("▼", ALERTA, "as regras da avaliação pedem reduzir a posição"),
    vd.NAO_APORTAR: ("■", ALERTA, "as regras da avaliação não sustentam novo "
                                  "aporte"),
    vd.LIVRE: ("✓", POSITIVO, "nenhuma regra eliminatória disparou"),
}

_BOA = {av_.ROTULO_LEITURA[k] for k in (av_.FORTE, av_.BARATO, av_.POSITIVO)}
_RUIM = {av_.ROTULO_LEITURA[k] for k in (av_.FRAGIL, av_.CARO, av_.NEGATIVO)}


def _t(texto: object) -> str:
    """Escapa, achata e tira o cifrão: dois ``$`` no mesmo bloco viram
    fórmula no markdown do Streamlit, e manchete americana tem cifrão."""
    return _linha(texto).replace("$", "&#36;")


def _card(conteudo: str, cor: str = INFO) -> str:
    return (f'<div class="app-relatorio-card" style="background-color:{_FUNDO};'
            f'border:1px solid {_BORDA};border-left:4px solid {cor};'
            'border-radius:10px;padding:14px 16px;margin-bottom:12px;'
            f'color:{_TEXTO};">{conteudo}</div>')


def _titulo(texto: str) -> str:
    return (f'<div style="font-size:0.72rem;font-weight:800;letter-spacing:.04em;'
            f'text-transform:uppercase;color:{_MUDO};margin-bottom:8px;">'
            f'{_t(texto)}</div>')


def _selo(icone: str, rotulo: str, cor: str) -> str:
    return (f'<span class="app-status-badge" style="--badge-color:{cor};'
            '--badge-bg:rgba(0,0,0,0.06)">'
            f'<span aria-hidden="true">{_t(icone)}</span> {_t(rotulo)}</span>')


def _cor_leitura(rotulo: str) -> tuple[str, str]:
    if rotulo in _BOA:
        return "▲", POSITIVO
    if rotulo in _RUIM:
        return "▼", NEGATIVO
    return "●", NEUTRO


def _sinal(p: float) -> str:
    return ra._sinal(p).replace("-", "−")


# -- cards ---------------------------------------------------------------------------

def card_cabecalho(rel: ra.RelatorioAnalista) -> str:
    icone, cor, frase = APARENCIA_LIMITE.get(
        rel.limite, ("·", NEUTRO, ""))
    motivo = (f'<div style="font-size:0.85rem;margin-top:8px;">'
              f'<strong>Por quê:</strong> {_t(rel.para_tela(rel.motivo_limite))}'
              '</div>'
              if rel.motivo_limite else "")
    nome = (f' <span style="font-weight:600;color:{_MUDO};">— {_t(rel.nome)}'
            '</span>' if rel.nome and rel.nome != rel.ticker else "")
    return _card(
        '<div style="display:flex;flex-wrap:wrap;justify-content:space-between;'
        'align-items:flex-start;gap:10px;">'
        f'<div style="font-size:1.1rem;font-weight:800;">{_t(rel.ticker)}'
        f'{nome}</div>'
        f'<div>{_selo(icone, "Limite: " + rel.rotulo_limite, cor)}</div></div>'
        f'<div style="font-size:0.8rem;color:{_MUDO};margin-top:6px;">'
        f'{_t(frase)}</div>{motivo}', cor)


def card_leituras(rel: ra.RelatorioAnalista) -> str:
    blocos = []
    for le in rel.leituras:
        icone, cor = _cor_leitura(le.leitura)
        nota = (f'<div style="font-size:0.75rem;color:{_MUDO};margin-top:6px;">'
                f'{_t(rel.para_tela(le.nota))}</div>' if le.nota else "")
        blocos.append(
            f'<div style="flex:1 1 180px;background-color:{_ELEVADO};'
            'border-radius:8px;padding:10px 12px;">'
            f'<div style="font-size:0.72rem;color:{_MUDO};">{_t(le.dimensao)}'
            '</div>'
            f'<div style="font-size:0.95rem;font-weight:800;color:{cor};'
            f'margin-top:2px;"><span aria-hidden="true">{icone}</span> '
            f'{_t(le.leitura)}</div>'
            f'<div style="font-size:0.75rem;color:{_MUDO};margin-top:2px;">'
            f'{le.favoraveis} a favor · {le.desfavoraveis} contra</div>'
            f'{nota}</div>')
    return _card(_titulo("Leitura por dimensão")
                 + '<div style="display:flex;flex-wrap:wrap;gap:10px;">'
                 + "".join(blocos) + "</div>")


def card_lista(titulo: str, itens, cor: str, icone: str,
               vazio: str = "Nenhum ponto apurado nos dados de hoje.") -> str:
    if itens:
        corpo = "".join(
            '<li style="margin:4px 0;list-style:none;">'
            f'<span aria-hidden="true" style="color:{cor};">{icone}</span> '
            f'{_t(x)}</li>' for x in itens)
        corpo = (f'<ul style="margin:0;padding:0;font-size:0.85rem;">{corpo}'
                 '</ul>')
    else:
        corpo = (f'<div style="font-size:0.85rem;color:{_MUDO};">{_t(vazio)}'
                 '</div>')
    return _card(_titulo(titulo) + corpo, cor)


def card_faixas(rel: ra.RelatorioAnalista) -> str:
    linhas = []
    for fx in rel.faixas:
        linhas.append(
            f'<div style="padding:8px 0;border-top:1px solid {_BORDA};">'
            f'<div style="font-size:0.9rem;"><strong>{_t(fx.rotulo)}</strong> '
            f'hoje {_t(fx.atual)} · interquartil {_t(fx.intervalo)}</div>'
            '<div style="font-size:0.9rem;font-weight:800;margin-top:2px;">'
            f'Preço {_t(_sinal(fx.variacao_min))} a '
            f'{_t(_sinal(fx.variacao_max))}</div>'
            f'<div style="font-size:0.75rem;color:{_MUDO};margin-top:2px;">'
            f'{_t(fx.premissa)}</div></div>')
    divergencia = (
        f'<div style="font-size:0.8rem;margin-top:8px;color:{ALERTA};">'
        f'<span aria-hidden="true">⚠</span> {_t(rel.leitura_faixas)}</div>'
        if rel.leitura_faixas else "")
    return _card(_titulo("Onde o preço está contra o próprio histórico")
                 + "".join(linhas) + divergencia, INFO)


def aparencia_gatilho(g: ra.Gatilho) -> tuple[str, str, str]:
    """(símbolo, cor, estado). Só para gatilho conferido: o sem dado é
    limitação e vai para Restrições."""
    if g.disparado:
        return (("⊘", NEGATIVO, "disparado") if g.elimina
                else ("⚠", ALERTA, "pede revisão"))
    return "✓", POSITIVO, "não disparado"


def card_gatilhos(rel: ra.RelatorioAnalista) -> str:
    conferidos = sorted((g for g in rel.gatilhos if g.disparado is not None),
                        key=lambda g: not g.disparado)
    if not conferidos:
        corpo = (f'<div style="font-size:0.85rem;color:{_MUDO};">'
                 'Nenhum gatilho conferido com os dados de hoje.</div>')
    else:
        corpo = ""
        for g in conferidos:
            icone, cor, estado = aparencia_gatilho(g)
            corpo += (
                f'<div style="padding:8px 0;border-top:1px solid {_BORDA};">'
                '<div style="display:flex;flex-wrap:wrap;justify-content:'
                'space-between;gap:6px;">'
                f'<div style="font-size:0.9rem;"><strong>{_t(g.indicador)}'
                f'</strong> · hoje {_t(g.atual)}</div>'
                f'<div>{_selo(icone, estado, cor)}</div></div>'
                f'<div style="font-size:0.75rem;color:{_MUDO};margin-top:2px;">'
                f'Regra: {_t(g.regra)}</div></div>')
    cor = (NEGATIVO if any(g.disparado and g.elimina for g in rel.gatilhos)
           else ALERTA if rel.disparados else POSITIVO)
    return _card(_titulo("O que mudaria a leitura (gatilhos)") + corpo, cor)


def card_noticias(rel: ra.RelatorioAnalista) -> str:
    return card_lista("Notícias de impacto", rel.noticias, INFO, "•")


def cards(rel: ra.RelatorioAnalista) -> list[str]:
    """Os cards do relatório, na ordem da tela (sem as duas colunas)."""
    def tela(itens):
        return [rel.para_tela(x) for x in itens]

    saida = [card_cabecalho(rel), card_leituras(rel)]
    saida.append(card_lista("Tese — o que sustenta", tela(rel.tese), POSITIVO,
                            "▲"))
    saida.append(card_lista("Riscos — o que pode dar errado",
                            tela(rel.riscos), NEGATIVO, "▼"))
    if rel.contexto:
        saida.append(card_lista("Contexto", tela(rel.contexto), NEUTRO, "●"))
    if rel.faixas:
        saida.append(card_faixas(rel))
    saida.append(card_gatilhos(rel))
    if rel.noticias:
        saida.append(card_noticias(rel))
    return saida


def render_relatorio(rel: ra.RelatorioAnalista) -> None:
    """O relatório na tela; as limitações vão para Restrições."""
    registrar_limitacoes(rel, modulo=MODULO, entidade=rel.ticker)
    todos = cards(rel)
    cabecalho, leituras, tese, riscos, *resto = todos
    st.markdown(cabecalho, unsafe_allow_html=True)
    st.markdown(leituras, unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(tese, unsafe_allow_html=True)
    with c2:
        st.markdown(riscos, unsafe_allow_html=True)
    for card in resto:
        st.markdown(card, unsafe_allow_html=True)
    st.caption(rel.aviso)


def _relatorio_em_cache(ticker: str, nome: str | None, setor: str | None,
                        mercado: str) -> ra.RelatorioAnalista:
    """O relatório do ativo, com cache de sessão. Falha não é guardada: o
    próximo rerun tenta de novo."""
    cache = st.session_state.setdefault(_CHAVE_CACHE, {})
    chave = (mercado, str(ticker).strip().upper())
    guardado = cache.get(chave)
    if guardado is not None and time.time() - guardado[0] < TTL_CACHE_S:
        return guardado[1]
    with st.spinner("Montando o relatório do analista…"):
        rel = ra.montar_relatorio(chave[1], nome or None, setor or None,
                                  mercado=mercado)
    cache[chave] = (time.time(), rel)
    return rel


def secao_relatorio(ticker: str, nome: str | None = None,
                    setor: str | None = None, *, mercado: str) -> str:
    """Mostra o relatório do ativo e devolve o texto dele para o contexto do
    chat do ativo (premissa máxima: a LLM recebe o que a tela concluiu). A
    falha é nomeada no texto, nunca some."""
    tk = str(ticker or "").strip().upper()
    try:
        rel = _relatorio_em_cache(tk, nome, setor, mercado)
    except Exception as exc:  # noqa: BLE001 - a tela segue sem o relatório
        falha_de_acao("Não foi possível montar o relatório do analista agora.",
                      exc)
        return (f"[Relatório de analista (regras) — {tk}]: falha ao montar "
                f"({type(exc).__name__}).")
    render_relatorio(rel)
    return ra.texto(rel)
