"""Detalhe por FII que só o armazém local tem.

A vitrine do Supabase guarda a foto do fundo: métricas do último informe, score
e peso na carteira. O bastante para selecionar, pouco para a LLM responder "o
VPA do HGLG11 vem caindo?" ou "o rendimento do KNCR11 subiu no último ano?". O
armazém tem a série do informe mensal da CVM desde 2017, os proventos desde
2014, a composição da carteira, a lista de imóveis, o score reconstruído mês a
mês e a fita diária da B3 (COTAHIST); este módulo lê um recorte curto disso
para os fundos da conversa.

Duas metades, separadas de propósito (mesma regra de ``us_detalhe_armazem``):

* :func:`ler_detalhe` é SQL e roda onde o armazém está -- direto no
  desenvolvimento, ou no servidor do túnel (``scripts/servir_armazem_leitura``).
  Devolve linhas cruas, sem conta.
* :func:`resumo_para_prompt` faz as contas e roda sempre no app, para que o PC
  servindo uma versão velha do código não mude o número que a LLM lê.
"""
from __future__ import annotations

import math
import re
import statistics
from datetime import date, datetime, timedelta

#: Tetos do recorte. 40 meses dão a variação de 36 do VPA mesmo com o informe
#: chegando com até 3 meses de atraso; ~400 dias cobrem a
#: faixa de 52 semanas do preço com folga.
MESES_SERIE = 40
DIAS_PRECO = 400
ANOS_PROVENTOS = 5
MESES_SCORE = 13
ANOS_COMPOSICAO = 2
ITENS_COMPOSICAO = 8
IMOVEIS_LISTADOS = 5
TICKERS_MAX = 10
#: 110 dias corridos dão os 63 pregões da janela longa mesmo com Carnaval e
#: feriados no meio (~70 pregões).
DIAS_LIQUIDEZ = 110
JANELAS_LIQUIDEZ = (21, 63)
#: Mais que um fim de semana prolongado sem pregão novo é a fita parada, não
#: o calendário.
DIAS_FITA_PARADA = 5

#: Métricas do informe mensal da CVM. ``leverage`` é passivo/ativo total e
#: ``dy_patrimonial_mes`` é o rendimento do mês sobre o patrimônio (fração).
_METRICAS = ("nav_per_share", "total_investors", "equity", "leverage",
             "dy_patrimonial_mes", "liquidity_ratio")
_TIPOS_COMPOSICAO = ("asset_class", "sector", "indexer", "region", "issuer", "debtor")

_SQL_SERIE = """
SELECT DISTINCT ON (ticker, metric_name, reference_date)
       ticker, metric_name, reference_date, value_numeric
FROM market.fii_metric_observations
WHERE ticker = ANY(:t) AND source = 'cvm_informe_mensal'
  AND metric_name = ANY(:m) AND reference_date >= :desde
  AND reference_date <= :hoje AND available_at::date <= :hoje
ORDER BY ticker, metric_name, reference_date, knowledge_at DESC NULLS LAST
"""

_SQL_PRECOS = """
SELECT ticker, date, COALESCE(adjusted_close, close) AS preco
FROM market.historical_prices
WHERE ticker = ANY(:t) AND date >= :desde AND date <= :hoje
ORDER BY ticker, date
"""

_SQL_PROVENTOS = """
SELECT DISTINCT ticker, COALESCE(ex_date, event_date) AS data_com, payment_date,
       type, amount
FROM market.dividends
WHERE ticker = ANY(:t) AND COALESCE(ex_date, event_date) >= :desde
  AND COALESCE(ex_date, event_date) <= :hoje
ORDER BY ticker, data_com
"""

# Uma só fonte e uma só data por tipo: a mesma classe vem do informe da CVM e
# da brapi, e somar as duas dobraria o peso. Emissor e devedor chegam como
# CNPJ; o nome sai do cadastro de entidades quando ele tem a razão social.
_SQL_COMPOSICAO = """
WITH ultima AS (
    SELECT DISTINCT ON (ticker, exposure_type)
           ticker, exposure_type, reference_date, source
    FROM market.fii_exposures
    WHERE ticker = ANY(:t) AND exposure_type = ANY(:tipos)
      AND reference_date >= :desde AND reference_date <= :hoje
      AND available_at::date <= :hoje
    ORDER BY ticker, exposure_type, reference_date DESC, knowledge_at DESC NULLS LAST
)
SELECT DISTINCT ON (e.ticker, e.exposure_type, e.exposure_name)
       e.ticker, e.exposure_type,
       COALESCE(nome.alias_name, e.exposure_name) AS exposure_name,
       e.exposure_weight, e.reference_date, e.source
FROM market.fii_exposures e
JOIN ultima u USING (ticker, exposure_type, reference_date, source)
LEFT JOIN LATERAL (
    SELECT a.alias_name FROM market.fii_entity_aliases a
    WHERE a.legal_identifier = e.exposure_name AND a.match_method = 'legal_name'
    ORDER BY a.id LIMIT 1
) nome ON e.exposure_name ~ '^[0-9]{14}$'
WHERE e.available_at::date <= :hoje
ORDER BY e.ticker, e.exposure_type, e.exposure_name, e.knowledge_at DESC NULLS LAST
"""

_SQL_IMOVEIS = """
SELECT ticker, nome_imovel, area_m2, vacancia, cidade, uf, segmento_imovel,
       pct_receita, updated_at
FROM market.fii_imoveis
WHERE ticker = ANY(:t)
"""

# A fita só tem linha no pregão em que o fundo negociou; o calendário vem do
# mercado inteiro, para que o dia sem negócio conte como zero em vez de sumir.
# Uma linha por pregão: se duas cargas deixarem o mesmo dia, vale a mais nova.
_SQL_PREGOES = """
SELECT DISTINCT ON (ticker, trade_date)
       ticker, trade_date, close, trades, financial_volume
FROM market.fii_b3_security_history
WHERE ticker = ANY(:t) AND trade_date >= :desde AND trade_date <= :hoje
ORDER BY ticker, trade_date, collected_at DESC NULLS LAST
"""

_SQL_CALENDARIO = """
SELECT DISTINCT trade_date AS pregao
FROM market.fii_b3_security_history
WHERE trade_date >= :desde AND trade_date <= :hoje
ORDER BY pregao
"""

_SQL_SCORE = """
SELECT ticker, reference_date, available_at, methodology_version, fii_type,
       type_score, confidence, coverage, data_readiness_status
FROM market.fii_pit_score_snapshots
WHERE ticker = ANY(:t) AND reference_date >= :desde AND reference_date <= :hoje
  AND available_at::date <= :hoje
"""


def normalizar_tickers(tickers) -> list[str]:
    return list(dict.fromkeys(
        str(t).strip().upper() for t in (tickers or []) if str(t).strip()))


def _versao(texto) -> tuple[int, ...]:
    """'6.10.0' > '6.9.0': em texto a ordem sai invertida."""
    return tuple(int(p) for p in re.findall(r"\d+", str(texto or ""))) or (-1,)


def _meses_antes(ref: date, meses: int) -> date:
    """1º dia do mês ``meses`` antes do de ``ref``."""
    total = ref.year * 12 + ref.month - 1 - meses
    return date(total // 12, total % 12 + 1, 1)


def ler_detalhe(engine, tickers, *, hoje: date | None = None) -> dict[str, dict]:
    """Linhas cruas por ticker: ``serie``, ``precos``, ``proventos``,
    ``composicao``, ``imoveis``, ``score``, ``pregoes`` (fita da B3) e
    ``calendario`` (pregões do mercado no mesmo recorte, igual para todos).

    Ticker sem nenhuma linha volta com listas vazias -- ausência também é
    resposta, e o resumo diz que o armazém não tem, em vez de omitir.
    """
    from sqlalchemy import text

    alvo = normalizar_tickers(tickers)[:TICKERS_MAX]
    hoje = hoje or date.today()
    saida = {t: {"serie": [], "precos": [], "proventos": [], "composicao": [],
                 "imoveis": [], "score": [], "pregoes": [], "calendario": []}
             for t in alvo}
    if not alvo:
        return saida
    with engine.connect() as conn:
        serie = conn.execute(text(_SQL_SERIE), {
            "t": alvo, "m": list(_METRICAS), "hoje": hoje,
            "desde": _meses_antes(hoje, MESES_SERIE)}).mappings().all()
        precos = conn.execute(text(_SQL_PRECOS), {
            "t": alvo, "hoje": hoje,
            "desde": hoje - timedelta(days=DIAS_PRECO)}).mappings().all()
        proventos = conn.execute(text(_SQL_PROVENTOS), {
            "t": alvo, "hoje": hoje,
            "desde": hoje - timedelta(days=366 * ANOS_PROVENTOS)}).mappings().all()
        composicao = conn.execute(text(_SQL_COMPOSICAO), {
            "t": alvo, "tipos": list(_TIPOS_COMPOSICAO), "hoje": hoje,
            "desde": hoje - timedelta(days=366 * ANOS_COMPOSICAO)}).mappings().all()
        imoveis = conn.execute(text(_SQL_IMOVEIS), {"t": alvo}).mappings().all()
        score = conn.execute(text(_SQL_SCORE), {
            "t": alvo, "hoje": hoje,
            "desde": _meses_antes(hoje, MESES_SCORE)}).mappings().all()
        janela = {"hoje": hoje, "desde": hoje - timedelta(days=DIAS_LIQUIDEZ)}
        pregoes = conn.execute(text(_SQL_PREGOES), {"t": alvo, **janela}).mappings().all()
        calendario = [r["pregao"] for r in
                      conn.execute(text(_SQL_CALENDARIO), janela).mappings().all()]

    for r in serie:
        saida[r["ticker"]]["serie"].append(
            {"metrica": r["metric_name"], "ref": r["reference_date"],
             "valor": r["value_numeric"]})
    for r in precos:
        saida[r["ticker"]]["precos"].append(
            {"date": r["date"], "preco": r["preco"]})
    for r in proventos:
        saida[r["ticker"]]["proventos"].append(
            {"data_com": r["data_com"], "pagamento": r["payment_date"],
             "tipo": r["type"], "valor": r["amount"]})
    por_tipo: dict[tuple, list] = {}
    for r in composicao:
        por_tipo.setdefault((r["ticker"], r["exposure_type"]), []).append(r)
    for (tk, tipo), linhas in sorted(por_tipo.items()):
        linhas.sort(key=lambda r: _num(r["exposure_weight"]) or 0, reverse=True)
        for r in linhas[:ITENS_COMPOSICAO]:
            saida[tk]["composicao"].append(
                {"tipo": tipo, "nome": r["exposure_name"], "peso": r["exposure_weight"],
                 "ref": r["reference_date"], "fonte": r["source"],
                 "itens_no_tipo": len(linhas)})
    for r in pregoes:
        saida[r["ticker"]]["pregoes"].append(
            {"date": r["trade_date"], "fechamento": r["close"],
             "negocios": r["trades"], "volume": r["financial_volume"]})
    for tk in saida:
        saida[tk]["calendario"] = list(calendario)
    for r in imoveis:
        saida[r["ticker"]]["imoveis"].append(
            {k: r[k] for k in ("nome_imovel", "area_m2", "vacancia", "cidade", "uf",
                               "segmento_imovel", "pct_receita", "updated_at")})
    # Cada versão da metodologia tem a sua reconstrução; fica só a mais nova
    # que o ticker tiver, e dentro dela a publicação mais recente de cada mês.
    por_ticker: dict[str, list] = {}
    for r in score:
        por_ticker.setdefault(r["ticker"], []).append(r)
    for tk, linhas in por_ticker.items():
        versao = max((r["methodology_version"] for r in linhas), key=_versao)
        por_mes: dict = {}
        for r in linhas:
            if r["methodology_version"] != versao:
                continue
            atual = por_mes.get(r["reference_date"])
            if atual is None or (r["available_at"] or datetime.min) > (atual["available_at"] or datetime.min):
                por_mes[r["reference_date"]] = r
        for ref in sorted(por_mes):
            r = por_mes[ref]
            saida[tk]["score"].append(
                {"ref": ref, "versao": versao, "tipo": r["fii_type"],
                 "score": r["type_score"], "confianca": r["confidence"],
                 "cobertura": r["coverage"], "prontidao": r["data_readiness_status"]})
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
    return f"R$ {x:,.2f}"


def _variacao(novo: float | None, velho: float | None) -> float | None:
    if novo is None or velho is None or velho <= 0:
        return None
    return novo / velho - 1


def resumo_serie(serie: list[dict]) -> dict | None:
    """Último informe e variações de 12 e 36 meses do informe mensal."""
    por_metrica: dict[str, dict[date, float]] = {}
    for r in serie:
        ref, valor = _data(r.get("ref")), _num(r.get("valor"))
        if ref is not None and valor is not None:
            por_metrica.setdefault(str(r.get("metrica")), {})[ref.replace(day=1)] = valor
    vpa = por_metrica.get("nav_per_share") or {}
    if not vpa:
        return None
    ultimo = max(vpa)

    def em(metrica: str, meses: int = 0) -> float | None:
        return (por_metrica.get(metrica) or {}).get(_meses_antes(ultimo, meses))

    dy = por_metrica.get("dy_patrimonial_mes") or {}
    dy12 = [v for ref, v in dy.items() if _meses_antes(ultimo, 11) <= ref <= ultimo]
    dy_ant = [v for ref, v in dy.items()
              if _meses_antes(ultimo, 23) <= ref <= _meses_antes(ultimo, 12)]
    return {
        "ref": ultimo, "vpa": em("nav_per_share"),
        "vpa12": _variacao(em("nav_per_share"), em("nav_per_share", 12)),
        "vpa36": _variacao(em("nav_per_share"), em("nav_per_share", 36)),
        "cotistas": em("total_investors"),
        "cotistas12": _variacao(em("total_investors"), em("total_investors", 12)),
        "pl": em("equity"), "alavancagem": em("leverage"),
        "alavancagem12": em("leverage", 12), "liquidez": em("liquidity_ratio"),
        "dy12m": sum(dy12) if len(dy12) == 12 else None, "meses_dy": len(dy12),
        "dy12m_anterior": sum(dy_ant) if len(dy_ant) == 12 else None,
    }


def resumo_precos(precos: list[dict]) -> dict | None:
    """Retornos por data, não por contagem de pregões: a série de FII do
    armazém é diária em trechos e mensal em outros."""
    serie = sorted((d, p) for d, p in ((_data(r.get("date")), _num(r.get("preco")))
                                       for r in precos)
                   if d is not None and p is not None and p > 0)
    if len(serie) < 2:
        return None
    ultimo_dia, ultimo = serie[-1]

    def em(dias: int) -> float | None:
        antes = [p for d, p in serie if d <= ultimo_dia - timedelta(days=dias)]
        return antes[-1] if antes else None

    ano = [p for d, p in serie if d > ultimo_dia - timedelta(days=365)]
    pico, dd = ano[0], 0.0
    for p in ano:
        pico = max(pico, p)
        dd = min(dd, p / pico - 1)
    return {"ultimo_dia": ultimo_dia, "ultimo": ultimo, "pontos": len(ano),
            "r3m": _variacao(ultimo, em(91)), "r12m": _variacao(ultimo, em(365)),
            "max52": max(ano), "min52": min(ano), "dd12m": dd}


def resumo_liquidez(pregoes: list[dict], calendario: list) -> dict | None:
    """Volume e negócios nos últimos 21 e 63 pregões do mercado.

    Pregão sem negócio do fundo entra como zero: a mediana de quem negocia um
    dia sim, outro não, é a metade do que a média dos dias negociados diria.
    """
    dias = sorted({d for d in map(_data, calendario or []) if d is not None})
    if not dias:
        return None
    por_dia = {d: r for d, r in ((_data(r.get("date")), r) for r in pregoes or [])
               if d is not None}

    def janela(n: int) -> dict | None:
        recorte = dias[-n:]
        if len(recorte) < n:
            return None
        volumes = [(_num(por_dia[d].get("volume")) or 0.0) if d in por_dia else 0.0
                   for d in recorte]
        negocios = [(_num(por_dia[d].get("negocios")) or 0.0) if d in por_dia else 0.0
                    for d in recorte]
        return {"volume_mediano": statistics.median(volumes), "volume_total": sum(volumes),
                "negocios_medianos": statistics.median(negocios),
                "negociados": sum(1 for v in volumes if v > 0), "pregoes": n}

    negociados = [d for d, r in por_dia.items() if (_num(r.get("volume")) or 0) > 0]
    return {"ultimo_pregao": dias[-1],
            "ultimo_negocio": max(negociados) if negociados else None,
            **{f"j{n}": janela(n) for n in JANELAS_LIQUIDEZ}}


def _linha_liquidez(d: dict, hoje: date) -> str:
    if "calendario" not in d:
        # Servidor do túnel rodando código anterior a esta leitura.
        return ("    Liquidez na B3: não veio nesta leitura (o servidor do armazém "
                "roda uma versão antiga do código).")
    liq = resumo_liquidez(d.get("pregoes") or [], d.get("calendario") or [])
    if liq is None:
        return ("    Liquidez na B3: o armazém não tem a fita do COTAHIST dos últimos "
                f"{DIAS_LIQUIDEZ} dias.")
    idade = (hoje - liq["ultimo_pregao"]).days
    cabeca = (f"    Liquidez na B3 (COTAHIST até o pregão de {liq['ultimo_pregao']:%d/%m/%Y}"
              + (f", {idade} dias atrás — fita do armazém parada" if idade > DIAS_FITA_PARADA
                 else "") + ")")
    if liq["ultimo_negocio"] is None:
        return f"{cabeca}: nenhum negócio no recorte de {DIAS_LIQUIDEZ} dias."
    partes = []
    for n in JANELAS_LIQUIDEZ:
        j = liq[f"j{n}"]
        if j is None:
            partes.append(f"janela de {n} pregões incompleta na fita")
            continue
        partes.append(f"{n} pregões: volume mediano {_brl(j['volume_mediano'])}/dia "
                      f"(total {_brl(j['volume_total'])}), "
                      f"{j['negocios_medianos']:,.0f} negócios/dia, negociado em "
                      f"{j['negociados']} de {n}")
    atraso = ("" if liq["ultimo_negocio"] == liq["ultimo_pregao"] else
              f"; último negócio em {liq['ultimo_negocio']:%d/%m/%Y}")
    return (f"{cabeca}: " + "; ".join(partes)
            + f" (pregão sem negócio conta zero){atraso}.")


def resumo_proventos(proventos: list[dict], hoje: date) -> dict:
    datas = [(_data(r.get("data_com")), _num(r.get("valor"))) for r in proventos]
    datas = sorted((d, v) for d, v in datas if d is not None and v is not None and v > 0)
    corte, corte_ant = hoje - timedelta(days=365), hoje - timedelta(days=730)
    ultimos12 = [v for d, v in datas if d > corte]
    anteriores = [v for d, v in datas if corte_ant < d <= corte]
    contagem: dict[date, int] = {}
    for d, _ in datas:
        contagem[d] = contagem.get(d, 0) + 1
    return {"soma12m": sum(ultimos12), "pagamentos12m": len(ultimos12),
            "soma12m_anterior": sum(anteriores) if anteriores else None,
            "ultimo": datas[-1][0] if datas else None,
            "ultimo_valor": datas[-1][1] if datas else None,
            "datas_duplas": sorted(d for d, n in contagem.items() if n > 1 and d > corte)}


def resumo_imoveis(imoveis: list[dict]) -> dict | None:
    if not imoveis:
        return None
    area_vac = [(a, v) for a, v in ((_num(i.get("area_m2")), _num(i.get("vacancia")))
                                    for i in imoveis) if a and v is not None]
    area = sum(a for a, _ in area_vac)
    ordem = sorted(imoveis, key=lambda i: (_num(i.get("pct_receita")) or -1,
                                           _num(i.get("area_m2")) or -1), reverse=True)
    return {"n": len(imoveis),
            "vacancia_area": sum(a * v for a, v in area_vac) / area if area else None,
            "com_receita": sum(1 for i in imoveis if _num(i.get("pct_receita")) is not None),
            "principais": ordem[:IMOVEIS_LISTADOS]}


_NOMES_TIPO = {"asset_class": "classe de ativo", "sector": "setor", "indexer": "indexador",
               "region": "região", "issuer": "emissor (CNPJ quando o cadastro não tem o nome)",
               "debtor": "devedor dos CRI (CNPJ quando o cadastro não tem o nome)"}


def _nome_exposicao(nome) -> str:
    """CNPJ pontuado para a LLM reconhecer; o '0' da CVM é devedor não informado."""
    texto = str(nome or "").strip()
    if texto in ("", "0"):
        return "não identificado"
    if re.fullmatch(r"\d{14}", texto):
        return f"{texto[:2]}.{texto[2:5]}.{texto[5:8]}/{texto[8:12]}-{texto[12:]}"
    return texto


def _linhas_composicao(composicao: list[dict]) -> list[str]:
    por_tipo: dict[str, list] = {}
    for c in composicao:
        por_tipo.setdefault(str(c.get("tipo")), []).append(c)
    linhas = []
    for tipo in _TIPOS_COMPOSICAO:
        itens = por_tipo.get(tipo)
        if not itens:
            continue
        ref = _data(itens[0].get("ref"))
        total = int(_num(itens[0].get("itens_no_tipo")) or len(itens))
        partes = ", ".join(f"{_nome_exposicao(i.get('nome'))} {_pct(_num(i.get('peso')), sinal=False)}"
                           for i in itens)
        resto = f" (+{total - len(itens)} menores)" if total > len(itens) else ""
        linhas.append(f"      {_NOMES_TIPO[tipo]} ({ref:%m/%Y}, {itens[0].get('fonte')}): "
                      f"{partes}{resto}" if ref else f"      {_NOMES_TIPO[tipo]}: {partes}{resto}")
    return linhas


def resumo_para_prompt(detalhe: dict[str, dict], *, origem: str,
                       hoje: date | None = None) -> str:
    """Bloco de texto para o prompt, uma seção por fundo."""
    hoje = hoje or date.today()
    if not detalhe:
        return ""
    linhas = [f"DETALHE DO ARMAZÉM LOCAL ({origem}) — histórico do informe mensal da "
              "CVM, preço, liquidez na B3, proventos, composição, imóveis e score mês "
              "a mês que a vitrine não guarda. Números calculados em código."]
    for tk, d in detalhe.items():
        linhas.append(f"  {tk}:")
        s = resumo_serie(d.get("serie") or [])
        if s is None:
            linhas.append("    Informe mensal CVM: o armazém não tem série deste fundo.")
        else:
            dy = ("n/d" if s["dy12m"] is None
                  else f"{s['dy12m']:.2%} (12 meses anteriores: "
                       f"{'n/d' if s['dy12m_anterior'] is None else format(s['dy12m_anterior'], '.2%')})")
            cotistas = "n/d" if s["cotistas"] is None else f"{s['cotistas']:,.0f}"
            linhas.append(
                f"    Informe mensal CVM (ref. {s['ref']:%m/%Y}): VPA R$ {s['vpa']:,.2f} "
                f"({_pct(s['vpa12'])} em 12m, {_pct(s['vpa36'])} em 36m); "
                f"PL {_brl(s['pl'])}; cotistas {cotistas}"
                f" ({_pct(s['cotistas12'])} em 12m); passivo/ativo "
                f"{_pct(s['alavancagem'], sinal=False)} (há 12m "
                f"{_pct(s['alavancagem12'], sinal=False)}); caixa/ativo "
                f"{_pct(s['liquidez'], sinal=False)}; rendimento patrimonial 12m {dy}.")
        p = resumo_precos(d.get("precos") or [])
        if p is None:
            linhas.append("    Preço: o armazém não tem série recente deste fundo.")
        else:
            idade = (hoje - p["ultimo_dia"]).days
            vpa = s["vpa"] if s else None
            pvp = f"; P/VP {p['ultimo'] / vpa:.2f}" if vpa else ""
            linhas.append(
                f"    Preço (cotação de {p['ultimo_dia']:%d/%m/%Y}"
                + (f", {idade} dias atrás — série do armazém parada" if idade > 20 else "")
                + f"): R$ {p['ultimo']:,.2f}{pvp}; retorno 3m {_pct(p['r3m'])}, "
                f"12m {_pct(p['r12m'])}; faixa 12m {p['min52']:,.2f}–{p['max52']:,.2f}; "
                f"queda máx. 12m {_pct(p['dd12m'])} ({p['pontos']} cotações no ano).")
        linhas.append(_linha_liquidez(d, hoje))
        pv = resumo_proventos(d.get("proventos") or [], hoje)
        if pv["ultimo"] is None:
            linhas.append(f"    Proventos: nenhum registrado nos últimos {ANOS_PROVENTOS} anos.")
        else:
            rend = ""
            if p is not None and p["ultimo"] > 0 and pv["soma12m"] > 0:
                rend = f" (≈{pv['soma12m'] / p['ultimo']:.2%} do preço)"
            ant = ("" if pv["soma12m_anterior"] is None
                   else f"; 12 meses anteriores R$ {pv['soma12m_anterior']:,.4f} "
                        f"({_pct(_variacao(pv['soma12m'], pv['soma12m_anterior']))})")
            duplas = ("" if not pv["datas_duplas"] else
                      f"; ATENÇÃO: {len(pv['datas_duplas'])} data(s)-com com dois valores "
                      "diferentes, ambos somados — pode ser eco da fonte")
            linhas.append(
                f"    Proventos: R$ {pv['soma12m']:,.4f}/cota em 12m{rend}, "
                f"{pv['pagamentos12m']} pagamento(s){ant}; último com data-com "
                f"{pv['ultimo']:%d/%m/%Y} (R$ {pv['ultimo_valor']:,.4f}){duplas}.")
        comp = _linhas_composicao(d.get("composicao") or [])
        if comp:
            linhas.append("    Composição (peso no ativo; última data disponível por tipo):")
            linhas.extend(comp)
        im = resumo_imoveis(d.get("imoveis") or [])
        if im is not None:
            vac = ("n/d" if im["vacancia_area"] is None
                   else f"{im['vacancia_area']:.1%}")
            principais = "; ".join(
                f"{i.get('nome_imovel')}"
                + (f" {_pct(_num(i.get('pct_receita')), sinal=False)} da receita"
                   if _num(i.get("pct_receita")) is not None else "")
                + (f", vacância {_pct(_num(i.get('vacancia')), sinal=False)}"
                   if _num(i.get("vacancia")) is not None else "")
                for i in im["principais"])
            linhas.append(f"    Imóveis: {im['n']} cadastrados; vacância física ponderada "
                          f"pela área {vac}; maiores: {principais}.")
        score = d.get("score") or []
        if score:
            ult = score[-1]
            marcos = [x for x in (score[-7] if len(score) >= 7 else None,
                                  score[0] if len(score) >= 13 else None) if x]
            hist = "; ".join(f"{_data(x['ref']):%m/%Y} {_num(x['score']):.1f}"
                             for x in marcos if _num(x.get("score")) is not None)
            ult_score = _num(ult.get("score"))
            linhas.append(
                f"    Score PIT reconstruído (metodologia {ult.get('versao')}, tipo "
                f"{ult.get('tipo')}): {_data(ult['ref']):%m/%Y} "
                f"{'n/d' if ult_score is None else f'{ult_score:.1f}'}"
                f" (confiança {_pct(_num(ult.get('confianca')), sinal=False)})"
                + (f"; antes: {hist}" if hist else "") + ".")
    return "\n".join(linhas)
