"""Detalhe por empresa americana que só o armazém local tem.

A vitrine do Supabase (``company_snapshots``) guarda um resumo por empresa: o
bastante para pontuar e montar carteira, pouco para a LLM responder "como foi o
último trimestre da AAPL" ou "quanto a ação caiu do topo". O armazém tem o
preço diário (2,9 GB), os demonstrativos trimestrais e o histórico de proventos;
este módulo lê um recorte curto disso para os tickers que a pergunta cita.

Duas metades, separadas de propósito:

* :func:`ler_detalhe` é SQL e roda onde o armazém está -- direto no
  desenvolvimento, ou no servidor do túnel (``scripts/servir_armazem_leitura``).
  Devolve linhas cruas, sem conta.
* :func:`resumo_para_prompt` faz as contas e roda sempre no app. Assim a
  fórmula é uma só: o PC servindo uma versão velha do código não muda o número
  que a LLM lê (mesma regra das notícias por ativo).
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta

#: Tetos do recorte. ~400 dias corridos cobrem 252 pregões com folga para o
#: retorno de 12 meses; 8 trimestres dão a comparação ano contra ano de 4.
DIAS_PRECO = 400
TRIMESTRES = 8
EXERCICIOS = 3
ANOS_PROVENTOS = 5
SIMBOLOS_MAX = 10

_CONTAS = ("revenue", "gross_profit", "operating_income", "net_income",
           "eps_diluted", "operating_cash_flow", "capex", "free_cash_flow")

_SQL_DEMONSTRATIVOS = """
SELECT i.symbol, i.period, i.fiscal_year, i.fiscal_quarter, i.reference_date,
       i.available_at, i.revenue, i.gross_profit, i.operating_income,
       i.net_income, i.eps_diluted, c.operating_cash_flow, c.capex,
       c.free_cash_flow, i.updated_at
FROM market_us.income_statements i
LEFT JOIN market_us.cash_flow_statements c
  ON c.company_id = i.company_id AND c.period = i.period
 AND c.fiscal_year = i.fiscal_year AND c.fiscal_quarter = i.fiscal_quarter
WHERE i.symbol = ANY(:s) AND i.available_at <= :hoje
  AND i.quality_status = 'validated'
  AND i.reference_date >= :desde
"""

_SQL_PRECOS = """
SELECT symbol, date, COALESCE(adjusted_close, close) AS preco, close, volume
FROM market_us.prices_daily
WHERE symbol = ANY(:s) AND date >= :desde AND date <= :hoje
ORDER BY symbol, date
"""

_SQL_PROVENTOS = """
SELECT symbol, ex_date, COALESCE(adjusted_amount, amount) AS valor, ingested_at
FROM market_us.dividends
WHERE symbol = ANY(:s) AND ex_date >= :desde AND ex_date <= :hoje
ORDER BY symbol, ex_date
"""


def normalizar_simbolos(simbolos) -> list[str]:
    return list(dict.fromkeys(
        str(s).strip().upper() for s in (simbolos or []) if str(s).strip()))


def ler_detalhe(engine, simbolos, *, hoje: date | None = None) -> dict[str, dict]:
    """Linhas cruas por símbolo: ``precos``, ``trimestres``, ``exercicios``, ``proventos``.

    Símbolo sem nenhuma linha volta com listas vazias -- ausência também é
    resposta, e o resumo diz que o armazém não tem, em vez de omitir.
    """
    from sqlalchemy import text

    alvo = normalizar_simbolos(simbolos)[:SIMBOLOS_MAX]
    hoje = hoje or date.today()
    saida = {s: {"precos": [], "trimestres": [], "exercicios": [], "proventos": []}
             for s in alvo}
    if not alvo:
        return saida
    with engine.connect() as conn:
        demo = conn.execute(text(_SQL_DEMONSTRATIVOS), {
            "s": alvo, "hoje": hoje,
            "desde": hoje - timedelta(days=366 * (EXERCICIOS + 1))}).mappings().all()
        precos = conn.execute(text(_SQL_PRECOS), {
            "s": alvo, "hoje": hoje,
            "desde": hoje - timedelta(days=DIAS_PRECO)}).mappings().all()
        prov = conn.execute(text(_SQL_PROVENTOS), {
            "s": alvo, "hoje": hoje,
            "desde": hoje - timedelta(days=366 * ANOS_PROVENTOS)}).mappings().all()

    # Mais de uma linha por período só acontece com company_id duplicado para o
    # mesmo símbolo; fica a revisão mais recente.
    por_periodo: dict[tuple, dict] = {}
    for r in demo:
        chave = (r["symbol"], r["period"], r["fiscal_year"], r["fiscal_quarter"])
        atual = por_periodo.get(chave)
        if atual is None or (r["updated_at"] or datetime.min) > (atual["updated_at"] or datetime.min):
            por_periodo[chave] = dict(r)
    for (sym, period, _fy, _fq), r in sorted(
            por_periodo.items(), key=lambda kv: kv[1]["reference_date"], reverse=True):
        linha = {k: r[k] for k in ("fiscal_year", "fiscal_quarter", "reference_date",
                                   "available_at", *_CONTAS)}
        destino = saida[sym]["trimestres" if period == "quarterly" else "exercicios"]
        if len(destino) < (TRIMESTRES if period == "quarterly" else EXERCICIOS):
            destino.append(linha)
    for r in precos:
        saida[r["symbol"]]["precos"].append(
            {"date": r["date"], "preco": r["preco"], "close": r["close"], "volume": r["volume"]})
    # A chave única dos proventos inclui o valor: a mesma data-ex pode vir duas
    # vezes com valores diferentes (fontes que divergem). Fica a última gravada.
    ultimos: dict[tuple, dict] = {}
    for r in prov:
        chave = (r["symbol"], r["ex_date"])
        atual = ultimos.get(chave)
        if atual is None or (r["ingested_at"] or datetime.min) >= (atual["ingested_at"] or datetime.min):
            ultimos[chave] = dict(r)
    for (sym, ex), r in sorted(ultimos.items()):
        saida[sym]["proventos"].append({"ex_date": ex, "valor": r["valor"]})
    return saida


# ─────────────────────────────────────────────────────────────────────────────
# Resumo (roda no app)
# ─────────────────────────────────────────────────────────────────────────────

def _data(valor) -> date | None:
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    try:
        return date.fromisoformat(str(valor)[:10])
    except ValueError:
        return None


def _num(valor) -> float | None:
    try:
        x = float(valor)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _usd(x: float | None) -> str:
    if x is None:
        return "n/d"
    a = abs(x)
    if a >= 1e9:
        return f"{x / 1e9:,.2f} bi"
    if a >= 1e6:
        return f"{x / 1e6:,.1f} mi"
    return f"{x:,.0f}"


def _pct(x: float | None) -> str:
    return "n/d" if x is None else f"{x:+.1%}"


def _variacao(novo: float | None, velho: float | None) -> float | None:
    if novo is None or velho is None or velho <= 0:
        return None
    return novo / velho - 1


def resumo_precos(precos: list[dict]) -> dict | None:
    serie = [(d, p) for d, p in ((_data(r.get("date")), _num(r.get("preco"))) for r in precos)
             if d is not None and p is not None and p > 0]
    serie.sort()
    if len(serie) < 2:
        return None
    ultimo_dia, ultimo = serie[-1]
    valores = [p for _, p in serie]

    def retorno(pregoes: int) -> float | None:
        return _variacao(ultimo, valores[-1 - pregoes]) if len(valores) > pregoes else None

    ano = valores[-253:]
    pico, dd = ano[0], 0.0
    for p in ano:
        pico = max(pico, p)
        dd = min(dd, p / pico - 1)
    logs = [math.log(b / a) for a, b in zip(ano, ano[1:])]
    vol = None
    if len(logs) >= 20:
        media = sum(logs) / len(logs)
        vol = math.sqrt(sum((x - media) ** 2 for x in logs) / (len(logs) - 1) * 252)
    giros = [c * v for c, v in ((_num(r.get("close")), _num(r.get("volume")))
                                for r in precos[-63:]) if c is not None and v is not None]
    return {
        "ultimo_dia": ultimo_dia, "ultimo": ultimo, "pregoes": len(valores),
        "r1m": retorno(21), "r3m": retorno(63), "r6m": retorno(126), "r12m": retorno(252),
        "max52": max(ano), "min52": min(ano), "do_topo": ultimo / max(ano) - 1,
        "dd12m": dd, "vol": vol,
        "giro3m": sum(giros) / len(giros) if giros else None,
    }


def resumo_proventos(proventos: list[dict], hoje: date) -> dict:
    datas = [(_data(r.get("ex_date")), _num(r.get("valor"))) for r in proventos]
    datas = [(d, v) for d, v in datas if d is not None and v is not None and v > 0]
    corte = hoje - timedelta(days=365)
    ultimos12 = [v for d, v in datas if d > corte]
    anos = {d.year for d, _ in datas}
    seguidos, ano = 0, hoje.year - 1
    while ano in anos:
        seguidos, ano = seguidos + 1, ano - 1
    return {"soma12m": sum(ultimos12), "pagamentos12m": len(ultimos12),
            "anos_seguidos": seguidos,
            "ultimo": max(datas)[0] if datas else None,
            "ultimo_valor": max(datas)[1] if datas else None}


def _linha_trimestre(t: dict, anterior: dict | None) -> str:
    receita, lucro = _num(t.get("revenue")), _num(t.get("net_income"))
    margem = lucro / receita if receita and lucro is not None else None
    ref = _data(t.get("reference_date"))
    rotulo = f"FY{t.get('fiscal_year')}Q{t.get('fiscal_quarter')}"
    partes = [f"{rotulo} ({ref:%d/%m/%Y})" if ref else rotulo,
              f"receita {_usd(receita)}", f"lucro {_usd(lucro)}",
              f"margem líq. {'n/d' if margem is None else f'{margem:.1%}'}",
              f"FCF {_usd(_num(t.get('free_cash_flow')))}"]
    if anterior is not None:
        partes.append("a/a receita " + _pct(_variacao(receita, _num(anterior.get("revenue")))))
        la, lb = lucro, _num(anterior.get("net_income"))
        if la is not None and lb is not None and lb > 0:
            partes.append("a/a lucro " + _pct(la / lb - 1))
    return "; ".join(partes)


def resumo_para_prompt(detalhe: dict[str, dict], *, origem: str,
                       hoje: date | None = None) -> str:
    """Bloco de texto para o prompt, uma seção por ticker."""
    hoje = hoje or date.today()
    if not detalhe:
        return ""
    linhas = [f"DETALHE DO ARMAZÉM LOCAL ({origem}) — preço diário, trimestres e "
              "proventos que a vitrine não guarda. Números calculados em código."]
    for sym, d in detalhe.items():
        linhas.append(f"  {sym}:")
        p = resumo_precos(d.get("precos") or [])
        if p is None:
            linhas.append("    Preço diário: o armazém não tem série recente deste ticker.")
        else:
            idade = (hoje - p["ultimo_dia"]).days
            vol = "n/d" if p["vol"] is None else f"{p['vol']:.0%}"
            linhas.append(
                f"    Preço (ajustado, pregão de {p['ultimo_dia']:%d/%m/%Y}"
                + (f", {idade} dias atrás" if idade > 4 else "") + f"): US$ {p['ultimo']:,.2f}; "
                f"retorno 1m {_pct(p['r1m'])}, 3m {_pct(p['r3m'])}, 6m {_pct(p['r6m'])}, "
                f"12m {_pct(p['r12m'])}; faixa 52s {p['min52']:,.2f}–{p['max52']:,.2f} "
                f"({_pct(p['do_topo'])} do topo); queda máx. 12m {_pct(p['dd12m'])}; "
                f"vol. anualizada {vol}; "
                f"giro médio 3m US$ {_usd(p['giro3m'])}/dia.")
        trimestres = d.get("trimestres") or []
        if trimestres:
            por_chave = {(t.get("fiscal_year"), t.get("fiscal_quarter")): t for t in trimestres}
            linhas.append("    Trimestres (mais recente primeiro; a/a = mesmo trimestre do ano fiscal anterior):")
            for t in trimestres[:4]:
                anterior = por_chave.get(((t.get("fiscal_year") or 0) - 1, t.get("fiscal_quarter")))
                linhas.append("      " + _linha_trimestre(t, anterior))
        else:
            linhas.append("    Trimestres: sem demonstrativo trimestral validado no armazém.")
        exercicios = d.get("exercicios") or []
        if exercicios:
            linhas.append("    Exercícios: " + " | ".join(
                f"FY{e.get('fiscal_year')} receita {_usd(_num(e.get('revenue')))}, "
                f"lucro {_usd(_num(e.get('net_income')))}, FCF {_usd(_num(e.get('free_cash_flow')))}"
                for e in exercicios))
        pv = resumo_proventos(d.get("proventos") or [], hoje)
        if pv["ultimo"] is None:
            # O armazém guarda proventos das ações do universo; ETF (SPY, IEFA)
            # fica de fora e "nenhum registrado" leria como "não paga".
            linhas.append(f"    Proventos: nenhum registrado no armazém nos últimos "
                          f"{ANOS_PROVENTOS} anos (ele cobre as ações do universo, "
                          "não ETF nem fundo) -- ausência aqui não prova que não pagou.")
        else:
            rend = ""
            if p is not None and p["ultimo"] > 0 and pv["soma12m"] > 0:
                rend = f" (≈{pv['soma12m'] / p['ultimo']:.2%} do preço)"
            linhas.append(
                f"    Proventos: US$ {pv['soma12m']:,.4f}/ação em 12m{rend}, "
                f"{pv['pagamentos12m']} pagamento(s); último ex em {pv['ultimo']:%d/%m/%Y} "
                f"(US$ {pv['ultimo_valor']:,.4f}); "
                f"pagou em {pv['anos_seguidos']} ano(s) seguido(s) até {hoje.year - 1}.")
    return "\n".join(linhas)
