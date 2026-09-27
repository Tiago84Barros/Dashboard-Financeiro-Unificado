"""
core/inteligencia_ativos/painel.py
Painel da Inteligência dos Ativos: o resumo da carteira e um cartão por ativo.

Puro: só lê o que ``analisar_carteira`` já montou (``ContextoInvestidor`` e
``AnaliseAtivo``), mais os dicts de carteira e de proventos que a tela já tem.
Nada aqui calcula número novo, lê banco ou chama LLM: o painel é a vista
consolidada das etapas, não uma etapa a mais.

Coberto por tests/test_inteligencia_ativos_painel.py.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from core.estrategia import politica as pol
from core.inteligencia_ativos import calculos as calc
from core.inteligencia_ativos import informacoes as inf
from core.inteligencia_ativos import modelos as m
from core.inteligencia_ativos import valuation as val

# Ações que pedem um olhar do usuário. As outras dizem que está tudo em ordem.
ACOES_DE_REVISAO = (m.REAVALIAR_TESE, m.REDUZIR_CONCENTRACAO,
                    m.COMPARAR_ALTERNATIVAS, m.REAVALIAR_APORTES)

TESE_VALIDA = "valida"
TESE_EM_RISCO = "em_risco"
TESE_SEM_VEREDITO = "sem_veredito"
ROTULO_TESE = {TESE_VALIDA: "Válida",
               TESE_EM_RISCO: "Sinal contra a tese",
               TESE_SEM_VEREDITO: "Sem veredito"}

SEM_RISCO = "Nenhum sinal objetivo de risco."
SEM_EVENTO = "Nenhum evento com data conhecida."
MAX_ALERTAS = 6
MAX_EVENTOS = 6


@dataclass(frozen=True)
class LinhaAlocacao:
    rotulo: str
    atual: float
    alvo: float | None
    desvio: float | None       # pp; atual − alvo
    status: str                # calc.DENTRO | ACIMA | ABAIXO | SEM_REFERENCIA


@dataclass(frozen=True)
class Concentracao:
    dimensao: str              # rótulo da dimensão ("Ativo", "Setor"...)
    chave: str
    peso: float                # % da base
    base: str


@dataclass(frozen=True)
class EventoCarteira:
    data: str                  # ISO
    ticker: str
    rotulo: str
    relevancia: str


@dataclass(frozen=True)
class AtivoEmRevisao:
    ticker: str
    acao: str
    rotulo: str
    motivo: str


@dataclass(frozen=True)
class ResumoCarteira:
    patrimonio: float
    total_investido: float | None
    rentabilidade_pct: float | None       # None: sem cotação ou câmbio
    resultado: float | None               # mercado − investido
    renda_12m: float | None               # None: proventos indisponíveis
    renda_total: float | None
    alocacao: tuple[LinhaAlocacao, ...]
    fora_da_politica: float
    concentracoes: tuple[Concentracao, ...]
    objetivo: str
    estrategia: str
    horizonte: str
    perfil: str
    cenario: str
    cenario_revisao: bool                 # há sinal de revisão do cenário
    alertas: tuple[calc.Alerta, ...]
    eventos: tuple[EventoCarteira, ...]
    revisao: tuple[AtivoEmRevisao, ...]
    versao_politica: int


@dataclass(frozen=True)
class CardAtivo:
    ticker: str
    nome: str
    moeda: str
    valor: float | None
    peso: float
    faixa: str
    papel: str
    tese: str                  # TESE_*
    valuation: str
    risco: str
    evento: str
    acao: str
    acao_rotulo: str

    @property
    def tese_rotulo(self) -> str:
        return ROTULO_TESE[self.tese]

    @property
    def em_revisao(self) -> bool:
        return self.acao in ACOES_DE_REVISAO


# -- formatação ----------------------------------------------------------------

def _pct(x: float | None, casas: int = 1) -> str:
    return "—" if x is None else f"{x:.{casas}f}%".replace(".", ",")


def _rot(chave: str, valor) -> str:
    return pol.formatar(chave, valor) if valor is not None else "—"


def _num(x) -> float | None:
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


# -- resumo da carteira ----------------------------------------------------------

def _alocacao(c: calc.Calculos) -> tuple[LinhaAlocacao, ...]:
    return tuple(LinhaAlocacao(a.rotulo, a.atual, a.faixa.alvo,
                               a.diferenca_para_alvo, a.status)
                 for a in c.alocacao.get(calc.DIM_CLASSE, ()))


def _concentracoes(c: calc.Calculos) -> tuple[Concentracao, ...]:
    saida = []
    for dim in (calc.DIM_ATIVO, calc.DIM_SETOR, calc.DIM_EMISSOR):
        k = c.concentracao.get(dim)
        if k is None or k.peso_da_base <= 0 or k.maior is None:
            continue
        saida.append(Concentracao(calc.ROTULO_DIMENSAO[dim], k.maior.chave,
                                  k.maior.peso, k.base))
    return tuple(saida)


def _texto_cenario(ctx: m.ContextoInvestidor) -> str:
    c = ctx.cenario
    if c is None:
        return "Cenário de Investimentos ilegível agora."
    if c.vazio:
        return "Nenhum Cenário de Investimentos cadastrado."
    from core.cenario.modelo import CHAVES
    return (f"Versão {c.versao}: {len(c.preenchidos)} de {len(CHAVES)} itens "
            "preenchidos.")


def _eventos(analises, hoje: dt.date) -> tuple[EventoCarteira, ...]:
    saida = []
    for a in analises:
        if not a.eventos.dados:
            continue
        for e in inf.Eventos.de_dict(a.eventos.dados).itens:
            if (e.data or "")[:10] >= hoje.isoformat():
                saida.append(EventoCarteira(e.data[:10], a.ativo.ticker,
                                            e.rotulo, e.relevancia))
    saida.sort(key=lambda e: (e.data, e.ticker))
    return tuple(saida[:MAX_EVENTOS])


def _revisao(analises) -> tuple[AtivoEmRevisao, ...]:
    em = [a for a in analises if a.acao.estado in ACOES_DE_REVISAO]
    em.sort(key=lambda a: (-m.PRIORIDADE_ACAO[a.acao.estado],
                           -a.ativo.peso_atual, a.ativo.ticker))
    return tuple(AtivoEmRevisao(a.ativo.ticker, a.acao.estado, a.acao.rotulo,
                                a.acao.justificativas[0]
                                if a.acao.justificativas else "")
                 for a in em)


def resumo(ctx: m.ContextoInvestidor, analises, carteira: dict | None = None,
           proventos: dict | None = None, *,
           hoje: dt.date | None = None) -> ResumoCarteira:
    carteira = carteira or {}
    hoje = hoje or dt.date.today()
    c = ctx.calculos
    investido = _num(carteira.get("total_investido"))
    rentab = (_num(carteira.get("rentabilidade_total_pct"))
              if carteira.get("rentabilidade_total_disponivel") else None)
    resultado = (ctx.total_mercado - investido
                 if rentab is not None and investido else None)
    renda_ok = bool(proventos) and proventos.get("data_source") != "error"
    alertas = tuple(a for a in (c.alertas if c else ())
                    if a.severidade in (calc.ALTA, calc.MEDIA))[:MAX_ALERTAS]
    return ResumoCarteira(
        patrimonio=ctx.total_mercado,
        total_investido=investido,
        rentabilidade_pct=rentab,
        resultado=resultado,
        renda_12m=_num(proventos.get("total_12m")) if renda_ok else None,
        renda_total=_num(proventos.get("total_historico")) if renda_ok else None,
        alocacao=_alocacao(c) if c else (),
        fora_da_politica=ctx.peso_fora_da_politica,
        concentracoes=_concentracoes(c) if c else (),
        objetivo=_rot("objective", ctx.objetivo),
        estrategia=_rot("predominant_strategy", ctx.estrategia),
        horizonte=_rot("time_horizon", ctx.horizonte),
        perfil=_rot("risk_profile", ctx.perfil_risco),
        cenario=_texto_cenario(ctx),
        cenario_revisao=bool(ctx.sinais_cenario),
        alertas=alertas,
        eventos=_eventos(analises, hoje),
        revisao=_revisao(analises),
        versao_politica=ctx.versao_politica,
    )


# -- cartão de cada ativo ----------------------------------------------------------

def status_tese(tese: m.Tese) -> str:
    if tese.valida is True:
        return TESE_VALIDA
    if tese.valida is False:
        return TESE_EM_RISCO
    return TESE_SEM_VEREDITO


def _faixa(a: m.AnaliseAtivo) -> str:
    fx = a.faixa
    if fx.piso_ativo is not None or fx.alvo_ativo is not None:
        alvo = f" · alvo {_pct(fx.alvo_ativo)}" if fx.alvo_ativo is not None else ""
        return f"{_pct(fx.piso_ativo)} a {_pct(fx.teto_ativo)}{alvo}"
    partes = []
    if fx.alvo_classe is not None:
        partes.append(f"classe alvo {_pct(fx.alvo_classe, 0)}")
    if fx.teto_ativo is not None:
        partes.append(f"teto {_pct(fx.teto_ativo, 0)}")
    return " · ".join(partes) or "sem alvo definido"


def texto_valuation(a: m.AnaliseAtivo) -> str:
    """A métrica que tem comparação, em uma linha. Comparação, não veredito."""
    s = a.valuation
    if not s.dados:
        return s.resumo if s.estado != m.PENDENTE else "—"
    v = val.Valuation.de_dict(s.dados)
    if not v.com_dado:
        return "Sem métrica com valor atual."
    comparada = next((ln for ln in v.com_dado
                      if ln.posicao_historica != val.SEM_DADO), None)
    if comparada is None:
        ln = v.com_dado[0]
        return f"{ln.rotulo} {ln.texto_atual(v.moeda)}"
    pos = val._POSICAO_TXT[comparada.posicao_historica]
    ligacao = " a" if comparada.posicao_historica == val.EM_LINHA else " da"
    return (f"{comparada.rotulo} {comparada.texto_atual(v.moeda)} · "
            f"{pos}{ligacao} média histórica")


def riscos(a: m.AnaliseAtivo, ctx: m.ContextoInvestidor) -> tuple[str, ...]:
    """Sinais objetivos de risco do ativo, do mais grave para o menos.

    Gatilho da tese disparado vem antes de limite da estratégia, que vem
    antes de alerta de setor ou emissor: é a ordem em que cada um pede ação.
    """
    saida = [f"{g.descricao} — {g.detalhe}" if g.detalhe else g.descricao
             for g in a.tese.gatilhos if g.disparado]
    alertas = ctx.calculos.alertas if ctx.calculos else ()
    info = a.ativo
    chaves = {(calc.DIM_ATIVO, info.ticker)}
    if info.setor:
        chaves.add((calc.DIM_SETOR, info.setor))
    if a.faixa.emissor:
        chaves.add((calc.DIM_EMISSOR, a.faixa.emissor))
    for al in sorted(alertas, key=lambda x: calc._ORDEM_SEVERIDADE[x.severidade]):
        if (al.dimensao, al.chave) in chaves:
            saida.append(al.mensagem)
    return tuple(dict.fromkeys(saida))


def _proximo_evento(a: m.AnaliseAtivo, hoje: dt.date) -> str:
    if not a.eventos.dados:
        return SEM_EVENTO
    futuros = [e for e in inf.Eventos.de_dict(a.eventos.dados).itens
               if (e.data or "")[:10] >= hoje.isoformat()]
    if not futuros:
        return SEM_EVENTO
    e = futuros[0]
    return f"{e.rotulo} em {inf._data_br(e.data)}"


def card(a: m.AnaliseAtivo, ctx: m.ContextoInvestidor, *,
         hoje: dt.date | None = None) -> CardAtivo:
    hoje = hoje or dt.date.today()
    rs = riscos(a, ctx)
    return CardAtivo(
        ticker=a.ativo.ticker,
        nome=a.ativo.nome,
        moeda=a.ativo.moeda,
        valor=a.ativo.valor_mercado,
        peso=a.ativo.peso_atual,
        faixa=_faixa(a),
        papel=a.papel_principal.rotulo if a.papel_principal else "—",
        tese=status_tese(a.tese),
        valuation=texto_valuation(a),
        risco=rs[0] if rs else SEM_RISCO,
        evento=_proximo_evento(a, hoje),
        acao=a.acao.estado,
        acao_rotulo=a.acao.rotulo,
    )


def cards(analises, ctx: m.ContextoInvestidor, *,
          hoje: dt.date | None = None) -> list[CardAtivo]:
    """Ativos em revisão primeiro; depois, do maior peso para o menor."""
    todos = [card(a, ctx, hoje=hoje) for a in analises]
    return sorted(todos, key=lambda c: (not c.em_revisao,
                                        -m.PRIORIDADE_ACAO[c.acao], -c.peso,
                                        c.ticker))
