"""
core/ir_renda_variavel.py
Apuração mensal do imposto de renda sobre ganho de capital em renda variável
(B3), com DARF, compensação de prejuízo e cobertura declarada.

Regra aplicada (Lei 11.033/2004, IN RFB 1.585/2015 — a MP 1.303/2025, que
unificava as alíquotas, perdeu a vigência sem ser votada):

  operações comuns (ações, units, ETF, BDR)   15% sobre o ganho líquido
  day trade (mesmo ativo comprado e vendido    20%
      no mesmo dia, fora FII)
  FII (comum e day trade)                      20%

  - Isenção: ganho em AÇÕES (inclui units) no mês em que o total vendido de
    ações fica em até R$ 20.000. Não vale para ETF, BDR, FII, day trade nem
    direito de subscrição (a Receita fechou essa leitura em 2021).
  - Direito de subscrição (GMAT1, HGLG12) recebido da empresa tem custo zero:
    a venda sem compra no extrato é ganho integral, não custo desconhecido.
    Direitos vivem semanas, então não há direito "anterior ao extrato".
    O prejuízo em ações num mês isento continua compensável.
  - Prejuízo compensa só dentro da mesma cesta (comum, day trade, FII), sem
    prazo.
  - IRRF ("dedo-duro"): 0,005% sobre a venda em operação comum e FII, 1% sobre
    o ganho do day trade; deduz do imposto do mês. O extrato da B3 não traz
    as notas de corretagem, então aqui ele é ESTIMADO. Cada ficha do
    programa guarda o seu saldo (ver `apurar`), que passa para os meses
    seguintes do mesmo ano; o que sobra em dezembro vai para a declaração.
  - DARF código 6015, vencimento no último dia útil do mês seguinte. Abaixo
    de R$ 10 não se paga: o valor acumula para o próximo DARF.

Custo de aquisição: preço médio ponderado por ativo, com a corretagem da
compra somada ao custo e a da venda descontada do ganho. PETR4 e PETR4F são
o mesmo ativo. Desdobro, grupamento, bonificação, fração e subscrição da
Movimentação entram pelas MESMAS regras do preço médio da carteira
(`data_pipeline/importers/investments/positions.py`), para que o custo do IR
e o PM exibido não divirjam.

Day trade: o fisco só o reconhece quando compra e venda passam pela MESMA
corretora. O extrato da B3 consolida as corretoras, então compra numa e venda
na outra no mesmo dia sai aqui como day trade. É raro, mas a tela avisa.

Cobertura: o extrato de Negociação da B3 começa em nov/2019. Venda de ativo
comprado antes disso chega sem custo, e o ganho dela não é conhecido. Essa
parte NÃO é tratada como custo zero nem como ganho zero: a cesta do mês fica
marcada como incompleta, e o prejuízo que ela carrega para a frente também.
O imposto exibido nesses meses cobre só a parte com custo conhecido.

Tudo aqui é puro: sem banco, sem rede. O carregamento fica em
``carregar_operacoes``.
"""
from __future__ import annotations

import calendar
import json
import logging
import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

LIMITE_ISENCAO_ACOES = Decimal("20000")
DARF_MINIMO = Decimal("10")
CODIGO_DARF = "6015"

ALIQUOTA = {"comum": Decimal("0.15"), "day_trade": Decimal("0.20"), "fii": Decimal("0.20")}
IRRF_VENDA = Decimal("0.00005")      # 0,005% sobre a venda (comum e FII)
IRRF_DAY_TRADE = Decimal("0.01")     # 1% sobre o ganho do day trade
IRRF_MINIMO = Decimal("1")           # a corretora dispensa retenção abaixo de R$ 1

CESTAS = ("comum", "day_trade", "fii")
ROTULO_CESTA = {"comum": "Operações comuns", "day_trade": "Day trade", "fii": "FII"}

_ZERO = Decimal("0")
_EPS = Decimal("0.0001")

CAMINHO_CNPJ = Path(__file__).resolve().parents[1] / "data" / "public" / "cnpj_emissores.json"


# ─────────────────────────────────────────────────────────────────────────────
# Classificação
# ─────────────────────────────────────────────────────────────────────────────

def _base(ticker: str) -> str:
    t = (ticker or "").strip().upper()
    return t[:-1] if t.endswith("F") and len(t) > 4 else t


_DIREITO_ACAO = re.compile(r"^[A-Z]{4}[12]$")
# Código de negociação da B3 (PETR4, TAEE11, AAPL34, FAMB11B). O que não tem
# esse formato ("CDB", código de cota de fundo fechado) não é renda variável:
# renda fixa tem IR retido na fonte e não entra na apuração mensal.
_TICKER_B3 = re.compile(r"^[A-Z0-9]{4}\d{1,2}[A-Z]?$")


def e_direito(ticker: str, tipo: str | None) -> bool:
    """Direito de subscrição: sufixo 1/2 numa ação, 12 num FII (XXXX11 → XXXX12)."""
    from core.fii_ticker import e_ticker_fii

    t = _base(ticker)
    return tipo == "direito" or (
        tipo == "fii" and t.endswith("12") and e_ticker_fii(t[:-2] + "11"))


def tipo_fiscal(ticker: str, classe: str | None) -> str | None:
    """'acao', 'direito', 'etf', 'bdr', 'fii' ou None (fora do escopo).

    O BDR é reconhecido pelo sufixo 32-35/39 do ticker, porque o cadastro de
    ativos o guarda como 'stock'. Units (TAEE11, KLBN11) cadastradas como
    'stock' são ações para o fisco e entram na isenção.
    """
    c = (classe or "").strip().lower()
    t = _base(ticker)
    if not _TICKER_B3.match(t):
        return None
    if c == "reit":
        return "fii"
    if c == "etf":
        return "etf"
    if c == "stock":
        if len(t) >= 6 and t[-2:] in {"32", "33", "34", "35", "39"}:
            return "bdr"
        if _DIREITO_ACAO.match(t):
            return "direito"
        return "acao"
    return None


def _cesta_comum(tipo: str) -> str:
    return "fii" if tipo == "fii" else "comum"


# ─────────────────────────────────────────────────────────────────────────────
# Calendário do DARF
# ─────────────────────────────────────────────────────────────────────────────

def _pascoa(ano: int) -> date:
    a, b, c = ano % 19, ano // 100, ano % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    mes = (h + l_ - 7 * m + 114) // 31
    dia = (h + l_ - 7 * m + 114) % 31 + 1
    return date(ano, mes, dia)


def feriados_bancarios(ano: int) -> set[date]:
    """Feriados nacionais em que os bancos não abrem (DARF não vence neles)."""
    p = _pascoa(ano)
    fixos = {(1, 1), (4, 21), (5, 1), (9, 7), (10, 12), (11, 2), (11, 15), (12, 25)}
    if ano >= 2024:
        fixos.add((11, 20))   # Consciência Negra, feriado nacional desde 2024
    moveis = {p - timedelta(days=48), p - timedelta(days=47),   # carnaval
              p - timedelta(days=2),                            # sexta santa
              p + timedelta(days=60)}                           # corpus christi
    return {date(ano, m, d) for m, d in fixos} | moveis


def vencimento_darf(ano: int, mes: int) -> date:
    """Último dia útil do mês seguinte ao da apuração."""
    ano_v, mes_v = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
    d = date(ano_v, mes_v, calendar.monthrange(ano_v, mes_v)[1])
    feriados = feriados_bancarios(ano_v)
    while d.weekday() >= 5 or d in feriados:
        d -= timedelta(days=1)
    return d


# ─────────────────────────────────────────────────────────────────────────────
# Operações realizadas
# ─────────────────────────────────────────────────────────────────────────────

def _dec(v: Any) -> Decimal:
    return Decimal(str(v if v is not None else 0))


def _mes(d: Any) -> str:
    return str(d)[:7]


def _realizacoes(transacoes: list[dict], eventos: list[dict] | None,
                 ) -> tuple[list[dict], list[dict]]:
    """Percorre compras, vendas e eventos e devolve cada venda realizada.

    Cada realização: mes, data, ticker, tipo, cesta, valor_venda, ganho
    (só da parte com custo), qtd_sem_custo e valor_sem_custo.
    """
    realizacoes, alertas, _ = _percorrer(transacoes, eventos)
    return realizacoes, alertas


def _percorrer(transacoes: list[dict], eventos: list[dict] | None,
               cortes: tuple[str, ...] | list[str] = (),
               ) -> tuple[list[dict], list[dict], dict[str, dict]]:
    """`_realizacoes` com a foto da posição em cada data de ``cortes``.

    A foto ('AAAA-MM-DD') é o que sobra DEPOIS de tudo o que aconteceu
    naquele dia: {ticker: {"qtd", "custo", "tipo", "sem_custo"}} -- é o saldo
    a custo de aquisição que vai para Bens e Direitos em 31/12. ``sem_custo``
    marca o ativo que, até a data, já teve venda sem custo conhecido: a parte
    comprada antes do extrato não está na foto.
    """
    from data_pipeline.importers.investments.positions import (
        _aplicar_evento,
        _liquidar_reorganizacoes,
    )

    tipos: dict[str, str] = {}
    fora: set[str] = set()
    dias: dict[tuple, dict] = defaultdict(lambda: {"buy": [], "sell": []})
    for tx in transacoes:
        b = _base(str(tx.get("ticker") or ""))
        t = tipo_fiscal(b, tx.get("classe"))
        if t is None:
            fora.add(b)
            continue
        tipos.setdefault(b, t)
        lado = str(tx.get("type") or "").lower()
        if lado in ("buy", "sell"):
            dias[(str(tx["transaction_date"]), b)][lado].append(tx)

    state: dict[str, dict] = defaultdict(lambda: {"qty": _ZERO, "avg_price": _ZERO})
    grupos = {b: [b] for b in tipos}
    alertas: list[dict] = [{"ticker": b, "type": "fora_do_escopo",
                            "detail": "classe fora de ações, FII, ETF e BDR"}
                           for b in sorted(fora)]

    agenda: list[tuple] = []
    for ev in _liquidar_reorganizacoes(eventos or []):
        agenda.append((str(ev.get("event_date") or ""), 0, "", ev))
    for (dia, b) in dias:
        agenda.append((dia, 1, b, None))
    agenda.sort(key=lambda x: (x[0], x[1], x[2]))

    pendentes = sorted({str(c)[:10] for c in cortes})
    fotos: dict[str, dict] = {}
    com_venda_sem_custo: set[str] = set()

    def fotografar(corte: str) -> None:
        fotos[corte] = {
            b: {"qtd": s["qty"], "custo": s["qty"] * s["avg_price"],
                "tipo": tipos.get(b), "sem_custo": b in com_venda_sem_custo}
            for b, s in sorted(state.items()) if s["qty"] > _EPS
        }

    realizacoes: list[dict] = []
    for dia, ordem, b, ev in agenda:
        while pendentes and dia[:10] > pendentes[0]:
            fotografar(pendentes.pop(0))
        if ordem == 0:
            _aplicar_evento(ev, state, grupos, alertas)
            continue
        tipo = tipos[b]
        s = state[b]
        compras, vendas = dias[(dia, b)]["buy"], dias[(dia, b)]["sell"]
        q_c = sum((_dec(t["quantity"]) for t in compras), _ZERO)
        v_c = sum((_dec(t["quantity"]) * _dec(t["unit_price"]) + _dec(t.get("fees"))
                   for t in compras), _ZERO)
        q_v = sum((_dec(t["quantity"]) for t in vendas), _ZERO)
        bruto_v = sum((_dec(t["quantity"]) * _dec(t["unit_price"]) for t in vendas), _ZERO)
        taxa_v = sum((_dec(t.get("fees")) for t in vendas), _ZERO)
        liq_v = bruto_v - taxa_v

        # Day trade: a quantidade comprada E vendida no mesmo dia se casa
        # entre si, a preço médio do dia de cada lado, antes de tocar a posição.
        q_dt = min(q_c, q_v)
        if q_dt > _EPS:
            custo_dt = v_c * q_dt / q_c
            receita_dt = liq_v * q_dt / q_v
            realizacoes.append({
                "mes": _mes(dia), "data": dia, "ticker": b, "tipo": tipo,
                "cesta": "fii" if tipo == "fii" else "day_trade",
                "day_trade": True,
                "valor_venda": bruto_v * q_dt / q_v,
                "ganho": receita_dt - custo_dt,
                "qtd_sem_custo": _ZERO, "valor_sem_custo": _ZERO,
            })
        q_c_resto = q_c - q_dt
        q_v_resto = q_v - q_dt

        if q_c_resto > _EPS:
            custo = v_c * q_c_resto / q_c
            nova = s["qty"] + q_c_resto
            s["avg_price"] = (s["qty"] * s["avg_price"] + custo) / nova
            s["qty"] = nova

        if q_v_resto > _EPS:
            coberta = min(q_v_resto, s["qty"])
            sem = q_v_resto - coberta
            receita = liq_v * q_v_resto / q_v
            receita_coberta = receita * coberta / q_v_resto
            if sem > _EPS and e_direito(b, tipo):
                # Direito recebido da empresa: custo zero, ganho integral.
                receita_coberta, sem = receita, _ZERO
            if sem > _EPS:
                com_venda_sem_custo.add(b)
            realizacoes.append({
                "mes": _mes(dia), "data": dia, "ticker": b, "tipo": tipo,
                "cesta": _cesta_comum(tipo), "day_trade": False,
                "valor_venda": bruto_v * q_v_resto / q_v,
                "ganho": receita_coberta - coberta * s["avg_price"],
                "qtd_sem_custo": sem if sem > _EPS else _ZERO,
                "valor_sem_custo": (receita - receita_coberta) if sem > _EPS else _ZERO,
            })
            s["qty"] -= coberta
            if s["qty"] <= _EPS:
                s["qty"], s["avg_price"] = _ZERO, _ZERO
    for corte in pendentes:
        fotografar(corte)
    return realizacoes, alertas, fotos


def resultado_realizado(transacoes: list[dict], eventos: list[dict] | None = None,
                        ) -> dict:
    """Lucro (ou prejuízo) já realizado em vendas, somado desde o início do extrato.

    Usa as mesmas realizações do IR (preço médio ponderado, corretagem, day
    trade, eventos da Movimentação). Venda sem custo conhecido -- ativo
    comprado antes do primeiro extrato -- não entra como ganho nem como
    zero: o valor dela sai em ``valor_sem_custo``, para a tela dizer que ficou
    de fora.
    """
    realizacoes, _ = _realizacoes(transacoes, eventos)
    return {
        "ganho": float(sum((r["ganho"] for r in realizacoes), _ZERO)),
        "valor_vendido": float(sum((r["valor_venda"] for r in realizacoes), _ZERO)),
        "valor_sem_custo": float(sum((r["valor_sem_custo"] for r in realizacoes), _ZERO)),
        "n_vendas": len(realizacoes),
        "sem_custo_por_ticker": _sem_custo_por_ticker(realizacoes),
    }


def _sem_custo_por_ticker(realizacoes: list[dict]) -> dict[str, dict]:
    """{ticker: {"qtd", "valor", "primeira_venda"}} das vendas sem custo."""
    out: dict[str, dict] = {}
    for r in realizacoes:
        if r["qtd_sem_custo"] <= 0:
            continue
        d = out.setdefault(r["ticker"], {"qtd": 0.0, "valor": 0.0,
                                         "primeira_venda": str(r["data"])})
        d["qtd"] += float(r["qtd_sem_custo"])
        d["valor"] += float(r["valor_sem_custo"])
        d["primeira_venda"] = min(d["primeira_venda"], str(r["data"]))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Apuração mensal
# ─────────────────────────────────────────────────────────────────────────────

def _meses_entre(primeiro: str, ultimo: str) -> list[str]:
    a, m = int(primeiro[:4]), int(primeiro[5:7])
    fa, fm = int(ultimo[:4]), int(ultimo[5:7])
    out = []
    while (a, m) <= (fa, fm):
        out.append(f"{a:04d}-{m:02d}")
        a, m = (a + 1, 1) if m == 12 else (a, m + 1)
    return out


def _pote(anteriores: Decimal, no_mes: Decimal) -> dict:
    return {"no_mes": no_mes, "meses_anteriores": anteriores,
            "usado": _ZERO, "a_compensar": anteriores + no_mes}


def _usar(pote: dict, devido: Decimal) -> Decimal:
    """Abate o pote do imposto e devolve o que sobra a pagar."""
    usado = min(pote["a_compensar"], max(devido, _ZERO))
    pote["usado"] += usado
    pote["a_compensar"] -= usado
    return devido - usado


def apurar(transacoes: list[dict], eventos: list[dict] | None = None,
           hoje: date | None = None, cortes: tuple[str, ...] | list[str] = ()) -> dict:
    """Apuração mês a mês, do primeiro mês com venda até o mês corrente.

    ``transacoes``: dicts com transaction_date, ticker, classe ('stock',
    'reit', 'etf', ...), type ('buy'/'sell'), quantity, unit_price, fees.
    ``eventos``: linhas da Movimentação, como ``positions._load_events``.
    ``cortes``: datas ('AAAA-MM-DD') em que fotografar a posição a custo;
    a foto sai em ``fotos`` (ver `_percorrer`).

    O IRRF segue as fichas do programa da Receita, cada uma com o seu saldo:

      RENDA VARIÁVEL  "IR fonte de Day-Trade" (1% do ganho) e "IR fonte
                      (Lei nº 11.033/2004)" (0,005% da venda comum), abatidos
                      nessa ordem do imposto de ações + day trade;
      FII/FIAGRO      "Imposto retido" (0,005% da venda), só contra o
                      imposto dos fundos.

    Os saldos zeram em janeiro. O que sobra da Lei 11.033 em dezembro
    (renda variável e FII) vai para Imposto Pago/Retido, linha 03; o de
    day trade não vai -- só por pedido de restituição (P&R 2026, 714).
    """
    hoje = hoje or date.today()
    realizacoes, alertas, fotos = _percorrer(transacoes, eventos, cortes)
    if not realizacoes:
        return {"meses": [], "anos": [], "alertas": alertas, "pendente": None,
                "fotos": fotos}

    por_mes: dict[str, list[dict]] = defaultdict(list)
    for r in realizacoes:
        por_mes[r["mes"]].append(r)

    ultimo = max(max(por_mes), f"{hoje.year:04d}-{hoje.month:02d}")
    prejuizo = {c: _ZERO for c in CESTAS}
    incerto = {c: False for c in CESTAS}
    saldo = {"day_trade": _ZERO, "lei_11033": _ZERO, "fii": _ZERO}
    acumulado = {"rv": _ZERO, "fii": _ZERO}   # DARF abaixo de R$ 10 que passa adiante
    ano_corrente = None
    sobra_ano: dict[int, dict] = {}
    meses: list[dict] = []

    for mes in _meses_entre(min(por_mes), ultimo):
        ano, m = int(mes[:4]), int(mes[5:7])
        if ano != ano_corrente:
            if ano_corrente is not None:
                sobra_ano[ano_corrente] = dict(saldo)
            saldo = {k: _ZERO for k in saldo}
            ano_corrente = ano
        rs = por_mes.get(mes, [])
        if not rs and acumulado["rv"] + acumulado["fii"] == 0:
            continue

        vendas_acoes = sum((r["valor_venda"] for r in rs
                            if r["tipo"] == "acao" and not r["day_trade"]), _ZERO)
        isento = vendas_acoes <= LIMITE_ISENCAO_ACOES
        cestas: dict[str, dict] = {}
        for c in CESTAS:
            rc = [r for r in rs if r["cesta"] == c]
            # No mês isento, as ações se compensam ENTRE SI primeiro: o ganho
            # líquido positivo é isento e só o prejuízo líquido passa adiante.
            # Separar venda a venda isentaria o ganho de uma ação e ainda
            # guardaria a perda de outra para abater imposto futuro.
            acoes = sum((r["ganho"] for r in rc
                         if r["tipo"] == "acao" and not r["day_trade"]), _ZERO)
            resultado = sum((r["ganho"] for r in rc), _ZERO)
            ganho_isento = _ZERO
            if isento and c == "comum" and acoes > 0:
                ganho_isento = acoes
                resultado -= acoes
            sem_custo = sorted({r["ticker"] for r in rc if r["qtd_sem_custo"] > 0})
            carregado_incerto = incerto[c]
            if sem_custo:
                incerto[c] = True
            anterior = prejuizo[c]
            usado = _ZERO
            if resultado > 0:
                usado = min(resultado, prejuizo[c])
                prejuizo[c] -= usado
                base = resultado - usado
            else:
                prejuizo[c] += -resultado
                base = _ZERO
            imposto = base * ALIQUOTA[c]
            if c == "day_trade":
                irrf = sum((r["ganho"] for r in rc if r["ganho"] > 0), _ZERO) * IRRF_DAY_TRADE
            else:
                vendido = sum((r["valor_venda"] for r in rc), _ZERO)
                irrf = vendido * IRRF_VENDA
            irrf = irrf if irrf >= IRRF_MINIMO else _ZERO
            cestas[c] = {
                "vendas": sum((r["valor_venda"] for r in rc), _ZERO),
                "resultado": resultado,
                "ganho_isento": ganho_isento,
                "prejuizo_anterior": anterior,
                "prejuizo_compensado": usado,
                "base": base,
                "imposto": imposto,
                "irrf": irrf,
                "prejuizo_a_compensar": prejuizo[c],
                "sem_custo": sem_custo,
                "valor_sem_custo": sum((r["valor_sem_custo"] for r in rc), _ZERO),
                "incompleta": bool(sem_custo),
                "prejuizo_incerto": incerto[c] or carregado_incerto,
            }

        potes = {
            "day_trade": _pote(saldo["day_trade"], cestas["day_trade"]["irrf"]),
            "lei_11033": _pote(saldo["lei_11033"], cestas["comum"]["irrf"]),
            "fii": _pote(saldo["fii"], cestas["fii"]["irrf"]),
        }
        rv_devido = cestas["comum"]["imposto"] + cestas["day_trade"]["imposto"]
        rv_a_pagar = _usar(potes["lei_11033"], _usar(potes["day_trade"], rv_devido))
        fii_devido = cestas["fii"]["imposto"]
        fii_a_pagar = _usar(potes["fii"], fii_devido)
        for k in saldo:
            saldo[k] = potes[k]["a_compensar"]

        imposto_total = rv_devido + fii_devido
        irrf_mes = sum((p["no_mes"] for p in potes.values()), _ZERO)
        irrf_usado = sum((p["usado"] for p in potes.values()), _ZERO)
        devido = rv_a_pagar + fii_a_pagar + acumulado["rv"] + acumulado["fii"]
        # O DARF que o mês gera (com o acumulado abaixo de R$ 10), por ficha.
        # É o que DEVERIA ser recolhido; se foi, só o comprovante diz.
        if devido >= DARF_MINIMO:
            darf_ficha = {"rv": rv_a_pagar + acumulado["rv"], "fii": fii_a_pagar + acumulado["fii"]}
            darf, acumulado = devido, {"rv": _ZERO, "fii": _ZERO}
        else:
            darf_ficha = {"rv": _ZERO, "fii": _ZERO}
            darf = _ZERO
            acumulado = {"rv": rv_a_pagar + acumulado["rv"],
                         "fii": fii_a_pagar + acumulado["fii"]}
        em_curso = (ano, m) == (hoje.year, hoje.month)
        meses.append({
            "mes": mes,
            "em_curso": em_curso,
            "vendas_acoes": vendas_acoes,
            "isento_acoes": isento,
            "cestas": cestas,
            "imposto": imposto_total,
            "irrf_estimado": irrf_mes,
            "irrf_deduzido": irrf_usado,
            "irrf": potes,
            "rv": {"imposto_devido": rv_devido, "a_pagar": rv_a_pagar, "darf": darf_ficha["rv"]},
            "fii": {"imposto_devido": fii_devido, "a_pagar": fii_a_pagar,
                    "darf": darf_ficha["fii"]},
            "darf": darf,
            "acumulado_proximo": acumulado["rv"] + acumulado["fii"],
            "vencimento": vencimento_darf(ano, m),
            "incompleto": any(cestas[c]["incompleta"] for c in CESTAS),
        })
    if ano_corrente is not None:
        sobra_ano[ano_corrente] = dict(saldo)

    anos: list[dict] = []
    for ano in sorted({int(x["mes"][:4]) for x in meses}):
        ms = [x for x in meses if x["mes"].startswith(f"{ano:04d}")]
        fim = ms[-1]["cestas"]
        sobra = sobra_ano.get(ano) or {}
        anos.append({
            "ano": ano,
            "ganho_isento": sum((x["cestas"]["comum"]["ganho_isento"] for x in ms), _ZERO),
            "darf": sum((x["darf"] for x in ms), _ZERO),
            "imposto_devido": sum((x["imposto"] for x in ms), _ZERO),
            "prejuizo_fim": {c: fim[c]["prejuizo_a_compensar"] for c in CESTAS},
            "irrf_a_declarar": sobra.get("lei_11033", _ZERO) + sobra.get("fii", _ZERO),
            "irrf_day_trade_restituir": sobra.get("day_trade", _ZERO),
            "meses_incompletos": [x["mes"] for x in ms if x["incompleto"]],
        })

    pendente = next((x for x in reversed(meses)
                     if x["darf"] > 0 and not x["em_curso"]
                     and x["vencimento"] >= hoje), None)
    return {"meses": meses, "anos": anos, "alertas": alertas, "pendente": pendente,
            "fotos": fotos}


# ─────────────────────────────────────────────────────────────────────────────
# Carregamento
# ─────────────────────────────────────────────────────────────────────────────

def _tickers_etf(conn) -> set[str]:
    """ETFs da B3 no cadastro de mercado; vazio se o schema não existe."""
    from sqlalchemy import text

    try:
        with conn.begin_nested():
            rows = conn.execute(text(
                "SELECT UPPER(ticker) FROM market.assets "
                "WHERE asset_type = 'etf' AND exchange = 'B3'")).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.info("ir_renda_variavel: cadastro de ETFs indisponível: %s", exc)
        return set()
    return {r[0] for r in rows}


def ler_cnpj_emissores(caminho: Path = CAMINHO_CNPJ) -> dict[str, list[str]]:
    """{ticker: [CNPJ, razão social]} das companhias abertas (CVM), publicado
    por ``scripts/publicar_cnpj_emissores.py``; vazio se o arquivo falta."""
    try:
        return json.loads(caminho.read_text(encoding="utf-8")).get("tickers") or {}
    except (OSError, ValueError) as exc:
        logger.info("ir_renda_variavel: cnpj_emissores indisponível: %s", exc)
        return {}


def _classe_fiscal(ticker: str, classe: str | None, etfs: set[str],
                   companhias: set[str]) -> str | None:
    t = _base(ticker)
    if t in etfs:
        return "etf"
    if (classe or "").strip().lower() == "reit" and t in companhias:
        return "stock"
    return classe


def carregar_operacoes(engine, user_id: str) -> tuple[list[dict], list[dict]]:
    """Negociações da B3 com a classe do ativo, e os eventos da Movimentação.

    A posição anterior ao extrato que o investidor declarou entra como
    compra sintética na frente de tudo (`core.posicao_anterior`): sem ela,
    venda de lote comprado antes de nov/2019 não tem custo.

    A classe sai do cadastro do usuário, que grava todo XXXX11 como 'reit';
    quando o cadastro de mercado diz que o ticker é ETF (BOVA11, BITH11), vale
    o ETF: a venda vai para operações comuns a 15%, não para a cesta de FII.
    E quando a CVM lista o ticker como valor mobiliário de companhia aberta
    (SAPR11, unit da Sanepar), é ação: FII não é companhia aberta.
    """
    from sqlalchemy import text

    from core.posicao_anterior import como_compras
    from core.posicao_anterior import listar as listar_abertura
    from data_pipeline.importers.investments.positions import _load_events

    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT it.transaction_date, a.ticker, a.class, it.type,
                       it.quantity, it.unit_price, it.fees
                FROM investment_transactions it
                JOIN assets a ON a.id = it.asset_id
                WHERE it.user_id = :uid
                  AND COALESCE(a.currency, 'BRL') = 'BRL'
                ORDER BY it.transaction_date ASC, it.created_at ASC
            """),
            {"uid": user_id},
        ).fetchall()
        eventos = _load_events(conn, user_id)
        abertura = listar_abertura(user_id, conn)
        etfs = _tickers_etf(conn)
    companhias = set(ler_cnpj_emissores())
    transacoes = [
        {"transaction_date": r[0], "ticker": r[1],
         "classe": _classe_fiscal(str(r[1] or ""), r[2], etfs, companhias), "type": r[3],
         "quantity": r[4], "unit_price": r[5], "fees": r[6] or 0}
        for r in rows
    ]
    return como_compras(abertura, transacoes) + transacoes, eventos
