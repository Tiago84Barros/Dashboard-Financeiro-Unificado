"""
views/inteligencia_ativos_fit.py
Bloco "Portfolio Fit" da aba Inteligência dos Ativos.

Mostra a pré-leitura de fit pelas regras da política (sempre) e, sob
demanda, a leitura por LLM validada por ``core/inteligencia_ativos/
portfolio_fit.py``. A chamada só acontece no botão: LLM na renderização
trava a tela e os testes headless.

As três dimensões aparecem lado a lado e nunca somadas: não há nota geral
nem ranking. Cada conclusão separa fato, interpretação, impacto na carteira
e ação a considerar.

O Cenário de Investimentos entra só como premissa. Quando os dados publicados
ou a própria LLM apontam contradição, a tela avisa (``aviso_cenario``) e manda
o usuário a Configurações; nada aqui altera o cenário.

Cada cartão sai num st.markdown só e usa apenas var(--app-*).
Coberto por tests/test_inteligencia_ativos_portfolio_fit.py.
"""
from __future__ import annotations

import hashlib
from html import escape

import streamlit as st

from core.inteligencia_ativos import modelos as m
from core.inteligencia_ativos import portfolio_fit as pf

_COR_NIVEL = {
    "forte": "primary", "atrativo": "primary", "alto": "primary",
    "adequada": "info", "neutro": "info", "medio": "info",
    "fraca": "danger", "esticado": "warning", "baixo": "danger",
    pf.INSUFICIENTE: "subtle",
}

_COR_STATUS = {pf.APROVADA: "primary", pf.COM_RESSALVAS: "warning",
               pf.REJEITADA: "danger"}
_ROTULO_STATUS = {pf.APROVADA: "Resposta validada",
                  pf.COM_RESSALVAS: "Resposta validada com ressalvas",
                  pf.REJEITADA: "Resposta rejeitada"}


def _caixa(corpo: str, *, borda: str = "border") -> str:
    return ('<div style="background:var(--app-surface);border:1px solid '
            f'var(--app-{borda});border-radius:12px;padding:14px 16px;'
            f'margin:8px 0 12px 0">{corpo}</div>')


def _titulo(texto: str, sub: str | None = None) -> str:
    s = ('<div style="font-size:1.02rem;font-weight:800;color:var(--app-text)">'
         f'{escape(texto)}</div>')
    if sub:
        s += ('<div style="font-size:0.8rem;color:var(--app-subtle);'
              f'margin:2px 0 8px 0">{escape(sub)}</div>')
    return s


def _itens(titulo: str, itens, cor: str = "text") -> str:
    itens = list(itens)
    if not itens:
        corpo = ('<div style="font-size:0.84rem;color:var(--app-subtle)">'
                 f'{escape(pf.NAO_DISPONIVEL)}</div>')
    else:
        corpo = "".join(
            '<li style="margin:2px 0;font-size:0.85rem;color:var(--app-text)">'
            f'{escape(str(x))}</li>' for x in itens)
        corpo = f'<ul style="margin:2px 0 0 0;padding-left:18px">{corpo}</ul>'
    return ('<div style="margin-top:10px"><div style="font-size:0.8rem;'
            f'font-weight:700;color:var(--app-{cor})">{escape(titulo)}</div>'
            f'{corpo}</div>')


def _paragrafo(titulo: str, texto: str) -> str:
    cor = "subtle" if texto in (pf.NAO_DISPONIVEL, pf.NAO_CONCLUSIVO) else "text"
    return ('<div style="margin-top:10px"><div style="font-size:0.8rem;'
            f'font-weight:700;color:var(--app-muted)">{escape(titulo)}</div>'
            f'<div style="font-size:0.86rem;color:var(--app-{cor});'
            f'line-height:1.45">{escape(texto)}</div></div>')


# -- cartões (puros) ---------------------------------------------------------------

def cartao_fit_regras(fit: pf.FitRegras) -> str:
    cor = _COR_NIVEL.get(fit.nivel, "text")
    corpo = (_titulo("Portfolio Fit pelas regras da sua política",
                     "Leitura determinística: limites, alvo da classe e papel "
                     "do ativo. Não olha fundamentos nem preço.")
             + '<div style="font-size:1.1rem;font-weight:800;'
             f'color:var(--app-{cor})">'
             f'{escape(pf.ROTULO_NIVEL[fit.nivel])}</div>')
    if fit.bloqueios:
        corpo += _itens("Limites da política violados", fit.bloqueios, "danger")
    corpo += _itens("A favor", fit.a_favor, "primary")
    corpo += _itens("Contra", fit.contra, "warning")
    return _caixa(corpo)


def cartao_dimensoes(leitura: pf.Leitura) -> str:
    blocos = ""
    for d in leitura.dimensoes:
        cor = _COR_NIVEL.get(d.nivel, "text")
        blocos += (
            '<div style="flex:1 1 220px;background:var(--app-surface-raised);'
            'border:1px solid var(--app-border);border-radius:10px;'
            'padding:10px 12px"><div style="font-size:0.78rem;font-weight:700;'
            f'color:var(--app-muted)">{escape(d.rotulo)}</div>'
            '<div style="font-size:1.05rem;font-weight:800;'
            f'color:var(--app-{cor});margin:2px 0 6px 0">'
            f'{escape(d.rotulo_nivel)}</div>'
            '<div style="font-size:0.8rem;color:var(--app-text)"><b>Fato:</b> '
            f'{escape(d.fato)}</div><div style="font-size:0.8rem;'
            'color:var(--app-muted);margin-top:4px"><b>Interpretação:</b> '
            f'{escape(d.interpretacao)}</div></div>')
    return _caixa(
        _titulo("Três dimensões independentes",
                "Não existe nota geral nem ranking: fundamentos, valuation e "
                "adequação à carteira podem apontar para lados diferentes.")
        + f'<div style="display:flex;flex-wrap:wrap;gap:8px">{blocos}</div>')


def cartao_conclusoes(leitura: pf.Leitura) -> str:
    th = ('<th style="text-align:left;padding:6px 8px;font-size:0.76rem;'
          'color:var(--app-muted);border-bottom:1px solid var(--app-border)">')
    td = ('<td style="vertical-align:top;padding:6px 8px;font-size:0.83rem;'
          'color:var(--app-text);border-bottom:1px solid var(--app-border)">')
    if not leitura.conclusoes:
        linhas = (f'<tr>{td}{escape(pf.NAO_CONCLUSIVO)}</td>{td}</td>{td}</td>'
                  f'{td}</td></tr>')
    else:
        linhas = "".join(
            "<tr>" + "".join(f"{td}{escape(x)}</td>" for x in
                             (c.fato, c.interpretacao, c.impacto, c.acao))
            + "</tr>" for c in leitura.conclusoes)
    cab = "".join(f"{th}{t}</th>" for t in (
        "Fato", "Interpretação", "Impacto na carteira", "Ação a considerar"))
    return _caixa(
        _titulo("Conclusões", "Cada linha separa o dado, a leitura dele, o "
                "que muda para a sua carteira e o que considerar.")
        + '<div style="overflow-x:auto"><table style="width:100%;'
        f'border-collapse:collapse"><thead><tr>{cab}</tr></thead>'
        f'<tbody>{linhas}</tbody></table></div>')


def cartao_detalhes(leitura: pf.Leitura) -> str:
    papeis = [m.PAPEIS.get(p, p) for p in leitura.papeis]
    corpo = (_titulo("Leitura completa")
             + '<div style="font-size:0.86rem;color:var(--app-text)">'
             f'<b>Tese:</b> {escape(pf.TESE.get(leitura.tese, leitura.tese))}'
             f' · <b>Ação a considerar:</b> {escape(leitura.rotulo_acao)}</div>'
             + _itens("Papel do ativo", papeis))
    for campo, titulo in (
            ("portfolio_impact", "Impacto na carteira"),
            ("scenario_impact", "Impacto do cenário"),
            ("fundamental_analysis", "Fundamentos"),
            ("valuation_analysis", "Valuation"),
            ("peer_analysis", "Comparação com pares"),
            ("market_behavior", "Liquidez, retorno e volatilidade (armazém)"),
            ("reasoning_summary", "Resumo do raciocínio")):
        corpo += _paragrafo(titulo, leitura.textos.get(campo, pf.NAO_DISPONIVEL))
    corpo += _itens("Riscos", leitura.listas.get("risks", ()), "warning")
    corpo += _itens("Oportunidades", leitura.listas.get("opportunities", ()),
                    "primary")
    corpo += _itens("Eventos a acompanhar",
                    leitura.listas.get("events_to_watch", ()))
    corpo += _itens("Lacunas de dado", leitura.listas.get("data_gaps", ()),
                    "subtle")
    return _caixa(corpo)


def cartao_validacao(leitura: pf.Leitura) -> str:
    cor = _COR_STATUS.get(leitura.status, "text")
    corpo = (_titulo("Validação da resposta",
                     "O código confere formato, valores permitidos, dimensões "
                     "sem dado e se cada número está no contexto enviado.")
             + f'<div style="font-weight:800;color:var(--app-{cor})">'
             f'{escape(_ROTULO_STATUS.get(leitura.status, leitura.status))}'
             '</div>')
    if leitura.divergencia_regras:
        corpo += _paragrafo("Divergência com as regras",
                            leitura.divergencia_regras)
    if leitura.correcoes:
        corpo += _itens("Corrigido pelo validador", leitura.correcoes, "warning")
    if leitura.problemas:
        corpo += _itens("Problemas na resposta", leitura.problemas, "danger")
    if leitura.numeros_sem_ancora:
        corpo += _itens("Números que não estão no contexto (possível "
                        "invenção; confira antes de usar)",
                        leitura.numeros_sem_ancora, "danger")
    return _caixa(corpo, borda="border" if leitura.status == pf.APROVADA
                  else cor)


def aviso_cenario(ctx: m.ContextoInvestidor,
                  leitura: pf.Leitura | None = None) -> str | None:
    """Aviso de revisão do cenário, ou None. Só avisa: quem muda é o usuário."""
    from core.cenario.modelo import FRASE_REVISAO

    motivos = [s.texto for s in ctx.sinais_cenario]
    if leitura is not None and leitura.revisao_cenario:
        motivos.append("A leitura por LLM apontou fatos que contradizem o "
                       "cenário lido dos dados.")
    if not motivos:
        return None
    corpo = (f'<div style="font-weight:800;color:var(--app-warning)">'
             f'{escape(FRASE_REVISAO)}</div>'
             + _itens("Por quê", motivos)
             + '<div style="font-size:0.82rem;color:var(--app-subtle);'
             'margin-top:6px">O cenário é lido dos dados e se atualiza '
             'sozinho quando as séries mudam; nada foi alterado.</div>')
    return _caixa(corpo, borda="warning")


def chave_sessao(analise: m.AnaliseAtivo, contexto: dict) -> str:
    """A leitura guardada vale só para este ativo, esta política e este
    contexto: mudou a carteira ou a estratégia, pede de novo."""
    h = hashlib.sha256(pf.contexto_json(contexto).encode("utf-8")).hexdigest()
    return f"ia_fit_{analise.ativo.ticker}_{analise.versao_politica}_{h[:12]}"


# -- tela ------------------------------------------------------------------------

def render(analise: m.AnaliseAtivo,
           ctx: m.ContextoInvestidor) -> pf.Leitura | None:
    """Desenha o bloco e devolve a leitura por LLM desta sessão, se houver."""
    from core.inteligencia_ativos import leitura_llm
    from core.llm_b3 import llm_disponivel

    st.markdown(cartao_fit_regras(pf.fit_por_regras(analise, ctx)),
                unsafe_allow_html=True)
    contexto = pf.contexto(analise, ctx)
    chave = chave_sessao(analise, contexto)

    if not llm_disponivel():
        st.caption("Leitura por LLM indisponível: nenhum provedor configurado.")
    elif st.button("Gerar leitura de Portfolio Fit", key=f"{chave}_botao",
                   type="primary"):
        with st.spinner("Lendo o ativo dentro da sua carteira…"):
            st.session_state[chave] = leitura_llm.gerar(analise, ctx)

    leitura = st.session_state.get(chave)
    aviso = aviso_cenario(ctx, leitura if isinstance(leitura, pf.Leitura)
                          else None)
    if aviso:
        st.markdown(aviso, unsafe_allow_html=True)
    if isinstance(leitura, pf.Leitura):
        if leitura.status != pf.REJEITADA:
            st.markdown(cartao_dimensoes(leitura), unsafe_allow_html=True)
            st.markdown(cartao_conclusoes(leitura), unsafe_allow_html=True)
            st.markdown(cartao_detalhes(leitura), unsafe_allow_html=True)
        st.markdown(cartao_validacao(leitura), unsafe_allow_html=True)

    with st.expander("Contexto estruturado que a LLM recebe"):
        st.caption("Só o que a análise acima já montou, sem o banco inteiro. "
                   "O Cenário de Investimentos vai em "
                   "scenario.cenario_do_investidor, só para leitura. O bloco "
                   "de contexto de mercado é anexado na hora da chamada.")
        st.json(contexto, expanded=False)
    return leitura if isinstance(leitura, pf.Leitura) else None
