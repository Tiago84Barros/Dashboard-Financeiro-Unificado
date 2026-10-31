"""
data_pipeline/importers/investments/positions.py
================================================
Recalcula `portfolio_positions` a partir de `investment_transactions`.

Portado de `migration/08_compute_portfolio_positions.py`, exposto como
função reutilizável (sem CLI, sem prints). Chamado automaticamente após
cada importação manual bem-sucedida.

Algoritmo: custo médio ponderado (padrão BR).
  buy:  new_avg = (qty_atual*avg + buy_qty*price + fees) / (qty_atual + buy_qty)
  sell: qty_atual -= sell_qty   (avg_price inalterado)
  venda sem cobertura: qty e avg_price voltam a zero -- o histórico está
        truncado (o relatório de Negociação da B3 começa em nov/2019), não
        há posição negativa a carregar para dentro da média seguinte.
  final: somente ativos com qty > 0 e avg_price > 0 são gravados.

Lote padrão e fracionário (2026-10-04): PETR3 e PETR3F são a MESMA ação e
dividem quantidade e custo médio -- o estado é indexado pelo ticker-base
(`_base`), como já faz `core/ir_renda_variavel.py`. Indexar por asset_id
deixava a venda no lote de cotas compradas no fracionário descoberta (zerada,
com alerta `quantidade_negativa`) e o fracionário vivo, com o custo antigo
dentro da média da compra seguinte. A posição é gravada no asset_id do lote
padrão; só o fracionário negociado fica no asset_id dele.

Eventos corporativos (2026-09-24): as linhas cruas da Movimentação da B3
(`investment_movement_events`, SQL 075) entram na mesma sequência, ANTES das
negociações do mesmo dia -- a venda do dia do crédito já é em cotas novas:
  desdobro / grupamento: qty muda pelo sentido, custo total preservado;
  bonificação em ativos: qty sobe e o custo sobe pelo custo atribuído que a
        B3 publica (preço unitário, a regra fiscal); sem preço, o custo é
        preservado e o PM dilui;
  fração em ativos (débito): sai como uma venda -- PM inalterado;
  recibo de subscrição: compra da cota (`XXXX12..15` -> `XXXX11`) pelo valor
        publicado; sem valor, fica de fora (cota sem o dinheiro que a pagou);
  transferência, incorporação etc.: ignorados.
Evento sobre posição zerada (histórico truncado antes de nov/2019) não cria
quantidade sem custo: vira alerta. Errar aqui tem de custar cobertura -- a
conciliação em `core/investimentos.py` já reprova PM cuja quantidade não
fecha com a posição -- e nunca um preço médio inventado.

Troca de código e negociação que faltou (2026-10-12): conciliação contra a
Área do Investidor da B3 achou duas classes de erro que o extrato de
Negociação sozinho não resolve.
  atualização: em geral a B3 só reafirma a posição (BBAS3 1.479, MXRF11
        1.352) e somar dobraria. Mas quando o código NOVO não tem posição e
        exatamente UM código vivo divide com ele o nome do emissor na
        Movimentação, é troca de código: TRPL3 -> ISAE3 (nov/2024), RBFF11 ->
        RBFM11 (jan/2026). O custo do antigo passa para o novo com a
        quantidade creditada e o antigo zera -- antes ele ficava vivo para
        sempre, uma posição fantasma, e o novo era vendido descoberto.
  transferência - liquidação: a liquidação (D+2) é o lado do caixa de cada
        pregão, líquida por ativo e dia. Ela só completa o extrato; nunca o
        substitui. Uma negociação é sintetizada quando
          * o pregão existe no extrato e a diferença é uma linha dele repetida
            -- mesmo sentido, múltiplo da quantidade, mesmo preço (TAEE3: 100
            no extrato, 200 liquidadas a 11,13). É o rastro do importador que
            fundia execuções idênticas; diferença de outro tamanho não entra
            (no outro usuário ela vinha de empréstimo e exercício), ou
          * o pregão é anterior à primeira negociação do extrato (liquidação
            de 03/01/2020 de compras de 30/12/2019).
        Liquidação sem pregão nenhum no meio do histórico (exercício de
        opção, empréstimo) fica de fora: sem o pregão não há como saber se
        já está contada. Toda negociação sintetizada vira alerta com a origem.

Idempotência: UPSERT em (portfolio_id, asset_id) + DELETE das posições que
o recálculo não produziu -- sem isso, ativo que deixa de qualificar fica
publicado para sempre com o valor do último recálculo em que qualificou.
"""
from __future__ import annotations

import logging
import re
import unicodedata
import uuid
from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

logger = logging.getLogger(__name__)

PORTFOLIO_NAME = "Carteira Principal"
PORTFOLIO_TYPE = "personal"  # respeita CHECK portfolios.type

# Rótulos da Movimentação (minúsculos, como o importador grava).
EV_DESDOBRO = "desdobro"
EV_GRUPAMENTO = "grupamento"
EV_BONIFICACAO = "bonificação em ativos"
EV_FRACAO = "fração em ativos"
EV_SUBSCRICAO = "recibo de subscrição"
EVENTOS_DE_POSICAO = frozenset({EV_DESDOBRO, EV_GRUPAMENTO, EV_BONIFICACAO,
                                EV_FRACAO, EV_SUBSCRICAO})
EV_ATUALIZACAO = "atualização"
EV_LIQUIDACAO = "transferência - liquidação"
# O que `_load_events` traz: os eventos acima mais as duas pistas que só
# completam o extrato (troca de código e negociação ausente).
EVENTOS_CARREGADOS = EVENTOS_DE_POSICAO | {EV_ATUALIZACAO, EV_LIQUIDACAO}

# Pregão -> liquidação: D+2 úteis. A janela vai de 2 dias corridos (nunca
# D+1: o pregão de 02/01 liquida em 06/01, não em 03/01) a 7, folga para
# feriados que `_uteis_antes` não conhece.
JANELA_LIQUIDACAO_DIAS = 7

_RX_RECIBO_FII = re.compile(r"^([A-Z]{3}[A-Z0-9])1[2-5]$")
# Só papel de bolsa tem pregão a casar; LCI, CDB e Tesouro não.
_RX_PAPEL_B3 = re.compile(r"^[A-Z]{4}\d{1,2}$")


def _base(ticker: str) -> str:
    """Forma-base do ticker, como o `pp_base` de core/investimentos agrega."""
    t = (ticker or "").strip().upper()
    return t[:-1] if t.endswith("F") and len(t) > 4 else t


def _sinal(direcao: str) -> int:
    d = (direcao or "").strip().lower()
    if d.startswith(("cred", "créd", "entrada")):
        return 1
    if d.startswith(("deb", "déb", "saida", "saída")):
        return -1
    return 0


def _data(v: Any) -> date | None:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _uteis_antes(d: date, n: int) -> date:
    while n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def _nome_emissor(ticker: str, produto: str) -> str:
    """'TRPL3 - CTEEP - CIA TRANSMISSÃO...' -> 'CTEEPCIATRANSMISSAO...'."""
    p = (produto or "").strip()
    pref = f"{(ticker or '').strip().upper()} - "
    if p.upper().startswith(pref):
        p = p[len(pref):]
    p = unicodedata.normalize("NFKD", p).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]", "", p.upper())


def _nomes(events: list[dict]) -> dict[str, set[str]]:
    """Ticker-base -> nomes do emissor. A B3 publica mais de um por ticker
    (o antigo e o atual), então a comparação é entre conjuntos."""
    nomes: dict[str, set[str]] = defaultdict(set)
    for ev in events:
        tk = str(ev.get("ticker") or "")
        n = _nome_emissor(tk, str(ev.get("product") or ""))
        if n:
            nomes[_base(tk)].add(n)
    return nomes


# ─────────────────────────────────────────────────────────────────────────────
# Cálculo em memória
# ─────────────────────────────────────────────────────────────────────────────

def _liquidar_reorganizacoes(events: list[dict]) -> list[dict]:
    """Desdobro e grupamento viram UMA linha líquida por dia e ativo.

    A B3 pode publicar a reorganização como a diferença (crédito de 100 num
    desdobro de 100 para 200) ou como as duas pontas (débito das 1.000
    antigas e crédito das 100 novas num grupamento). Linha a linha, o
    segundo formato debitaria a posição inteira -- recusado -- e depois
    somaria as novas por cima das antigas. O saldo líquido do dia é o mesmo
    nos dois formatos.
    """
    liquido: dict[tuple, Decimal] = {}
    outros: list[dict] = []
    for ev in events:
        mov = str(ev.get("movement") or "").strip().lower()
        if mov not in (EV_DESDOBRO, EV_GRUPAMENTO):
            outros.append(ev)
            continue
        chave = (str(ev.get("event_date") or ""), _base(str(ev.get("ticker") or "")), mov)
        q = abs(Decimal(str(ev.get("quantity") or "0")))
        liquido[chave] = liquido.get(chave, Decimal("0")) + _sinal(str(ev.get("direction") or "")) * q
    for (dia, tk, mov), q in liquido.items():
        if q:
            outros.append({"event_date": dia, "movement": mov, "ticker": tk,
                           "direction": "Credito" if q > 0 else "Debito",
                           "quantity": abs(q)})
    return outros


def _linha_repetida(linhas: list[tuple[Decimal, Decimal]], falta: Decimal,
                    liq: dict) -> bool:
    """A falta tem a assinatura da linha idêntica que o importador fundia.

    Até 12/10/2026 o external_id da Negociação não levava a ordem da linha,
    e duas execuções iguais no mesmo pregão viravam uma. O que sobra disso é
    exato: a falta é múltipla de uma linha do dia, do mesmo lado e ao mesmo
    preço que a liquidação publica. Diferença de outro tamanho (empréstimo,
    exercício de opção, liquidação de terceiros) não é reconstruída.
    """
    if not falta:
        return False
    lado = 1 if falta > 0 else -1
    q_preco, custo = liq[lado]
    if q_preco <= 0:
        return False
    preco_liq = custo / q_preco
    for q, preco in linhas:
        if q * falta <= 0 or preco <= 0:
            continue
        if abs(falta) % abs(q) == 0 and abs(preco - preco_liq) <= preco_liq * Decimal("0.01"):
            return True
    return False


def _reconstruir_liquidacao(transactions: list[dict], liquidacoes: list[dict],
                            ) -> tuple[list[dict], list[dict]]:
    """Negociações que a liquidação prova e o extrato não traz.

    Devolve (negociações sintéticas, alertas). Regras na docstring do módulo.
    A liquidação é casada pelo saldo líquido do pregão (compra 23 e venda 20
    no mesmo dia liquidam 3), no pregão mais perto de D-2 úteis.
    """
    if not liquidacoes:
        return [], []
    pregao: dict[tuple, Decimal] = defaultdict(Decimal)
    linhas: dict[tuple, list[tuple[Decimal, Decimal]]] = defaultdict(list)
    modelo: dict[str, dict] = {}
    datas: list[date] = []
    for tx in transactions:
        d = _data(tx.get("transaction_date"))
        b = _base(str(tx.get("ticker") or ""))
        lado = str(tx.get("type") or "").lower()
        if d is None:
            continue
        datas.append(d)
        if not _RX_PAPEL_B3.match(b) or lado not in ("buy", "sell"):
            continue
        q = abs(Decimal(str(tx.get("quantity") or "0")))
        pregao[(b, d)] += q if lado == "buy" else -q
        linhas[(b, d)].append((q if lado == "buy" else -q,
                               Decimal(str(tx.get("unit_price") or "0"))))
        if b not in modelo or str(tx.get("ticker") or "").strip().upper() == b:
            modelo[b] = tx
    primeiro = min(datas) if datas else None

    liq: dict[tuple, dict] = {}
    for ev in liquidacoes:
        d = _data(ev.get("event_date"))
        b = _base(str(ev.get("ticker") or ""))
        sinal = _sinal(str(ev.get("direction") or ""))
        q = abs(Decimal(str(ev.get("quantity") or "0")))
        if d is None or not sinal or q <= 0 or not _RX_PAPEL_B3.match(b):
            continue
        L = liq.setdefault((b, d), {"net": Decimal("0"), "asset_id": None,
                                    1: [Decimal("0"), Decimal("0")],
                                    -1: [Decimal("0"), Decimal("0")]})
        L["net"] += sinal * q
        L["asset_id"] = L["asset_id"] or ev.get("asset_id")
        preco = ev.get("unit_price")
        if preco is None and ev.get("total_value") is not None:
            preco = abs(Decimal(str(ev["total_value"]))) / q
        if preco is not None and Decimal(str(preco)) > 0:
            L[sinal][0] += q
            L[sinal][1] += q * Decimal(str(preco))

    usados: set[tuple] = set()
    sinteticas: list[dict] = []
    alerts: list[dict] = []
    for (b, d) in sorted(liq, key=lambda k: (k[1], k[0])):
        L = liq[(b, d)]
        net = L["net"]
        if not net:
            continue
        esperado = _uteis_antes(d, 2)
        candidatos = [(b, d - timedelta(days=k)) for k in range(2, JANELA_LIQUIDACAO_DIAS + 1)]
        candidatos = sorted((c for c in candidatos if c in pregao and c not in usados),
                            key=lambda c: abs((c[1] - esperado).days))
        if any(pregao[c] == net for c in candidatos):
            usados.add(next(c for c in candidatos if pregao[c] == net))
            continue
        parcial = next((c for c in candidatos
                        if _linha_repetida(linhas[c], net - pregao[c], L)), None)
        if parcial is not None:
            usados.add(parcial)
            falta, dia = net - pregao[parcial], parcial[1]
            motivo = f"pregão de {dia} com {abs(pregao[parcial])} no extrato e {abs(net)} liquidadas"
        elif not candidatos and primeiro is not None and esperado < primeiro:
            falta, dia = net, esperado
            motivo = f"pregão anterior ao início do extrato ({primeiro})"
        else:
            continue
        lado = 1 if falta > 0 else -1
        q_preco, custo = L[lado]
        if q_preco <= 0:
            alerts.append({"asset_id": None, "ticker": b, "type": "liquidacao_sem_preco",
                           "detail": f"liquidação de {d}: {abs(falta)} cotas sem preço; fora"})
            continue
        m = modelo.get(b, {})
        aid = m.get("asset_id") or L["asset_id"]
        if aid is None:
            alerts.append({"asset_id": None, "ticker": b, "type": "liquidacao_sem_ativo",
                           "detail": f"liquidação de {d}: {b} sem cadastro em assets; fora"})
            continue
        sinteticas.append({**m, "asset_id": aid, "ticker": b,
                           "user_id": m.get("user_id") or (transactions[0].get("user_id")
                                                           if transactions else None),
                           "type": "buy" if lado > 0 else "sell",
                           "quantity": abs(falta), "unit_price": custo / q_preco,
                           "fees": Decimal("0"), "transaction_date": dia,
                           "origem": "liquidação"})
        alerts.append({"asset_id": aid, "ticker": b, "type": "negociacao_da_liquidacao",
                       "detail": (f"{'compra' if lado > 0 else 'venda'} de {abs(falta)} "
                                  f"a {(custo / q_preco):.4f} vinda da liquidação de {d}: {motivo}")})
    return sinteticas, alerts


def _sequencia(transactions: list[dict], events: list[dict]) -> list[dict]:
    """Negociações e eventos numa ordem só: por data, eventos antes.

    Sem eventos, a ordem recebida (a do SQL) é mantida intacta.
    """
    if not events:
        return list(transactions)
    itens = [(tx.get("transaction_date"), 1, i, tx) for i, tx in enumerate(transactions)]
    itens += [(ev.get("event_date"), 0, i, {**ev, "type": "evento"})
              for i, ev in enumerate(events)]
    return [x[3] for x in sorted(itens, key=lambda x: (str(x[0] or ""), x[1], x[2]))]


def _suceder_codigo(ev: dict, state: dict, grupos: dict[str, list[str]],
                    nomes: dict[str, set[str]], alerts: list[dict]) -> None:
    """Atualização que é troca de código: o custo do antigo passa ao novo."""
    novo = _base(str(ev.get("ticker") or ""))
    qty = abs(Decimal(str(ev.get("quantity") or "0")))
    if _sinal(str(ev.get("direction") or "")) <= 0 or qty <= 0 or not novo:
        return
    if novo in state and state[novo]["qty"] > Decimal("0.0001"):
        return  # reafirmação da posição que já existe
    meus = nomes.get(novo) or set()
    if not meus:
        return
    antigos = [b for b, s in state.items()
               if b != novo and s["qty"] > Decimal("0.0001") and (nomes.get(b) or set()) & meus]
    if len(antigos) != 1:
        if antigos:
            alerts.append({"asset_id": None, "ticker": novo, "type": "troca_de_codigo_ambigua",
                           "detail": f"atualização em {ev.get('event_date')}: "
                                     f"{', '.join(sorted(antigos))} dividem o emissor; ignorada"})
        return
    velho = state[antigos[0]]
    if velho["qty"] != qty:
        # Troca de código é 1 por 1. Quantidade diferente é outra coisa com o
        # mesmo emissor -- o recibo de subscrição da CVC (CVCB3 43, CVCB11 5)
        # levaria o custo das 43 para as 5.
        alerts.append({"asset_id": None, "ticker": novo, "type": "troca_de_codigo_quantidade",
                       "detail": f"atualização em {ev.get('event_date')}: {antigos[0]} tinha "
                                 f"{velho['qty']}, {novo} recebeu {qty}; ignorada"})
        return
    s = state[novo]
    grupos.setdefault(novo, [novo])
    custo = velho["qty"] * velho["avg_price"]
    for k in ("user_id", "asset_id"):
        if k in velho and s.get(k) is None:
            s[k] = ev.get(k) if k == "asset_id" else velho[k]
    if "ticker" in s and not s["ticker"]:
        s["ticker"] = novo
    s["qty"], s["avg_price"] = qty, custo / qty
    velho["qty"], velho["avg_price"] = Decimal("0"), Decimal("0")
    alerts.append({"asset_id": s.get("asset_id"), "ticker": novo, "type": "troca_de_codigo",
                   "detail": f"{antigos[0]} -> {novo} em {ev.get('event_date')}: "
                             f"{qty} cotas, custo de {antigos[0]} preservado"})


def _aplicar_evento(ev: dict, state: dict, grupos: dict[str, list[str]],
                    alerts: list[dict], nomes: dict[str, set[str]] | None = None) -> None:
    """Um evento da Movimentação sobre o estado (ver docstring do módulo)."""
    mov = str(ev.get("movement") or "").strip().lower()
    if mov == EV_ATUALIZACAO:
        if nomes:
            _suceder_codigo(ev, state, grupos, nomes, alerts)
        return
    base = _base(str(ev.get("ticker") or ""))
    if mov == EV_SUBSCRICAO:
        m = _RX_RECIBO_FII.match(base)
        base = f"{m.group(1)}11" if m else base
    sinal = _sinal(str(ev.get("direction") or ""))
    qty = abs(Decimal(str(ev.get("quantity") or "0")))
    if mov not in EVENTOS_DE_POSICAO or not sinal or qty <= 0:
        return

    def alerta(tipo: str, detalhe: str) -> None:
        alerts.append({"asset_id": None, "ticker": base, "type": tipo,
                       "detail": f"{mov} em {ev.get('event_date')}: {detalhe}"})

    aids = grupos.get(base) or []
    if not aids:
        alerta("evento_sem_ativo", "ticker sem nenhuma negociação importada")
        return
    # PETR4 e PETR4F somam na mesma posição (pp_base): o evento vai para o
    # ativo que mais tem cotas agora -- o que tem a base de custo a ajustar.
    aid = max(aids, key=lambda a: state[a]["qty"])
    s = state[aid]
    q0 = s["qty"]
    custo0 = q0 * s["avg_price"]

    if mov == EV_SUBSCRICAO:
        if sinal < 0:
            return
        valor = abs(Decimal(str(ev.get("total_value") or "0")))
        if valor <= 0:
            alerta("subscricao_sem_valor", "cotas sem o valor pago no extrato; fora do PM")
            return
        s["qty"] = q0 + qty
        s["avg_price"] = (custo0 + valor) / s["qty"]
        return

    if q0 <= Decimal("0.0001"):
        alerta("evento_sem_posicao",
               "posição zerada no histórico importado (truncado?); evento ignorado")
        return

    if mov == EV_FRACAO:
        if sinal > 0:
            alerta("fracao_credito", "crédito de fração sem custo conhecido; ignorado")
            return
        s["qty"] = max(q0 - qty, Decimal("0"))
        if s["qty"] <= Decimal("0.0001"):
            s["qty"], s["avg_price"] = Decimal("0"), Decimal("0")
        return

    novo = q0 + sinal * qty
    if novo <= Decimal("0.0001"):
        alerta("evento_zeraria_posicao", f"débito de {qty} sobre {q0} cotas; ignorado")
        return
    custo = custo0
    if mov == EV_BONIFICACAO and sinal > 0:
        preco = Decimal(str(ev.get("unit_price") or "0"))
        if preco > 0:
            custo += qty * preco
    s["qty"] = novo
    s["avg_price"] = custo / novo


def _compute(
    transactions: list[dict],
    events: list[dict] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Custo médio ponderado em memória. Retorna (positions, alerts).

    ``events``: linhas cruas da Movimentação (event_date, movement,
    direction, ticker, quantity, unit_price, total_value; product e
    asset_id quando houver -- o nome do emissor liga a troca de código).
    """
    events = list(events or [])
    liquidacoes = [ev for ev in events
                   if str(ev.get("movement") or "").strip().lower() == EV_LIQUIDACAO]
    events = [ev for ev in events
              if str(ev.get("movement") or "").strip().lower() != EV_LIQUIDACAO]
    sinteticas, alerts = _reconstruir_liquidacao(transactions, liquidacoes)
    transactions = list(transactions) + sinteticas
    nomes = _nomes(events + liquidacoes)

    # Estado por ticker-base: lote e fracionário são uma posição só.
    state: dict[str, dict] = defaultdict(lambda: {
        "qty":        Decimal("0"),
        "avg_price":  Decimal("0"),
        "user_id":    None,
        "asset_id":   None,
        "ticker":     "",
    })
    # alerts já traz a procedência das negociações vindas da liquidação.

    def chave(tx: dict) -> str:
        # Sem ticker não há base a juntar: cada ativo segue sozinho.
        return _base(str(tx.get("ticker") or "")) or f"#{tx['asset_id']}"

    grupos: dict[str, list[str]] = {}
    for tx in transactions:
        ticker = str(tx.get("ticker") or "").strip().upper()
        base = chave(tx)
        grupos[base] = [base]
        s = state[base]
        # Onde a posição é gravada: o asset_id do lote padrão quando ele foi
        # negociado; senão o primeiro visto (só fracionário, ex.: MXRF11F).
        if s["asset_id"] is None or (ticker == base and s["ticker"] != base):
            s["asset_id"] = str(tx["asset_id"])
            s["ticker"] = ticker

    for tx in _sequencia(transactions, _liquidar_reorganizacoes(events or [])):
        if tx["type"] == "evento":
            _aplicar_evento(tx, state, grupos, alerts, nomes)
            continue
        asset_id = str(tx["asset_id"])
        tx_type  = str(tx["type"]).lower()
        qty      = Decimal(str(tx["quantity"]))
        price    = Decimal(str(tx["unit_price"]))
        fees     = Decimal(str(tx.get("fees") or "0"))

        s = state[chave(tx)]
        s["user_id"]  = str(tx["user_id"])

        if tx_type == "buy":
            buy_cost = qty * price + fees
            new_qty  = s["qty"] + qty
            if new_qty > 0:
                s["avg_price"] = (s["qty"] * s["avg_price"] + buy_cost) / new_qty
            s["qty"] = new_qty
        elif tx_type == "sell":
            new_qty = s["qty"] - qty
            if new_qty < Decimal("-0.0001"):
                # Venda sem cobertura: o relatorio de Negociacao da B3 comeca
                # em nov/2019, entao ativo comprado antes disso chega aqui so
                # com a venda. Deixar a quantidade NEGATIVA envenenava a
                # compra seguinte -- em `(qty*avg + custo) / novo_qty` o termo
                # `qty*avg` vira credito e afunda a media. Foi assim que 747
                # cotas de BBAS3 ficaram com R$ 0,000245 de preco medio, um
                # numero positivo que escapava do guarda `avg <= 0` no fim.
                #
                # O que sabemos e que o historico esta truncado, nao que o
                # investidor ficou devendo acoes. Zerar quantidade e base de
                # custo assume o minimo defensavel: o pedaco que conhecemos
                # acabou ali. Se sobrara posicao anterior ao corte, a
                # quantidade recalculada fica ABAIXO da posicao real e a
                # cobertura em `core/investimentos.py` reprova o PM sozinha.
                alerts.append({
                    "asset_id": asset_id,
                    "ticker":   s["ticker"],
                    "type":     "quantidade_negativa",
                    "detail":   f"venda sem cobertura (qty antes={s['qty']}, venda={qty})",
                })
                new_qty = Decimal("0")
            s["qty"] = new_qty
            if s["qty"] <= Decimal("0.0001"):
                # Posicao zerada: a proxima compra abre base de custo nova.
                s["qty"]       = Decimal("0")
                s["avg_price"] = Decimal("0")
        else:
            alerts.append({
                "asset_id": asset_id,
                "ticker":   s["ticker"],
                "type":     "tipo_desconhecido",
                "detail":   f"type='{tx_type}' ignorado",
            })

    positions: list[dict] = []
    for s in state.values():
        asset_id = s["asset_id"]
        qty = s["qty"]
        avg = s["avg_price"]
        if qty <= Decimal("0.0001"):
            continue
        if asset_id is None:
            alerts.append({"asset_id": None, "ticker": s["ticker"], "type": "posicao_sem_ativo",
                           "detail": "código novo sem cadastro em assets; posição não gravada"})
            continue
        if avg <= Decimal("0"):
            alerts.append({
                "asset_id": asset_id,
                "ticker":   s["ticker"],
                "type":     "preco_medio_invalido",
                "detail":   "qty positiva mas avg_price <= 0 — provavelmente vendas sem cobertura",
            })
            continue

        q  = qty.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
        ap = avg.quantize(Decimal("0.000001"),   rounding=ROUND_HALF_UP)
        ti = (q * ap).quantize(Decimal("0.01"),  rounding=ROUND_HALF_UP)
        positions.append({
            "asset_id":       asset_id,
            "user_id":        s["user_id"],
            "ticker":         s["ticker"],
            "quantity":       q,
            "average_price":  ap,
            "total_invested": ti,
        })

    return positions, alerts


# ─────────────────────────────────────────────────────────────────────────────
# Acesso ao banco
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_portfolio(conn: Connection, user_id: str) -> str:
    row = conn.execute(
        text("""
            SELECT id FROM portfolios
            WHERE user_id = :uid AND name = :name
            LIMIT 1
        """),
        {"uid": user_id, "name": PORTFOLIO_NAME},
    ).fetchone()
    if row:
        return str(row[0])

    row = conn.execute(
        text("""
            INSERT INTO portfolios (user_id, name, type, active)
            VALUES (:uid, :name, :type, TRUE)
            RETURNING id
        """),
        {"uid": user_id, "name": PORTFOLIO_NAME, "type": PORTFOLIO_TYPE},
    ).fetchone()
    return str(row[0])


def _load_transactions(conn: Connection, user_id: str) -> list[dict]:
    rows = conn.execute(
        text("""
            SELECT
                it.user_id, it.asset_id, it.type,
                it.quantity, it.unit_price, it.fees, it.transaction_date,
                a.ticker
            FROM investment_transactions it
            JOIN assets a ON a.id = it.asset_id
            WHERE it.user_id = :uid
            ORDER BY it.transaction_date ASC, it.created_at ASC
        """),
        {"uid": user_id},
    ).fetchall()
    return [
        {
            "user_id":          str(r[0]),
            "asset_id":         str(r[1]),
            "type":             r[2],
            "quantity":         r[3],
            "unit_price":       r[4],
            "fees":             r[5] or 0,
            "transaction_date": r[6],
            "ticker":           r[7],
        }
        for r in rows
    ]


def _load_events(conn: Connection, user_id: str) -> list[dict]:
    """Eventos da Movimentação; lista vazia se a tabela ainda não existe.

    A tabela nasce no primeiro upload da Movimentação (SQL 075). `to_regclass`
    pergunta sem falhar: um SELECT numa tabela ausente abortaria a transação
    do recálculo inteiro no Postgres, e o UPSERT das posições iria junto.
    """
    existe = conn.execute(
        text("SELECT to_regclass('investment_movement_events') IS NOT NULL")
    ).scalar()
    if not existe:
        return []
    rows = conn.execute(
        text("""
            SELECT e.event_date, e.movement, e.direction, e.ticker,
                   e.quantity, e.unit_price, e.total_value, e.product, a.id
            FROM investment_movement_events e
            LEFT JOIN assets a ON upper(a.ticker) = upper(e.ticker)
            WHERE e.user_id = :uid
              AND e.movement = ANY(:movs)
            ORDER BY e.event_date ASC, e.id ASC
        """),
        {"uid": user_id, "movs": sorted(EVENTOS_CARREGADOS)},
    ).fetchall()
    return [
        {
            "event_date":  r[0],
            "movement":    r[1],
            "direction":   r[2],
            "ticker":      r[3],
            "quantity":    r[4],
            "unit_price":  r[5],
            "total_value": r[6],
            "product":     r[7] if len(r) > 7 else None,
            "asset_id":    str(r[8]) if len(r) > 8 and r[8] is not None else None,
        }
        for r in rows
    ]


def _upsert(
    conn: Connection,
    positions: list[dict],
    portfolio_id: str,
    user_id: str,
) -> int:
    """UPSERT em lote via executemany do psycopg2.

    Otimização (2026-05-22): antes fazia 1 INSERT por posição. Em prod
    (Streamlit Cloud US ↔ Supabase sa-east-1) cada round-trip leva ~200ms,
    então 50 posições viravam ~10s. Com executemany, vai pra ~1 round-trip.
    """
    rows = [
        {
            "id":  str(uuid.uuid4()),
            "uid": user_id,
            "pid": portfolio_id,
            "aid": pos["asset_id"],
            "qty": str(pos["quantity"]),
            "ap":  str(pos["average_price"]),
            "ti":  str(pos["total_invested"]),
        }
        for pos in positions
        if pos["quantity"] > 0
    ]
    if not rows:
        return 0
    conn.execute(
        text("""
            INSERT INTO portfolio_positions
                (id, user_id, portfolio_id, asset_id,
                 quantity, average_price, total_invested)
            VALUES
                (:id, :uid, :pid, :aid, :qty, :ap, :ti)
            ON CONFLICT (portfolio_id, asset_id) DO UPDATE SET
                quantity       = EXCLUDED.quantity,
                average_price  = EXCLUDED.average_price,
                total_invested = EXCLUDED.total_invested,
                updated_at     = NOW()
        """),
        rows,
    )
    return len(rows)


def _remover_obsoletas(
    conn: Connection,
    portfolio_id: str,
    user_id: str,
    positions: list[dict],
) -> int:
    """Apaga da carteira as posições que o recálculo não produziu.

    O UPSERT sozinho só sabe escrever. Ativo que deixou de qualificar --
    vendido por inteiro, ou com preço médio inválido -- permanecia na tabela
    com o valor da última vez em que qualificou, e o app seguia publicando
    esse fóssil. BBAS3 ficou meses assim: 747 cotas a R$ 0,18 no total,
    vindas de um recálculo antigo, enquanto o cálculo atual nem gerava a
    linha.

    Escopo estreito de propósito: só esta carteira, só este usuário.
    """
    aids = [str(p["asset_id"]) for p in positions if p["quantity"] > 0]
    if aids:
        res = conn.execute(
            text("""
                DELETE FROM portfolio_positions
                WHERE portfolio_id = :pid
                  AND user_id      = :uid
                  AND NOT (asset_id::text = ANY(:aids))
            """),
            {"pid": portfolio_id, "uid": user_id, "aids": aids},
        )
    else:
        res = conn.execute(
            text("""
                DELETE FROM portfolio_positions
                WHERE portfolio_id = :pid
                  AND user_id      = :uid
            """),
            {"pid": portfolio_id, "uid": user_id},
        )
    return int(res.rowcount or 0)


# ─────────────────────────────────────────────────────────────────────────────
# API pública
# ─────────────────────────────────────────────────────────────────────────────

def recompute_for_user(engine: Engine, user_id: str) -> dict[str, Any]:
    """
    Recalcula portfolio_positions para um usuário a partir de todas as
    investment_transactions atuais.

    Retorna dict com:
      - ok: bool
      - transactions_loaded: int
      - events_loaded: int (eventos corporativos da Movimentação aplicados)
      - positions_upserted: int
      - positions_deleted: int
      - alerts: list[str] (resumido, ≤10 itens)
      - error: str | None
    """
    summary: dict[str, Any] = {
        "ok":                  False,
        "transactions_loaded": 0,
        "events_loaded":       0,
        "positions_upserted":  0,
        "positions_deleted":   0,
        "alerts":              [],
        "error":               None,
    }

    try:
        with engine.connect() as conn:
            with conn.begin():
                portfolio_id = _ensure_portfolio(conn, user_id)
                transactions = _load_transactions(conn, user_id)
                events = _load_events(conn, user_id)
                summary["transactions_loaded"] = len(transactions)
                summary["events_loaded"] = len(events)

                if not transactions:
                    summary["ok"] = True
                    return summary

                positions, alerts = _compute(transactions, events)
                upserted = _upsert(conn, positions, portfolio_id, user_id)
                summary["positions_upserted"] = upserted
                summary["positions_deleted"] = _remover_obsoletas(
                    conn, portfolio_id, user_id, positions
                )

                # Resume alertas: por tipo + ticker, máximo 10
                if alerts:
                    by_type: dict[str, dict[str, int]] = defaultdict(
                        lambda: defaultdict(int)
                    )
                    for a in alerts:
                        by_type[a["type"]][a.get("ticker") or "?"] += 1
                    msgs: list[str] = []
                    for kind, tickers in by_type.items():
                        for ticker, count in list(tickers.items())[:5]:
                            msgs.append(f"{kind}: {ticker} ({count}x)")
                    summary["alerts"] = msgs[:10]

        summary["ok"] = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("recompute_for_user falhou: %s", exc)
        summary["error"] = f"{type(exc).__name__}: {exc}"
    return summary
