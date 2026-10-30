"""Medição mensal do protocolo da Carteira 20 (seq 0, ``medicao``).

Puro: sem banco e sem rede. ``scripts/carteira20_medicao.py`` junta os insumos e
grava ``data/public/carteira20_medicao.json``; aqui só se faz a conta.

A curva diária (``core.carteira20_desempenho``) mostra o caminho da revisão
vigente. Esta é a medição que o protocolo congelou: mensal, das duas curvas,
atravessando as revisões, com imposto por preço médio e em reais do início.

Como cada regra do protocolo virou conta
----------------------------------------
- Duas curvas (``medicao.curvas``), cada uma com quatro carteiras que recebem o
  mesmo dinheiro nas mesmas datas (``medicao.comparacoes``): a Carteira 20, os
  pesos iguais do universo registrado, o BOVA11 e o CDI.

  * ``aportes``: R$ 1.000 no pregão de execução da inaugural e R$ 500 em cada
    fechamento de mês. Os proventos caem em caixa e entram no aporte seguinte
    (``operacao.proventos``);
  * ``teorica``: R$ 1.000 na execução da inaugural e nada mais. Os proventos
    voltam para a própria ação, em fração, como na curva diária.

- Fechamento: o último pregão de cada mês, a partir do mês seguinte ao da
  execução da inaugural -- "o primeiro mês fecha em 30/11/2026". O aporte de
  R$ 500 é feito no fechamento, depois da medição do mês; por isso não há aporte
  em 30/10/2026, que não é fechamento.
- Carteira 20: ações inteiras (``operacao.lote``), compradas no fechamento com o
  spread medido na execução de cada revisão e 0,30% de corretagem. Na inaugural,
  a compra de ``core.carteira20_desempenho.comprar_em_lotes``, a mesma da curva
  diária. No aporte, ``core.aporte.plano_de_aporte`` (``operacao.entre_revisoes``),
  sem venda; o troco que ele devolve compra, uma ação por vez, a mais abaixo do
  alvo -- a regra do troco da inaugural. O que não fecha uma ação fica em caixa,
  que não rende.
- Revisão (``operacao.na_revisao``): no fechamento do pregão de execução, vende
  o que saiu da lista e iguala os pesos das 20 em ações inteiras.
- Pesos iguais: frações e 0,30% por lado, no universo da revisão vigente; na
  revisão, troca para o universo novo e iguala. Os aportes vão para quem está
  abaixo do peso, como na Carteira 20.
- BOVA11: cotas inteiras com o spread do lote padrão e 0,30%; o troco fica em
  caixa e entra no aporte seguinte.
- IR das ações (``medicao.ir``): 15% sobre o ganho líquido realizado no mês, por
  preço médio, com o prejuízo compensado; isento no mês em que as vendas somam
  até R$ 20 mil, sem gastar o prejuízo acumulado. Pago no fechamento do mês,
  como no pequeno investidor da pesquisa (``codigo/pratico/simular.py``).
  Dividendos isentos; o JCP entra pelo valor da tabela ("já vem líquido").
- BOVA11 e CDI: 15% sobre o ganho, só no resgate.
- Cada fechamento traz o patrimônio (ações a mercado e caixa, depois do IR do
  mês) e o valor líquido de liquidação: quanto sobraria vendendo tudo naquele
  fechamento, com o spread de venda, a corretagem e o IR que a venda geraria.
- Real (``medicao.real``): o fator do IPCA desde o fechamento da execução da
  inaugural; o mês da execução entra pró-rata pelos dias corridos que faltavam.
  Mês com IPCA ainda não divulgado deixa o fator vazio, e a tela não mostra o
  valor real dele.

O rank-IC de cada revisão (``medicao.ic``) só existe seis meses depois de cada
execução e não está aqui.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date
from math import floor

import pandas as pd

from core import aporte as ap
from core import carteira20 as c20
from core.carteira20_desempenho import comprar_em_lotes

CAPITAL_INICIAL = 1000.0
APORTE_MENSAL = 500.0
CUSTO_LADO = 0.003
ALIQUOTA_IR = 0.15
ISENCAO_MENSAL_ACOES = 20_000.0

CURVAS = ("aportes", "teorica")
CARTEIRAS = ("carteira20", "pesos_iguais", "bova11", "cdi")
BOVA11 = "BOVA11"


@dataclass
class _Posicao:
    q: float = 0.0
    #: custo de aquisição total: preço, spread e corretagem (base do preço médio)
    custo: float = 0.0


@dataclass
class Carteira:
    """Posições, caixa e a apuração mensal do IR de uma das carteiras simuladas.

    ``reinveste``: provento volta para a própria ação, em fração (curva teórica);
    senão cai em caixa. ``isento_ate``: vendas no mês até esse valor não pagam
    IR (R$ 20 mil para ações; zero para o ETF, que não tem isenção).
    """

    reinveste: bool
    isento_ate: float = ISENCAO_MENSAL_ACOES
    pos: dict[str, _Posicao] = field(default_factory=dict)
    caixa: float = 0.0
    prejuizo: float = 0.0
    ir_pago: float = 0.0
    custos_pagos: float = 0.0
    proventos: float = 0.0
    _vendas_mes: float = 0.0
    _ganho_mes: float = 0.0

    def aplicar(self, i: str, preco: float, k: float, dinheiro: float) -> None:
        """Um pregão do ativo ``i``: ``k`` ações novas por antiga e ``dinheiro`` por antiga."""
        p = self.pos.get(i)
        if p is None or p.q <= 0:
            return
        antes = p.q
        p.q = antes * k
        credito = antes * dinheiro
        if credito:
            self.proventos += credito
            if self.reinveste and preco > 0:
                p.q += credito / preco
            else:
                self.caixa += credito

    def valor(self, precos: dict[str, float]) -> float:
        return sum(p.q * precos[i] for i, p in self.pos.items())

    def comprar(self, i: str, q: float, preco: float, spread: float = 0.0) -> None:
        if q <= 0:
            return
        total = q * preco * (1 + spread) * (1 + CUSTO_LADO)
        self.caixa -= total
        self.custos_pagos += total - q * preco
        p = self.pos.setdefault(i, _Posicao())
        p.q += q
        p.custo += total

    def comprar_valor(self, i: str, valor: float, preco: float) -> None:
        """Compra em frações: ``valor`` sai do caixa e 0,30% dele é a corretagem."""
        if valor <= 0:
            return
        self.caixa -= valor
        self.custos_pagos += valor * CUSTO_LADO
        p = self.pos.setdefault(i, _Posicao())
        p.q += valor * (1 - CUSTO_LADO) / preco
        p.custo += valor

    def vender(self, i: str, q: float, preco: float, spread: float = 0.0) -> None:
        p = self.pos[i]
        q = min(q, p.q)
        if q <= 0:
            return
        bruto = q * preco
        liquido = bruto * (1 - spread) * (1 - CUSTO_LADO)
        base = p.custo * q / p.q
        self.caixa += liquido
        self.custos_pagos += bruto - liquido
        self._vendas_mes += bruto
        self._ganho_mes += liquido - base
        p.q -= q
        p.custo -= base
        if p.q <= 1e-9:
            del self.pos[i]

    def _imposto(self, ganho: float, vendas: float, prejuizo: float) -> tuple[float, float]:
        """IR do mês e o prejuízo que sobra, pela regra de ``fechar_mes`` da pesquisa."""
        if ganho < 0:
            return 0.0, prejuizo - ganho
        if vendas <= self.isento_ate:
            return 0.0, prejuizo
        base = max(0.0, ganho - prejuizo)
        return ALIQUOTA_IR * base, max(0.0, prejuizo - ganho)

    def fechar_mes(self) -> None:
        imposto, self.prejuizo = self._imposto(self._ganho_mes, self._vendas_mes, self.prejuizo)
        self.caixa -= imposto
        self.ir_pago += imposto
        self._vendas_mes = self._ganho_mes = 0.0

    def liquidacao(self, precos: dict[str, float], spreads: dict[str, float]) -> float:
        """Caixa mais a venda de tudo agora, com custos e o IR que a venda geraria no mês."""
        bruto = liquido = base = 0.0
        for i, p in self.pos.items():
            v = p.q * precos[i]
            bruto += v
            liquido += v * (1 - spreads.get(i, 0.0)) * (1 - CUSTO_LADO)
            base += p.custo
        imposto, _ = self._imposto(self._ganho_mes + liquido - base,
                                   self._vendas_mes + bruto, self.prejuizo)
        return self.caixa + liquido - imposto


@dataclass
class _Cdi:
    valor: float = 0.0
    aportado: float = 0.0

    def liquidacao(self) -> float:
        return self.valor - ALIQUOTA_IR * max(0.0, self.valor - self.aportado)


# -- Calendário e inflação -----------------------------------------------------

def fechamentos(execucao: date, ate: date) -> list[date]:
    """Último pregão de cada mês, do mês seguinte ao da execução até ``ate``."""
    saida = []
    ano, mes = execucao.year, execucao.month
    while True:
        ano, mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
        dia = c20.ultimo_pregao_do_mes(ano, mes)
        if dia > ate:
            return saida
        saida.append(dia)


def fator_ipca(inicio: date, ate: date, ipca: dict[date, float]) -> float | None:
    """Inflação acumulada do fechamento de ``inicio`` ao fim do mês de ``ate``.

    ``ipca``: % no mês, pela data do dia 1 (como o SGS 433). O mês de ``inicio``
    entra pró-rata pelos dias corridos que faltavam para acabar; ``None`` quando
    algum mês ainda não foi divulgado.
    """
    fator = 1.0
    ano, mes = inicio.year, inicio.month
    while (ano, mes) <= (ate.year, ate.month):
        taxa = ipca.get(date(ano, mes, 1))
        if taxa is None:
            return None
        dias = calendar.monthrange(ano, mes)[1]
        fracao = (dias - inicio.day) / dias if (ano, mes) == (inicio.year, inicio.month) else 1.0
        fator *= (1 + taxa / 100.0) ** fracao
        ano, mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
    return fator


# -- Compras -------------------------------------------------------------------

def _efetivo(preco: float, spread: float) -> float:
    return preco * (1 + spread) * (1 + CUSTO_LADO)


def _completar(cart: Carteira, ids: list[str], precos: dict[str, float],
               spreads: dict[str, float]) -> None:
    """Gasta o caixa uma ação por vez na mais abaixo do alvo, como o troco da inaugural.

    Só compra se a ação couber no caixa e deixar a posição mais perto do alvo do
    que estava (``preço < 2 × falta``).
    """
    while True:
        alvo = (sum(cart.pos[i].q * precos[i] for i in ids if i in cart.pos) + cart.caixa) / len(ids)
        candidatos = []
        for i in ids:
            falta = alvo - (cart.pos[i].q * precos[i] if i in cart.pos else 0.0)
            ef = _efetivo(precos[i], spreads.get(i, 0.0))
            if ef <= cart.caixa + 1e-9 and precos[i] < 2 * falta:
                candidatos.append((-falta, i))
        if not candidatos:
            return
        _, i = min(candidatos)
        cart.comprar(i, 1, precos[i], spreads.get(i, 0.0))


def comprar_inaugural(cart: Carteira, ids: list[str], precos: dict[str, float],
                      spreads: dict[str, float]) -> None:
    efetivo = {i: _efetivo(precos[i], spreads.get(i, 0.0)) for i in ids}
    qtd, _ = comprar_em_lotes(efetivo, cart.caixa)
    for i in ids:
        cart.comprar(i, qtd[i], precos[i], spreads.get(i, 0.0))


def aportar_inteiras(cart: Carteira, ids: list[str], precos: dict[str, float],
                     spreads: dict[str, float]) -> None:
    """O caixa inteiro (aporte, troco e proventos) para as ações abaixo do peso, sem venda."""
    efetivo = {i: _efetivo(precos[i], spreads.get(i, 0.0)) for i in ids}
    plano = ap.plano_de_aporte({i: cart.pos[i].q * precos[i] for i in ids if i in cart.pos},
                               {i: 1.0 / len(ids) for i in ids}, cart.caixa,
                               precos=efetivo, lote=1)
    for a in plano.alocacoes:
        if a.cotas:
            cart.comprar(a.symbol, a.cotas, precos[a.symbol], spreads.get(a.symbol, 0.0))
    _completar(cart, ids, precos, spreads)


def aportar_fracoes(cart: Carteira, ids: list[str], precos: dict[str, float]) -> None:
    plano = ap.plano_de_aporte({i: cart.pos[i].q * precos[i] for i in ids if i in cart.pos},
                               {i: 1.0 / len(ids) for i in ids}, cart.caixa)
    for a in plano.alocacoes:
        cart.comprar_valor(a.symbol, min(a.valor_aportado, cart.caixa), precos[a.symbol])


def igualar_inteiras(cart: Carteira, ids: list[str], precos: dict[str, float],
                     compra: dict[str, float], venda: dict[str, float]) -> None:
    """Revisão: vende o que saiu, vende o excesso de quem passou do alvo e completa."""
    for i in [i for i in cart.pos if i not in ids]:
        cart.vender(i, cart.pos[i].q, precos[i], venda.get(i, 0.0))
    alvo = (cart.valor(precos) + cart.caixa) / len(ids)
    for i in ids:
        if i in cart.pos:
            sobra = floor((cart.pos[i].q * precos[i] - alvo) / precos[i])
            if sobra > 0:
                cart.vender(i, sobra, precos[i], venda.get(i, 0.0))
    _completar(cart, ids, precos, compra)


def igualar_fracoes(cart: Carteira, ids: list[str], precos: dict[str, float]) -> None:
    for i in [i for i in cart.pos if i not in ids]:
        cart.vender(i, cart.pos[i].q, precos[i])
    alvo = (cart.valor(precos) + cart.caixa) / len(ids)
    for i in ids:
        if i in cart.pos and cart.pos[i].q * precos[i] > alvo:
            cart.vender(i, (cart.pos[i].q * precos[i] - alvo) / precos[i], precos[i])
    faltas = {i: max(0.0, alvo - (cart.pos[i].q * precos[i] if i in cart.pos else 0.0))
              for i in ids}
    total = sum(faltas.values())
    escala = min(1.0, cart.caixa / total) if total > 0 else 0.0
    for i, f in faltas.items():
        cart.comprar_valor(i, f * escala, precos[i])


def comprar_cotas(cart: Carteira, preco: float, spread: float) -> None:
    cotas = int(cart.caixa // _efetivo(preco, spread))
    cart.comprar(BOVA11, cotas, preco, spread)


# -- A medição -----------------------------------------------------------------

@dataclass
class Revisao:
    seq: int
    execucao: date
    carteira: list[str]
    universo: list[str]
    #: ``(compra, venda)`` medidos na execução; ação sem medida entra com zero
    custos: dict[str, tuple[float, float]] = field(default_factory=dict)
    custo_bova11: tuple[float, float] | None = None

    def compra(self, ids) -> dict[str, float]:
        return {i: max(float(self.custos.get(i, (0.0, 0.0))[0]), 0.0) for i in ids}

    def venda(self, ids) -> dict[str, float]:
        return {i: max(float(self.custos.get(i, (0.0, 0.0))[1]), 0.0) for i in ids}

    def etf(self) -> tuple[float, float]:
        c = self.custo_bova11 or (0.0, 0.0)
        return max(float(c[0]), 0.0), max(float(c[1]), 0.0)


def _reais(v: float) -> float:
    return round(float(v), 2) + 0.0  # sem "-0.0" de resto de arredondamento


def _mes_seguinte(dia: date) -> date:
    ano, mes = (dia.year + 1, 1) if dia.month == 12 else (dia.year, dia.month + 1)
    return c20.ultimo_pregao_do_mes(ano, mes)


class _Medicao:
    """O estado das oito carteiras e o que cada evento faz com elas."""

    def __init__(self, inaugural: date, pregoes: pd.DataFrame, bova11: pd.Series,
                 cdi: dict[date, float], ipca: dict[date, float],
                 tickers: dict[str, str]) -> None:
        self.inaugural, self.cdi, self.ipca, self.tickers = inaugural, cdi, ipca, tickers
        self.ultima = pregoes.groupby("id")["data"].max().to_dict()
        self.bova = bova11
        self.precos: dict[str, float] = {}
        self.curvas = {nome: {"carteira20": Carteira(reinveste=nome == "teorica"),
                              "pesos_iguais": Carteira(reinveste=nome == "teorica"),
                              "bova11": Carteira(reinveste=False, isento_ate=0.0),
                              "cdi": _Cdi()} for nome in CURVAS}
        self.vigente: Revisao | None = None
        self.pesos_ok = True
        self.aportes: list[tuple[date, float]] = []
        self.meses: list[dict] = []
        self.avisos: list[str] = []

    def nome(self, ids) -> str:
        return ", ".join(self.tickers.get(i, i) for i in sorted(ids))

    def sem_pregao(self, ids, dia: date) -> list[str]:
        """Ações cuja fonte ainda não chegou a ``dia``: não se sabe se pararam."""
        return [i for i in ids if self.ultima.get(i, date.min) < dia]

    def preco_bova(self, dia: date) -> float | None:
        if not len(self.bova) or self.bova.index.max() < dia:
            return None
        anteriores = self.bova[self.bova.index <= dia]
        return float(anteriores.iloc[-1]) if len(anteriores) else None

    def pregao(self, linhas) -> None:
        for x in linhas:
            self.precos[x.id] = float(x.preco)
            for c in self.curvas.values():
                for k in ("carteira20", "pesos_iguais"):
                    c[k].aplicar(x.id, float(x.preco), float(x.k), float(x.caixa))

    def render_cdi(self, de: date, ate: date, calendario: list[date]) -> date | None:
        """Taxas de ``[de, ate)``: a de ``d`` remunera até o pregão seguinte, como na curva diária."""
        faltante = next((d for d in calendario if de <= d < ate and d not in self.cdi), None)
        if faltante is not None:
            return faltante
        fator = 1.0
        for d, v in self.cdi.items():
            if de <= d < ate:
                fator *= 1.0 + v / 100.0
        for c in self.curvas.values():
            c["cdi"].valor *= fator
        return None

    def p(self, ids) -> dict[str, float]:
        return {i: self.precos[i] for i in ids}

    def revisao(self, rv: Revisao, dia: date) -> bool:
        """Compra da inaugural ou troca da revisão; ``False`` quando a fonte não cobre o dia."""
        inaugural = self.vigente is None
        mantidas = set() if inaugural else {i for c in self.curvas.values()
                                            for i in c["carteira20"].pos}
        if inaugural:
            faltam = [i for i in rv.carteira if i not in self.precos]
        else:
            faltam = self.sem_pregao(set(rv.carteira) | mantidas, dia)
        etf = self.preco_bova(dia)
        if faltam or etf is None:
            self.avisos.append(f"revisão {rv.seq} ({dia:%d/%m/%Y}) sem "
                               + (f"o fechamento de {self.nome(faltam)}" if faltam
                                  else "o fechamento do BOVA11") + " na fonte")
            return False
        sem_custo = [i for i in rv.carteira if i not in rv.custos]
        if sem_custo:
            self.avisos.append(f"revisão {rv.seq}: sem spread medido de {self.nome(sem_custo)}; "
                               "entram só com a corretagem")
        sem_uni = [i for i in rv.universo if i not in self.precos
                   or (not inaugural and self.ultima.get(i, date.min) < dia)]
        if self.pesos_ok and (sem_uni or not rv.universo):
            self.avisos.append(f"pesos iguais sem medição a partir de {dia:%d/%m/%Y}: "
                               + (self.nome(sem_uni) or "universo vazio") + " sem fechamento")
            self.pesos_ok = False
        for c in self.curvas.values():
            if inaugural:
                for k in ("carteira20", "pesos_iguais", "bova11"):
                    c[k].caixa = CAPITAL_INICIAL
                c["cdi"].valor = c["cdi"].aportado = CAPITAL_INICIAL
                comprar_inaugural(c["carteira20"], rv.carteira, self.p(rv.carteira),
                                  rv.compra(rv.carteira))
                if self.pesos_ok:
                    por_acao = CAPITAL_INICIAL / len(rv.universo)
                    for i in rv.universo:
                        c["pesos_iguais"].comprar_valor(i, por_acao, self.precos[i])
                comprar_cotas(c["bova11"], etf, rv.etf()[0])
            else:
                # spread de venda: o da revisão nova quando ela mediu, senão o da anterior
                venda = {**self.vigente.venda(mantidas),
                         **rv.venda([i for i in mantidas if i in rv.custos])}
                ids = set(rv.carteira) | set(c["carteira20"].pos)
                igualar_inteiras(c["carteira20"], rv.carteira, self.p(ids),
                                 rv.compra(rv.carteira), venda)
                if self.pesos_ok:
                    ids = set(rv.universo) | set(c["pesos_iguais"].pos)
                    igualar_fracoes(c["pesos_iguais"], rv.universo, self.p(ids))
        if inaugural:
            self.aportes.append((dia, CAPITAL_INICIAL))
        self.vigente = rv
        return True

    def fechamento(self, dia: date) -> bool:
        """Mede, apura o IR do mês e faz o aporte; ``False`` quando a fonte não cobre o dia."""
        rv = self.vigente
        faltam = self.sem_pregao(set(rv.carteira) | set(self.curvas["aportes"]["carteira20"].pos),
                                 dia)
        etf = self.preco_bova(dia)
        if faltam or etf is None:
            self.avisos.append(f"fechamento de {dia:%d/%m/%Y} ainda sem "
                               + (f"o pregão de {self.nome(faltam)}" if faltam
                                  else "o fechamento do BOVA11") + " na fonte")
            return False
        atrasadas = self.sem_pregao(rv.universo, dia)
        if self.pesos_ok and atrasadas:
            self.avisos.append(f"pesos iguais sem medição a partir de {dia:%d/%m/%Y}: "
                               f"{self.nome(atrasadas)} sem pregão na fonte")
            self.pesos_ok = False
        mes = {"fechamento": dia.isoformat(), **self._inflacao(dia)}
        liquidos = {nome: self._liquidos(c, etf) for nome, c in self.curvas.items()}
        for c in self.curvas.values():
            for k in ("carteira20", "pesos_iguais", "bova11"):
                c[k].fechar_mes()
        for nome, c in self.curvas.items():
            aportado = (sum(v for _, v in self.aportes) if nome == "aportes"
                        else CAPITAL_INICIAL)
            depois = self._patrimonios(c, etf)
            mes[nome] = {"aportado": _reais(aportado),
                         **{k: None if v is None else {**v, **depois[k]}
                            for k, v in liquidos[nome].items()}}
        mes["aportes"]["aportado_real"] = self._aportado_real(dia)
        mes["teorica"]["aportado_real"] = (None if mes["ipca_fator"] is None
                                           else _reais(CAPITAL_INICIAL))
        self.meses.append(mes)

        c = self.curvas["aportes"]
        for k in ("carteira20", "pesos_iguais", "bova11"):
            c[k].caixa += APORTE_MENSAL
        c["cdi"].valor += APORTE_MENSAL
        c["cdi"].aportado += APORTE_MENSAL
        aportar_inteiras(c["carteira20"], rv.carteira, self.p(rv.carteira), rv.compra(rv.carteira))
        if self.pesos_ok:
            aportar_fracoes(c["pesos_iguais"], rv.universo, self.p(rv.universo))
        comprar_cotas(c["bova11"], etf, rv.etf()[0])
        self.aportes.append((dia, APORTE_MENSAL))
        return True

    def _inflacao(self, dia: date) -> dict:
        fator = fator_ipca(self.inaugural, dia, self.ipca)
        return {"ipca_fator": None if fator is None else round(fator, 8)}

    def _aportado_real(self, dia: date) -> float | None:
        """Os aportes feitos até ``dia`` em reais da execução da inaugural."""
        if fator_ipca(self.inaugural, dia, self.ipca) is None:
            return None
        total = 0.0
        for d, v in self.aportes:
            f = 1.0 if d == self.inaugural else fator_ipca(self.inaugural, d, self.ipca)
            total += v / f
        return _reais(total)

    def _liquidos(self, c: dict, etf: float) -> dict:
        """Valor líquido de liquidação de cada carteira, antes do IR do mês e do aporte."""
        rv = self.vigente
        c20_ = c["carteira20"]
        saida = {"carteira20": {"liquido": _reais(c20_.liquidacao(self.p(c20_.pos),
                                                                  rv.venda(c20_.pos)))},
                 "pesos_iguais": None,
                 "bova11": {"liquido": _reais(c["bova11"].liquidacao({BOVA11: etf},
                                                                     {BOVA11: rv.etf()[1]}))},
                 "cdi": {"liquido": _reais(c["cdi"].liquidacao())}}
        if self.pesos_ok:
            pi = c["pesos_iguais"]
            saida["pesos_iguais"] = {"liquido": _reais(pi.liquidacao(self.p(pi.pos), {}))}
        return saida

    def _patrimonios(self, c: dict, etf: float) -> dict:
        """Depois do IR do mês: patrimônio, caixa, IR pago, custos e proventos acumulados."""
        saida = {}
        for k in ("carteira20", "pesos_iguais", "bova11"):
            cart: Carteira = c[k]
            p = {BOVA11: etf} if k == "bova11" else self.p(cart.pos)
            saida[k] = {"patrimonio": _reais(cart.valor(p) + cart.caixa),
                        "caixa": _reais(cart.caixa), "ir_pago": _reais(cart.ir_pago),
                        "custos": _reais(cart.custos_pagos), "proventos": _reais(cart.proventos),
                        "prejuizo_a_compensar": _reais(cart.prejuizo)}
        saida["cdi"] = {"patrimonio": _reais(c["cdi"].valor)}
        return saida


def calcular(revisoes: list[Revisao], pregoes: pd.DataFrame, bova11: pd.Series,
             cdi: dict[date, float], ipca: dict[date, float], *, ate: date,
             tickers: dict[str, str] | None = None, extras: tuple[date, ...] = ()) -> dict:
    """Os fechamentos mensais medidos até ``ate``.

    ``extras``: pregões tratados como fechamento, só para ensaiar a conta com
    dados que já existem (``scripts/carteira20_medicao.py --seco --ensaio-ate``).

    ``pregoes``: colunas ``id``, ``data``, ``preco``, ``k`` e ``caixa`` (ver
    ``core.carteira20_retornos.calcular(componentes=True)``), com linhas antes da
    execução da inaugural para o preço de quem não negociou nela.
    ``bova11``: fechamento por data. ``cdi``: % ao dia útil por data (SGS 12).
    ``ipca``: % no mês pela data do dia 1 (SGS 433).

    Um evento (revisão ou fechamento) só entra quando a fonte cobre o dia para
    todas as ações da Carteira 20 -- a última linha de cada uma é dele ou
    posterior; antes disso não se sabe se a ação parou --, para o BOVA11 e para
    o CDI de todos os pregões até ele. O primeiro que não é coberto encerra a
    conta, com aviso; a rodada seguinte recalcula tudo desde a inaugural. Ação
    do universo sem cobertura deixa só os pesos iguais vazios dali em diante.
    """
    tickers = tickers or {}
    if not revisoes:
        return {"meses": [], "avisos": ["nenhuma revisão registrada"], "dados_ate": None,
                "posicoes_aportes": [], "proximo_fechamento": None}
    inaugural = revisoes[0].execucao
    r = pregoes.copy()
    r["data"] = pd.to_datetime(r["data"]).dt.date
    r = r.sort_values(["data", "id"])
    bova = bova11.copy()
    bova.index = pd.to_datetime(bova.index).date
    m = _Medicao(inaugural, r, bova.sort_index(), cdi, ipca, tickers)
    m.pregao(r[r["data"] <= inaugural].itertuples(index=False))

    eventos: dict[date, list] = {}
    for rv in revisoes:
        if rv.execucao <= ate:
            eventos.setdefault(rv.execucao, []).append(rv)
    for f in sorted(set(fechamentos(inaugural, ate)) | {e for e in extras if inaugural < e <= ate}):
        eventos.setdefault(f, []).append(f)
    dias = sorted(({d for d in r["data"] if inaugural < d <= ate} | set(eventos)) - {inaugural})
    por_dia = {d: g for d, g in r[r["data"] > inaugural].groupby("data")}

    feito = None
    anterior = inaugural
    for dia in [inaugural, *dias]:
        if dia > inaugural:
            faltante = m.render_cdi(anterior, dia, [anterior, *dias])
            if faltante is not None:
                m.avisos.append(f"CDI sem a taxa de {faltante:%d/%m/%Y}; a medição para aí")
                break
            if dia in por_dia:
                m.pregao(por_dia[dia].itertuples(index=False))
            anterior = dia
        ok = True
        # no mesmo dia, a revisão vem antes do fechamento: o aporte já vai para a lista nova
        for ev in sorted(eventos.get(dia, []), key=lambda e: not isinstance(e, Revisao)):
            ok = m.revisao(ev, dia) if isinstance(ev, Revisao) else m.fechamento(dia)
            if not ok:
                break
        if not ok:
            break
        feito = dia

    ultimo = date.fromisoformat(m.meses[-1]["fechamento"]) if m.meses else inaugural
    posicoes = []
    if m.meses:
        c = m.curvas["aportes"]["carteira20"]
        posicoes = [{"id": i, "ticker": tickers.get(i, i), "quantidade": round(p.q, 6),
                     "valor": _reais(p.q * m.precos[i])}
                    for i, p in sorted(c.pos.items(), key=lambda kv: tickers.get(kv[0], kv[0]))]
    return {"meses": m.meses, "avisos": m.avisos,
            "dados_ate": None if feito is None else feito.isoformat(),
            "posicoes_aportes": posicoes, "proximo_fechamento": _mes_seguinte(ultimo).isoformat()}
