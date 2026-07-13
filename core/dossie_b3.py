"""
core/dossie_b3.py
Dossiê determinístico por empresa + parecer LLM narrativo.

Divisão de responsabilidades (a razão de existir deste módulo):
  1. DETERMINÍSTICO (Python/SQL) — todos os NÚMEROS e CHECAGENS são computados
     aqui, direto do banco (market.* + public.docs_corporativos): séries anuais,
     dividendo recorrente vs extraordinário, valuation a partir de dados brutos,
     sensibilidade a juros pela posição de dívida líquida, eventos societários
     datados e red flags de qualidade de dados (ex.: DY inflado por duplicação
     de classe na ingestão brapi).
  2. LLM — recebe o dossiê pronto e APENAS narra o parecer. É proibido de
     calcular; isso elimina a classe de erro "Selic alta prejudica os
     dividendos" em empresa com caixa líquido.

Consumidores:
  - views/portfolio_b3.py  → gate qualitativo de seleção (veto + substituição,
    aplicado DEPOIS do ranking estatístico; nunca altera score/FDR/Rank-IC).
  - views/analise_portfolio_b3.py → relatório completo por empresa na aba
    Avaliação de Portfólio.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date

import streamlit as st
from sqlalchemy import text

logger = logging.getLogger(__name__)

# Schema mínimo que o parecer LLM deve devolver — superset do schema usado por
# analisar_empresa (compatível com _render_empresa_expander/redistribuir_pesos).
_CLASSIFICACOES = ("aprovar", "aprovar_com_ressalvas", "vetar")


# ─────────────────────────────────────────────────────────────────────────────
# Acesso a banco (engine singleton do projeto — nunca criar engine próprio)
# ─────────────────────────────────────────────────────────────────────────────

def _rows(sql: str, **params) -> list[dict]:
    from core.database import get_engine
    eng = get_engine()
    if eng is None:
        return []
    with eng.connect() as conn:
        return [dict(r) for r in conn.execute(text(sql), params).mappings().all()]


def _f(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _mi(v) -> float | None:
    """R$ → R$ milhões (1 casa)."""
    v = _f(v)
    return None if v is None else round(v / 1e6, 1)


# ─────────────────────────────────────────────────────────────────────────────
# Blocos determinísticos do dossiê
# ─────────────────────────────────────────────────────────────────────────────

def _ident(tk: str) -> dict:
    # public.setores registra uma classe por empresa (ex.: só CEBR3) — o
    # fallback por prefixo cobre as demais classes (CEBR5/CEBR6).
    rows = _rows(
        'SELECT nome_empresa, "SETOR" AS setor, "SUBSETOR" AS subsetor, '
        '"SEGMENTO" AS segmento FROM public.setores '
        "WHERE ticker = :t OR ticker LIKE :pref ORDER BY (ticker = :t) DESC LIMIT 1",
        t=tk, pref=tk[:4] + "%",
    )
    if rows:
        r = rows[0]
        return {"nome": r["nome_empresa"], "setor": r["setor"],
                "subsetor": r["subsetor"], "segmento": r["segmento"]}
    rows = _rows(
        "SELECT c.name AS nome FROM market.assets a "
        "JOIN market.companies c ON c.id = a.company_id WHERE a.ticker = :t LIMIT 1",
        t=tk,
    )
    return {"nome": rows[0]["nome"] if rows else tk,
            "setor": None, "subsetor": None, "segmento": None}


def _series_anuais(tk: str, max_anos: int = 12) -> list[dict]:
    rows = _rows(
        """
        SELECT i.year, i.revenue, i.ebit, i.ebitda, i.net_income, i.eps,
               b.equity, b.cash, b.gross_debt, b.net_debt, b.total_assets,
               c.operating_cash_flow AS fco
        FROM market.income_statements i
        LEFT JOIN market.balance_sheets b
          ON b.ticker = i.ticker AND b.period = i.period AND b.year = i.year
        LEFT JOIN market.cash_flow_statements c
          ON c.ticker = i.ticker AND c.period = i.period AND c.year = i.year
        WHERE i.ticker = :t AND i.period = 'annual'
        ORDER BY i.year
        """,
        t=tk,
    )
    out: list[dict] = []
    for r in rows[-max_anos:]:
        rec, ll, pl = _f(r["revenue"]), _f(r["net_income"]), _f(r["equity"])
        out.append({
            "ano": int(r["year"]),
            "receita_mi": _mi(r["revenue"]), "ebit_mi": _mi(r["ebit"]),
            "ebitda_mi": _mi(r["ebitda"]), "lucro_mi": _mi(r["net_income"]),
            "pl_mi": _mi(r["equity"]), "caixa_mi": _mi(r["cash"]),
            "div_bruta_mi": _mi(r["gross_debt"]), "div_liq_mi": _mi(r["net_debt"]),
            "fco_mi": _mi(r["fco"]), "lpa": _f(r["eps"]),
            "margem_liq_pct": round(ll / rec * 100, 1) if rec and ll is not None else None,
            "roe_pct": round(ll / pl * 100, 1) if pl and ll is not None else None,
        })
    return out


def _trimestres(tk: str, n: int = 6) -> dict:
    rows = _rows(
        """
        SELECT year, quarter, revenue, net_income
        FROM market.income_statements
        WHERE ticker = :t AND period <> 'annual' AND quarter BETWEEN 1 AND 4
        ORDER BY year DESC, quarter DESC LIMIT :n
        """,
        t=tk, n=n + 4,
    )
    rows = rows[::-1]
    tris = [{"ano": int(r["year"]), "tri": int(r["quarter"]),
             "receita_mi": _mi(r["revenue"]), "lucro_mi": _mi(r["net_income"])}
            for r in rows]
    yoy: dict = {}
    if len(tris) >= 5:
        ult, ant = tris[-1], tris[-5]
        if ant["ano"] == ult["ano"] - 1 and ant["tri"] == ult["tri"]:
            if ant["receita_mi"] and ult["receita_mi"] is not None:
                yoy["receita_yoy_pct"] = round(
                    (ult["receita_mi"] / ant["receita_mi"] - 1) * 100, 1)
            if ant["lucro_mi"] and ult["lucro_mi"] is not None:
                yoy["lucro_yoy_pct"] = round(
                    (ult["lucro_mi"] / ant["lucro_mi"] - 1) * 100, 1)
            yoy["ref"] = f"{ult['ano']}T{ult['tri']} vs {ant['ano']}T{ant['tri']}"
    return {"serie": tris[-n:], "yoy": yoy}


def _dividendos(tk: str, preco: float | None) -> dict:
    rows = _rows(
        """
        SELECT COALESCE(ex_date, payment_date, event_date) AS dt, amount, type
        FROM market.dividends
        WHERE ticker = :t AND COALESCE(ex_date, payment_date, event_date) IS NOT NULL
        ORDER BY 1
        """,
        t=tk,
    )
    por_data: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        v = _f(r["amount"])
        if v:
            por_data[str(r["dt"])].append(v)

    por_ano_bruto: dict[str, float] = defaultdict(float)
    por_ano_conserv: dict[str, float] = defaultdict(float)
    datas_duplicadas = 0
    hoje = date.today()
    ult12_bruto = ult12_conserv = 0.0
    for dt, vals in por_data.items():
        ano = dt[:4]
        bruto, conserv = sum(vals), min(vals)
        por_ano_bruto[ano] += bruto
        por_ano_conserv[ano] += conserv
        if len(set(round(v, 6) for v in vals)) > 1:
            datas_duplicadas += 1
        try:
            d = date.fromisoformat(dt)
            if (hoje - d).days <= 365:
                ult12_bruto += bruto
                ult12_conserv += conserv
        except ValueError:
            pass

    suspeita_dup = datas_duplicadas >= 2 and ult12_bruto > ult12_conserv * 1.5
    dy = (lambda v: round(v / preco * 100, 1) if preco else None)
    return {
        "por_ano": {a: round(v, 4) for a, v in sorted(por_ano_conserv.items())[-8:]},
        "ult_12m_ps": round(ult12_conserv, 4),
        "dy_12m_pct": dy(ult12_conserv),
        "dy_12m_bruto_pct": dy(ult12_bruto),
        "suspeita_duplicacao_classe": suspeita_dup,
        "n_eventos": len(rows),
    }


def _precos(tk: str) -> dict:
    rows = _rows(
        """
        (SELECT date, close FROM market.historical_prices
          WHERE ticker = :t ORDER BY date DESC LIMIT 1)
        UNION ALL
        (SELECT date, close FROM market.historical_prices
          WHERE ticker = :t AND date <= CURRENT_DATE - INTERVAL '365 days'
          ORDER BY date DESC LIMIT 1)
        """,
        t=tk,
    )
    stats = _rows(
        """
        SELECT MIN(close) FILTER (WHERE date >= CURRENT_DATE - INTERVAL '365 days') AS min52,
               MAX(close) FILTER (WHERE date >= CURRENT_DATE - INTERVAL '365 days') AS max52
        FROM market.historical_prices WHERE ticker = :t
        """,
        t=tk,
    )
    out: dict = {"preco": None, "data_preco": None, "ret_12m_pct": None,
                 "min_52s": None, "max_52s": None}
    if rows:
        out["preco"] = _f(rows[0]["close"])
        out["data_preco"] = str(rows[0]["date"])
    if len(rows) >= 2 and _f(rows[1]["close"]):
        out["ret_12m_pct"] = round(
            (out["preco"] / _f(rows[1]["close"]) - 1) * 100, 1)
    if stats:
        out["min_52s"], out["max_52s"] = _f(stats[0]["min52"]), _f(stats[0]["max52"])
    return out


def _market_cap(tk: str) -> float | None:
    rows = _rows(
        """
        SELECT (metric_value)::numeric AS v
        FROM market.calculated_metric_vintages
        WHERE ticker = :t AND metric_name = 'marketCap' AND metric_value IS NOT NULL
        ORDER BY year DESC, available_at DESC LIMIT 1
        """,
        t=tk,
    )
    if not rows:
        rows = _rows(
            """
            SELECT metric_value AS v FROM market.calculated_metrics
            WHERE ticker = :t AND metric_name = 'marketCap' AND metric_value IS NOT NULL
            ORDER BY year DESC, updated_at DESC LIMIT 1
            """,
            t=tk,
        )
    return _f(rows[0]["v"]) if rows else None


def _metricas_snapshot(tk: str) -> dict:
    rows = _rows(
        """
        SELECT DISTINCT ON (metric_name) metric_name, metric_value
        FROM market.calculated_metrics
        WHERE ticker = :t AND metric_value IS NOT NULL
        ORDER BY metric_name, year DESC, quarter DESC NULLS LAST, updated_at DESC
        """,
        t=tk,
    )
    keep = ("DY", "P/L", "P/VP", "ROE", "ROIC", "Payout", "EV_EBIT",
            "Margem_Liquida", "Liquidez_Corrente", "Endividamento_Total")
    return {r["metric_name"]: round(_f(r["metric_value"]), 4)
            for r in rows if r["metric_name"] in keep and _f(r["metric_value"]) is not None}


def _eventos_societarios(tk: str, n: int = 12) -> dict:
    rows = _rows(
        """
        SELECT COALESCE(document_date, data) AS dt, COALESCE(categoria, tipo) AS cat,
               titulo
        FROM public.docs_corporativos
        WHERE ticker LIKE :pref
        ORDER BY COALESCE(document_date, data) DESC LIMIT :n
        """,
        pref=tk[:4] + "%", n=n,
    )
    eventos = [{"data": str(r["dt"]), "categoria": r["cat"],
                "titulo": (r["titulo"] or "")[:140]} for r in rows]
    return {
        "eventos": eventos,
        "n_docs": len(eventos),
        "docs_desde": eventos[-1]["data"] if eventos else None,
    }


def _valuation(mcap: float | None, serie: list[dict], preco: dict) -> dict:
    out: dict = {"market_cap_mi": _mi(mcap), **preco}
    ult = serie[-1] if serie else {}
    lucro = (ult.get("lucro_mi") or 0) * 1e6
    pl_pat = (ult.get("pl_mi") or 0) * 1e6
    ebit = (ult.get("ebit_mi") or 0) * 1e6
    nd = ult.get("div_liq_mi")
    if mcap and lucro > 0:
        out["pl_calc"] = round(mcap / lucro, 1)
    if mcap and pl_pat > 0:
        out["pvp_calc"] = round(mcap / pl_pat, 1)
    if mcap and ebit > 0 and nd is not None:
        out["ev_ebit_calc"] = round((mcap + nd * 1e6) / ebit, 1)
    out["ano_base_valuation"] = ult.get("ano")
    return out


def _sensibilidade_juros(serie: list[dict]) -> dict:
    ult = serie[-1] if serie else {}
    nd, pl = ult.get("div_liq_mi"), ult.get("pl_mi")
    if nd is None:
        return {"posicao": "indefinida",
                "regra": "Sem dado de dívida líquida — não assuma direção do efeito de juros."}
    if nd < 0:
        return {
            "posicao": "caixa_liquido", "div_liq_mi": nd,
            "regra": (f"CAIXA LÍQUIDO de R$ {abs(nd):,.0f} mi: Selic ALTA AUMENTA o lucro "
                      "(rendimento de aplicações); o risco assimétrico é a QUEDA da Selic "
                      "reduzir o resultado financeiro. Não escreva que juros altos "
                      "prejudicam esta empresa pelo canal da dívida."),
        }
    alav = round(nd / pl, 2) if pl else None
    if alav is not None and alav > 0.5:
        return {"posicao": "endividada", "div_liq_mi": nd, "div_liq_pl": alav,
                "regra": (f"Endividada (dív.líq./PL = {alav}): juros altos pressionam o "
                          "resultado financeiro; queda da Selic é alívio direto.")}
    return {"posicao": "moderada", "div_liq_mi": nd, "div_liq_pl": alav,
            "regra": "Alavancagem moderada: efeito de juros existe mas não domina a tese."}


def _checks(serie: list[dict], tris: dict, divs: dict, met: dict,
            docs: dict, val: dict) -> list[str]:
    """Red flags determinísticas — vão para o LLM e para a UI."""
    flags: list[str] = []
    if divs.get("suspeita_duplicacao_classe"):
        flags.append(
            "DADOS: proventos com valores distintos na mesma data-ex — provável "
            "duplicação por classe de ação na ingestão (DY do banco pode estar inflado; "
            f"12m bruto={divs.get('dy_12m_bruto_pct')}% vs conservador={divs.get('dy_12m_pct')}%).")
    dy_met = met.get("DY")
    dy_re = divs.get("dy_12m_pct")
    if dy_met is not None and dy_re and dy_met * 100 > dy_re * 1.6:
        flags.append(
            f"DADOS: métrica DY do banco ({dy_met*100:.1f}%) diverge do recomputado "
            f"({dy_re:.1f}%) — usar o recomputado.")
    if len(serie) >= 2:
        a, b = serie[-2], serie[-1]
        if (b.get("pl_mi") or 0) < (a.get("pl_mi") or 0) and (b.get("lucro_mi") or 0) > 0:
            flags.append(
                f"PATRIMÔNIO EM QUEDA COM LUCRO POSITIVO ({a['ano']}→{b['ano']}: "
                f"PL {a.get('pl_mi')}→{b.get('pl_mi')} R$ mi): distribuição acima do lucro "
                "(dividendo extraordinário/reversão de reservas) — dividendo atual pode não ser recorrente.")
    if serie and all(s.get("fco_mi") is None for s in serie):
        flags.append("COBERTURA: sem demonstração de fluxo de caixa no banco — qualidade do lucro não verificável.")
    if serie and all(s.get("ebitda_mi") is None for s in serie):
        flags.append("COBERTURA: EBITDA ausente na DRE estruturada.")
    if not docs.get("n_docs"):
        flags.append("COBERTURA: nenhum documento CVM indexado — parecer sem base documental.")
    if len(serie) < 5:
        flags.append(f"COBERTURA: apenas {len(serie)} ano(s) de DRE anual — histórico curto.")
    yoy = tris.get("yoy", {})
    if (yoy.get("lucro_yoy_pct") is not None and yoy["lucro_yoy_pct"] < -25):
        flags.append(f"MOMENTUM: lucro do último trimestre caiu {yoy['lucro_yoy_pct']}% "
                     f"a/a ({yoy.get('ref')}).")
    return flags


@st.cache_data(ttl=3600, show_spinner=False)
def build_dossie(ticker: str) -> dict:
    """Dossiê determinístico completo de um ticker (só banco, sem LLM)."""
    tk = ticker.strip().upper().replace(".SA", "")
    try:
        serie = _series_anuais(tk)
        tris = _trimestres(tk)
        precos = _precos(tk)
        divs = _dividendos(tk, precos.get("preco"))
        met = _metricas_snapshot(tk)
        docs = _eventos_societarios(tk)
        val = _valuation(_market_cap(tk), serie, precos)
        dossie = {
            "ticker": tk,
            **_ident(tk),
            "serie_anual": serie,
            "trimestres": tris,
            "dividendos": divs,
            "valuation": val,
            "metricas_banco": met,
            "sensibilidade_juros": _sensibilidade_juros(serie),
            "eventos_societarios": docs,
            "red_flags": [],
        }
        dossie["red_flags"] = _checks(serie, tris, divs, met, docs, val)
        return dossie
    except Exception as exc:  # banco fora, ticker inexistente etc.
        logger.warning("build_dossie(%s) falhou: %s", tk, exc)
        return {"ticker": tk, "erro": str(exc)[:300]}


# ─────────────────────────────────────────────────────────────────────────────
# Dossiê → texto de prompt
# ─────────────────────────────────────────────────────────────────────────────

def dossie_to_text(d: dict) -> str:
    if d.get("erro"):
        return f"DOSSIÊ INDISPONÍVEL: {d['erro']}"
    L: list[str] = []
    L.append(f"EMPRESA: {d['ticker']} — {d.get('nome')} | Setor: {d.get('setor')} / "
             f"{d.get('subsetor')} / {d.get('segmento')}")

    L.append("\nSÉRIE ANUAL (R$ mi) — ano | receita | ebit | lucro | PL | caixa | dív.líq | FCO | mrg.líq% | ROE%:")
    for s in d.get("serie_anual", []):
        L.append(f"  {s['ano']} | {s['receita_mi']} | {s['ebit_mi']} | {s['lucro_mi']} | "
                 f"{s['pl_mi']} | {s['caixa_mi']} | {s['div_liq_mi']} | {s['fco_mi']} | "
                 f"{s['margem_liq_pct']} | {s['roe_pct']}")

    tris = d.get("trimestres", {})
    if tris.get("serie"):
        L.append("\nTRIMESTRES RECENTES (R$ mi) — receita | lucro:")
        for t in tris["serie"]:
            L.append(f"  {t['ano']}T{t['tri']} | {t['receita_mi']} | {t['lucro_mi']}")
        if tris.get("yoy"):
            L.append(f"  Último tri a/a ({tris['yoy'].get('ref')}): "
                     f"receita {tris['yoy'].get('receita_yoy_pct')}% | "
                     f"lucro {tris['yoy'].get('lucro_yoy_pct')}%")

    dv = d.get("dividendos", {})
    L.append(f"\nDIVIDENDOS (R$/ação, dedup conservador por data-ex): por ano {dv.get('por_ano')}"
             f" | últimos 12m = {dv.get('ult_12m_ps')} (DY {dv.get('dy_12m_pct')}%)")
    if dv.get("suspeita_duplicacao_classe"):
        L.append("  ATENÇÃO: soma bruta no banco está inflada por duplicação de classe "
                 f"(DY bruto {dv.get('dy_12m_bruto_pct')}% — NÃO usar).")

    v = d.get("valuation", {})
    L.append(f"\nVALUATION (calculado dos dados brutos, ano-base {v.get('ano_base_valuation')}): "
             f"preço={v.get('preco')} ({v.get('data_preco')}) | ret.12m={v.get('ret_12m_pct')}% | "
             f"52s=[{v.get('min_52s')}–{v.get('max_52s')}] | mcap={v.get('market_cap_mi')} R$ mi | "
             f"P/L={v.get('pl_calc')} | P/VP={v.get('pvp_calc')} | EV/EBIT={v.get('ev_ebit_calc')}")
    if d.get("metricas_banco"):
        L.append(f"  Métricas do banco (checar contra o calculado): {d['metricas_banco']}")

    sj = d.get("sensibilidade_juros", {})
    L.append(f"\nREGRA DE JUROS (obrigatória, já decidida pelos dados): {sj.get('regra')}")

    ev = d.get("eventos_societarios", {})
    if ev.get("eventos"):
        L.append(f"\nEVENTOS SOCIETÁRIOS (docs CVM indexados: {ev['n_docs']}, desde {ev['docs_desde']}):")
        for e in ev["eventos"]:
            L.append(f"  {e['data']} [{e['categoria']}] {e['titulo']}")

    if d.get("red_flags"):
        L.append("\nRED FLAGS DETERMINÍSTICAS (verificadas em código, não são opinião):")
        for f in d["red_flags"]:
            L.append(f"  - {f}")
    return "\n".join(L)


# ─────────────────────────────────────────────────────────────────────────────
# Parecer LLM (narrativa sobre o dossiê)
# ─────────────────────────────────────────────────────────────────────────────

_PROMPT_PARECER = """\
Você é um analista de investimentos institucional cético, especializado em B3. Recebe abaixo um \
DOSSIÊ com todos os números JÁ CALCULADOS deterministicamente a partir do banco de dados, mais \
trechos datados de documentos CVM. Sua tarefa é NARRAR um parecer — você está PROIBIDO de \
calcular, extrapolar ou inventar números: todo número citado deve estar literalmente no dossiê \
ou nos trechos CVM (cite a data do documento ao usar um fato dele).

REGRAS DE HONESTIDADE (obrigatórias):
1. Siga a REGRA DE JUROS do dossiê ao descrever sensibilidade macro — ela já foi decidida pelos dados.
2. Separe dividendo RECORRENTE de EXTRAORDINÁRIO quando as red flags indicarem distribuição acima \
do lucro; nesse caso, diga explicitamente que o yield atual tem prazo de validade.
3. Riscos em ordem de MATERIALIDADE real (governança/controlador e sustentabilidade de lucro antes \
de riscos genéricos de setor). Frases que valem para qualquer empresa são proibidas.
4. Reporte as RED FLAGS de qualidade de dados na seção qualidade_dados — não as esconda.
5. VETO ("classificacao_selecao"="vetar") é exceção grave, não opinião de preço: use apenas com \
evidência no dossiê/documentos (ex.: interferência do controlador destruindo valor, lucro \
insustentável mascarado, dados insuficientes para confiar na tese, evento societário de alto risco). \
Ressalvas relevantes sem gravidade de veto → "aprovar_com_ressalvas".

CONTEXTO DE PORTFÓLIO: {portfolio_ctx}
PARES DO SEGMENTO: {peers_ctx}

=== DOSSIÊ DETERMINÍSTICO ===
{dossie}

=== TRECHOS DE DOCUMENTOS CVM (cronológicos, cabeçalho [data | tipo | título]) ===
{rag}

Responda APENAS com JSON válido, EXATAMENTE neste schema:
{{
  "perspectiva": "forte" | "moderada" | "fraca",
  "acao_sugerida": "manter" | "aumentar" | "reduzir" | "revisar",
  "confianca": <int 0-100>,
  "score_qualitativo": <int 0-100>,
  "classificacao_selecao": "aprovar" | "aprovar_com_ressalvas" | "vetar",
  "motivo_selecao": "<1-2 linhas: por que aprovar/ressalvar/vetar ESTA empresa>",
  "resumo": "<síntese do parecer, 4-6 linhas, com números do dossiê>",
  "alerta_principal": "<maior risco em 1 linha>",
  "proxima_acao": "<o que monitorar, 1 linha>",
  "riscos": ["<risco 1 (o mais material)>", "<risco 2>", "<risco 3>", "<risco 4 opcional>"],
  "catalisadores": ["<catalisador datado 1>", "<catalisador 2>"],
  "sensibilidade_macro": ["<fator 1 coerente com a REGRA DE JUROS>", "<fator 2>"],
  "alocacao_sugerida_pct": <float 0.0-25.0>,
  "justificativa_alocacao": "<1-2 linhas>",
  "tese_final": "<conclusão 2-3 linhas>",
  "relatorio": {{
    "empresa_hoje": "<o que a empresa É hoje: negócios, controlador, transformações relevantes — 3-6 linhas>",
    "resultados": "<leitura dos resultados anuais e trimestres recentes, qualidade do crescimento — 3-6 linhas>",
    "dividendos": "<recorrente vs extraordinário, sustentabilidade, próximos eventos datados — 3-5 linhas>",
    "valuation": "<múltiplos calculados e o que já está no preço — 2-4 linhas>",
    "governanca_controlador": "<eventos societários datados e o que revelam — 2-5 linhas>",
    "qualidade_dados": "<red flags e lacunas do banco que limitam este parecer — 2-4 linhas>"
  }}
}}
"""


def _parecer_fallback(tk: str, motivo: str = "LLM indisponível") -> dict:
    return {
        "perspectiva": "moderada", "acao_sugerida": "revisar",
        "confianca": 0, "score_qualitativo": 50,
        "classificacao_selecao": "aprovar_com_ressalvas",
        "motivo_selecao": f"Parecer não gerado ({motivo}) — mantido por padrão, sem veto.",
        "resumo": f"Parecer qualitativo indisponível para {tk}: {motivo}.",
        "alerta_principal": "Análise qualitativa não executada.",
        "proxima_acao": "Reexecutar quando o LLM estiver disponível.",
        "riscos": [], "catalisadores": [], "sensibilidade_macro": [],
        "alocacao_sugerida_pct": None, "justificativa_alocacao": "",
        "tese_final": "", "relatorio": {},
    }


@st.cache_data(ttl=86400, show_spinner=False)
def _parecer_llm_cached(prompt: str, _tk: str) -> dict:
    """Uma chamada LLM por (prompt) por dia — o prompt embute o hash do dossiê."""
    from core.llm_b3 import _call_llm, _parse_json, _report_model
    raw = _call_llm(prompt, model=_report_model())
    return _parse_json(raw, _parecer_fallback(_tk, "resposta não interpretável"))


def _sanitizar_parecer(p: dict, tk: str) -> dict:
    base = _parecer_fallback(tk, "campos ausentes")
    out = {**base, **(p or {})}
    if out.get("classificacao_selecao") not in _CLASSIFICACOES:
        out["classificacao_selecao"] = "aprovar_com_ressalvas"
    if not isinstance(out.get("relatorio"), dict):
        out["relatorio"] = {}
    return out


def gerar_parecer_empresa(
    ticker: str,
    rag_context: str = "",
    peers_ctx: str = "",
    portfolio_ctx: str = "",
) -> tuple[dict, dict]:
    """
    Constrói o dossiê determinístico e pede ao LLM apenas a narrativa.
    Retorna (parecer, dossie). Nunca levanta exceção: em falha de LLM devolve
    fallback neutro (sem veto) com o dossiê intacto.
    """
    tk = ticker.strip().upper().replace(".SA", "")
    dossie = build_dossie(tk)
    if dossie.get("erro"):
        return _parecer_fallback(tk, f"dossiê indisponível: {dossie['erro']}"), dossie
    prompt = _PROMPT_PARECER.format(
        portfolio_ctx=portfolio_ctx or "(sem contexto de portfólio)",
        peers_ctx=peers_ctx or "(sem pares mapeados)",
        dossie=dossie_to_text(dossie),
        rag=rag_context or "(nenhum trecho CVM disponível)",
    )
    try:
        parecer = _parecer_llm_cached(prompt, tk)
    except Exception as exc:
        logger.warning("Parecer LLM falhou para %s: %s", tk, exc)
        return _parecer_fallback(tk, str(exc)[:200]), dossie
    return _sanitizar_parecer(parecer, tk), dossie


# ─────────────────────────────────────────────────────────────────────────────
# Gate de seleção (Criação de Portfólio)
# ─────────────────────────────────────────────────────────────────────────────

def quali_gate_disponivel() -> bool:
    try:
        from core.llm_b3 import llm_disponivel
        return llm_disponivel()
    except Exception:
        return False


def avaliar_para_selecao(ticker: str) -> dict:
    """
    Avaliação enxuta para o gate de seleção: dossiê + RAG curto + parecer.
    Retorna {"classificacao", "motivo", "parecer", "dossie"}.
    Falha de LLM/dados NUNCA veta (fail-open): a estatística decide sozinha.
    """
    tk = ticker.strip().upper().replace(".SA", "")
    rag_ctx = ""
    try:
        from core.rag_b3 import format_rag_context, retrieve_chunks
        chunks, _ = retrieve_chunks(tk, top_k_total=40, months_back=36)
        rag_ctx = format_rag_context(chunks, max_chars=6000)
    except Exception:
        pass
    parecer, dossie = gerar_parecer_empresa(tk, rag_context=rag_ctx)
    return {
        "classificacao": parecer.get("classificacao_selecao", "aprovar_com_ressalvas"),
        "motivo": parecer.get("motivo_selecao", ""),
        "parecer": parecer,
        "dossie": dossie,
    }
