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
    ações fica em até R$ 20.000. Não vale para ETF, BDR, FII nem day trade.
    O prejuízo em ações num mês isento continua compensável.
  - Prejuízo compensa só dentro da mesma cesta (comum, day trade, FII), sem
    prazo.
  - IRRF ("dedo-duro"): 0,005% sobre a venda em operação comum e FII, 1% sobre
    o ganho do day trade; deduz do imposto do mês. O extrato da B3 não traz
    as notas de corretagem, então aqui ele é ESTIMADO. O saldo não usado
    passa para os meses seguintes do mesmo ano; o que sobra em dezembro vai
    para a declaração anual.
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
import logging
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
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


# ─────────────────────────────────────────────────────────────────────────────
# Classificação
# ─────────────────────────────────────────────────────────────────────────────

def _base(ticker: str) -> str:
    t = (ticker or "").strip().upper()
    return t[:-1] if t.endswith("F") and len(t) > 4 else t


def tipo_fiscal(ticker: str, classe: str | None) -> str | None:
    """'acao', 'etf', 'bdr', 'fii' ou None (fora do escopo deste módulo).

    O BDR é reconhecido pelo sufixo 32-35/39 do ticker, porque o cadastro de
    ativos o guarda como 'stock'. Units (TAEE11, KLBN11) cadastradas como
    'stock' são ações para o fisco e entram na isenção.
    """
    c = (classe or "").strip().lower()
    t = _base(ticker)
    if c == "reit":
        return "fii"
    if c == "etf":
        return "etf"
    if c == "stock":
        if len(t) >= 6 and t[-2:] in {"32", "33", "34", "35", "39"}:
            return "bdr"
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

    realizacoes: list[dict] = []
    for dia, ordem, b, ev in agenda:
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
    return realizacoes, alertas


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


def apurar(transacoes: list[dict], eventos: list[dict] | None = None,
           hoje: date | None = None) -> dict:
    """Apuração mês a mês, do primeiro mês com venda até o mês corrente.

    ``transacoes``: dicts com transaction_date, ticker, classe ('stock',
    'reit', 'etf', ...), type ('buy'/'sell'), quantity, unit_price, fees.
    ``eventos``: linhas da Movimentação, como ``positions._load_events``.
    """
    hoje = hoje or date.today()
    realizacoes, alertas = _realizacoes(transacoes, eventos)
    if not realizacoes:
        return {"meses": [], "anos": [], "alertas": alertas, "pendente": None}

    por_mes: dict[str, list[dict]] = defaultdict(list)
    for r in realizacoes:
        por_mes[r["mes"]].append(r)

    ultimo = max(max(por_mes), f"{hoje.year:04d}-{hoje.month:02d}")
    prejuizo = {c: _ZERO for c in CESTAS}
    incerto = {c: False for c in CESTAS}
    irrf_saldo = _ZERO
    acumulado = _ZERO          # DARF abaixo de R$ 10 que passa adiante
    ano_corrente = None
    irrf_a_declarar: dict[int, Decimal] = defaultdict(lambda: _ZERO)
    meses: list[dict] = []

    for mes in _meses_entre(min(por_mes), ultimo):
        ano, m = int(mes[:4]), int(mes[5:7])
        if ano != ano_corrente:
            if ano_corrente is not None and irrf_saldo > 0:
                irrf_a_declarar[ano_corrente] += irrf_saldo
            irrf_saldo, ano_corrente = _ZERO, ano
        rs = por_mes.get(mes, [])
        if not rs and acumulado == 0:
            continue

        vendas_acoes = sum((r["valor_venda"] for r in rs
                            if r["tipo"] == "acao" and not r["day_trade"]), _ZERO)
        isento = vendas_acoes <= LIMITE_ISENCAO_ACOES
        cestas: dict[str, dict] = {}
        irrf_mes = _ZERO
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
            irrf_mes += irrf
            cestas[c] = {
                "vendas": sum((r["valor_venda"] for r in rc), _ZERO),
                "resultado": resultado,
                "ganho_isento": ganho_isento,
                "prejuizo_compensado": usado,
                "base": base,
                "imposto": imposto,
                "prejuizo_a_compensar": prejuizo[c],
                "sem_custo": sem_custo,
                "valor_sem_custo": sum((r["valor_sem_custo"] for r in rc), _ZERO),
                "incompleta": bool(sem_custo),
                "prejuizo_incerto": incerto[c] or carregado_incerto,
            }

        imposto_total = sum((cestas[c]["imposto"] for c in CESTAS), _ZERO)
        irrf_saldo += irrf_mes
        irrf_usado = min(irrf_saldo, imposto_total)
        irrf_saldo -= irrf_usado
        devido = imposto_total - irrf_usado + acumulado
        if devido >= DARF_MINIMO:
            darf, acumulado = devido, _ZERO
        else:
            darf, acumulado = _ZERO, devido
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
            "darf": darf,
            "acumulado_proximo": acumulado,
            "vencimento": vencimento_darf(ano, m),
            "incompleto": any(cestas[c]["incompleta"] for c in CESTAS),
        })
    if irrf_saldo > 0 and ano_corrente is not None:
        irrf_a_declarar[ano_corrente] += irrf_saldo

    anos: list[dict] = []
    for ano in sorted({int(x["mes"][:4]) for x in meses}):
        ms = [x for x in meses if x["mes"].startswith(f"{ano:04d}")]
        fim = ms[-1]["cestas"]
        anos.append({
            "ano": ano,
            "ganho_isento": sum((x["cestas"]["comum"]["ganho_isento"] for x in ms), _ZERO),
            "darf": sum((x["darf"] for x in ms), _ZERO),
            "prejuizo_fim": {c: fim[c]["prejuizo_a_compensar"] for c in CESTAS},
            "irrf_a_declarar": irrf_a_declarar.get(ano, _ZERO),
            "meses_incompletos": [x["mes"] for x in ms if x["incompleto"]],
        })

    pendente = next((x for x in reversed(meses)
                     if x["darf"] > 0 and not x["em_curso"]
                     and x["vencimento"] >= hoje), None)
    return {"meses": meses, "anos": anos, "alertas": alertas, "pendente": pendente}


# ─────────────────────────────────────────────────────────────────────────────
# Carregamento
# ─────────────────────────────────────────────────────────────────────────────

def carregar_operacoes(engine, user_id: str) -> tuple[list[dict], list[dict]]:
    """Negociações da B3 com a classe do ativo, e os eventos da Movimentação."""
    from sqlalchemy import text

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
    transacoes = [
        {"transaction_date": r[0], "ticker": r[1], "classe": r[2], "type": r[3],
         "quantity": r[4], "unit_price": r[5], "fees": r[6] or 0}
        for r in rows
    ]
    return transacoes, eventos
