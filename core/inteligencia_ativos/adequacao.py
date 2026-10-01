"""
core/inteligencia_ativos/adequacao.py
Adequação do ativo ao portfólio: o núcleo da análise.

Responde à quarta pergunta ("faz sentido para ESTE investidor, nesta carteira
e para ESTE objetivo?") só com o que já é conhecido sem dado de mercado: a
política concluída, a carteira completa e o papel do ativo. Fundamentos e
valuation ainda não entram, e por isso toda ``Acao`` sai com
``completa=False``: a leitura é de adequação, não um veredito sobre o ativo.

Nada aqui manda comprar ou vender. As ações são estados de adequação
(``modelos.ROTULO_ACAO``); quando várias regras disparam, vale a de maior
``PRIORIDADE_ACAO`` e as outras viram justificativa.

Puro. Coberto por tests/test_inteligencia_ativos_adequacao.py.
"""
from __future__ import annotations

from core.estrategia import politica as pol
from core.inteligencia_ativos import calculos as calc_
from core.inteligencia_ativos import modelos as m
from core.inteligencia_ativos.modelos import (
    Acao,
    ContextoInvestidor,
    FaixaAlvo,
    Gatilho,
    Impacto,
    InfoBasica,
    Papel,
    Resposta,
    Secao,
    Tese,
)

# Desvio da classe em relação ao alvo que ainda conta como "na faixa". É
# convenção do sistema, não resposta do usuário, e a justificativa diz isso.
# Definida em calculos.py, que monta a faixa da classe com ela.
TOLERANCIA_PP = calc_.TOLERANCIA_CLASSE_PP
# Folga até o teto por ativo abaixo da qual um aporte já não é "compatível".
FOLGA_MINIMA_PP = 1.0

_PAPEIS_RENDA = {"income", "dividend", "real_estate_income"}

# Papéis que são o foco de cada estratégia predominante da política.
FOCO_DA_ESTRATEGIA: dict[str, set[str]] = {
    "dividendos": _PAPEIS_RENDA,
    "crescimento": {"growth", "international_diversification",
                    "sector_exposure", "opportunity"},
    "preservacao": {"capital_preservation", "fixed_income_core", "liquidity",
                    "inflation_protection", "emergency_reserve", "defensive"},
    "equilibrada": set(m.PAPEIS),
}

_ROTULO_ESTRATEGIA = dict(pol.POR_CHAVE["predominant_strategy"].opcoes)
_ROTULO_OBJETIVO = dict(pol.POR_CHAVE["objective"].opcoes)
_ROTULO_CLASSE = dict(pol.CLASSES)


def _r(x: float | None) -> float | None:
    return None if x is None else round(x, 2)


# -- faixa desejada -------------------------------------------------------------

def faixa(info: InfoBasica, ctx: ContextoInvestidor) -> FaixaAlvo:
    """O que a estratégia (e a faixa do usuário, se houver) diz deste ativo.

    Só lê números prontos de ``ctx.calculos``; nada é somado aqui.
    """
    calc = ctx.calculos
    classe = info.classe_politica
    lc = calc.linha(calc_.DIM_CLASSE, classe) if classe else None
    la = calc.linha(calc_.DIM_ATIVO, info.ticker)
    ls = calc.linha(calc_.DIM_SETOR, info.setor) if info.setor else None
    pa = calc.peso(info.ticker)
    fa = la.faixa if la else calc_.Faixa()
    teto = fa.teto
    return FaixaAlvo(
        alvo_classe=ctx.alocacao_alvo.get(classe) if classe else None,
        peso_classe=_r(lc.atual) if lc else None,
        desvio_classe=_r(lc.diferenca_para_alvo) if lc else None,
        teto_ativo=teto,
        folga_ativo=_r(teto - info.peso_atual) if teto is not None else None,
        teto_setor=(ls.faixa.teto if ls else ctx.limite_por_setor),
        peso_setor=_r(ls.atual) if ls else None,
        piso_ativo=fa.piso,
        alvo_ativo=fa.alvo,
        diferenca_para_alvo_ativo=_r(la.diferenca_para_alvo) if la else None,
        overweight_ativo=_r(la.overweight) if la else 0.0,
        underweight_ativo=_r(la.underweight) if la else 0.0,
        status_ativo=la.status if la else None,
        emissor=pa.emissor if pa else None,
        indexador=pa.indexador if pa else None,
    )


# -- papel × política -----------------------------------------------------------

def conflito_com_estrategia(papel: Papel | None,
                            ctx: ContextoInvestidor) -> str | None:
    """Motivo de o papel principal contrariar a política, ou None.

    Só conta como conflito o que a própria política torna incoerente: um
    ativo de oportunidade numa carteira de preservação de perfil
    conservador. "Não é o foco" (crescimento numa estratégia de dividendos)
    não é conflito: complementa.
    """
    if papel is None:
        return None
    if (ctx.estrategia == "preservacao" and ctx.perfil_risco == "conservador"
            and papel.codigo in ("opportunity", "growth")):
        return (f"papel de {papel.rotulo} numa estratégia de preservação com "
                "perfil conservador")
    if ctx.horizonte == "curto" and papel.codigo == "opportunity":
        return "ativo de oportunidade com horizonte curto"
    return None


def alinhamento(papel: Papel | None, ctx: ContextoInvestidor) -> str:
    if papel is None:
        return "Sem papel identificado, não dá para dizer como ele serve à estratégia."
    estrategia = _ROTULO_ESTRATEGIA.get(ctx.estrategia or "", "—")
    conflito = conflito_com_estrategia(papel, ctx)
    if conflito:
        return f"Em tensão com a sua estratégia: {conflito}."
    foco = FOCO_DA_ESTRATEGIA.get(ctx.estrategia or "", set())
    if ctx.estrategia == "equilibrada":
        return f"Compatível com a estratégia {estrategia.lower()}."
    if papel.codigo in foco:
        return (f"Alinhado ao foco da sua estratégia ({estrategia.lower()}): "
                f"{papel.rotulo}.")
    return (f"Não é o foco da sua estratégia ({estrategia.lower()}), mas pode "
            "complementá-la.")


# -- tese -----------------------------------------------------------------------

_TENDENCIA_FUNDAMENTOS = ("Depende da tendência dos fundamentos (histórico); "
                          "os valores atuais estão na seção Fundamentos.")


def tese(info: InfoBasica, papeis: tuple[Papel, ...], fx: FaixaAlvo,
         ctx: ContextoInvestidor) -> Tese:
    principal = next((p for p in papeis if p.principal), None)
    if principal is None:
        por_que = "Não há um papel identificado para este ativo na carteira."
    else:
        por_que = f"Está na carteira para {principal.rotulo}"
        secundarios = [p.rotulo for p in papeis if not p.principal]
        if secundarios:
            por_que += f", com {', '.join(secundarios)}"
        objetivo = _ROTULO_OBJETIVO.get(ctx.objetivo or "")
        if objetivo:
            por_que += f", dentro do objetivo de {objetivo.lower()}"
        por_que += "."

    codigos = {p.codigo for p in papeis}
    gatilhos: list[Gatilho] = []
    if codigos & _PAPEIS_RENDA:
        gatilhos.append(Gatilho("reducao_dividendos",
                                "Redução estrutural de dividendos",
                                m.PENDENTE, None,
                                _TENDENCIA_FUNDAMENTOS))
    if info.classe_politica in ("acoes_br", "exterior"):
        gatilhos.append(Gatilho("aumento_divida",
                                "Aumento relevante de dívida", m.PENDENTE,
                                None, _TENDENCIA_FUNDAMENTOS))
        gatilhos.append(Gatilho("perda_qualidade",
                                "Perda de qualidade operacional", m.PENDENTE,
                                None, _TENDENCIA_FUNDAMENTOS))
    gatilhos.append(Gatilho("mudanca_estrategia",
                            "Mudança de estratégia do emissor ou gestor",
                            m.PENDENTE, None,
                            "Depende da etapa de relatórios."))

    motivos_conc = _excessos(info, fx)
    if fx.teto_ativo is None and fx.teto_setor is None:
        gatilhos.append(Gatilho("concentracao", "Aumento de concentração",
                                m.DISPONIVEL, None,
                                "Sua estratégia não define limite por ativo "
                                "nem por setor."))
    else:
        gatilhos.append(Gatilho("concentracao", "Aumento de concentração",
                                m.DISPONIVEL, bool(motivos_conc),
                                "; ".join(motivos_conc) or "Dentro dos limites."))

    fora = _fora_da_funcao(info, fx, principal, ctx)
    gatilhos.append(Gatilho("funcao_original",
                            "Ativo deixando de cumprir sua função na carteira",
                            m.DISPONIVEL, bool(fora),
                            fora or "O papel continua previsto na estratégia."))

    avaliados = [g for g in gatilhos if g.disparado is not None]
    if any(g.disparado for g in avaliados):
        valida: bool | None = False
    else:
        valida = None   # sem fundamentos, "não disparou" ainda não é "válida"
    return Tese(por_que, alinhamento(principal, ctx), valida, tuple(gatilhos))


def _excessos(info: InfoBasica, fx: FaixaAlvo) -> list[str]:
    saida = []
    if fx.teto_ativo is not None and info.peso_atual > fx.teto_ativo:
        saida.append(f"pesa {info.peso_atual:.1f}% da carteira, acima do seu "
                     f"limite por ativo de {fx.teto_ativo:g}%")
    if (fx.teto_setor is not None and fx.peso_setor is not None
            and fx.peso_setor > fx.teto_setor):
        saida.append(f"o setor {info.setor} soma {fx.peso_setor:.1f}%, acima "
                     f"do seu limite por setor de {fx.teto_setor:g}%")
    return saida


def _fora_da_funcao(info: InfoBasica, fx: FaixaAlvo, principal: Papel | None,
                    ctx: ContextoInvestidor) -> str | None:
    if info.classe_politica is None:
        return (f"a classe {info.classe} não está entre as classes da sua "
                "alocação-alvo")
    if fx.alvo_classe == 0:
        return (f"sua alocação-alvo prevê 0% em "
                f"{_ROTULO_CLASSE[info.classe_politica].lower()}")
    return conflito_com_estrategia(principal, ctx)


# -- impacto --------------------------------------------------------------------

def impacto(info: InfoBasica, fx: FaixaAlvo, ctx: ContextoInvestidor) -> Impacto:
    obs = []
    rotulo_classe = (_ROTULO_CLASSE.get(info.classe_politica)
                     if info.classe_politica else None)
    if rotulo_classe and fx.peso_classe:
        dentro = info.peso_atual / fx.peso_classe * 100
        obs.append(f"Representa {info.peso_atual:.1f}% da carteira e "
                   f"{dentro:.0f}% de {rotulo_classe.lower()}.")
    else:
        obs.append(f"Representa {info.peso_atual:.1f}% da carteira.")
    if fx.desvio_classe is not None:
        if abs(fx.desvio_classe) <= TOLERANCIA_PP:
            obs.append(f"{rotulo_classe} está na faixa: {fx.peso_classe:.1f}% "
                       f"contra alvo de {fx.alvo_classe:g}%.")
        else:
            lado = "acima" if fx.desvio_classe > 0 else "abaixo"
            obs.append(f"{rotulo_classe} está {abs(fx.desvio_classe):.1f} pp "
                       f"{lado} do alvo ({fx.peso_classe:.1f}% contra "
                       f"{fx.alvo_classe:g}%). Um aporte aqui "
                       + ("afasta" if lado == "acima" else "aproxima")
                       + " a carteira do alvo.")
    teto_classe = (ctx.limites_classe.get(info.classe_politica)
                   if info.classe_politica else None)
    if teto_classe is not None and fx.peso_classe is not None:
        obs.append(f"Limite máximo de {rotulo_classe.lower()}: "
                   f"{teto_classe:g}% (hoje {fx.peso_classe:.1f}%).")
    if fx.peso_setor is not None and info.setor:
        teto = f", limite {fx.teto_setor:g}%" if fx.teto_setor is not None else ""
        obs.append(f"Setor {info.setor}: {fx.peso_setor:.1f}% da carteira{teto}.")
    return Impacto(info.peso_atual, fx.peso_classe, fx.alvo_classe,
                   fx.desvio_classe, fx.peso_setor, fx.teto_setor, tuple(obs))


# -- ação a considerar ------------------------------------------------------------

def acao(info: InfoBasica, fx: FaixaAlvo, tese_: Tese,
         ctx: ContextoInvestidor) -> Acao:
    candidatas: list[tuple[str, str]] = []

    funcao = next((g for g in tese_.gatilhos if g.codigo == "funcao_original"),
                  None)
    if funcao and funcao.disparado:
        candidatas.append((m.REAVALIAR_TESE, f"Tese em dúvida: {funcao.detalhe}."))

    for motivo in _excessos(info, fx):
        candidatas.append((m.REDUZIR_CONCENTRACAO, f"Concentração: {motivo}."))
    teto_classe = (ctx.limites_classe.get(info.classe_politica)
                   if info.classe_politica else None)
    if (teto_classe is not None and fx.peso_classe is not None
            and fx.peso_classe > teto_classe):
        candidatas.append((m.REDUZIR_CONCENTRACAO,
                           f"A classe soma {fx.peso_classe:.1f}%, acima do seu "
                           f"limite de {teto_classe:g}%."))

    if fx.status_ativo == calc_.ABAIXO and fx.piso_ativo is not None:
        candidatas.append((m.APORTE_COMPATIVEL,
                           f"O ativo pesa {info.peso_atual:.1f}%, "
                           f"{fx.underweight_ativo:.1f} pp abaixo do mínimo de "
                           f"{fx.piso_ativo:g}% da faixa que você definiu."))

    tol = f"tolerância de {TOLERANCIA_PP:g} pp adotada pelo sistema"
    if fx.desvio_classe is not None:
        if fx.desvio_classe > TOLERANCIA_PP:
            candidatas.append((m.REAVALIAR_APORTES,
                               f"A classe está {fx.desvio_classe:.1f} pp acima "
                               f"do alvo ({tol})."))
        elif fx.desvio_classe < -TOLERANCIA_PP:
            if fx.folga_ativo is not None and fx.folga_ativo < FOLGA_MINIMA_PP:
                candidatas.append((m.EXPOSICAO_ADEQUADA,
                                   "A classe está abaixo do alvo, mas este "
                                   "ativo já está no seu limite por ativo: o "
                                   "aporte na classe cabe melhor em outro "
                                   "ativo."))
            else:
                candidatas.append((m.APORTE_COMPATIVEL,
                                   f"A classe está {-fx.desvio_classe:.1f} pp "
                                   f"abaixo do alvo ({tol})."))
        else:
            candidatas.append((m.EXPOSICAO_ADEQUADA,
                               f"A classe está dentro do alvo ({tol})."))

    if not candidatas:
        candidatas.append((m.MANTER, "Nenhuma regra de adequação pede mudança."))

    candidatas.sort(key=lambda c: m.PRIORIDADE_ACAO[c[0]], reverse=True)
    estado = candidatas[0][0]
    justificativas = [j for _, j in candidatas]
    justificativas.append("Leitura só de adequação à carteira: fundamentos e "
                          "valuation ainda não entram nesta ação.")
    return Acao(estado, tuple(justificativas), completa=False)


# -- as quatro perguntas ------------------------------------------------------------

def questoes(papeis_texto: list[str], acao_: Acao,
             fundamentos: Secao, valuation: Secao) -> dict[str, Resposta]:
    def _externa(chave: str, secao: Secao) -> Resposta:
        if secao.estado in (m.DISPONIVEL, m.SEM_DADOS):
            return Resposta(m.PERGUNTAS[chave], secao.estado, secao.resumo)
        return Resposta(m.PERGUNTAS[chave], m.PENDENTE,
                        "Ainda não avaliado: depende da etapa de "
                        f"{secao.titulo.lower()}.")
    return {
        m.Q_FUNDAMENTOS: _externa(m.Q_FUNDAMENTOS, fundamentos),
        m.Q_VALUATION: _externa(m.Q_VALUATION, valuation),
        m.Q_FUNCAO: Resposta(m.PERGUNTAS[m.Q_FUNCAO], m.DISPONIVEL,
                             " ".join(papeis_texto)),
        m.Q_ADEQUACAO: Resposta(m.PERGUNTAS[m.Q_ADEQUACAO], m.DISPONIVEL,
                                f"{acao_.rotulo}. {acao_.justificativas[0]}"),
    }
