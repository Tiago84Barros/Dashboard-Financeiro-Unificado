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

Este módulo só reagrupa o que ``analisar_carteira`` já montou; não lê banco
nem chama LLM. O único número novo é a % devida sugerida (``alvos_sugeridos``,
pedido do usuário em 30/09/2026): o alvo da classe repartido pelo inverso da
volatilidade, com teto por ativo. O detalhe completo continua na aba, abaixo,
sob demanda.

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


@dataclass(frozen=True)
class AlvoSugerido:
    """Quanto o ativo deveria pesar para a classe ficar equilibrada em risco.

    O alvo da classe vem da sua estratégia; a divisão entre os ativos da
    classe é do sistema (30/09/2026, pedido do usuário: "deve-se sim sugerir
    a porcentagem que o ativo deve atingir"). Cada ativo recebe uma fatia
    proporcional ao inverso da volatilidade, para que todos contribuam com
    risco parecido. O limite por ativo da estratégia é teto, e o que passa
    dele vai para os outros.
    """
    peso: float                   # % da carteira
    volatilidade: float | None    # % a.a. usada no cálculo (None = mediana)
    limitado: bool                # bateu no limite por ativo
    alvo_classe: float
    metodo: str                   # "inverso da volatilidade" | "pesos iguais"


METODO_VOL = "inverso da volatilidade"
METODO_IGUAL = "pesos iguais"


def volatilidade(a: m.AnaliseAtivo) -> float | None:
    """Volatilidade anualizada do ativo (%), lida da comparação com pares."""
    c = _comparacao(a)
    if c is None:
        return None
    for ln in c.linhas:
        if ln.chave == "volatilidade" and _tem_valor(ln.valor) and ln.valor > 0:
            return float(ln.valor)
    return None


def _distribuir(cotas: dict[str, float], total: float,
                tetos: dict[str, float | None]) -> tuple[dict[str, float], set]:
    """Divide ``total`` proporcionalmente às cotas, respeitando tetos; o que
    sobra de quem bateu no teto vai para os demais (water-filling)."""
    pesos: dict[str, float] = {}
    presos: set[str] = set()
    livres = dict(cotas)
    resto = total
    while livres:
        soma = sum(livres.values())
        tentativa = {k: resto * v / soma for k, v in livres.items()}
        estourou = {k for k, w in tentativa.items()
                    if tetos.get(k) is not None and w > tetos[k] + 1e-9}
        if not estourou:
            pesos.update(tentativa)
            break
        for k in estourou:
            pesos[k] = float(tetos[k])
            resto -= pesos[k]
            presos.add(k)
            livres.pop(k)
    return pesos, presos


def alvos_sugeridos(b: "Bloco") -> dict[str, AlvoSugerido]:
    """Alvo sugerido de cada ativo do bloco. Vazio sem alvo da classe. Puro.

    Sem volatilidade de um ativo, ele recebe a mediana dos demais; sem
    nenhuma, os pesos ficam iguais. Reserva e renda fixa não entram: lá a
    estratégia fala em classe e em meses de reserva, não em risco de preço.
    """
    if b.alvo_classe is None or b.chave in GRUPOS_EM_LISTA or not b.analises:
        return {}
    vols = {a.ativo.ticker: volatilidade(a) for a in b.analises}
    conhecidas = sorted(v for v in vols.values() if v)
    if conhecidas:
        meio = len(conhecidas) // 2
        mediana = (conhecidas[meio] if len(conhecidas) % 2
                   else (conhecidas[meio - 1] + conhecidas[meio]) / 2)
        cotas = {tk: 1.0 / (v or mediana) for tk, v in vols.items()}
        metodo = METODO_VOL
    else:
        cotas = {tk: 1.0 for tk in vols}
        metodo = METODO_IGUAL
    tetos = {a.ativo.ticker: a.faixa.teto_ativo for a in b.analises}
    pesos, presos = _distribuir(cotas, float(b.alvo_classe), tetos)
    return {tk: AlvoSugerido(pesos[tk], vols[tk], tk in presos,
                             float(b.alvo_classe), metodo)
            for tk in vols}


def peso_devido(a: m.AnaliseAtivo,
                sugerido: AlvoSugerido | None = None) -> PesoDevido:
    """O quanto o ativo deveria pesar.

    1. Faixa que o usuário definiu para o ativo, se houver.
    2. Senão, o alvo sugerido (alvo da classe dividido por risco).
    3. Sem alvo da classe, só o limite por ativo, ou nada.
    """
    fx = a.faixa
    if fx.alvo_ativo is not None:
        return PesoDevido(_pct(fx.alvo_ativo),
                          f"alvo que você definiu (faixa {_pct(fx.piso_ativo)}"
                          f" a {_pct(fx.teto_ativo)})")
    if sugerido is not None:
        dif = sugerido.peso - a.ativo.peso_atual
        if abs(dif) < 0.5:
            onde = "já está no alvo"
        elif dif > 0:
            onde = f"faltam {_pp(dif)[1:]}"
        else:
            onde = f"{_pp(-dif)[1:]} acima"
        base = (f"inverso da volatilidade ({_pct(sugerido.volatilidade)} a.a.)"
                if sugerido.metodo == METODO_VOL and sugerido.volatilidade
                else "volatilidade mediana da classe"
                if sugerido.metodo == METODO_VOL else "pesos iguais")
        teto = (f"; travado no limite por ativo de {_pct(fx.teto_ativo, 0)}"
                if sugerido.limitado else "")
        return PesoDevido(_pct(sugerido.peso),
                          f"sugestão: alvo da classe ({_pct(sugerido.alvo_classe, 0)}"
                          f") dividido pelo {base}{teto}. {onde.capitalize()}.")
    if fx.teto_ativo is not None:
        return PesoDevido(f"até {_pct(fx.teto_ativo, 0)}",
                          "limite por ativo da sua estratégia; folga de "
                          f"{_pp(fx.folga_ativo)}. A estratégia não tem alvo "
                          "para a classe.")
    return PesoDevido("sem alvo",
                      "a sua estratégia não define alvo para esta classe")


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
    def alvos(self) -> dict[str, AlvoSugerido]:
        return alvos_sugeridos(self)

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
class Ponto:
    ticker: str
    texto: str                          # valor formatado
    posicao: float                      # 0 a 100, na faixa da métrica
    eh_ativo: bool


@dataclass(frozen=True)
class MetricaPares:
    """Uma métrica no painel de comparação: o ativo, os pares na mesma
    régua e a mediana do segmento inteiro. Sem "melhor" nem "pior": a leitura
    é a neutra de ``pares.LEITURA``."""
    chave: str
    rotulo: str
    texto_ativo: str
    texto_mediana: str
    n_pares: int
    posicao: str                        # valuation.ACIMA/ABAIXO/EM_LINHA/SEM_DADO
    pontos: tuple[Ponto, ...]
    mediana_pos: float | None           # 0 a 100
    leitura: str


ROTULO_POSICAO = {prs.ACIMA: "acima da mediana", prs.ABAIXO: "abaixo da mediana",
                  prs.EM_LINHA: "em linha com a mediana",
                  prs.SEM_DADO: "sem dado do ativo"}


@dataclass(frozen=True)
class TabelaPares:
    titulo: str                         # "mesmo segmento (Bancos)"
    colunas: tuple[str, ...]            # métricas
    linhas: tuple[tuple[str, tuple[str, ...]], ...]   # (ticker, valores)
    motivo: str | None = None           # por que não há tabela
    metricas: tuple[MetricaPares, ...] = ()
    nomes: tuple[tuple[str, str], ...] = ()           # (ticker, nome)
    n_grupo: int = 0                    # pares no segmento inteiro


def _metrica_pares(ln, pares, ticker: str, moeda: str) -> MetricaPares:
    brutos = [(ticker, ln.valor, True)] + [
        (p.ticker, p.metricas.get(ln.chave), False) for p in pares]
    valores = [float(v) for _, v, _ in brutos if _tem_valor(v)]
    if _tem_valor(ln.mediana_pares):
        valores.append(float(ln.mediana_pares))
    lo, hi = (min(valores), max(valores)) if valores else (0.0, 0.0)

    def _pos(v: float) -> float:
        return 50.0 if hi - lo < 1e-12 else 100.0 * (v - lo) / (hi - lo)
    pontos = tuple(Ponto(tk, formatar(v, ln.unidade, moeda), _pos(float(v)), eh)
                   for tk, v, eh in brutos if _tem_valor(v))
    med = ln.mediana_pares if _tem_valor(ln.mediana_pares) else None
    return MetricaPares(
        ln.chave, ln.metrica,
        formatar(ln.valor, ln.unidade, moeda) if _tem_valor(ln.valor) else "—",
        formatar(med, ln.unidade, moeda) if med is not None else "—",
        ln.n_pares, ln.posicao, pontos,
        None if med is None else _pos(float(med)),
        prs.LEITURA.get(ln.chave, ""))


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
    nomes = ((a.ativo.ticker, a.ativo.nome or ""),) + tuple(
        (p.ticker, p.nome or "") for p in pares)
    return TabelaPares(
        titulo, tuple(ln.metrica for ln in metricas), tuple(linhas),
        metricas=tuple(_metrica_pares(ln, pares, a.ativo.ticker, c.moeda)
                       for ln in metricas),
        nomes=nomes, n_grupo=len(c.grupo.pares))


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


def noticias_setor(a: m.AnaliseAtivo, n: int = N_NOTICIAS):
    """(itens, segmento): notícias dos pares quando o ativo não tem as suas."""
    if not a.noticias.dados:
        return (), None
    ns = inf.Noticias.de_dict(a.noticias.dados)
    if ns.itens:
        return (), None
    return ns.setor[:n], ns.setor_rotulo


def precisa_noticiario_geral(a: m.AnaliseAtivo, n: int = N_NOTICIAS) -> bool:
    """A caixa nunca fica em branco: com menos de ``n`` manchetes do ativo e
    do segmento, ela completa com o noticiário geral do mercado."""
    return len(noticias(a, n)[0]) + len(noticias_setor(a, n)[0]) < n


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
    lido_dos_dados: bool = False


def macro(a: m.AnaliseAtivo, ctx: m.ContextoInvestidor) -> Macro:
    """O que do ambiente econômico mais pesa para a classe do ativo e o que
    o cenário (lido dos dados desde 30/09/2026) diz sobre isso. A leitura do
    impacto é da LLM (Portfolio Fit); aqui só o dado. Puro."""
    chaves = cen.relevantes(a.ativo.classe_politica)
    canais = tuple(cen.ROTULO[k] for k in chaves)
    c = ctx.cenario
    dados = c is not None and c.origem == cen.DADOS
    if c is None or c.vazio:
        return Macro(canais, (), (), True, dados)
    premissas = []
    for k in chaves:
        it = c.item(k)
        if not it.preenchido:
            continue
        direcao = cen.ROTULO_DIRECAO.get(it.expected_direction)
        premissas.append(f"{cen.ROTULO[k]}: {it.current_value}"
                         + (f" · tendência {direcao.lower()}" if direcao else ""))
    sinais = tuple(s.texto for s in ctx.sinais_cenario if s.chave in chaves)
    return Macro(canais, tuple(premissas), sinais, False, dados)


# -- cenário econômico (lido dos dados) ---------------------------------------------

@dataclass(frozen=True)
class LinhaCenario:
    rotulo: str
    valor: str | None          # None: sem dado
    tendencia: str | None      # alta / estavel / queda / incerta
    fonte: str
    referencia: str | None     # DD/MM/AAAA


def cenario_atual(c) -> tuple[LinhaCenario, ...]:
    """As 12 variáveis do cenário em linhas de tela, na ordem do modelo. Puro."""
    if c is None:
        return ()
    saida = []
    for k in cen.CHAVES:
        it = c.item(k)
        ref = cen.data_iso(it.last_updated)
        saida.append(LinhaCenario(
            cen.ROTULO[k], it.current_value or None,
            it.expected_direction or None, it.source or "",
            ref.strftime("%d/%m/%Y") if ref else None))
    return tuple(saida)
