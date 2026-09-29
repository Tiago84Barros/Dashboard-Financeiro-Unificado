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
10:1 aparece como +900 % num dia. O resumo do pregão tira do retorno e da
volatilidade os dias com salto acima de :data:`SALTO_SUSPEITO` e diz quantos
tirou. A Memória de Mercado trata o mesmo salto na construção (metodologia
1.1.0): retroajusta o que o COTAHIST marca como evento societário, não mede a
janela que atravessa salto sem marcador e tira o salto do índice de referência.
Por isso só a safra da versão corrente é lida -- a 1.0.0 continua no banco, com
o retorno anormal que o índice sem filtro estragava.
"""
from __future__ import annotations

import math
import statistics
from datetime import date, datetime, timedelta

from core.memoria_mercado import MEMORIA_MERCADO_VERSAO
from core.memoria_mercado.serie import SALTO_SUSPEITO

#: ~400 dias cobrem 12 meses de pregão com folga para o atraso da carga.
DIAS_PREGAO = 400
EVENTOS_POR_TICKER = 6
TICKERS_MAX = 10
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
    WHERE simbolo = ANY(:t) AND data_evento <= :hoje AND versao_metodologia = :versao
) x
WHERE n <= :n
"""

_SQL_EVENTOS = f"""
SELECT simbolo, data_evento, data_pregao_zero, tipo_evento, drawdown,
       pregoes_ate_o_pior, pregoes_ate_recuperar, recuperacao_observada,
       razao_volume, deriva_pre_evento, janelas, persistencia, limitacoes,
       versao_metodologia
FROM ({_ULTIMOS_EVENTOS}) ev
ORDER BY simbolo, data_evento
"""

_HORIZONTES = ("1", "5", "20", "60")
#: Limitações da safra que mudam a leitura de um evento; as demais (índice
#: sintético, recuperação como piso) valem para todos e iriam repetidas.
_AVISOS = ("salto acima", "preco retroajustado", "drawdown nao medido")
#: ``persistencia`` compara o anormal de 5 pregões com o de 60.
_PERSISTENCIA = {"persistente": "o movimento persistiu",
                 "reversao_parcial": "o movimento reverteu em parte",
                 "reversao": "o movimento reverteu",
                 "sem_movimento": "sem movimento relevante"}


def normalizar_tickers(tickers) -> list[str]:
    return list(dict.fromkeys(
        str(t).strip().upper() for t in (tickers or []) if str(t).strip()))


def ler_detalhe(engine, tickers, *, hoje: date | None = None,
                engine_eventos=None) -> dict[str, dict]:
    """Linhas cruas por ticker: ``pregoes`` e ``eventos``.

    ``engine`` é o banco do COTAHIST; ``engine_eventos`` o da safra da Memória
    de Mercado (:func:`core.memoria_mercado.destino.url_memoria`), outro banco
    do mesmo container. Sem ela, os eventos saem de ``engine`` -- que guarda
    uma safra 1.0.0 abandonada, deixada de fora pelo filtro de versão.

    Ticker sem nenhuma linha volta com listas vazias -- ausência também é
    resposta, e o resumo diz que o armazém não tem, em vez de omitir.
    """
    from sqlalchemy import text

    alvo = normalizar_tickers(tickers)[:TICKERS_MAX]
    hoje = hoje or date.today()
    saida = {t: {"nome": None, "pregoes": [], "eventos": []} for t in alvo}
    if not alvo:
        return saida
    with engine.connect() as conn:
        pregoes = conn.execute(text(_SQL_PREGAO), {
            "t": alvo, "hoje": hoje,
            "desde": hoje - timedelta(days=DIAS_PREGAO)}).mappings().all()
    with (engine_eventos or engine).connect() as conn:
        eventos = conn.execute(text(_SQL_EVENTOS), {
            "t": alvo, "hoje": hoje, "n": EVENTOS_POR_TICKER,
            "versao": MEMORIA_MERCADO_VERSAO}).mappings().all()

    # Linha curta: são ~250 por papel e tudo passa pelo túnel.
    for r in pregoes:
        saida[r["ticker"]]["nome"] = r["issuer_short_name"] or saida[r["ticker"]]["nome"]
        saida[r["ticker"]]["pregoes"].append(
            {"date": r["trade_date"], "preco": r["preco"], "negocios": r["trades"],
             "volume": r["financial_volume"]})
    for r in eventos:
        janelas = r["janelas"] if isinstance(r["janelas"], dict) else {}
        j = {h: janelas.get(h) or {} for h in _HORIZONTES}
        saida[r["simbolo"]]["eventos"].append(
            {"data": r["data_evento"], "pregao_zero": r["data_pregao_zero"],
             "tipo": r["tipo_evento"], "versao": r.get("versao_metodologia"),
             "retornos": {h: j[h].get("retorno_ativo") for h in _HORIZONTES},
             "anormais": {h: j[h].get("retorno_anormal") for h in _HORIZONTES},
             "motivos": {h: j[h]["motivo_ausencia"] for h in _HORIZONTES
                         if j[h].get("motivo_ausencia")},
             "persistencia": r.get("persistencia"),
             "avisos": [str(x) for x in (r.get("limitacoes") or [])
                        if str(x).startswith(_AVISOS)],
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


def _linha_evento(e: dict) -> str:
    """Uma linha por divulgação: retorno do papel e, ao lado, o anormal.

    O salto de preço já foi tratado na construção da safra (metodologia
    1.1.0): horizonte que atravessa salto sem marcador societário vem sem
    número e com o motivo, e é o motivo que sai aqui -- "não medido" é
    diferente de "não aconteceu". Evento de outra versão (túnel servido por
    código velho) mostra só o retorno do papel: o anormal dela veio do índice
    sem filtro.
    """
    corrente = e.get("versao") == MEMORIA_MERCADO_VERSAO
    ret = e.get("retornos") or {}
    anormal = (e.get("anormais") or {}) if corrente else {}
    motivos = e.get("motivos") or {}
    partes, sem_medida = [], []
    for h in _HORIZONTES:
        valor = _num(ret.get(h))
        if valor is None:
            if "salto" in str(motivos.get(h) or ""):
                sem_medida.append(f"{h}d")
            continue
        ar_ = _num(anormal.get(h))
        partes.append(f"{h}d {_pct(valor)}"
                      + ("" if ar_ is None else f" (anormal {_pct(ar_)})"))
    dd, rec = _num(e.get("drawdown")), e.get("recuperou")
    pregoes_rec = _num(e.get("pregoes_ate_recuperar"))
    if dd is None:
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
    antes = "" if deriva is None else f"; 20 pregões antes {_pct(deriva)}"
    persist = _PERSISTENCIA.get(e.get("persistencia")) if corrente else None
    persist = f"; {persist}" if persist else ""
    d = _data(e.get("data"))
    if partes:
        retornos = ", ".join(partes)
    elif sem_medida:
        retornos = "retornos não medidos"
    else:
        retornos = "retornos não medidos (série sem densidade)"
    notas = []
    if sem_medida:
        notas.append(f"{', '.join(sem_medida)} não medido(s): salto de preço bruto "
                     "sem marcador societário na janela")
    notas.extend(str(a) for a in e.get("avisos") or []
                 if not str(a).startswith("salto acima"))
    if not corrente:
        notas.append(f"metodologia {e.get('versao') or '?'}, anterior ao filtro de "
                     "salto: retorno anormal omitido")
    corte = f" [{'; '.join(notas)}]" if notas else ""
    return (f"      {d:%d/%m/%Y}" if d else "      ?") + ": " + retornos \
        + queda + volume + antes + persist + corte + "."


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
                f"    Reação a resultados anuais (últimos {len(eventos)}; retorno do "
                "próprio papel após a divulgação e, entre parênteses, o anormal — "
                "descontado o que o mercado fez, medido contra um índice sintético "
                "equiponderado das ações do armazém, não o Ibovespa; desdobramento "
                "e grupamento já retroajustados):")
            linhas.extend(_linha_evento(e) for e in eventos)
    return "\n".join(linhas)
