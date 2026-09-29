"""Detalhe por ação da B3 que só o armazém local tem.

Balanços, métricas, proventos e preço ajustado já estão no Supabase, com mais
cobertura que no armazém -- não entram aqui. O que só o armazém guarda é:

* o pregão diário da B3 (COTAHIST): volume financeiro, número de negócios e
  quantidade, que dizem se o papel tem liquidez para a posição;
* a Memória de Mercado: como o papel reagiu a cada divulgação de resultado
  anual desde 2011 (``memoria_mercado.eventos_medidos``).

Duas metades, separadas de propósito (mesma regra de ``fii_detalhe_armazem``):

* :func:`ler_detalhe` é SQL e roda onde o armazém está -- direto no
  desenvolvimento, ou no servidor do túnel (``scripts/servir_armazem_leitura``).
  Devolve linhas cruas, sem conta.
* :func:`resumo_para_prompt` faz as contas e roda sempre no app, para que o PC
  servindo uma versão velha do código não mude o número que a LLM lê.

O COTAHIST é **bruto**: não desconta provento nem desdobramento. Um grupamento
10:1 aparece como +900 % num dia. O resumo tira do retorno e da volatilidade os
dias com salto acima de :data:`SALTO_SUSPEITO` e diz quantos tirou.
"""
from __future__ import annotations

import math
import statistics
from datetime import date, datetime, timedelta

#: ~400 dias cobrem 12 meses de pregão com folga para o atraso da carga.
DIAS_PREGAO = 400
EVENTOS_POR_TICKER = 6
TICKERS_MAX = 10
#: Variação diária acima disso no preço bruto é quase sempre evento
#: societário (desdobramento, grupamento), não mercado.
SALTO_SUSPEITO = 0.35
#: Série parada há mais que isso vira aviso no prompt.
DIAS_PARADA = 20

#: Preço por ação: ``close_unitario`` já divide pelo fator de cotação.
_PRECO = "COALESCE(close_unitario, close / NULLIF(fator_cotacao, 0))"

_SQL_PREGAO = f"""
SELECT ticker, trade_date, issuer_short_name, {_PRECO} AS preco, trades,
       financial_volume
FROM market.b3_security_history
WHERE ticker = ANY(:t) AND bdi = '02' AND trade_date >= :desde
  AND trade_date <= :hoje
ORDER BY ticker, trade_date
"""

_ULTIMOS_EVENTOS = """
SELECT * FROM (
    SELECT e.*, ROW_NUMBER() OVER (PARTITION BY simbolo
                                   ORDER BY data_evento DESC) AS n
    FROM memoria_mercado.eventos_medidos e
    WHERE simbolo = ANY(:t) AND data_evento <= :hoje
) x
WHERE n <= :n
"""

_SQL_EVENTOS = f"""
SELECT simbolo, data_evento, data_pregao_zero, tipo_evento, drawdown,
       pregoes_ate_o_pior, pregoes_ate_recuperar, recuperacao_observada,
       razao_volume, deriva_pre_evento, janelas
FROM ({_ULTIMOS_EVENTOS}) ev
ORDER BY simbolo, data_evento
"""

#: Saltos de preço bruto em volta de cada evento: é o que diz se o retorno de
#: 60 pregões é mercado ou desdobramento. A defasagem é calculada dentro da
#: janela de cada evento -- entre duas janelas há um ano sem linha, e a
#: variação de um ano inteiro pareceria salto de um dia.
_SQL_SALTOS = f"""
WITH ev AS (
    SELECT simbolo, data_pregao_zero FROM ({_ULTIMOS_EVENTOS}) x
    WHERE data_pregao_zero IS NOT NULL
), precos AS (
    SELECT h.ticker, h.trade_date, ev.data_pregao_zero AS janela,
           COALESCE(h.close_unitario, h.close / NULLIF(h.fator_cotacao, 0)) AS p
    FROM market.b3_security_history h
    JOIN ev ON h.ticker = ev.simbolo
           AND h.trade_date BETWEEN ev.data_pregao_zero - :antes
                                AND ev.data_pregao_zero + :depois
    WHERE h.bdi = '02'
)
SELECT DISTINCT ticker, trade_date, ret FROM (
    SELECT ticker, trade_date,
           p / NULLIF(LAG(p) OVER (PARTITION BY ticker, janela
                                   ORDER BY trade_date), 0) - 1 AS ret
    FROM precos
) y
WHERE abs(ret) > :salto
ORDER BY ticker, trade_date
"""
#: Dias corridos em volta do pregão zero que a checagem de salto cobre: os 20
#: pregões da deriva antes, os 120 da recuperação depois.
_DIAS_ANTES, _DIAS_DEPOIS = 35, 190

#: Só o retorno do próprio papel sai das janelas. O índice de referência da
#: Memória de Mercado é a média equiponderada dos retornos brutos do painel, sem
#: filtro de salto: um grupamento num único papel move o "mercado" inteiro. Ele
#: já mostrou +88 % em 20 pregões (02/2016) e +29 % em 02/2025, quando o
#: Ibovespa andou de lado; retorno anormal e persistência derivam dele.
_HORIZONTES = ("1", "5", "20", "60")


def normalizar_tickers(tickers) -> list[str]:
    return list(dict.fromkeys(
        str(t).strip().upper() for t in (tickers or []) if str(t).strip()))


def ler_detalhe(engine, tickers, *, hoje: date | None = None) -> dict[str, dict]:
    """Linhas cruas por ticker: ``pregoes``, ``eventos`` e ``saltos``.

    Ticker sem nenhuma linha volta com listas vazias -- ausência também é
    resposta, e o resumo diz que o armazém não tem, em vez de omitir.
    """
    from sqlalchemy import text

    alvo = normalizar_tickers(tickers)[:TICKERS_MAX]
    hoje = hoje or date.today()
    saida = {t: {"nome": None, "pregoes": [], "eventos": [], "saltos": []} for t in alvo}
    if not alvo:
        return saida
    with engine.connect() as conn:
        pregoes = conn.execute(text(_SQL_PREGAO), {
            "t": alvo, "hoje": hoje,
            "desde": hoje - timedelta(days=DIAS_PREGAO)}).mappings().all()
        eventos = conn.execute(text(_SQL_EVENTOS), {
            "t": alvo, "hoje": hoje, "n": EVENTOS_POR_TICKER}).mappings().all()
        saltos = conn.execute(text(_SQL_SALTOS), {
            "t": alvo, "hoje": hoje, "n": EVENTOS_POR_TICKER, "salto": SALTO_SUSPEITO,
            "antes": _DIAS_ANTES, "depois": _DIAS_DEPOIS}).mappings().all()

    # Linha curta: são ~250 por papel e tudo passa pelo túnel.
    for r in pregoes:
        saida[r["ticker"]]["nome"] = r["issuer_short_name"] or saida[r["ticker"]]["nome"]
        saida[r["ticker"]]["pregoes"].append(
            {"date": r["trade_date"], "preco": r["preco"], "negocios": r["trades"],
             "volume": r["financial_volume"]})
    for r in saltos:
        saida[r["ticker"]]["saltos"].append({"date": r["trade_date"], "ret": r["ret"]})
    for r in eventos:
        janelas = r["janelas"] if isinstance(r["janelas"], dict) else {}
        saida[r["simbolo"]]["eventos"].append(
            {"data": r["data_evento"], "pregao_zero": r["data_pregao_zero"],
             "tipo": r["tipo_evento"],
             "retornos": {h: (janelas.get(h) or {}).get("retorno_ativo")
                          for h in _HORIZONTES},
             "drawdown": r["drawdown"], "pregoes_ate_o_pior": r["pregoes_ate_o_pior"],
             "pregoes_ate_recuperar": r["pregoes_ate_recuperar"],
             "recuperou": r["recuperacao_observada"],
             "razao_volume": r["razao_volume"], "deriva_pre": r["deriva_pre_evento"]})
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


def _pct(x: float | None, sinal: bool = True) -> str:
    if x is None:
        return "n/d"
    return f"{x:+.1%}" if sinal else f"{x:.1%}"


def _brl(x: float | None) -> str:
    if x is None:
        return "n/d"
    a = abs(x)
    if a >= 1e9:
        return f"R$ {x / 1e9:,.2f} bi"
    if a >= 1e6:
        return f"R$ {x / 1e6:,.1f} mi"
    if a >= 1e3:
        return f"R$ {x / 1e3:,.0f} mil"
    return f"R$ {x:,.2f}"


def _preco(r: dict) -> float | None:
    p = _num(r.get("preco"))
    return p if p is not None and p > 0 else None


def resumo_pregoes(pregoes: list[dict]) -> dict | None:
    """Liquidez, retornos e volatilidade do pregão bruto.

    Retorno composto só com os dias sem salto: é o movimento de mercado, não a
    variação do preço de tela, e por isso não bate com o preço ajustado da
    vitrine quando há provento no meio -- o bloco diz isso.
    """
    serie = sorted(
        ((d, r) for d, r in ((_data(r.get("date")), r) for r in pregoes) if d is not None),
        key=lambda x: x[0])
    if not serie:
        return None
    ultimo_dia, ultima = serie[-1]
    diarios: list[tuple[date, float]] = []
    saltos: list[tuple[date, float]] = []
    anterior = None
    for d, r in serie:
        p = _preco(r)
        if p is None:
            continue
        if anterior is not None:
            ret = p / anterior - 1
            (saltos if abs(ret) > SALTO_SUSPEITO else diarios).append((d, ret))
        anterior = p

    def composto(dias: int) -> float | None:
        corte = ultimo_dia - timedelta(days=dias)
        if serie[0][0] > corte + timedelta(days=7):
            return None  # série não alcança o começo da janela
        nivel = 1.0
        for d, ret in diarios:
            if d > corte:
                nivel *= 1 + ret
        return nivel - 1

    ano = [ret for d, ret in diarios if d > ultimo_dia - timedelta(days=365)]
    vol = statistics.stdev(ano) * math.sqrt(252) if len(ano) >= 20 else None
    recentes = [r for d, r in serie if d > ultimo_dia - timedelta(days=91)]
    ult21 = [r for _, r in serie[-21:]]
    ult63 = [r for _, r in serie[-63:]]

    def mediana(linhas: list[dict], campo: str) -> float | None:
        valores = [v for v in (_num(r.get(campo)) for r in linhas) if v is not None]
        return statistics.median(valores) if valores else None

    return {
        "ultimo_dia": ultimo_dia, "preco": _preco(ultima),
        "vol21": mediana(ult21, "volume"), "vol63": mediana(ult63, "volume"),
        "negocios21": mediana(ult21, "negocios"),
        "pregoes_3m": len(recentes), "pregoes_ano": sum(
            1 for d, _ in serie if d > ultimo_dia - timedelta(days=365)),
        "r1m": composto(30), "r3m": composto(91), "r12m": composto(365),
        "vol_anual": vol, "saltos": saltos,
    }


def _dias_corridos(pregoes: int) -> int:
    """Folga generosa: 5 pregões por semana e alguns feriados."""
    return math.ceil(pregoes * 7 / 5) + 5


def _linha_evento(e: dict, saltos: list[date]) -> str:
    """Uma linha por divulgação; horizonte que atravessa um salto sai.

    O retorno da Memória de Mercado é do preço bruto: um desdobramento 2:1 no
    meio da janela vira "-50 % em 60 pregões". A série do evento não está
    aqui, mas as datas dos saltos estão, e basta saber se uma caiu dentro.
    """
    zero = _data(e.get("pregao_zero")) or _data(e.get("data"))

    def cruza(inicio: int, fim: int) -> bool:
        if zero is None:
            return False
        return any(zero + timedelta(days=inicio) < s <= zero + timedelta(days=fim)
                   for s in saltos)

    ret = e.get("retornos") or {}
    partes, cortados = [], []
    for h in _HORIZONTES:
        valor = _num(ret.get(h))
        if valor is None:
            continue
        if cruza(0, _dias_corridos(int(h))):
            cortados.append(f"{h}d")
        else:
            partes.append(f"{h}d {_pct(valor)}")
    dd, rec = _num(e.get("drawdown")), e.get("recuperou")
    pregoes_rec = _num(e.get("pregoes_ate_recuperar"))
    if dd is not None and cruza(0, _dias_corridos(120)):
        queda = "; pior queda omitida (salto na janela)"
    elif dd is None:
        queda = ""
    elif rec is True and pregoes_rec is not None:
        queda = f"; pior queda {_pct(dd)}, recuperou em {pregoes_rec:.0f} pregões"
    elif rec is False:
        queda = f"; pior queda {_pct(dd)}, não recuperou em 120 pregões"
    else:
        queda = f"; pior queda {_pct(dd)}"
    rv = _num(e.get("razao_volume"))
    volume = "" if rv is None else f"; volume {rv:.1f}× o anterior"
    deriva = _num(e.get("deriva_pre"))
    antes = ("" if deriva is None or cruza(-_dias_corridos(20), 0)
             else f"; 20 pregões antes {_pct(deriva)}")
    d = _data(e.get("data"))
    if partes:
        retornos = ", ".join(partes)
    elif cortados:
        retornos = "retornos omitidos"
    else:
        retornos = "retornos não medidos (série sem densidade)"
    corte = ("" if not cortados else
             f" [{', '.join(cortados)} omitido(s): salto de preço bruto na janela, "
             "provável desdobramento ou grupamento]")
    return (f"      {d:%d/%m/%Y}" if d else "      ?") + ": " + retornos \
        + queda + volume + antes + corte + "."


def resumo_para_prompt(detalhe: dict[str, dict], *, origem: str,
                       hoje: date | None = None) -> str:
    """Bloco de texto para o prompt, uma seção por ação."""
    hoje = hoje or date.today()
    if not detalhe:
        return ""
    linhas = [f"DETALHE DO ARMAZÉM LOCAL ({origem}) — pregão diário da B3 (COTAHIST, "
              "preço bruto) e reação histórica a resultados anuais que a vitrine não "
              "guarda. Números calculados em código."]
    for tk, d in detalhe.items():
        p = resumo_pregoes(d.get("pregoes") or [])
        nome = f" ({d['nome']})" if d.get("nome") else ""
        linhas.append(f"  {tk}{nome}:")
        if p is None:
            linhas.append("    Pregão diário: o armazém não tem negócios deste papel "
                          f"nos últimos {DIAS_PREGAO} dias.")
        else:
            idade = (hoje - p["ultimo_dia"]).days
            parada = (f", {idade} dias atrás — série do armazém parada; o preço atual "
                      "está na vitrine" if idade > DIAS_PARADA else "")
            negocios = "n/d" if p["negocios21"] is None else f"{p['negocios21']:,.0f}"
            preco = "n/d" if p["preco"] is None else f"R$ {p['preco']:,.2f}"
            linhas.append(
                f"    Liquidez (pregão de {p['ultimo_dia']:%d/%m/%Y}{parada}): volume "
                f"financeiro mediano {_brl(p['vol21'])}/dia em 21 pregões, "
                f"{_brl(p['vol63'])}/dia em 63; {negocios} negócios/dia; negociado em "
                f"{p['pregoes_3m']} pregões nos últimos 3 meses e {p['pregoes_ano']} "
                "em 12.")
            saltos = ""
            if p["saltos"]:
                saltos = ("; excluídos " + ", ".join(
                    f"{d:%d/%m/%Y} ({_pct(r)})" for d, r in p["saltos"][-3:])
                    + " — salto de preço bruto, provável evento societário")
            linhas.append(
                f"    Preço bruto {preco}; retorno de mercado (sem proventos) 1m "
                f"{_pct(p['r1m'])}, 3m {_pct(p['r3m'])}, 12m {_pct(p['r12m'])}; "
                f"volatilidade anualizada {_pct(p['vol_anual'], sinal=False)}{saltos}.")
        eventos = d.get("eventos") or []
        if not eventos:
            linhas.append("    Reação a resultados: a Memória de Mercado não tem "
                          "eventos medidos deste papel.")
        else:
            linhas.append(
                f"    Reação a resultados anuais (últimos {len(eventos)}; retorno bruto "
                "do próprio papel após a divulgação — o retorno anormal da Memória de "
                "Mercado ficou de fora porque o índice de referência dela tem defeito "
                "conhecido):")
            datas_salto = [s for s in (_data(x.get("date")) for x in d.get("saltos") or [])
                      if s is not None]
            linhas.extend(_linha_evento(e, datas_salto) for e in eventos)
    return "\n".join(linhas)
