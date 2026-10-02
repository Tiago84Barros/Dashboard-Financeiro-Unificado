"""
core/inteligencia_ativos/portfolio_fit.py
Portfolio Fit: a leitura por LLM de um ativo DENTRO da carteira do usuário.

A pergunta não é "o ativo é bom?". É "faz sentido para ESTE investidor,
nesta carteira, com este objetivo?". Por isso a análise segue uma ordem fixa
(``ORDEM_ANALISE``) que começa no investidor e só chega ao ativo depois de
passar pela política, pela alocação e pela concentração.

Três dimensões independentes, que nunca viram uma nota só:

* qualidade dos fundamentos (``fundamental_quality``);
* atratividade do valuation (``valuation_attractiveness``);
* adequação à carteira (``portfolio_fit``).

Fundamentos fortes com valuation esticado e fit alto, ou fundamentos fortes
com valuation atrativo e fit baixo por concentração, são respostas
diferentes e ficam visíveis como tais. ``validar`` remove qualquer campo de
nota geral ou ranking que a LLM devolva.

Este módulo é puro:

* ``contexto`` monta o objeto estruturado que a LLM recebe (não o banco);
* ``fit_por_regras`` é a pré-leitura determinística do fit, a partir da
  política e dos cálculos; entra no contexto como dado;
* ``mensagens`` é o prompt;
* ``validar`` confere a resposta: chaves, tipos, valores permitidos,
  dimensões sem dado forçadas a "insuficiente" e números sem âncora no
  contexto.

O Cenário de Investimentos do usuário (``core/cenario``) entra em
``scenario.cenario_do_investidor`` como premissa só de leitura, com
``REGRA_CENARIO`` no prompt. A resposta não tem por onde alterá-lo: nada
daqui grava, e ``validar`` descarta qualquer chave que tente reescrevê-lo
(``CHAVES_CENARIO``). Se a LLM achar que os fatos o contradizem, ela escreve
``FRASE_REVISAO`` e a tela mostra o aviso; quem muda o cenário é o usuário.

A chamada ao provedor mora em ``leitura_llm.py``.
Coberto por tests/test_inteligencia_ativos_portfolio_fit.py.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

from core.cenario.modelo import FRASE_REVISAO, REGRA_CENARIO
from core.contexto_mercado import REGRA_CONTEXTO_MERCADO
from core.estrategia import politica as pol
from core.inteligencia_ativos import adequacao
from core.inteligencia_ativos import calculos as calc_
from core.inteligencia_ativos import fundamentos as fund
from core.inteligencia_ativos import informacoes as inf
from core.inteligencia_ativos import modelos as m
from core.inteligencia_ativos import pares as prs
from core.inteligencia_ativos import valuation as val
from core.inteligencia_ativos.referencia_modelo import REGRA_REFERENCIA
from core.llm_grounding import check_grounding

NAO_DISPONIVEL = "Dado não disponível."
NAO_CONCLUSIVO = "Não existem informações suficientes para concluir."

# -- a ordem obrigatória --------------------------------------------------------

ORDEM_ANALISE: tuple[tuple[str, str], ...] = (
    ("objetivo", "Objetivo do investidor"),
    ("horizonte", "Horizonte"),
    ("liquidez", "Necessidade de liquidez"),
    ("risco", "Perfil e capacidade de risco"),
    ("politica", "Política de investimentos"),
    ("alocacao_atual", "Alocação atual"),
    ("alocacao_alvo", "Alocação-alvo"),
    ("concentracao", "Concentração"),
    ("papel", "Papel do ativo"),
    ("cenario", "Cenário"),
    ("fundamentos", "Fundamentos"),
    ("valuation", "Valuation"),
    ("pares", "Comparação com pares"),
    ("noticias", "Notícias"),
    ("eventos", "Próximos eventos"),
    ("alternativas", "Alternativas"),
)

# -- as três dimensões ------------------------------------------------------------

QUALIDADE = "fundamental_quality"
VALUATION = "valuation_attractiveness"
FIT = "portfolio_fit"

INSUFICIENTE = "insuficiente"

NIVEIS: dict[str, tuple[str, ...]] = {
    QUALIDADE: ("forte", "adequada", "fraca", INSUFICIENTE),
    VALUATION: ("atrativo", "neutro", "esticado", INSUFICIENTE),
    FIT: ("alto", "medio", "baixo", INSUFICIENTE),
}

ROTULO_DIMENSAO = {
    QUALIDADE: "Qualidade dos fundamentos",
    VALUATION: "Atratividade do valuation",
    FIT: "Adequação à carteira (Portfolio Fit)",
}

ROTULO_NIVEL = {
    "forte": "Forte", "adequada": "Adequada", "fraca": "Fraca",
    "atrativo": "Atrativo", "neutro": "Neutro", "esticado": "Esticado",
    "alto": "Alto", "medio": "Médio", "baixo": "Baixo",
    INSUFICIENTE: "Informação insuficiente",
}

TESE: dict[str, str] = {
    "mantida": "Tese mantida",
    "sob_observacao": "Tese sob observação",
    "em_duvida": "Tese em dúvida",
    "invalidada": "Tese invalidada",
    INSUFICIENTE: "Informação insuficiente",
}

# Campos de nota geral que a resposta não pode trazer: as dimensões são
# independentes e a tela não soma nem ordena.
CHAVES_PROIBIDAS = frozenset({
    "score", "overall", "overall_score", "overall_rating", "ranking", "rank",
    "nota", "nota_geral", "final_score", "total_score", "recommendation",
    "recomendacao", "veredito_geral",
})

# Chaves com que a resposta tentaria reescrever o cenário do usuário.
CHAVES_CENARIO = frozenset({
    "scenario", "scenario_update", "updated_scenario", "new_scenario",
    "scenario_changes", "investment_scenario", "cenario",
    "cenario_atualizado", "novo_cenario", "cenario_de_investimentos",
})

CAMPOS_TEXTO = ("fundamental_analysis", "valuation_analysis", "peer_analysis",
                "market_behavior", "scenario_impact", "portfolio_impact",
                "reasoning_summary")
CAMPOS_LISTA = ("risks", "opportunities", "events_to_watch", "data_gaps")
CHAVES_CONCLUSAO = ("fact", "interpretation", "portfolio_impact",
                    "action_to_consider")

_MAX_TEXTO = 2000
_MAX_ITENS = 10

# -- pré-leitura de fit por regras ------------------------------------------------

# Estados do ``Acao`` que, sozinhos, dizem que o ativo não cabe como está.
_BLOQUEIO = "bloqueio"
_CONTRA = "contra"
_FAVOR = "favor"


@dataclass(frozen=True)
class FitRegras:
    """Leitura determinística do fit, antes da LLM.

    ``bloqueios`` são violações da própria política (limite por ativo,
    setor ou classe; tese fora da função). Com bloqueio, o fit não pode ser
    "alto" em nenhuma leitura, nem na da LLM (``validar`` corrige).
    """
    nivel: str
    a_favor: tuple[str, ...]
    contra: tuple[str, ...]
    bloqueios: tuple[str, ...]

    def como_dict(self) -> dict:
        return asdict(self)


def fit_por_regras(analise: m.AnaliseAtivo,
                   ctx: m.ContextoInvestidor) -> FitRegras:
    """Só a política, a carteira e o papel. Puro."""
    info, fx = analise.ativo, analise.faixa
    favor: list[str] = []
    contra: list[str] = []
    bloqueios: list[str] = []

    funcao = next((g for g in analise.tese.gatilhos
                   if g.codigo == "funcao_original"), None)
    if funcao is not None and funcao.disparado:
        bloqueios.append(f"Fora da função na carteira: {funcao.detalhe}.")
    for motivo in adequacao._excessos(info, fx):
        bloqueios.append(f"Concentração: {motivo}.")
    teto_classe = (ctx.limites_classe.get(info.classe_politica)
                   if info.classe_politica else None)
    if (teto_classe is not None and fx.peso_classe is not None
            and fx.peso_classe > teto_classe):
        bloqueios.append(f"A classe soma {calc_.fmt_pct(fx.peso_classe)}, acima do "
                         f"limite de {calc_.fmt_pct(teto_classe)}.")

    tol = adequacao.TOLERANCIA_PP
    if fx.desvio_classe is not None:
        if fx.desvio_classe > tol:
            contra.append(f"A classe está {calc_.fmt_pp(fx.desvio_classe)} acima do "
                          "alvo: novo aporte afasta a carteira do alvo.")
        elif fx.desvio_classe < -tol:
            favor.append(f"A classe está {calc_.fmt_pp(-fx.desvio_classe)} abaixo "
                         "do alvo: aporte na classe aproxima do alvo.")
        else:
            favor.append("A classe está dentro do alvo.")
    if fx.folga_ativo is not None:
        if fx.folga_ativo < adequacao.FOLGA_MINIMA_PP and fx.folga_ativo >= 0:
            contra.append(f"Folga de {calc_.fmt_pp(fx.folga_ativo)} até o limite "
                          "por ativo: pouco espaço para aumentar.")
        elif fx.folga_ativo >= adequacao.FOLGA_MINIMA_PP:
            favor.append(f"Folga de {calc_.fmt_pp(fx.folga_ativo)} até o limite "
                         "por ativo.")
    if fx.status_ativo == calc_.ABAIXO and fx.piso_ativo is not None:
        favor.append(f"Abaixo do mínimo de {calc_.fmt_pct(fx.piso_ativo)} da faixa que "
                     "você definiu para o ativo.")
    elif fx.status_ativo == calc_.ACIMA and fx.teto_ativo is not None:
        contra.append("Acima da faixa que você definiu para o ativo.")

    principal = analise.papel_principal
    if principal is not None:
        conflito = adequacao.conflito_com_estrategia(principal, ctx)
        if conflito is None:
            foco = adequacao.FOCO_DA_ESTRATEGIA.get(ctx.estrategia or "", set())
            if principal.codigo in foco:
                favor.append(f"Papel principal ({principal.rotulo}) é foco "
                             "da estratégia.")
    else:
        contra.append("Sem papel identificado na carteira.")

    if bloqueios:
        nivel = "baixo"
    elif contra and not favor:
        nivel = "baixo"
    elif contra:
        nivel = "medio"
    elif favor:
        nivel = "alto"
    else:
        nivel = "medio"
    return FitRegras(nivel, tuple(favor), tuple(contra), tuple(bloqueios))


# -- contexto estruturado ----------------------------------------------------------

def _r(x, casas: int = 2):
    if x is None:
        return None
    try:
        return round(float(x), casas)
    except (TypeError, ValueError):
        return None


_ROTULO_CLASSE = dict(pol.CLASSES)


def _politica(ctx: m.ContextoInvestidor) -> dict:
    def rot(chave: str, valor):
        if valor is None:
            return NAO_DISPONIVEL
        pergunta = pol.POR_CHAVE.get(chave)
        opcoes = dict(getattr(pergunta, "opcoes", None) or ())
        return opcoes.get(valor, valor)
    return {
        "versao": ctx.versao_politica,
        "objetivo": rot("objective", ctx.objetivo),
        "horizonte": rot("time_horizon", ctx.horizonte),
        "necessidade_liquidez": rot("liquidity_need", ctx.necessidade_liquidez),
        "perfil_risco": rot("risk_profile", ctx.perfil_risco),
        "capacidade_risco": rot("risk_capacity", ctx.capacidade_risco),
        "estrategia_predominante": rot("predominant_strategy", ctx.estrategia),
        "limite_por_ativo_pct": ctx.limite_por_ativo,
        "limite_por_setor_pct": ctx.limite_por_setor,
        "restricoes": list(ctx.restricoes) or [NAO_DISPONIVEL],
    }


def _alocacao(analise: m.AnaliseAtivo, ctx: m.ContextoInvestidor) -> dict:
    fx = analise.faixa
    classes = []
    for chave, rotulo in pol.CLASSES:
        atual = ctx.peso_por_classe.get(chave, 0.0)
        alvo = ctx.alocacao_alvo.get(chave)
        classes.append({
            "classe": rotulo, "atual_pct": _r(atual),
            "alvo_pct": alvo, "limite_pct": ctx.limites_classe.get(chave),
            "desvio_pp": _r(atual - alvo) if alvo is not None else None,
        })
    return {
        "por_classe": classes,
        "fora_da_politica_pct": ctx.peso_fora_da_politica,
        "tolerancia_classe_pp": adequacao.TOLERANCIA_PP,
        "classe_do_ativo": (_ROTULO_CLASSE.get(analise.ativo.classe_politica)
                            if analise.ativo.classe_politica
                            else "fora das classes da política"),
        "faixa_do_ativo": {
            "alvo_por_ativo": ("não definido pela política (só há alvo por "
                               "classe)" if fx.alvo_ativo is None
                               else fx.alvo_ativo),
            "piso_pct": fx.piso_ativo, "alvo_pct": fx.alvo_ativo,
            "teto_pct": fx.teto_ativo, "status": fx.status_ativo,
            "overweight_pp": fx.overweight_ativo,
            "underweight_pp": fx.underweight_ativo,
        },
    }


def _concentracao(analise: m.AnaliseAtivo, ctx: m.ContextoInvestidor) -> dict:
    info, fx = analise.ativo, analise.faixa
    calc = ctx.calculos
    dims = {}
    for dim, c in (calc.concentracao or {}).items():
        maior = c.maior
        dims[calc_.ROTULO_DIMENSAO.get(dim, dim)] = {
            "base": c.base, "hhi": _r(c.hhi, 3),
            "numero_efetivo": _r(c.numero_efetivo, 1),
            "top3_pct": _r(c.top3, 1),
            "maior": ({"grupo": maior.chave, "peso_pct": _r(maior.peso, 1)}
                      if maior else None),
            "cobertura_pct": _r(c.cobertura, 1),
        }
    chaves = {info.ticker, info.setor, info.classe_politica, fx.emissor} - {None}
    alertas = [{"severidade": a.severidade, "mensagem": a.mensagem,
                "do_ativo": a.chave in chaves}
               for a in calc.alertas
               if a.chave in chaves or a.severidade == calc_.ALTA]
    return {
        "peso_ativo_pct": info.peso_atual,
        "teto_ativo_pct": fx.teto_ativo,
        "folga_ativo_pp": fx.folga_ativo,
        "setor": info.setor or NAO_DISPONIVEL,
        "peso_setor_pct": fx.peso_setor,
        "teto_setor_pct": fx.teto_setor,
        "emissor": fx.emissor,
        "por_dimensao": dims,
        "alertas": alertas,
    }


def _ativo(analise: m.AnaliseAtivo) -> dict:
    i = analise.ativo
    return {
        "ticker": i.ticker, "nome": i.nome, "classe": i.classe,
        "subclasse": i.subclasse, "setor": i.setor, "moeda": i.moeda,
        "peso_pct": i.peso_atual,
        "papeis": [{"codigo": p.codigo, "rotulo": p.rotulo,
                    "principal": p.principal, "motivo": p.motivo}
                   for p in analise.papeis],
        "tese": analise.tese.por_que_esta_na_carteira,
        "alinhamento_com_estrategia": analise.tese.alinhamento,
        "gatilhos_da_tese": [{
            "gatilho": g.descricao,
            "estado": ("não avaliado" if g.disparado is None
                       else "DISPARADO" if g.disparado else "ok"),
            "detalhe": g.detalhe} for g in analise.tese.gatilhos],
    }


def _fundamentos(s: m.Secao) -> dict:
    if not s.dados:
        return {"estado": s.estado, "resumo": s.resumo, "indicadores": []}
    f = fund.Fundamentos.de_dict(s.dados)
    return {
        "estado": s.estado, "tipo": f.tipo, "motivo": f.motivo,
        "indicadores": [{"indicador": i.rotulo, "valor": i.texto(f.moeda),
                         "fonte": i.fonte, "referencia": i.referencia}
                        for i in f.disponiveis],
        "sem_dado": [i.rotulo for i in f.ausentes],
    }


def _valuation(s: m.Secao) -> dict:
    if not s.dados:
        return {"estado": s.estado, "resumo": s.resumo, "metricas": []}
    v = val.Valuation.de_dict(s.dados)
    linhas = []
    for li in v.com_dado:
        h, p = li.historico, li.pares
        linhas.append({
            "metrica": li.rotulo, "atual": li.texto_atual(v.moeda),
            "referencia": li.referencia,
            "mediana_historica": _r(h.mediana) if h else None,
            "periodo_historico": f"{h.inicio} a {h.fim}" if h else None,
            "percentil_historico": _r(li.percentil_historico, 0),
            "posicao_historica": li.posicao_historica,
            "mediana_pares": _r(p.mediana) if p else None,
            "n_pares": p.n if p else 0,
            "posicao_pares": li.posicao_pares,
        })
    return {"estado": s.estado, "tipo": v.tipo, "motivo": v.motivo,
            "grupo_pares": v.grupo_pares, "premissas": list(v.premissas),
            "metricas": linhas,
            "nao_aplicaveis": [li.rotulo for li in v.linhas if not li.aplicavel]}


def _pares(s: m.Secao) -> tuple[list, dict]:
    if not s.dados:
        return [], {"estado": s.estado, "resumo": s.resumo, "linhas": []}
    c = prs.ComparacaoPares.de_dict(s.dados)
    lista = [{"ticker": p.ticker, "nome": p.nome, "motivo": p.motivo}
             for p in c.grupo.pares]
    comp = {"estado": s.estado, "grupo": c.grupo.descricao,
            "motivo": c.motivo or c.grupo.motivo,
            "linhas": [{"metrica": li.metrica, "valor": li.texto_valor(c.moeda),
                        "mediana_pares": li.texto_mediana(c.moeda),
                        "n_pares": li.n_pares, "posicao": li.posicao}
                       for li in c.com_dado]}
    return lista, comp


def _noticias(s: m.Secao) -> list:
    if not s.dados:
        return []
    n = inf.Noticias.de_dict(s.dados)
    return [{"manchete": x.headline, "data": x.date, "fonte": x.source,
             "impacto": x.impact_level, "dimensoes": list(x.affected_dimension),
             "categoria": x.categoria} for x in n.itens]


def _relatorios(s: m.Secao) -> list:
    if not s.dados:
        return []
    r = inf.Relatorios.de_dict(s.dados)
    return [{"tipo": d.tipo, "titulo": d.titulo, "data": d.reference_date,
             "fonte": d.source} for d in r.documentos]


def _eventos(s: m.Secao) -> list:
    if not s.dados:
        return []
    e = inf.Eventos.de_dict(s.dados)
    return [{"tipo": x.tipo, "data": x.data, "descricao": x.descricao,
             "natureza": x.natureza, "fonte": x.source} for x in e.itens]


def _alternativas(analise: m.AnaliseAtivo, ctx: m.ContextoInvestidor,
                  pares_: list) -> dict:
    info = analise.ativo
    mesma = [{"ticker": p.ticker, "nome": p.nome, "peso_pct": _r(p.peso)}
             for p in ctx.calculos.pesos
             if p.ticker != info.ticker
             and p.classe_politica == info.classe_politica
             and info.classe_politica is not None]
    na_carteira = {p.ticker for p in ctx.calculos.pesos}
    return {
        "mesma_classe_na_carteira": mesma,
        "pares_fora_da_carteira": [p["ticker"] for p in pares_
                                   if p["ticker"] not in na_carteira],
    }


def lacunas(analise: m.AnaliseAtivo) -> list[str]:
    """Seções sem dado, ditas pelo código antes da LLM."""
    saida = []
    for s in analise.secoes_externas:
        if s.estado != m.DISPONIVEL or not s.dados:
            saida.append(f"{s.titulo}: {s.resumo}")
    return saida


def contexto(analise: m.AnaliseAtivo, ctx: m.ContextoInvestidor, *,
             cenario_mercado: str | None = None,
             mercado_armazem: dict | None = None,
             referencia_modelo: dict | None = None) -> dict:
    """O objeto que a LLM recebe. Só o que a análise já montou, nada do
    banco inteiro. ``cenario_mercado`` é o bloco de mercado (I/O), que vai
    no prompt como texto separado. ``mercado_armazem`` são os números do
    detalhe do armazém como campos (``armazem_fatos``): no texto, a LLM não
    os usava. ``referencia_modelo`` é ``ReferenciaModelo.para_llm``: a
    carteira recomendada do Portfólio Global, só como comparação -- fica
    fora de ``rules`` e de ``fit_por_regras``."""
    pares_, comparacao = _pares(analise.pares)
    regras = fit_por_regras(analise, ctx)
    return {
        "ordem_de_analise": [r for _, r in ORDEM_ANALISE],
        "portfolio": {
            "valor_total": _r(ctx.total_mercado),
            "base_do_peso": ctx.calculos.base_valor,
            "n_posicoes": len(ctx.calculos.pesos),
            "posicoes": [{"ticker": p.ticker, "classe": p.classe,
                          "setor": p.setor, "peso_pct": _r(p.peso)}
                         for p in ctx.calculos.pesos],
        },
        "policy": _politica(ctx),
        "allocation": _alocacao(analise, ctx),
        "concentration": _concentracao(analise, ctx),
        "scenario": {
            "cenario_do_investidor": (analise.cenario.dados
                                      if analise.cenario.estado == m.DISPONIVEL
                                      and analise.cenario.dados
                                      else NAO_DISPONIVEL),
            "secao_cenario": {"estado": analise.cenario.estado,
                              "resumo": analise.cenario.resumo},
            "contexto_mercado": ("no bloco CONTEXTO DE MERCADO, após este JSON"
                                 if cenario_mercado else NAO_DISPONIVEL),
        },
        "asset": _ativo(analise),
        "warehouse_market": (mercado_armazem if mercado_armazem is not None
                             else {"estado": NAO_DISPONIVEL}),
        "fundamentals": _fundamentos(analise.fundamentos),
        "valuation": _valuation(analise.valuation),
        "peers": pares_,
        "peer_comparison": comparacao,
        "news": _noticias(analise.noticias),
        "reports": _relatorios(analise.relatorios),
        "events": _eventos(analise.eventos),
        "alternatives": _alternativas(analise, ctx, pares_),
        "app_model_reference": (referencia_modelo
                                if referencia_modelo is not None
                                else {"estado": NAO_DISPONIVEL}),
        "rules": {
            "acao_a_considerar": analise.acao.estado,
            "acao_rotulo": analise.acao.rotulo,
            "justificativas": list(analise.acao.justificativas),
            "portfolio_fit_por_regras": regras.como_dict(),
        },
        "data_gaps": lacunas(analise),
    }


def contexto_json(contexto_: dict) -> str:
    return json.dumps(contexto_, ensure_ascii=False, separators=(",", ":"),
                      default=str)


def texto_ancora(contexto_: dict, cenario_mercado: str | None) -> str:
    """Tudo contra o que os números da resposta são conferidos."""
    return contexto_json(contexto_) + "\n" + (cenario_mercado or "")


# -- prompt --------------------------------------------------------------------------

def _esquema() -> str:
    niveis = {d: " | ".join(v) for d, v in NIVEIS.items()}
    return json.dumps({
        "asset_role": ["códigos de papel: " + ", ".join(m.PAPEIS)],
        "thesis_status": " | ".join(TESE),
        "fundamental_analysis": "texto",
        "valuation_analysis": "texto",
        "peer_analysis": "texto",
        "market_behavior": ("texto: liquidez, retornos e volatilidade de "
                            "\"warehouse_market\", com janela e data de "
                            "referência"),
        "scenario_impact": "texto",
        "portfolio_impact": "texto",
        "risks": ["texto"],
        "opportunities": ["texto"],
        "events_to_watch": ["texto"],
        "action_to_consider": " | ".join(m.ROTULO_ACAO),
        "reasoning_summary": "texto",
        "data_gaps": ["texto"],
        "dimensions": {
            QUALIDADE: {"level": niveis[QUALIDADE], "fact": "texto",
                        "interpretation": "texto"},
            VALUATION: {"level": niveis[VALUATION], "fact": "texto",
                        "interpretation": "texto"},
            FIT: {"level": niveis[FIT], "fact": "texto",
                  "interpretation": "texto"},
        },
        "conclusions": [{k: "texto" for k in CHAVES_CONCLUSAO}],
    }, ensure_ascii=False, indent=1)


def sistema() -> str:
    ordem = "\n".join(f"{n}. {r}" for n, (_, r) in
                      enumerate(ORDEM_ANALISE, start=1))
    return (
        "Você é um analista de investimentos que avalia se UM ativo faz "
        "sentido para ESTE investidor, dentro DESTA carteira. Não julgue se o "
        "ativo é bom em abstrato: avalie a adequação dele ao objetivo, à "
        "política e à carteira do usuário. Responda em português do Brasil.\n\n"
        "ORDEM OBRIGATÓRIA DE ANÁLISE (raciocine nesta ordem; o investidor "
        "vem antes do ativo):\n" + ordem + "\n\n"
        "TRÊS DIMENSÕES INDEPENDENTES:\n"
        f"- {QUALIDADE}: qualidade dos fundamentos.\n"
        f"- {VALUATION}: atratividade do valuation.\n"
        f"- {FIT}: adequação à carteira e à política do usuário.\n"
        "Avalie cada uma separadamente. NUNCA as combine numa nota, média, "
        "score ou ranking geral. Exemplos válidos: fundamentos fortes, "
        "valuation esticado e fit alto; ou fundamentos fortes, valuation "
        "atrativo e fit baixo por concentração excessiva.\n\n"
        "EXPLICABILIDADE: cada item de \"conclusions\" separa FATO (o dado do "
        "contexto, com a fonte), INTERPRETAÇÃO (sua leitura), IMPACTO NA "
        "CARTEIRA (o que muda para este usuário) e AÇÃO A CONSIDERAR.\n\n"
        "CONTROLE DE ALUCINAÇÃO:\n"
        "1. Use como fato só o que está no CONTEXTO ESTRUTURADO e no bloco "
        "CONTEXTO DE MERCADO, que pode trazer em seguida o DETALHE DO "
        "ARMAZÉM LOCAL do ativo (liquidez, preço, proventos, trimestres, "
        "score mês a mês; fonte declarada indisponível é lacuna, não zero). "
        "Não invente preço, indicador, notícia, "
        "dividendo, valuation, guidance, relatório ou evento.\n"
        "2. Todo número que você escrever precisa estar no contexto. Os "
        "números em \"rules\", \"allocation\" e \"concentration\" foram "
        "calculados pelo código: não recalcule.\n"
        f"3. Sem dado, escreva \"{NAO_DISPONIVEL}\" ou \"{NAO_CONCLUSIVO}\". "
        "Nunca preencha lacunas com números inventados. Ausência de dado não "
        "é sinal de segurança.\n"
        f"4. Sem indicadores em \"fundamentals\", {QUALIDADE} é "
        f"\"{INSUFICIENTE}\". Sem métricas em \"valuation\", {VALUATION} é "
        f"\"{INSUFICIENTE}\".\n"
        "5. \"rules.portfolio_fit_por_regras\" é a leitura do código. Com "
        f"bloqueio (limite da política violado), {FIT} não pode ser \"alto\". "
        "Se a sua leitura divergir da do código, explique o porquê.\n"
        "6. A política não tem alvo por ativo, só por classe. Não invente um.\n"
        "7. \"action_to_consider\" é um dos códigos permitidos; nenhum é "
        "ordem de compra ou venda.\n"
        "8. Desempenho passado, múltiplos e comparações não são previsão.\n"
        "9. \"warehouse_market\" traz, calculados em código, liquidez, "
        "retornos e volatilidade do ativo lidos do armazém. Eles vão em "
        "\"market_behavior\", com a janela de cada número e a data de "
        "referência; a reação a resultados anteriores (no bloco DETALHE DO "
        "ARMAZÉM LOCAL) também. Liquidez que limita entrar ou sair da "
        "posição vai ainda em \"risks\". Número do armazém que diverge de "
        "outro do contexto (ex.: a volatilidade de \"peer_comparison\", que "
        "vem da vitrine com outra série e janela) é escrito junto com o "
        "outro, cada um com a origem; não escolha um em silêncio. Estado "
        "\"indisponível\" é lacuna, não dado zero: diga isso em "
        "\"market_behavior\"; \"não se aplica\" também.\n\n"
        f"{REGRA_REFERENCIA}\n\n"
        f"{REGRA_CONTEXTO_MERCADO}\n\n"
        f"{REGRA_CENARIO}\n"
        "O cenário do usuário está em \"scenario.cenario_do_investidor\". "
        "Use \"scenario_impact\" para dizer como ele afeta este ativo nesta "
        "carteira e, se for o caso, para a frase de revisão. Não devolva "
        "chave nenhuma com um cenário novo.\n\n"
        "SAÍDA: responda SOMENTE com um objeto JSON neste formato:\n"
        + _esquema()
    )


def mensagens(contexto_: dict, cenario_mercado: str | None) -> list[dict]:
    usuario = ("=== CONTEXTO ESTRUTURADO (JSON) ===\n" + contexto_json(contexto_)
               + "\n\n" + (cenario_mercado or
                           "=== CONTEXTO DE MERCADO ===\n" + NAO_DISPONIVEL
                           + " Não trate a ausência como conjuntura neutra."))
    return [{"role": "system", "content": sistema()},
            {"role": "user", "content": usuario}]


# -- leitura da resposta ------------------------------------------------------------

def ler_json(bruto: str | None) -> dict | None:
    """O objeto JSON da resposta, tolerando cerca de código e ruído."""
    if not bruto:
        return None
    t = bruto.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    for candidato in (t, t[t.find("{"): t.rfind("}") + 1] if "{" in t else ""):
        try:
            d = json.loads(candidato)
        except (ValueError, TypeError):
            continue
        if isinstance(d, dict):
            return d
    return None


@dataclass(frozen=True)
class Dimensao:
    chave: str
    nivel: str
    fato: str
    interpretacao: str

    @property
    def rotulo(self) -> str:
        return ROTULO_DIMENSAO[self.chave]

    @property
    def rotulo_nivel(self) -> str:
        return ROTULO_NIVEL.get(self.nivel, self.nivel)


@dataclass(frozen=True)
class Conclusao:
    fato: str
    interpretacao: str
    impacto: str
    acao: str


APROVADA = "APROVADA"
COM_RESSALVAS = "COM_RESSALVAS"
REJEITADA = "REJEITADA"


@dataclass(frozen=True)
class Leitura:
    status: str
    papeis: tuple[str, ...] = ()
    tese: str = INSUFICIENTE
    textos: dict = field(default_factory=dict)       # CAMPOS_TEXTO
    listas: dict = field(default_factory=dict)       # CAMPOS_LISTA
    acao: str | None = None
    dimensoes: tuple[Dimensao, ...] = ()
    conclusoes: tuple[Conclusao, ...] = ()
    problemas: tuple[str, ...] = ()        # o que estava errado na resposta
    correcoes: tuple[str, ...] = ()        # o que o validador trocou
    numeros_sem_ancora: tuple[str, ...] = ()
    divergencia_regras: str | None = None
    fit_regras: FitRegras | None = None
    revisao_cenario: bool = False          # a LLM escreveu FRASE_REVISAO
    modelo: str | None = None              # "provedor/modelo" que respondeu

    @property
    def rotulo_acao(self) -> str:
        return m.ROTULO_ACAO.get(self.acao or "", NAO_DISPONIVEL)

    def dimensao(self, chave: str) -> Dimensao | None:
        return next((d for d in self.dimensoes if d.chave == chave), None)


def falha(motivo: str, fit_regras: FitRegras | None = None) -> Leitura:
    return Leitura(status=REJEITADA, problemas=(motivo,),
                   fit_regras=fit_regras)


def _texto(v) -> str:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        v = str(v)
    if not isinstance(v, str) or not v.strip():
        return NAO_DISPONIVEL
    v = v.strip()
    return v if len(v) <= _MAX_TEXTO else v[:_MAX_TEXTO].rstrip() + "…"


def _lista(v) -> tuple[list[str], bool]:
    """(itens, estava_bem_formada)."""
    if v is None:
        return [], True
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list):
        return [], False
    saida: list[str] = []
    ok = True
    for x in v:
        if not isinstance(x, str) or not x.strip():
            ok = False
            continue
        if x.strip() not in saida:
            saida.append(x.strip()[:_MAX_TEXTO])
    return saida[:_MAX_ITENS], ok


def _tem_fundamentos(ctx_: dict) -> bool:
    return bool((ctx_.get("fundamentals") or {}).get("indicadores"))


def _tem_valuation(ctx_: dict) -> bool:
    return bool((ctx_.get("valuation") or {}).get("metricas"))


def validar(dado: dict | None, contexto_: dict, ancora: str) -> Leitura:
    """Confere e normaliza a resposta da LLM. Puro.

    Nunca inventa conteúdo: campo ausente vira ``NAO_DISPONIVEL`` e
    dimensão sem dado vira ``insuficiente``. Tudo o que foi trocado fica em
    ``correcoes``; o que veio malformado, em ``problemas``.
    """
    regras_d = ((contexto_.get("rules") or {})
                .get("portfolio_fit_por_regras") or {})
    regras = (FitRegras(regras_d.get("nivel", "medio"),
                        tuple(regras_d.get("a_favor") or ()),
                        tuple(regras_d.get("contra") or ()),
                        tuple(regras_d.get("bloqueios") or ()))
              if regras_d else None)
    if not isinstance(dado, dict):
        return falha("A resposta não é um objeto JSON.", regras)

    problemas: list[str] = []
    correcoes: list[str] = []

    proibidas = sorted(k for k in dado if str(k).lower() in CHAVES_PROIBIDAS)
    dims_brutas = dado.get("dimensions")
    if isinstance(dims_brutas, dict):
        proibidas += sorted(f"dimensions.{k}" for k in dims_brutas
                            if str(k).lower() in CHAVES_PROIBIDAS)
    if proibidas:
        correcoes.append("Nota geral ou ranking removido (as dimensões são "
                         "independentes): " + ", ".join(proibidas) + ".")
    reescrita = sorted(k for k in dado if str(k).lower() in CHAVES_CENARIO)
    if reescrita:
        correcoes.append("A resposta tentou reescrever o Cenário de "
                         "Investimentos (" + ", ".join(reescrita) + "); "
                         "descartado. O cenário só muda pelo usuário.")

    papeis_, ok = _lista(dado.get("asset_role"))
    desconhecidos = [p for p in papeis_ if p not in m.PAPEIS]
    if desconhecidos or not ok:
        problemas.append("Papéis fora da lista permitida descartados: "
                         + (", ".join(desconhecidos) or "item malformado") + ".")
    papeis_ = [p for p in papeis_ if p in m.PAPEIS]

    tese = dado.get("thesis_status")
    if tese not in TESE:
        problemas.append(f"Status da tese inválido ({tese!r}); considerado "
                         "insuficiente.")
        tese = INSUFICIENTE

    textos = {c: _texto(dado.get(c)) for c in CAMPOS_TEXTO}
    listas = {}
    for c in CAMPOS_LISTA:
        itens, ok = _lista(dado.get(c))
        if not ok:
            problemas.append(f"Campo {c} malformado: itens inválidos "
                             "descartados.")
        listas[c] = itens
    # As lacunas que o código já conhece não dependem de a LLM repeti-las.
    for gap in contexto_.get("data_gaps") or ():
        if gap not in listas["data_gaps"]:
            listas["data_gaps"].append(gap)

    acao = dado.get("action_to_consider")
    acao_regras = (contexto_.get("rules") or {}).get("acao_a_considerar")
    if acao not in m.ROTULO_ACAO:
        correcoes.append(f"Ação {acao!r} fora da lista permitida; mantida a "
                         "ação das regras.")
        acao = acao_regras

    dims: list[Dimensao] = []
    fonte = dims_brutas if isinstance(dims_brutas, dict) else {}
    if not isinstance(dims_brutas, dict):
        problemas.append("Campo dimensions ausente ou malformado.")
    for chave in (QUALIDADE, VALUATION, FIT):
        bruto = fonte.get(chave)
        bruto = bruto if isinstance(bruto, dict) else {}
        nivel = bruto.get("level")
        fato = _texto(bruto.get("fact"))
        interp = _texto(bruto.get("interpretation"))
        if nivel not in NIVEIS[chave]:
            if bruto:
                problemas.append(f"Nível inválido em {chave} ({nivel!r}).")
            nivel = INSUFICIENTE
        sem_dado = ((chave == QUALIDADE and not _tem_fundamentos(contexto_))
                    or (chave == VALUATION and not _tem_valuation(contexto_)))
        if sem_dado and nivel != INSUFICIENTE:
            correcoes.append(f"{ROTULO_DIMENSAO[chave]}: a resposta dizia "
                             f"\"{ROTULO_NIVEL.get(nivel, nivel)}\" sem dado no "
                             "contexto; trocado por informação insuficiente.")
            nivel = INSUFICIENTE
            interp = NAO_CONCLUSIVO
        if (chave == FIT and regras is not None and regras.bloqueios
                and nivel == "alto"):
            correcoes.append("Portfolio Fit \"alto\" com limite da política "
                             "violado; trocado pelo nível das regras.")
            nivel = regras.nivel
        if nivel == INSUFICIENTE and interp == NAO_DISPONIVEL:
            interp = NAO_CONCLUSIVO
        dims.append(Dimensao(chave, nivel, fato, interp))

    conclusoes: list[Conclusao] = []
    brutas = dado.get("conclusions")
    if not isinstance(brutas, list):
        brutas = []
    for c in brutas[:_MAX_ITENS]:
        if not isinstance(c, dict):
            problemas.append("Conclusão malformada descartada.")
            continue
        conclusoes.append(Conclusao(*(_texto(c.get(k))
                                      for k in CHAVES_CONCLUSAO)))
    if not conclusoes:
        problemas.append("Nenhuma conclusão separada em fato, interpretação, "
                         "impacto e ação.")

    corpo = "\n".join(
        list(textos.values())
        + [x for v in listas.values() for x in v
           if x not in (contexto_.get("data_gaps") or ())]
        + [x for d in dims for x in (d.fato, d.interpretacao)]
        + [x for c in conclusoes for x in (c.fato, c.interpretacao, c.impacto,
                                           c.acao)])
    relatorio = check_grounding(corpo, ancora)
    sem_ancora = tuple(dict.fromkeys(c.raw for c in relatorio.ungrounded))

    fit = next(d for d in dims if d.chave == FIT)
    divergencia = None
    if regras is not None and fit.nivel not in (regras.nivel, INSUFICIENTE):
        divergencia = (f"A LLM leu o fit como \"{fit.rotulo_nivel}\"; as "
                       "regras da política, como "
                       f"\"{ROTULO_NIVEL[regras.nivel]}\".")

    revisao = FRASE_REVISAO.lower() in corpo.lower()

    status = (COM_RESSALVAS if (problemas or correcoes or sem_ancora)
              else APROVADA)
    return Leitura(status=status, papeis=tuple(papeis_), tese=tese,
                   textos=textos, listas=listas, acao=acao,
                   dimensoes=tuple(dims), conclusoes=tuple(conclusoes),
                   problemas=tuple(problemas), correcoes=tuple(correcoes),
                   numeros_sem_ancora=sem_ancora,
                   divergencia_regras=divergencia, fit_regras=regras,
                   revisao_cenario=revisao)
