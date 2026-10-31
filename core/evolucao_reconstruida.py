"""
core/evolucao_reconstruida.py
Evolução Patrimonial de quem não tem foto de custódia.

A curva preferencial vem das fotos (Posição Detalhada da B3, consolidado da
corretora). Quem só subiu os extratos de Negociação e Movimentação, e no
máximo o relatório do Tesouro, ficava com um ponto só: em 12/10/2026 a
ADELAIDE tinha 494 negociações de 01/2020 a 10/2026 e a tela mostrava
"out/26". Aqui a carteira é remontada no fim de cada mês com o mesmo motor
do recálculo de posições (custo médio, desdobramentos, trocas de código,
liquidações) e cada posição é avaliada pelo fechamento bruto da B3 no último
pregão do mês (``data/public/precos_mensais_b3.json.gz``).

Sem fechamento no mês (ou nos ``HERANCA_MESES`` anteriores), a posição entra
pelo custo e o ticker vai para ``sem_preco`` do ponto -- nunca some e nunca
herda preço retroajustado. Renda fixa sem cotação (LCI, CDB) entra pelo custo.
"""
from __future__ import annotations

import calendar
import logging
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

ARQUIVO_PRECOS = (Path(__file__).resolve().parents[1]
                  / "data" / "public" / "precos_mensais_b3.json.gz")
# Ticker sem pregão num mês (suspensão, baixa liquidez) herda o fechamento de
# até dois meses antes; mais que isso é preço velho demais para patrimônio.
HERANCA_MESES = 2
# Classes cotadas em bolsa: sem fechamento, o ticker é nomeado como sem preço.
# As outras (renda fixa, fundos) entram pelo custo por método, não por falta.
CLASSES_COTADAS = frozenset({"stock", "reit", "etf", "fii", "bdr", "fiagro"})


def _indice(ano: int, mes: int) -> int:
    return ano * 12 + mes - 1


def carregar_precos() -> dict:
    """``{ticker: (indice_inicial, [fechamentos])}``; ``{}`` sem o arquivo."""
    from core.inteligencia_ativos import arquivo_publicado

    art = arquivo_publicado.ler(str(ARQUIVO_PRECOS), "precos_mensais_b3") or {}
    precos = {}
    for tk, (inicio, serie) in (art.get("por_ticker") or {}).items():
        ano, mes = (int(p) for p in str(inicio).split("-")[:2])
        precos[tk] = (_indice(ano, mes), serie)
    return precos


def fechamento(precos: dict, ticker: str, ano: int, mes: int) -> float | None:
    """Fechamento do mês, herdando até ``HERANCA_MESES`` meses; senão ``None``."""
    item = precos.get(str(ticker or "").strip().upper())
    if not item:
        return None
    inicio, serie = item
    alvo = _indice(ano, mes) - inicio
    for i in range(alvo, alvo - HERANCA_MESES - 1, -1):
        if 0 <= i < len(serie) and serie[i]:
            return float(serie[i])
    return None


def fim_do_mes(ano: int, mes: int) -> date:
    return date(ano, mes, calendar.monthrange(ano, mes)[1])


def meses_entre(primeiro: date, ultimo_exclusivo: date) -> list[tuple[int, int]]:
    """(ano, mês) de ``primeiro`` até o mês anterior a ``ultimo_exclusivo``."""
    i, fim = _indice(primeiro.year, primeiro.month), _indice(ultimo_exclusivo.year,
                                                             ultimo_exclusivo.month)
    return [(k // 12, k % 12 + 1) for k in range(i, fim)]


def _data(v) -> date | None:
    if v is None:
        return None
    if hasattr(v, "date") and callable(v.date):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def reconstruir(transactions: list[dict], events: list[dict], ativos: dict,
                precos: dict, meses: list[tuple[int, int]],
                excluir: set[str] | frozenset = frozenset()) -> list[dict]:
    """Puro. Um ponto por mês: carteira em BRL no fim do mês.

    ``ativos``: ``{asset_id: {"ticker", "class", "currency"}}``. Ativo em
    outra moeda fica de fora (o exterior entra por ``_exterior_nas_datas``),
    assim como os de ``excluir`` (já cobertos por foto, como o Tesouro).

    Cada ponto: ``mes`` (último dia do mês), ``valor_mercado``,
    ``valor_investido`` (custo médio das posições abertas), ``sem_preco``
    (tickers cotados avaliados pelo custo) e ``a_custo`` (renda fixa e
    afins, pelo custo por método).
    """
    from data_pipeline.importers.investments.positions import _base, _compute

    tx = sorted(((d, t) for t in transactions
                 if (d := _data(t.get("transaction_date"))) is not None),
                key=lambda x: x[0])
    ev = sorted(((d, e) for e in events
                 if (d := _data(e.get("event_date"))) is not None),
                key=lambda x: x[0])
    pontos = []
    for ano, mes in meses:
        corte = fim_do_mes(ano, mes)
        tx_ate = [t for d, t in tx if d <= corte]
        if not tx_ate:
            continue
        posicoes, _ = _compute(tx_ate, [e for d, e in ev if d <= corte])
        vm = vi = 0.0
        sem_preco, a_custo = [], []
        for p in posicoes:
            aid = str(p["asset_id"])
            if aid in excluir:
                continue
            ativo = ativos.get(aid) or {}
            if str(ativo.get("currency") or "BRL").upper() != "BRL":
                continue
            custo = float(p["total_invested"])
            qtd = float(p["quantity"])
            ticker = _base(str(p.get("ticker") or ativo.get("ticker") or ""))
            preco = fechamento(precos, ticker, ano, mes)
            vi += custo
            if preco is not None:
                vm += qtd * preco
            else:
                vm += custo
                classe = str(ativo.get("class") or "").lower()
                (sem_preco if classe in CLASSES_COTADAS or not classe
                 else a_custo).append(ticker)
        pontos.append({"mes": corte, "valor_mercado": round(vm, 2),
                       "valor_investido": round(vi, 2),
                       "sem_preco": sorted(set(sem_preco)),
                       "a_custo": sorted(set(a_custo))})
    return pontos


def _sem_acento(v) -> str:
    import unicodedata

    t = unicodedata.normalize("NFKD", str(v or "")).encode("ascii", "ignore").decode()
    return t.strip().lower()


# Movimentação do Tesouro: compra soma, venda/resgate/vencimento baixa pelo
# custo médio. Transferência entre corretoras vem em par (crédito e débito do
# mesmo título no mesmo dia) e não muda a posição; fica de fora.
_TD_ENTRA = frozenset({"compra"})
_TD_SAI = frozenset({"venda", "resgate", "vencimento", "resgate antecipado"})


def reconstruir_tesouro(movimentos: list[dict], pus: dict,
                        meses: list[tuple[int, int]]) -> dict:
    """Puro. ``{(ano, mes): {"vm", "vi", "a_custo"}}`` do Tesouro Direto.

    ``movimentos``: event_date, movement, chave (TIPCA2029...), quantity,
    total_value. ``pus``: ``{(chave, ano, mes): PU}`` do último dia do mês com
    taxa publicada. Título sem PU no mês entra pelo custo e é nomeado em
    ``a_custo``.
    """
    movs = sorted(((d, m) for m in movimentos
                   if (d := _data(m.get("event_date"))) is not None),
                  key=lambda x: x[0])
    carteira: dict[str, list[float]] = {}  # chave -> [qtd, custo]
    resultado = {}
    i = 0
    for ano, mes in meses:
        corte = fim_do_mes(ano, mes)
        while i < len(movs) and movs[i][0] <= corte:
            m = movs[i][1]
            i += 1
            tipo = _sem_acento(m.get("movement"))
            qtd = float(m.get("quantity") or 0)
            if qtd <= 0:
                continue
            pos = carteira.setdefault(m["chave"], [0.0, 0.0])
            if tipo in _TD_ENTRA:
                pos[0] += qtd
                pos[1] += float(m.get("total_value") or 0)
            elif tipo in _TD_SAI and pos[0] > 0:
                fracao = min(qtd, pos[0]) / pos[0]
                pos[1] -= pos[1] * fracao
                pos[0] = max(pos[0] - qtd, 0.0)
        vm = vi = 0.0
        a_custo = []
        for chave, (qtd, custo) in sorted(carteira.items()):
            if qtd <= 1e-6:
                continue
            vi += custo
            pu = pus.get((chave, ano, mes))
            if pu:
                vm += qtd * pu
            else:
                vm += custo
                a_custo.append(chave)
        if vi or vm:
            resultado[(ano, mes)] = {"vm": round(vm, 2), "vi": round(vi, 2),
                                     "a_custo": a_custo}
    return resultado


_SQL_TD_MOVIMENTOS = """
    SELECT event_date, movement, product, quantity, total_value
    FROM investment_movement_events
    WHERE user_id = :uid AND product ILIKE 'tesouro%'
    ORDER BY event_date, id
"""
# PU de resgate (o que o investidor recebe), o mesmo que a foto do Tesouro
# usa como valor de mercado. Último dia com taxa publicada em cada mês.
_SQL_TD_PUS = """
    SELECT DISTINCT ON (security_key, date_trunc('month', base_date))
           security_key, base_date, COALESCE(sell_pu, base_pu) AS pu
    FROM tesouro_market_rates
    WHERE security_key = ANY(:chaves) AND COALESCE(sell_pu, base_pu) > 0
    ORDER BY security_key, date_trunc('month', base_date), base_date DESC
"""
# Mês com foto do Tesouro: a foto manda no Tesouro daquele mês.
_SQL_TD_FOTOS = """
    WITH td AS (
        SELECT report_date, market_value, invested_value
        FROM portfolio_position_snapshots
        WHERE user_id = :uid
          AND (source_id LIKE 'td-snap-%' OR source_table = 'tesouro_direto')
    ), ultima AS (
        SELECT MAX(report_date) AS d FROM td GROUP BY date_trunc('month', report_date)
    )
    SELECT td.report_date, SUM(td.market_value) AS vm, SUM(td.invested_value) AS vi
    FROM td JOIN ultima ON td.report_date = ultima.d
    GROUP BY td.report_date
"""


def _tesouro_do_banco(conn, owner: str, meses: list[tuple[int, int]]) -> dict:
    from sqlalchemy import text

    from data_pipeline.importers.investments.tesouro_direto import tesouro_security_key

    movimentos = [
        {"event_date": r.event_date, "movement": r.movement,
         "chave": tesouro_security_key(r.product, r.product),
         "quantity": r.quantity, "total_value": r.total_value}
        for r in conn.execute(text(_SQL_TD_MOVIMENTOS), {"uid": owner}).fetchall()
    ]
    pus = {}
    chaves = sorted({m["chave"] for m in movimentos})
    if chaves:
        for r in conn.execute(text(_SQL_TD_PUS), {"chaves": chaves}).fetchall():
            d = _data(r.base_date)
            pus[(str(r.security_key), d.year, d.month)] = float(r.pu)
    resultado = reconstruir_tesouro(movimentos, pus, meses)
    for r in conn.execute(text(_SQL_TD_FOTOS), {"uid": owner}).fetchall():
        d = _data(r.report_date)
        if (d.year, d.month) in set(meses):
            resultado[(d.year, d.month)] = {
                "vm": round(float(r.vm or 0), 2),
                "vi": round(float(r.vi or 0), 2) if r.vi is not None else None,
                "a_custo": []}
    return resultado


_SQL_ATIVOS = """
    SELECT id, ticker, class, currency FROM assets WHERE id::text = ANY(:ids)
"""
_SQL_ATIVOS_EM_FOTO = """
    SELECT DISTINCT asset_id::text FROM portfolio_position_snapshots
    WHERE user_id = :uid AND asset_id IS NOT NULL
"""


def reconstruir_do_banco(conn, owner: str, hoje: date | None = None) -> list[dict] | None:
    """Pontos do primeiro mês com negociação até o mês anterior ao corrente.

    ``None`` quando não há como reconstruir (sem negociação ou sem o arquivo
    de preços): quem chama mantém a curva que tinha.
    """
    from sqlalchemy import text

    from data_pipeline.importers.investments.positions import (
        _load_events,
        _load_transactions,
    )

    precos = carregar_precos()
    if not precos:
        return None
    transactions = _load_transactions(conn, owner)
    datas = [d for t in transactions if (d := _data(t.get("transaction_date")))]
    if not datas:
        return None
    events = _load_events(conn, owner)
    ids = sorted({str(t["asset_id"]) for t in transactions}
                 | {str(e["asset_id"]) for e in events if e.get("asset_id")})
    ativos = {str(r["id"]): {"ticker": r["ticker"], "class": r["class"],
                             "currency": r["currency"]}
              for r in conn.execute(text(_SQL_ATIVOS), {"ids": ids}).mappings()}
    excluir = {str(r[0]) for r in conn.execute(text(_SQL_ATIVOS_EM_FOTO),
                                               {"uid": owner}).fetchall()}
    meses = meses_entre(min(datas), hoje or date.today())
    pontos = reconstruir(transactions, events, ativos, precos, meses, excluir)
    try:
        with conn.begin_nested():  # falha aqui não aborta o resto da curva
            tesouro = _tesouro_do_banco(conn, owner, meses)
    except Exception as exc:  # sem Tesouro, a curva segue com o resto
        logger.warning("[evolucao] Tesouro histórico indisponível (%s).",
                       type(exc).__name__)
        tesouro = None
    for p in pontos:
        td = (tesouro or {}).get((p["mes"].year, p["mes"].month))
        if td:
            p["valor_mercado"] = round(p["valor_mercado"] + td["vm"], 2)
            p["valor_investido"] = (round(p["valor_investido"] + td["vi"], 2)
                                    if td["vi"] is not None else None)
            p["a_custo"] = sorted(set(p["a_custo"]) | set(td["a_custo"]))
        p["tesouro_ok"] = tesouro is not None
    return pontos
