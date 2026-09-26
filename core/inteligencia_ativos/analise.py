"""
core/inteligencia_ativos/analise.py
Monta a ``AnaliseAtivo`` de uma posição, sempre dentro do contexto do
investidor.

``analisar`` exige um ``ContextoInvestidor``, e esse contexto só nasce de uma
política concluída com a carteira completa (``contexto.montar``). Não há
caminho para analisar um ativo isolado.

Fluxo::

    posição da carteira ─► InfoBasica ─► papéis ─► faixa desejada
         ─► seções externas (provedores) ─► tese ─► impacto ─► ação
         ─► as quatro perguntas

``texto_para_llm`` junta política + carteira + a análise estruturada: é a
entrada da etapa de LLM, que vai redigir a leitura sem poder sair do que
está ali.

Puro. Coberto por tests/test_inteligencia_ativos_adequacao.py.
"""
from __future__ import annotations

from core.inteligencia_ativos import (adequacao, calculos, fundamentos,
                                      informacoes, pares, papeis, secoes,
                                      valuation)
from core.inteligencia_ativos.contexto import classe_politica, texto_carteira
from core.inteligencia_ativos.modelos import (
    AnaliseAtivo,
    ContextoInvestidor,
    InfoBasica,
    Papel,
)

# A subclasse é calculada em calculos.py (a alocação por subclasse usa a
# mesma regra); reexportada aqui.
subclasse = calculos.subclasse


def _num(x) -> float | None:
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def info_basica(posicao: dict, peso: float | None = None) -> InfoBasica:
    """``peso`` é o de ``calculos.pesos``; sem ele (uso avulso), vale o
    ``pct_carteira`` que veio da carteira."""
    ticker = str(posicao.get("ticker") or "").strip().upper()
    if peso is None:
        peso = _num(posicao.get("pct_carteira")) or 0.0
    return InfoBasica(
        ticker=ticker,
        nome=str(posicao.get("nome") or ticker),
        classe=str(posicao.get("classe") or "Outros"),
        classe_politica=classe_politica(posicao),
        subclasse=subclasse(posicao),
        setor=posicao.get("setor") or None,
        moeda=str(posicao.get("moeda") or "BRL").upper(),
        valor_investido=_num(posicao.get("total_investido")),
        valor_mercado=_num(posicao.get("valor_mercado")),
        peso_atual=round(peso, 2),
    )


def analisar(posicao: dict, ctx: ContextoInvestidor, *,
             papeis_usuario: tuple[Papel, ...] | None = None) -> AnaliseAtivo:
    if not isinstance(ctx, ContextoInvestidor):
        raise TypeError("analisar exige o ContextoInvestidor (política "
                        "concluída + carteira completa).")
    calc = ctx.calculos.peso(str(posicao.get("ticker") or ""))
    info = info_basica(posicao, calc.peso if calc else None)
    lista_papeis = papeis_usuario or papeis.inferir(info)
    fx = adequacao.faixa(info, ctx)
    externas = secoes.coletar(info, ctx)
    tese = adequacao.tese(info, lista_papeis, fx, ctx)
    acao = adequacao.acao(info, fx, tese, ctx)
    return AnaliseAtivo(
        ativo=info,
        papeis=lista_papeis,
        faixa=fx,
        tese=tese,
        fundamentos=externas["fundamentos"],
        valuation=externas["valuation"],
        pares=externas["pares"],
        cenario=externas["cenario"],
        noticias=externas["noticias"],
        relatorios=externas["relatorios"],
        eventos=externas["eventos"],
        impacto=adequacao.impacto(info, fx, ctx),
        acao=acao,
        questoes=adequacao.questoes(papeis.em_linguagem_natural(lista_papeis),
                                    acao, externas["fundamentos"],
                                    externas["valuation"]),
        versao_politica=ctx.versao_politica,
    )


def analisar_carteira(ctx: ContextoInvestidor) -> list[AnaliseAtivo]:
    """Uma análise por posição, da maior para a menor."""
    def _peso(p):
        pa = ctx.calculos.peso(str(p.get("ticker") or ""))
        return pa.peso if pa else 0.0
    ordem = sorted(ctx.posicoes, key=_peso, reverse=True)
    return [analisar(p, ctx) for p in ordem]


def texto_para_llm(analise: AnaliseAtivo, ctx: ContextoInvestidor) -> str:
    """Entrada da etapa de LLM: política, carteira e a análise estruturada."""
    a = analise.ativo
    linhas = [ctx.texto_politica, "", texto_carteira(ctx), "",
              f"=== ATIVO EM ANÁLISE: {a.ticker} ===",
              f"Nome: {a.nome} · Classe: {a.classe}"
              + (f" ({a.subclasse})" if a.subclasse else "")
              + f" · Setor: {a.setor or '—'} · Peso: {a.peso_atual:.1f}%"]
    fx = analise.faixa
    linhas.append("[Números do ativo — calculados pelo código, não recalcule]")
    linhas.append(f"- Peso: {calculos.fmt_pct(a.peso_atual, 2)} · emissor "
                  f"{fx.emissor or '—'}"
                  + (f" · indexador {fx.indexador}" if fx.indexador else ""))
    if fx.teto_ativo is not None or fx.piso_ativo is not None:
        linhas.append(
            f"- Faixa do ativo: mínimo {calculos.fmt_pct(fx.piso_ativo)}, alvo "
            f"{calculos.fmt_pct(fx.alvo_ativo)}, máximo "
            f"{calculos.fmt_pct(fx.teto_ativo)} · overweight "
            f"{calculos.fmt_pp(fx.overweight_ativo)} · underweight "
            f"{calculos.fmt_pp(fx.underweight_ativo)} · status {fx.status_ativo}")
    chaves = {a.ticker, a.setor, a.classe_politica, fx.emissor} - {None}
    for al in ctx.calculos.alertas:
        if al.chave in chaves:
            linhas.append(f"- Alerta ({al.severidade}): {al.mensagem}")
    linhas += papeis.em_linguagem_natural(analise.papeis)
    linhas.append(f"Tese: {analise.tese.por_que_esta_na_carteira}")
    linhas.append(f"Alinhamento: {analise.tese.alinhamento}")
    linhas.append("\n[Impacto na carteira]")
    linhas += [f"- {o}" for o in analise.impacto.observacoes]
    linhas.append("\n[Gatilhos de revisão da tese]")
    for g in analise.tese.gatilhos:
        estado = ("não avaliado" if g.disparado is None
                  else "DISPARADO" if g.disparado else "ok")
        linhas.append(f"- {g.descricao}: {estado}. {g.detalhe}")
    linhas.append("\n[Seções de mercado]")
    for s in analise.secoes_externas:
        linhas.append(f"- {s.titulo}: {s.estado}. {s.resumo}")
    if analise.fundamentos.dados:
        linhas.append("")
        linhas.append(fundamentos.texto(
            fundamentos.Fundamentos.de_dict(analise.fundamentos.dados),
            a.ticker))
    if analise.valuation.dados:
        linhas.append("")
        linhas.append(valuation.texto(
            valuation.Valuation.de_dict(analise.valuation.dados), a.ticker))
    if analise.pares.dados:
        linhas.append("")
        linhas.append(pares.texto(
            pares.ComparacaoPares.de_dict(analise.pares.dados), a.ticker))
    if analise.noticias.dados:
        linhas.append("")
        linhas.append(informacoes.texto_noticias(
            informacoes.Noticias.de_dict(analise.noticias.dados), a.ticker))
    if analise.relatorios.dados:
        linhas.append("")
        linhas.append(informacoes.texto_relatorios(
            informacoes.Relatorios.de_dict(analise.relatorios.dados), a.ticker))
    if analise.eventos.dados:
        linhas.append("")
        linhas.append(informacoes.texto_eventos(
            informacoes.Eventos.de_dict(analise.eventos.dados), a.ticker))
    linhas.append(f"\n[Ação a considerar (regras)] {analise.acao.rotulo}")
    linhas += [f"- {j}" for j in analise.acao.justificativas]
    return "\n".join(linhas)
