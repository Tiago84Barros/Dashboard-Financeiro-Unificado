"""
core/inteligencia_ativos/resumida.py
Página resumida da Inteligência dos Ativos (30/09/2026).

A aba ficou carregada: resumo, cartões, cálculos, 13 etapas, Portfolio Fit e
histórico, tudo aberto de uma vez. O usuário desenhou o que quer ver primeiro:

    Reserva de emergência → Tesouro Selic, valor e %
    Renda fixa            → as demais, valor e %
    Ações                 → uma caixa por ativo: % atual, % devida, manter /
                            comprar / vender, substituto se vender, dois pares
                            numa tabela, notícias, relatórios e o macro

Este módulo só reagrupa o que ``analisar_carteira`` já montou; não lê banco,
não chama LLM e não calcula número novo. O detalhe completo continua na aba,
abaixo, sob demanda.

Coberto por tests/test_inteligencia_ativos_resumida.py.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.cenario import modelo as cen
from core.estrategia import politica as pol
from core.inteligencia_ativos import informacoes as inf
from core.inteligencia_ativos import modelos as m
from core.inteligencia_ativos import pares as prs
from core.inteligencia_ativos.fundamentos import _tem_valor, formatar

# -- grupos da página, na ordem da tela ------------------------------------------

RESERVA = "reserva"
RENDA_FIXA = "renda_fixa"
ACOES = "acoes_br"
FIIS = "fiis"
EXTERIOR = "exterior"
OUTROS = "outros"

GRUPOS: tuple[tuple[str, str], ...] = (
    (RESERVA, "Reserva de emergência"),
    (RENDA_FIXA, "Renda fixa"),
    (ACOES, "Ações"),
    (FIIS, "Fundos imobiliários"),
    (EXTERIOR, "Internacional"),
    (OUTROS, "Outros ativos"),
)
ROTULO_GRUPO = dict(GRUPOS)
# Grupos que aparecem como lista simples (valor e %). Os demais ganham uma
# caixa de expansão por ativo.
GRUPOS_EM_LISTA = (RESERVA, RENDA_FIXA)

# -- manter / comprar / vender -----------------------------------------------------

MANTER = "manter"
COMPRAR = "comprar"
VENDER = "vender"
ROTULO_DECISAO = {MANTER: "Manter", COMPRAR: "Comprar", VENDER: "Vender"}

# A ação da análise é leitura de adequação, com sete estados. A página
# resumida a traduz nas três palavras que o usuário pediu, e mostra o estado
# original ao lado: "Vender" de REDUZIR_CONCENTRACAO é vender parte.
DECISAO: dict[str, tuple[str, str]] = {
    m.APORTE_COMPATIVEL: (COMPRAR, "novos aportes cabem na estratégia"),
    m.REAVALIAR_TESE: (VENDER, "avaliar venda: a tese está em dúvida"),
    m.REDUZIR_CONCENTRACAO: (VENDER, "vender parte: reduzir concentração"),
    m.COMPARAR_ALTERNATIVAS: (VENDER, "avaliar troca por uma alternativa"),
    m.REAVALIAR_APORTES: (MANTER, "manter, sem novos aportes por ora"),
    m.EXPOSICAO_ADEQUADA: (MANTER, "exposição já adequada"),
    m.MANTER: (MANTER, "nenhuma regra pede mudança"),
}

AVISO_DECISAO = ("Leitura de adequação à sua estratégia (pesos, limites e "
                 "tese), não ordem de compra ou venda. Fundamentos e valuation "
                 "ainda não entram nela.")
AVISO_SUBSTITUTO = ("Candidatos do mesmo grupo de comparação, fora da sua "
                    "carteira. O sistema não os ordena como melhores: compare "
                    "na tabela antes de decidir.")

N_PARES_TABELA = 2
N_NOTICIAS = 3
N_RELATORIOS = 3


def _eh_selic(a: m.AnaliseAtivo) -> bool:
    texto = f"{a.ativo.nome} {a.ativo.ticker}".upper()
    return "SELIC" in texto or "LFT" in texto.split()


def grupo(a: m.AnaliseAtivo) -> str:
    """Em qual bloco da página o ativo aparece. Puro.

    Reserva de emergência é o Tesouro Selic (o do rascunho) ou o que o
    usuário declarar com esse papel. O resto da renda fixa é "Renda fixa".
    """
    if (any(p.codigo == "emergency_reserve" for p in a.papeis)
            or (a.ativo.classe_politica == "renda_fixa" and _eh_selic(a))):
        return RESERVA
    cp = a.ativo.classe_politica
    if cp in (RENDA_FIXA, ACOES, FIIS, EXTERIOR):
        return cp
    return OUTROS


@dataclass(frozen=True)
class Decisao:
    codigo: str          # MANTER | COMPRAR | VENDER
    detalhe: str         # por que, em poucas palavras
    motivo: str          # a primeira justificativa da análise

    @property
    def rotulo(self) -> str:
        return ROTULO_DECISAO[self.codigo]


def decisao(a: m.AnaliseAtivo) -> Decisao:
    codigo, detalhe = DECISAO.get(a.acao.estado, (MANTER, a.acao.rotulo))
    motivo = a.acao.justificativas[0] if a.acao.justificativas else ""
    return Decisao(codigo, detalhe, motivo)


# -- porcentagem devida ------------------------------------------------------------

def _pct(x: float | None, casas: int = 1) -> str:
    return "—" if x is None else f"{x:.{casas}f}%".replace(".", ",")


def _pp(x: float | None) -> str:
    return "—" if x is None else f"{x:+.1f} pp".replace(".", ",")


@dataclass(frozen=True)
class PesoDevido:
    valor: str           # o número principal ("5,0%" ou "até 10%")
    detalhe: str         # de onde vem


def peso_devido(a: m.AnaliseAtivo) -> PesoDevido:
    """O quanto o ativo deveria pesar, sem inventar alvo que ninguém definiu.

    A estratégia tem alvo por classe e, se respondido, limite por ativo. Só
    quando o usuário define uma faixa para o ativo há um alvo individual.
    """
    fx = a.faixa
    if fx.alvo_ativo is not None:
        return PesoDevido(_pct(fx.alvo_ativo),
                          f"alvo que você definiu (faixa {_pct(fx.piso_ativo)}"
                          f" a {_pct(fx.teto_ativo)})")
    if fx.teto_ativo is not None:
        return PesoDevido(f"até {_pct(fx.teto_ativo, 0)}",
                          "limite por ativo da sua estratégia; folga de "
                          f"{_pp(fx.folga_ativo)}. Sem alvo individual.")
    return PesoDevido("sem alvo",
                      "a estratégia define alvo por classe, não por ativo")


# -- blocos da página ---------------------------------------------------------------

@dataclass(frozen=True)
class Linha:
    ticker: str
    nome: str
    moeda: str
    valor: float | None
    peso: float


@dataclass(frozen=True)
class Bloco:
    chave: str
    rotulo: str
    peso: float                         # % da carteira
    valor: float                        # soma do valor de mercado
    alvo_classe: float | None           # alvo da classe da política
    peso_classe: float | None           # % atual da classe inteira
    analises: tuple[m.AnaliseAtivo, ...]

    @property
    def linhas(self) -> tuple[Linha, ...]:
        return tuple(Linha(a.ativo.ticker, a.ativo.nome, a.ativo.moeda,
                           a.ativo.valor_mercado, a.ativo.peso_atual)
                     for a in self.analises)


def blocos(analises, ctx: m.ContextoInvestidor) -> list[Bloco]:
    """Os ativos agrupados na ordem da página, cada grupo por peso. Puro."""
    por_grupo: dict[str, list[m.AnaliseAtivo]] = {}
    for a in analises:
        por_grupo.setdefault(grupo(a), []).append(a)
    saida = []
    for chave, rotulo in GRUPOS:
        itens = sorted(por_grupo.get(chave, ()),
                       key=lambda a: (-a.ativo.peso_atual, a.ativo.ticker))
        if not itens:
            continue
        classe = "renda_fixa" if chave == RESERVA else chave
        saida.append(Bloco(
            chave, rotulo,
            peso=sum(a.ativo.peso_atual for a in itens),
            valor=sum(a.ativo.valor_mercado or 0.0 for a in itens),
            alvo_classe=ctx.alocacao_alvo.get(classe),
            peso_classe=ctx.peso_por_classe.get(classe),
            analises=tuple(itens)))
    return saida


def meta_reserva(politica: dict | None) -> str | None:
    """"6 meses de gastos" se a estratégia disser; senão None."""
    if not politica:
        return None
    meses = pol.valor(politica, "emergency_reserve_months")
    if meses:
        return f"{meses:g} meses de gastos, segundo a sua estratégia"
    if pol.valor(politica, "has_emergency_reserve") is False:
        return "A sua estratégia registra que ainda não há reserva separada"
    return None


# -- pares e substitutos --------------------------------------------------------------

@dataclass(frozen=True)
class TabelaPares:
    titulo: str                         # "mesmo segmento (Bancos)"
    colunas: tuple[str, ...]            # métricas
    linhas: tuple[tuple[str, tuple[str, ...]], ...]   # (ticker, valores)
    motivo: str | None = None           # por que não há tabela


def _comparacao(a: m.AnaliseAtivo) -> prs.ComparacaoPares | None:
    if not a.pares.dados:
        return None
    return prs.ComparacaoPares.de_dict(a.pares.dados)


def tabela_pares(a: m.AnaliseAtivo, n: int = N_PARES_TABELA) -> TabelaPares:
    """O ativo e os ``n`` pares mais próximos, lado a lado, métrica a
    métrica. Só colunas com algum dado. Puro."""
    c = _comparacao(a)
    if c is None or not c.grupo.pares:
        motivo = ((c.motivo or c.grupo.motivo) if c else None) or a.pares.resumo
        return TabelaPares("", (), (), motivo or inf.NAO_DISPONIVEL)
    pares = sorted(c.grupo.pares, key=lambda p: p.distancia)[:n]
    metricas = [ln for ln in c.linhas
                if ln.valor is not None
                or any(_tem_valor(p.metricas.get(ln.chave)) for p in pares)]
    if not metricas:
        return TabelaPares("", (), (), "Nenhuma métrica com dado para "
                                       "comparar com os pares.")

    def _val(v, ln) -> str:
        return formatar(v, ln.unidade, c.moeda) if _tem_valor(v) else "—"
    linhas = [(a.ativo.ticker, tuple(_val(ln.valor, ln) for ln in metricas))]
    linhas += [(p.ticker, tuple(_val(p.metricas.get(ln.chave), ln)
                                for ln in metricas)) for p in pares]
    titulo = f"mesmo {c.grupo.nivel} ({c.grupo.valor})"
    return TabelaPares(titulo, tuple(ln.metrica for ln in metricas),
                       tuple(linhas))


def substitutos(a: m.AnaliseAtivo, na_carteira, n: int = N_PARES_TABELA
                ) -> tuple[prs.Par, ...]:
    """Pares do mesmo grupo que não estão na carteira, os mais próximos
    primeiro. Proximidade é de perfil (porte, risco), não de qualidade."""
    c = _comparacao(a)
    if c is None:
        return ()
    fora = [p for p in c.grupo.pares if p.ticker not in set(na_carteira)]
    return tuple(sorted(fora, key=lambda p: p.distancia)[:n])


# -- notícias e relatórios ------------------------------------------------------------

def noticias(a: m.AnaliseAtivo, n: int = N_NOTICIAS):
    """(itens, motivo): as ``n`` manchetes mais recentes, ou por que não há."""
    if not a.noticias.dados:
        return (), a.noticias.resumo
    ns = inf.Noticias.de_dict(a.noticias.dados)
    itens = sorted(ns.itens, key=lambda i: i.date or "", reverse=True)[:n]
    return tuple(itens), (None if itens else (ns.motivo or a.noticias.resumo))


def relatorios(a: m.AnaliseAtivo, n: int = N_RELATORIOS):
    """(documentos, motivo): os ``n`` documentos oficiais mais recentes."""
    if not a.relatorios.dados:
        return (), a.relatorios.resumo
    r = inf.Relatorios.de_dict(a.relatorios.dados)
    docs = sorted(r.documentos, key=lambda d: d.reference_date or "",
                  reverse=True)[:n]
    return tuple(docs), (None if docs else (r.motivo or a.relatorios.resumo))


# -- macro --------------------------------------------------------------------------

@dataclass(frozen=True)
class Macro:
    canais: tuple[str, ...]              # variáveis que mais pesam na classe
    premissas: tuple[str, ...]           # o que o usuário registrou delas
    sinais: tuple[str, ...]              # divergência calculada pelo código
    sem_cenario: bool


def macro(a: m.AnaliseAtivo, ctx: m.ContextoInvestidor) -> Macro:
    """O que do ambiente econômico mais pesa para a classe do ativo e o que
    o seu cenário diz sobre isso. A leitura do impacto é da LLM (Portfolio
    Fit); aqui só o dado. Puro."""
    chaves = cen.relevantes(a.ativo.classe_politica)
    canais = tuple(cen.ROTULO[k] for k in chaves)
    c = ctx.cenario
    if c is None or c.vazio:
        return Macro(canais, (), (), True)
    premissas = []
    for k in chaves:
        it = c.item(k)
        if not it.preenchido:
            continue
        direcao = cen.ROTULO_DIRECAO.get(it.expected_direction)
        premissas.append(f"{cen.ROTULO[k]}: {it.current_value}"
                         + (f" · tendência {direcao.lower()}" if direcao else ""))
    sinais = tuple(s.texto for s in ctx.sinais_cenario if s.chave in chaves)
    return Macro(canais, tuple(premissas), sinais, False)
