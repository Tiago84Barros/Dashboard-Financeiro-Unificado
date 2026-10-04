"""
core/backtest_liquido.py
Premissas e contas do retorno LÍQUIDO dos backtests (auditoria app4, item 19:
B3-08, FII-10, EUA-J).

Para pessoa física brasileira o retorno que importa é o que sobra em reais
depois de custo, imposto e câmbio. Os três motores mostravam o bruto: o FII
girava 263% ao ano sem IR sobre o ganho, os EUA saíam em dólar sem retenção
nem IOF, e as safras da B3 não pagavam custo nenhum entre um abril e outro.

Tudo o que muda o número está numa constante comentada aqui, e as telas
mostram estas mesmas constantes (``premissas_*``) ao lado do resultado. Uma
alíquota que só existe dentro do cálculo é uma alíquota que ninguém contesta.

Alíquotas brasileiras vêm de ``core.ir_renda_variavel`` (PR #331), a mesma
fonte da apuração real da carteira. O ``apurar`` de lá não é reaproveitado:
ele trabalha com notas de corretagem (quantidade, preço, data), e o backtest
trabalha com pesos sem escala -- não há "R$ 20 mil vendidos no mês" num
portfólio de peso 1,0. O que se reaproveita é a regra, não o motor.

Puro: sem banco, sem rede.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from core.ir_renda_variavel import ALIQUOTA, LIMITE_ISENCAO_ACOES

# ── FII ──────────────────────────────────────────────────────────────────────
# Rendimento distribuído por FII é isento para PF (Lei 11.033/2004, art. 3º,
# III); o ganho de capital na venda da cota paga 20% sem isenção mensal, e o
# prejuízo só compensa ganho de FII. O backtest já descontava custo de giro
# (15 bps de transação + 10 bps de slippage por unidade de turnover, padrão de
# ``robust_optimizer_point_in_time_backtest``); faltava o imposto.
IR_FII_GANHO = float(ALIQUOTA["fii"])

# ── Ações B3 ─────────────────────────────────────────────────────────────────
# 15% sobre o ganho em operação comum. A isenção de vendas até R$ 20 mil/mês
# NÃO é aplicada no backtest: ela depende do valor da carteira, que o backtest
# não tem (pesos), e o rebalanceamento anual de abril vende de uma vez a
# parcela que sai -- com R$ 100 mil e giro de 50%, são R$ 50 mil vendidos num
# mês, fora da isenção. Para carteira pequena o imposto aqui é conservador.
IR_ACOES_GANHO = float(ALIQUOTA["comum"])
ISENCAO_VENDAS_ACOES_MES = float(LIMITE_ISENCAO_ACOES)

# Emolumentos B3 de pessoa física por ponta: negociação 0,005% + liquidação
# 0,025% (tabela B3 de ações à vista). Corretagem zero nas corretoras de
# varejo. Meia-spread por ponta vem de ``core.transaction_costs``
# (SPREAD_BPS_LARGE_CAP_DEF / 2 = 5 bps).
EMOLUMENTOS_B3_BPS_POR_PONTA = 3.0

# ── EUA ──────────────────────────────────────────────────────────────────────
# Retenção na fonte de 30% sobre dividendo pago a não residente sem tratado
# (Brasil e EUA não têm acordo para evitar bitributação). Sob a Lei
# 14.754/2023 o dividendo ainda é tributado a 15% no Brasil, com crédito do
# imposto pago lá até esse limite -- então o custo efetivo é os 30%.
RETENCAO_DIVIDENDOS_EUA = 0.30

# O painel anual dos EUA é montado com preço ajustado (retorno total) e não
# separa dividendo de variação de preço. Sem a separação, a retenção incide
# sobre um dividend yield declarado: 1,5% ao ano, a ordem de grandeza do
# S&P 500 entre 2010 e 2025 (1,3% a 2,2%). Quando o painel trouxer a coluna
# ``fwd_price_return``, o yield de cada ação substitui esta premissa.
DIVIDEND_YIELD_EUA_PREMISSA = 0.015

# Lei 14.754/2023: ganho de capital em aplicação financeira no exterior de PF
# paga 15% na declaração anual, sem a isenção de R$ 35 mil/mês que valia até
# 2023. Aplicamos a regra atual a todo o histórico -- conservador para os
# anos antes de 2024.
IR_EXTERIOR_GANHO = 0.15

# IOF-câmbio de PF: 1,1% na remessa para investimento no exterior e 0,38% no
# ingresso de volta (Decreto 6.306/2007 com a redação do Decreto
# 12.499/2025). Cobrado uma vez na entrada e uma na saída: o backtest não
# tem aportes, então não há remessas intermediárias.
IOF_REMESSA_EXTERIOR = 0.011
IOF_INGRESSO_EXTERIOR = 0.0038

# Custo de giro nos EUA por unidade de turnover (0,5·Σ|Δw| = vender x e
# comprar x): meia-spread de 5 bps em cada ponta para large cap (mesma régua
# de ``core.transaction_costs``) = 10 bps, mais 10 bps de slippage. Corretagem
# zero nas plataformas que a pessoa física usa (Avenue, Nomad, Inter).
CUSTO_TRANSACAO_EUA_BPS = 10.0
SLIPPAGE_EUA_BPS = 10.0


def premissas_fii(transaction_cost: float, slippage: float) -> dict[str, Any]:
    return {
        "custo_transacao_bps": round(float(transaction_cost) * 1e4, 4),
        "slippage_bps": round(float(slippage) * 1e4, 4),
        "ir_ganho_capital": IR_FII_GANHO,
        "rendimento_isento": True,
        "compensa_prejuizo": True,
        "ir_na_liquidacao_final": False,
    }


def premissas_eua() -> dict[str, Any]:
    return {
        "custo_transacao_bps": CUSTO_TRANSACAO_EUA_BPS,
        "slippage_bps": SLIPPAGE_EUA_BPS,
        "retencao_dividendos": RETENCAO_DIVIDENDOS_EUA,
        "dividend_yield_premissa": DIVIDEND_YIELD_EUA_PREMISSA,
        "ir_ganho_capital": IR_EXTERIOR_GANHO,
        "iof_remessa": IOF_REMESSA_EXTERIOR,
        "iof_ingresso": IOF_INGRESSO_EXTERIOR,
        "compensa_prejuizo": True,
        "ir_na_liquidacao_final": False,
    }


def _finito(valor: Any) -> float | None:
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    return numero if math.isfinite(numero) else None


class RastreadorIR:
    """Custo de aquisição e IR realizado de uma carteira de pesos, sem escala.

    O backtest só conhece pesos e retornos. Para saber quanto de ganho uma
    venda realiza é preciso lembrar o custo de cada posição; aqui ele é
    guardado como fração do valor da carteira, renormalizado a cada
    rebalanceamento -- o que torna a conta independente do patrimônio.

    Uso, por período::

        ir = rastreador.rebalancear(pesos_alvo)    # no início do período
        rastreador.evoluir(retorno_total, renda)   # ao fim do período

    ``renda`` é a parcela do retorno total que veio de provento reinvestido;
    ela entra no custo (comprar cota com o dividendo é aquisição nova) e por
    isso não é tributada de novo na venda. Para FII, renda = retorno total −
    retorno de preço.

    Duas simplificações declaradas:
      * o IR é pago por fora do rebalanceamento (não se vende mais para
        pagá-lo, o que realizaria um pouco mais de ganho -- efeito de segunda
        ordem);
      * não há IR na liquidação final, porque o benchmark de comparação
        também não paga: é a comparação de quem continua investido.
    """

    def __init__(self, aliquota: float):
        self.aliquota = float(aliquota)
        self.valor: dict[str, float] = {}
        self.custo: dict[str, float] = {}
        self.prejuizo = 0.0

    def evoluir(self, retorno_total: Mapping[str, Any],
                renda: Mapping[str, Any] | None = None) -> None:
        """Aplica o retorno do período a cada posição aberta.

        Retorno ausente vale zero, a mesma convenção do bruto (a posição fica
        parada), para o imposto não inventar ganho onde o retorno não existe.
        """
        renda = renda or {}
        for ticker in list(self.valor):
            atual = self.valor[ticker]
            tr = _finito(retorno_total.get(ticker)) or 0.0
            parcela_renda = _finito(renda.get(ticker)) or 0.0
            self.valor[ticker] = atual * (1.0 + tr)
            self.custo[ticker] = self.custo.get(ticker, 0.0) + atual * parcela_renda

    def rebalancear(self, alvo: Mapping[str, float]) -> float:
        """Leva a carteira aos pesos ``alvo`` e devolve o IR como fração dela."""
        total = sum(max(v, 0.0) for v in self.valor.values())
        if total > 0:
            # Renormaliza tudo pelo valor de hoje: o prejuízo acumulado é um
            # valor em reais, e como fração encolhe quando a carteira cresce.
            self.valor = {t: v / total for t, v in self.valor.items()}
            self.custo = {t: c / total for t, c in self.custo.items()}
            self.prejuizo /= total
        soma_alvo = sum(float(w) for w in alvo.values() if _finito(w))
        alvo_norm = {
            str(t): float(w) / soma_alvo for t, w in alvo.items()
            if _finito(w) and soma_alvo > 0
        }
        ganho = 0.0
        for ticker in set(self.valor) | set(alvo_norm):
            atual = max(self.valor.get(ticker, 0.0), 0.0)
            custo = self.custo.get(ticker, 0.0)
            destino = alvo_norm.get(ticker, 0.0)
            if atual > destino and atual > 0:
                vendido = atual - destino
                fracao = vendido / atual
                ganho += vendido - custo * fracao
                custo *= 1.0 - fracao
            elif destino > atual:
                custo += destino - atual
            if destino > 0:
                self.valor[ticker] = destino
                self.custo[ticker] = custo
            else:
                self.valor.pop(ticker, None)
                self.custo.pop(ticker, None)
        base = ganho - self.prejuizo
        if base > 0:
            self.prejuizo = 0.0
            ir = self.aliquota * base
        else:
            self.prejuizo = -base
            ir = 0.0
        return ir


# ── EUA: walk-forward anual convertido para reais ────────────────────────────

def _cambio_no_mes(fx: Any, data: Any) -> float | None:
    """Câmbio do fim do mês de ``data`` (índice mensal em fim de mês)."""
    import pandas as pd

    mes = pd.Timestamp(data).normalize() + pd.offsets.MonthEnd(0)
    try:
        valor = fx.get(mes)
    except Exception:  # noqa: BLE001 - série sem o mês = ausência
        valor = None
    numero = _finito(valor)
    return numero if numero is not None and numero > 0 else None


def liquido_eua_brl(periodos: list[dict], fx: Any, *,
                    periodos_por_ano: int = 1) -> dict[str, Any]:
    """Retorno líquido em reais do walk-forward anual dos EUA (EUA-J).

    ``periodos``: um dict por data de rebalanceamento, na ordem, com
    ``date``, ``pesos`` {símbolo: peso}, ``turnover``, ``fwd`` {símbolo:
    retorno total em USD}, ``fwd_preco`` {símbolo: retorno de preço} (pode vir
    vazio) e ``ew_usd`` (retorno do equal-weight em USD). ``fx`` é a série
    mensal de USDBRL indexada no fim do mês.

    Ordem das contas, por período (todas em fração da carteira):

    1. custo de giro em USD: turnover × (10 + 10 bps);
    2. retenção de 30% sobre a parcela de dividendo de cada ação -- a
       premissa de 1,5% a.a. quando o painel não separa preço de provento;
    3. conversão: (1 + r_usd) × câmbio_fim / câmbio_início − 1;
    4. IR de 15% sobre o ganho realizado EM REAIS no rebalanceamento (Lei
       14.754: custo e venda convertidos na data de cada um, então a variação
       cambial é ganho tributável); o dividendo líquido reinvestido entra no
       custo, porque já foi tributado na fonte;
    5. IOF de 1,1% na remessa (primeiro período) e de 0,38% no ingresso
       (último).

    O IOF de volta é cobrado e o IR final não, de propósito: o IOF é custo
    certo da ida e volta, conhecido no dia da remessa; o IR sobre o ganho
    ainda não realizado depende de quando a pessoa vende, e o equal-weight de
    comparação também não o paga.

    O equal-weight de referência sai em reais, BRUTO de custo e imposto: é o
    universo, não um investidor -- a mesma escolha do IFIX no FII.

    Falta de câmbio em qualquer data devolve ``ok=False`` com os meses
    nomeados: pular o período quebraria o custo de aquisição, e zerar o
    câmbio inventaria um retorno.
    """
    import numpy as np
    import pandas as pd

    from core.us_backtest import bootstrap_mean_ci, performance_stats

    if not periodos:
        return {"ok": False, "motivo": "sem períodos"}
    horizonte_meses = 12 // max(int(periodos_por_ano), 1)
    if fx is None or len(fx) == 0:
        return {"ok": False, "motivo": "sem série de USDBRL"}
    faltando: list[str] = []
    variacoes: list[float] = []
    for periodo in periodos:
        inicio = pd.Timestamp(periodo["date"])
        fim = inicio + pd.DateOffset(months=int(horizonte_meses))
        c0, c1 = _cambio_no_mes(fx, inicio), _cambio_no_mes(fx, fim)
        if c0 is None:
            faltando.append(f"{inicio:%Y-%m}")
        if c1 is None:
            faltando.append(f"{fim:%Y-%m}")
        variacoes.append(c1 / c0 if c0 and c1 else float("nan"))
    if faltando:
        return {"ok": False,
                "motivo": "USDBRL ausente em " + ", ".join(sorted(set(faltando)))}

    custo_unitario = (CUSTO_TRANSACAO_EUA_BPS + SLIPPAGE_EUA_BPS) / 10_000.0
    rastreador = RastreadorIR(IR_EXTERIOR_GANHO)
    bruto_brl, liquido, ew_brl = [], [], []
    custos, retencoes, irs, iofs = [], [], [], []
    yield_observado = 0
    ultimo = len(periodos) - 1
    for i, (periodo, variacao) in enumerate(zip(periodos, variacoes)):
        pesos = {str(s): float(w) for s, w in periodo["pesos"].items()}
        fwd = periodo.get("fwd") or {}
        fwd_preco = periodo.get("fwd_preco") or {}
        ir = rastreador.rebalancear(pesos)
        total_brl, renda_brl = {}, {}
        r_usd_bruto = retencao = 0.0
        for simbolo, peso in pesos.items():
            r = _finito(fwd.get(simbolo)) or 0.0
            preco = _finito(fwd_preco.get(simbolo))
            if preco is not None:
                dividendo = max(r - preco, 0.0)
                yield_observado += 1
            else:
                dividendo = DIVIDEND_YIELD_EUA_PREMISSA
            retido = RETENCAO_DIVIDENDOS_EUA * dividendo
            r_usd_bruto += peso * r
            retencao += peso * retido
            total_brl[simbolo] = (1.0 + r - retido) * variacao - 1.0
            renda_brl[simbolo] = (dividendo - retido) * variacao
        rastreador.evoluir(total_brl, renda_brl)
        custo = float(periodo.get("turnover") or 0.0) * custo_unitario
        fator = (1.0 + r_usd_bruto - retencao - custo) * variacao
        iof = 0.0
        if i == 0:
            fator *= 1.0 - IOF_REMESSA_EXTERIOR
            iof += IOF_REMESSA_EXTERIOR
        if i == ultimo:
            fator *= 1.0 - IOF_INGRESSO_EXTERIOR
            iof += IOF_INGRESSO_EXTERIOR
        bruto_brl.append((1.0 + r_usd_bruto) * variacao - 1.0)
        liquido.append(fator - 1.0 - ir)
        ew_brl.append((1.0 + float(periodo["ew_usd"])) * variacao - 1.0)
        custos.append(custo)
        retencoes.append(retencao)
        irs.append(ir)
        iofs.append(iof)

    datas = [periodo["date"] for periodo in periodos]
    bruto_s = pd.Series(bruto_brl, index=datas)
    liquido_s = pd.Series(liquido, index=datas)
    ew_s = pd.Series(ew_brl, index=datas)
    stats_bruto = performance_stats(bruto_s, periodos_por_ano)
    stats_liquido = performance_stats(liquido_s, periodos_por_ano)
    stats_ew = performance_stats(ew_s, periodos_por_ano)

    def _excesso(stats: dict) -> float | None:
        if stats["ann_return"] is None or stats_ew["ann_return"] is None:
            return None
        return float(stats["ann_return"] - stats_ew["ann_return"])

    premissas = premissas_eua()
    premissas["dividendo"] = ("observado no painel" if yield_observado
                              else "premissa de yield")
    return {
        "ok": True,
        "moeda": "BRL",
        "premissas": premissas,
        "portfolio_brl_bruto": stats_bruto,
        "portfolio_brl_liquido": stats_liquido,
        "equal_weight_brl": stats_ew,
        "excess_ann_vs_ew_brl_bruto": _excesso(stats_bruto),
        "excess_ann_vs_ew_liquido": _excesso(stats_liquido),
        "bootstrap_excess_liquido": bootstrap_mean_ci(liquido_s - ew_s),
        "custo_medio": float(np.mean(custos)),
        "retencao_media": float(np.mean(retencoes)),
        "ir_medio": float(np.mean(irs)),
        "iof_total": float(sum(iofs)),
        "variacao_cambial_media": float(np.mean(variacoes)) - 1.0,
        "equity_curve_liquido": list((1 + liquido_s).cumprod().values),
        "dates": [str(d) for d in datas],
    }
