"""
core/inteligencia_ativos/recomendacao.py
Peças da recomendação de movimentação: esticamento, confronto, custo e
convicção (29/10/2026).

Pedido do usuário (29/10/2026): as sugestões de venda, redução ou
substituição devem ser feitas "com cautela e considerando o contexto de cada
ativo". Uma empresa de qualidade não se vende porque a cotação passou de um
percentual: o limite é alerta para análise, nunca ordem. Antes de mover,
pesar juntos qualidade, tese, valuation contra o próprio histórico, o quanto
o preço esticou, o peso na carteira e os custos e impostos da saída.

A árvore de decisão mora em ``resumida.decisao``; este módulo mede o que ela
precisa além do peso:

* **Esticamento** — em que percentil do próprio histórico estão os
  múltiplos. "Caro" (``avaliacao``) já exige histórico e pares; esticado é
  caro *e* no topo do histórico: só isso justifica realizar parte de uma
  posição sólida.
* **Confronto** — um par só é substituto quando é claramente superior na
  qualidade e não pior em valuation nem em risco, numa comparação com pelo
  menos três métricas. Mais barato não basta (ordenar não é superar).
* **Custo da venda** — IR pela regra do tipo fiscal do ticker
  (``core.ir_renda_variavel``) e meia-spread só onde ele foi calibrado
  (``core.transaction_costs``). Onde não há regra calibrada (exterior, FII
  no spread), o texto diz que não calculou, e não inventa número.
* **Convicção** — alta, moderada ou baixa, pela cobertura das três
  dimensões e por quantos sinais apontam contra a ação sugerida.

Puro: nenhuma consulta a banco, nenhuma LLM.
Coberto por tests/test_inteligencia_recomendacao.py.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.inteligencia_ativos import avaliacao as av_
from core.inteligencia_ativos import modelos as m
from core.inteligencia_ativos import pares as prs
from core.inteligencia_ativos import valuation as val
from core.inteligencia_ativos.fundamentos import _tem_valor, formatar

# -- esticamento ---------------------------------------------------------------------

# Percentil do próprio histórico a partir do qual o múltiplo conta como no
# topo (para P/L, P/VP e EV/EBIT; no yield e no cap rate, o espelho: 100 −
# percentil, porque yield baixo é preço alto). 80 marca os 20% mais caros
# da história do ativo; 90, os 10% mais caros. Convenção do sistema, não
# calibrada.
PERCENTIL_ESTICADO = 80.0
PERCENTIL_MUITO_ESTICADO = 90.0
_ALTO_E_CARO = ("p_l", "p_vp", "ev_ebit")
_BAIXO_E_CARO = ("dividend_yield", "cap_rate")

ESTICADO, MUITO_ESTICADO = 1, 2


@dataclass(frozen=True)
class Esticamento:
    nivel: int                       # 0 | ESTICADO | MUITO_ESTICADO
    medido: bool                     # algum múltiplo com percentil histórico
    evidencias: tuple[str, ...]      # múltiplos no topo do histórico
    valorizacao: str | None = None   # retorno de 12 meses contra os pares
    excesso_pp: float | None = None  # retorno − mediana dos pares, em p.p.

    @property
    def rotulo(self) -> str:
        return {MUITO_ESTICADO: "muito esticado",
                ESTICADO: "esticado"}.get(self.nivel, "não esticado")


def _periodo(est: val.Estatistica | None) -> str:
    if est is None:
        return ""
    ini, fim = (est.inicio or "")[:4], (est.fim or "")[:4]
    return f", {ini}–{fim}" if ini and fim and ini != fim else ""


def esticamento(a: m.AnaliseAtivo, av: av_.Avaliacao) -> Esticamento:
    """O quanto o preço esticou contra o histórico do próprio ativo. Puro.

    Nível 1 (esticado): valuation caro e ao menos um múltiplo no percentil
    80 do histórico. Nível 2 (muito esticado): caro e um múltiplo no
    percentil 90, ou dois no 80. Sem "caro" (histórico e pares juntos) o
    nível fica 0 mesmo com múltiplo no topo: aí é alerta, não motivo. P/L de
    empresa cíclica não entra (no pico do ciclo o lucro infla e o P/L cai).
    """
    v = val.Valuation.de_dict(av_._secao(a.valuation))
    evid: list[str] = []
    topos: list[float] = []
    medido = False
    for ln in v.linhas:
        if (not ln.aplicavel or ln.atual is None
                or ln.percentil_historico is None
                or ln.chave not in _ALTO_E_CARO + _BAIXO_E_CARO):
            continue
        if ln.chave == "p_l" and av.perfil == av_.CICLICA:
            continue
        medido = True
        p = float(ln.percentil_historico)
        topo = p if ln.chave in _ALTO_E_CARO else 100.0 - p
        if topo < PERCENTIL_ESTICADO:
            continue
        topos.append(topo)
        h = ln.historico
        mediana = (f"mediana {formatar(h.mediana, ln.unidade, v.moeda)}"
                   f"{_periodo(h)}" if h is not None else "")
        espelho = ("; yield baixo é preço alto"
                   if ln.chave in _BAIXO_E_CARO else "")
        evid.append(f"{ln.rotulo} de {formatar(ln.atual, ln.unidade, v.moeda)}"
                    f" no percentil {p:.0f} do próprio histórico"
                    f" ({mediana}{espelho})" if mediana else
                    f"{ln.rotulo} de {formatar(ln.atual, ln.unidade, v.moeda)}"
                    f" no percentil {p:.0f} do próprio histórico{espelho}")
    nivel = 0
    if av.preco == av_.CARO and topos:
        muito = (any(t >= PERCENTIL_MUITO_ESTICADO for t in topos)
                 or len(topos) >= 2)
        nivel = MUITO_ESTICADO if muito else ESTICADO
    texto, excesso = _valorizacao(a)
    return Esticamento(nivel, medido, tuple(evid), texto, excesso)


def _valorizacao(a: m.AnaliseAtivo) -> tuple[str | None, float | None]:
    c = _comparacao(a)
    ln = c.linha("retorno_12m") if c is not None else None
    if ln is None or not _tem_valor(ln.valor):
        return None, None
    r = float(ln.valor)
    if not _tem_valor(ln.mediana_pares) or not ln.n_pares:
        return (f"Retorno de {av_._pct(r)} em 12 meses, com proventos (sem "
                "pares para comparar)"), None
    dif = r - float(ln.mediana_pares)
    rel = ("acima" if dif > 0 else "abaixo") if abs(dif) >= 0.05 else "em linha"
    return (f"Retorno de {av_._pct(r)} em 12 meses, com proventos: "
            f"{av_._br(abs(dif))} p.p. {rel} da mediana de "
            f"{av_._pares(ln.n_pares)} ({av_._pct(float(ln.mediana_pares))})"
            if rel != "em linha" else
            f"Retorno de {av_._pct(r)} em 12 meses, com proventos, em linha "
            f"com a mediana de {av_._pares(ln.n_pares)}"), dif


def _comparacao(a: m.AnaliseAtivo) -> prs.ComparacaoPares | None:
    if not a.pares.dados:
        return None
    return prs.ComparacaoPares.de_dict(a.pares.dados)


# -- confronto com um substituto ----------------------------------------------------

Q, V, R = "qualidade", "valuation", "risco"
# (chave, dimensão, maior é melhor). Múltiplos só com valor positivo dos
# dois lados: P/L negativo não é "barato".
METRICAS_CONFRONTO: dict[str, tuple[tuple[str, str, bool], ...]] = {
    prs.ACAO: (("roe", Q, True), ("margem_liquida", Q, True),
               ("p_l", V, False), ("p_vp", V, False), ("ev_ebit", V, False),
               ("dividend_yield", V, True), ("volatilidade", R, False)),
    prs.FII: (("alavancagem", Q, False), ("p_vp", V, False),
              ("dividend_yield", V, True), ("cap_rate", V, True),
              ("max_drawdown", R, False)),
}
_SO_POSITIVO = ("p_l", "p_vp", "ev_ebit")
# Diferença relativa até a qual dois valores empatam (a mesma régua de "em
# linha" do valuation).
EMPATE_RELATIVO = val.TOLERANCIA_EM_LINHA
# Menos de três métricas comparadas não sustenta uma troca. Convenção do
# sistema, não calibrada.
MIN_METRICAS_TROCA = 3


@dataclass(frozen=True)
class Confronto:
    ticker: str
    nome: str | None
    melhor: tuple[str, ...]          # onde o par supera o ativo
    pior: tuple[str, ...]            # onde o par perde
    saldo_qualidade: int
    saldo_valuation: int
    saldo_risco: int
    n: int                           # métricas comparadas

    @property
    def claramente_superior(self) -> bool:
        """Melhor na qualidade, não pior no preço nem no risco, com base
        mínima de comparação."""
        return (self.n >= MIN_METRICAS_TROCA and self.saldo_qualidade > 0
                and self.saldo_valuation >= 0 and self.saldo_risco >= 0)

    @property
    def resumo(self) -> str:
        partes = []
        if self.melhor:
            partes.append("supera em " + ", ".join(self.melhor))
        if self.pior:
            partes.append("perde em " + ", ".join(self.pior))
        corpo = "; ".join(partes) or "empata nas métricas comparadas"
        return f"{self.ticker} {corpo} ({self.n} métricas comparadas)"


def confrontar(a: m.AnaliseAtivo, par: prs.Par) -> Confronto | None:
    """O par contra o ativo, métrica a métrica, por dimensão. Puro."""
    c = _comparacao(a)
    if c is None:
        return None
    melhor, pior = [], []
    saldo = {Q: 0, V: 0, R: 0}
    n = 0
    for chave, dim, maior_melhor in METRICAS_CONFRONTO.get(c.tipo, ()):
        ln = c.linha(chave)
        if ln is None:
            continue
        x, y = ln.valor, par.metricas.get(chave)
        if not (_tem_valor(x) and _tem_valor(y)):
            continue
        x, y = float(x), float(y)
        if chave == "max_drawdown":
            x, y = abs(x), abs(y)
        if chave in _SO_POSITIVO and (x <= 0 or y <= 0):
            continue
        n += 1
        escala = max(abs(x), abs(y))
        if escala == 0 or abs(y - x) / escala <= EMPATE_RELATIVO:
            continue
        par_melhor = (y > x) == maior_melhor
        saldo[dim] += 1 if par_melhor else -1
        texto = (f"{ln.metrica} ({formatar(y, ln.unidade, c.moeda)} contra "
                 f"{formatar(x, ln.unidade, c.moeda)})")
        (melhor if par_melhor else pior).append(texto)
    if n == 0:
        return None
    return Confronto(par.ticker, par.nome, tuple(melhor), tuple(pior),
                     saldo[Q], saldo[V], saldo[R], n)


def melhor_substituto(a: m.AnaliseAtivo, candidatos) -> Confronto | None:
    """O primeiro candidato (os mais próximos primeiro) claramente superior,
    ou None."""
    for par in candidatos:
        cf = confrontar(a, par)
        if cf is not None and cf.claramente_superior:
            return cf
    return None


# -- custo da venda ------------------------------------------------------------------

@dataclass(frozen=True)
class CustoVenda:
    fracao: float                    # 0 a 1 da posição
    valor_bruto: float | None
    ganho: float | None              # None: sem custo de aquisição
    ir: float | None                 # None: não calculado
    custo_ordem: float | None        # meia-spread + corretagem; None: não calibrado
    linhas: tuple[str, ...]
    limitacoes: tuple[str, ...] = ()


def _reais(x: float) -> str:
    return "R$ " + f"{x:,.0f}".replace(",", ".")


def custo_da_venda(a: m.AnaliseAtivo, fracao: float) -> CustoVenda:
    """Imposto e custo de vender ``fracao`` da posição. Puro.

    Ganho = (valor de mercado − valor investido) × fração. IR pela regra do
    tipo fiscal: ação à vista tem isenção quando as vendas de ações do mês
    somam até R$ 20 mil, senão 15%; ETF e BDR, 15% sem isenção; FII, 20% sem
    isenção. Prejuízo não paga IR e compensa ganho futuro. Exterior não é
    calculado: a regra é outra e depende do câmbio de compra e de venda.
    """
    from core import ir_renda_variavel as ir
    from core import transaction_costs as tc

    info = a.ativo
    lim: list[str] = []
    linhas: list[str] = []
    vm = info.valor_mercado
    if not _tem_valor(vm) or vm <= 0:
        return CustoVenda(fracao, None, None, None, None, (),
                          ("Sem valor de mercado da posição: custo da venda "
                           "não calculado.",))
    bruto = float(vm) * fracao
    vi = info.valor_investido
    ganho = (bruto - float(vi) * fracao) if _tem_valor(vi) and vi > 0 else None
    if info.moeda != "BRL" or info.classe_politica == "exterior":
        return CustoVenda(fracao, bruto, ganho, None, None, (
            f"Venda de {fracao * 100:.0f}% da posição ≈ "
            f"{formatar(bruto, 'moeda', info.moeda)}.",),
            ("Imposto e custo de venda no exterior não calculados: a regra é "
             "a de ativo no exterior e o ganho depende do câmbio na compra e "
             "na venda.",))
    tipo = ir.tipo_fiscal(info.ticker,
                          "reit" if info.classe_politica == "fiis" else "stock")
    linhas.append(f"Venda de {fracao * 100:.0f}% da posição ≈ {_reais(bruto)}.")
    imposto: float | None = None
    if ganho is None:
        lim.append("Sem custo de aquisição da posição: ganho e imposto não "
                   "calculados.")
    elif ganho <= 0:
        imposto = 0.0
        linhas.append(f"Venda com prejuízo de {_reais(-ganho)}: não há IR, e "
                      "o prejuízo compensa ganhos futuros da mesma cesta.")
    elif tipo == "acao":
        isencao = float(ir.LIMITE_ISENCAO_ACOES)
        imposto = float(ir.ALIQUOTA["comum"]) * ganho
        if bruto > isencao:
            linhas.append(f"Ganho de {_reais(ganho)}: IR de 15% ≈ "
                          f"{_reais(imposto)} (venda acima de "
                          f"{_reais(isencao)} no mês).")
        else:
            linhas.append(f"Ganho de {_reais(ganho)}: isento se as vendas de "
                          f"ações no mês somarem até {_reais(isencao)}; acima "
                          f"disso, IR de 15% ≈ {_reais(imposto)}.")
            lim.append("As outras vendas de ações do mês não estão nesta "
                       "análise: a isenção depende delas.")
    elif tipo in ("etf", "bdr", "fii"):
        aliquota = float(ir.ALIQUOTA["fii" if tipo == "fii" else "comum"])
        imposto = aliquota * ganho
        rotulo = {"etf": "ETF", "bdr": "BDR", "fii": "FII"}[tipo]
        linhas.append(f"Ganho de {_reais(ganho)}: IR de {aliquota * 100:.0f}% "
                      f"≈ {_reais(imposto)} ({rotulo} não tem isenção mensal).")
    else:
        lim.append("Tipo fiscal não reconhecido pelo ticker: imposto não "
                   "calculado.")
    if imposto:
        linhas.append("A corretora retém 0,005% da venda (IRRF), abatido do IR "
                      "do mês.")
    custo: float | None = None
    if tipo == "acao":
        # Só a ação à vista da B3 tem spread calibrado (transaction_costs);
        # ETF, BDR e FII ficam como não estimados, não como zero.
        custo = tc.custo_venda(info.ticker, bruto, 0.0, 0.0, tc.CostConfig())[0]
        porte = "grande" if tc.is_large_cap(info.ticker) else "menor"
        linhas.append(f"Custo de execução (meia-spread estimada para empresa "
                      f"de porte {porte}) ≈ {_reais(custo)}.")
    else:
        lim.append("Spread de venda não calibrado para esta classe: custo de "
                   "execução não estimado.")
    return CustoVenda(fracao, bruto, ganho, imposto, custo, tuple(linhas),
                      tuple(lim))


# -- convicção -------------------------------------------------------------------------

ALTA, MODERADA, BAIXA = "alta", "moderada", "baixa"
ROTULO_CONVICCAO = {ALTA: "Alta", MODERADA: "Moderada", BAIXA: "Baixa"}
_ORDEM = {BAIXA: 0, MODERADA: 1, ALTA: 2}


@dataclass(frozen=True)
class Conviccao:
    grau: str
    porque: str


def cobertura(av: av_.Avaliacao) -> tuple[int, tuple[str, ...]]:
    """(dimensões com leitura, rótulos das que faltam)."""
    faltam = []
    if av.qualidade == av_.INSUFICIENTE:
        faltam.append("qualidade")
    if av.preco == av_.SEM_LEITURA:
        faltam.append("valuation")
    if av.mercado == av_.SEM_LEITURA:
        faltam.append("mercado")
    return 3 - len(faltam), tuple(faltam)


def _lista(xs) -> str:
    xs = list(xs)
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " e " + xs[-1]


def conviccao(favor, contra, av: av_.Avaliacao, teto: str | None = None,
              motivo_teto: str = "") -> Conviccao:
    """Grau de convicção da ação sugerida. Puro.

    * Baixa: no máximo uma dimensão lida, ou dois sinais contra, ou tantos
      contra quanto a favor.
    * Moderada: um sinal contra, ou uma dimensão sem leitura, ou um só
      sinal a favor.
    * Alta: dois ou mais sinais a favor, nenhum contra, três dimensões lidas.

    ``teto`` limita o grau (ex.: alerta só do noticiário, troca por um par
    que não passou pela avaliação completa).
    """
    favor, contra = list(favor), list(contra)
    n, faltam = cobertura(av)
    if n <= 1:
        grau = BAIXA
        porque = (f"só {n} de 3 dimensões com leitura (falta "
                  f"{_lista(faltam)}): a recomendação se apoia em pouco dado")
    elif len(contra) >= 2 or (contra and len(contra) >= len(favor)):
        grau = BAIXA
        porque = (f"sinais divididos — a favor: {_lista(favor) if favor else 'nenhum'}"
                  f"; contra: {_lista(contra)}")
    elif contra:
        grau = MODERADA
        porque = f"{_lista(favor)} sustentam, mas {contra[0]} pesa contra"
    elif len(favor) >= 2 and n == 3:
        grau = ALTA
        porque = (f"{_lista(favor)} apontam na mesma direção, sem sinal "
                  "contrário, com qualidade, valuation e mercado lidos")
    else:
        grau = MODERADA
        base = _lista(favor) if favor else "nenhum sinal contrário"
        falta = (f"falta a leitura de {_lista(faltam)}" if faltam
                 else "é um sinal só")
        porque = f"{base} sustenta, mas {falta}"
    if teto is not None and _ORDEM[grau] > _ORDEM[teto]:
        grau = teto
        porque = f"{porque}; limitada a {ROTULO_CONVICCAO[teto].lower()}: {motivo_teto}"
    return Conviccao(grau, porque[:1].upper() + porque[1:] + ".")
