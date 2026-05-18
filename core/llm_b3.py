"""
core/llm_b3.py
Módulo LLM para análise de portfólio B3.

Provider: OpenAI (gpt-4o-mini por padrão).
Usa dados quantitativos do Supabase + contexto macro + conhecimento treinado
sobre setores/segmentos do mercado brasileiro para gerar análise institucional.
"""
from __future__ import annotations

import json
import logging
import os

import numpy as np
import pandas as pd
import streamlit as st

logger = logging.getLogger(__name__)

_MODEL_DEFAULT = "gpt-4o-mini"
_TEMPERATURE   = 0.2
_TIMEOUT       = 90


# ─────────────────────────────────────────────────────────────────────────────
# Cliente OpenAI (resource-cached — uma instância por sessão)
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_resource
def _get_openai_client():
    try:
        from openai import OpenAI
        from core.config import settings
        key = getattr(settings, "OPENAI_API_KEY", None) or os.environ.get("OPENAI_API_KEY", "")
        if not key:
            return None
        return OpenAI(api_key=key, timeout=_TIMEOUT)
    except Exception as exc:
        logger.warning("OpenAI client init falhou: %s", exc)
        return None


def llm_disponivel() -> bool:
    return _get_openai_client() is not None


def _call_llm(prompt: str, model: str = _MODEL_DEFAULT) -> str:
    client = _get_openai_client()
    if client is None:
        raise RuntimeError("OPENAI_API_KEY não configurada em .env / Streamlit Secrets.")
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=_TEMPERATURE,
    )
    return resp.choices[0].message.content


def _parse_json(raw: str, fallback: dict) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip().rstrip("`").strip()
    try:
        return json.loads(raw)
    except Exception:
        # Tenta extrair o JSON do texto se houver ruído ao redor
        try:
            start = raw.index("{")
            end   = raw.rindex("}") + 1
            return json.loads(raw[start:end])
        except Exception:
            logger.warning("JSON parse falhou — usando fallback. Raw: %s", raw[:300])
            return fallback


# ─────────────────────────────────────────────────────────────────────────────
# Formatadores de contexto quantitativo
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_multiplos(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return "  Sem múltiplos disponíveis."
    cols = [c for c in [
        "ROE", "ROIC", "ROA", "Margem_Liquida", "Margem_EBITDA",
        "DY", "P/VP", "P/L", "Endividamento_Total", "Liquidez_Corrente",
    ] if c in df.columns]
    if not cols:
        return "  Múltiplos indisponíveis."
    d = df.copy()
    if "Data" in d.columns:
        d["Data"] = pd.to_datetime(d["Data"], errors="coerce")
        d = d.dropna(subset=["Data"]).sort_values("Data").tail(3)
    else:
        d = d.tail(3)
    lines = []
    for _, row in d.iterrows():
        ano = ""
        if "Data" in row:
            try:
                ano = f"({int(pd.Timestamp(row['Data']).year)})"
            except Exception:
                pass
        vals = [f"{c}={float(row[c]):.2f}" for c in cols if pd.notna(row.get(c))]
        if vals:
            lines.append(f"  {ano} {', '.join(vals)}")
    return "\n".join(lines) if lines else "  Insuficiente."


def _fmt_dre(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return "  Sem DRE disponível."
    cols = [c for c in [
        "Receita_Liquida", "Lucro_Liquido", "EBITDA", "Divida_Liquida",
    ] if c in df.columns]
    if not cols:
        return "  DRE indisponível."
    d = df.copy()
    if "Data" in d.columns:
        d["Data"] = pd.to_datetime(d["Data"], errors="coerce")
        d = d.dropna(subset=["Data"]).sort_values("Data").tail(3)
    else:
        d = d.tail(3)
    lines = []
    for _, row in d.iterrows():
        ano = ""
        if "Data" in row:
            try:
                ano = f"({int(pd.Timestamp(row['Data']).year)})"
            except Exception:
                pass
        vals = []
        for c in cols:
            v = row.get(c)
            if pd.notna(v):
                v = float(v)
                if abs(v) >= 1_000_000_000:
                    vals.append(f"{c}={v / 1e9:.1f}B")
                elif abs(v) >= 1_000_000:
                    vals.append(f"{c}={v / 1e6:.0f}M")
                else:
                    vals.append(f"{c}={v:.2f}")
        if vals:
            lines.append(f"  {ano} {', '.join(vals)}")
    return "\n".join(lines) if lines else "  Insuficiente."


def _fmt_macro(macro_hist: dict) -> str:
    if not macro_hist:
        return "  Cenário macro não disponível."
    anos = sorted(macro_hist.keys(), reverse=True)[:2]
    lines = []
    for ano in sorted(anos):
        d = macro_hist[ano]
        parts = []
        if "selic" in d:
            parts.append(f"Selic={d['selic'] * 100:.2f}%a.a.")
        if "ipca" in d:
            parts.append(f"IPCA={d['ipca'] * 100:.2f}%")
        if "cambio" in d:
            parts.append(f"USD/BRL={d['cambio']:.2f}")
        if "pib" in d:
            parts.append(f"PIB={d['pib'] * 100:.1f}%")
        if parts:
            lines.append(f"  {ano}: {', '.join(parts)}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Prompts
# ─────────────────────────────────────────────────────────────────────────────

_PROMPT_EMPRESA = """\
Você é um analista de investimentos institucional especializado no mercado brasileiro de capitais (B3), \
com profundo conhecimento setorial e acesso ao repertório analítico de casas como XP, Bradesco BBI, \
Itaú BBA, BTG Pactual, Safra e JPMorgan Brasil. Integre dados quantitativos fornecidos com a perspectiva \
qualitativa de mercado sobre esta empresa, seu setor e o cenário macroeconômico atual.

EMPRESA: {ticker} — {nome}
SETOR: {setor} | SEGMENTO: {segmento}
PESO QUANTITATIVO NO PORTFÓLIO: {peso_pct:.1f}%
SCORE QUANTITATIVO (0-100): {score:.1f} | ALPHA vs SELIC (histórico): {alpha_selic:+.1f}%

=== MÚLTIPLOS FINANCEIROS (últimos 3 anos) ===
{multiplos}

=== DEMONSTRATIVO DE RESULTADOS (últimos 3 anos) ===
{dre}

=== CENÁRIO MACROECONÔMICO ===
{macro}

=== DOCUMENTOS CORPORATIVOS CVM/IPE (trechos relevantes) ===
{rag_context}

=== CONTEXTO DO PORTFÓLIO ===
{portfolio_ctx}

Com base nos dados acima, nos documentos CVM quando disponíveis, e no seu conhecimento sobre esta \
empresa, setor e macroeconomia brasileira, gere uma análise estruturada em JSON com EXATAMENTE \
este schema (sem campos extras, sem texto fora do JSON):

{{
  "perspectiva": "forte" | "moderada" | "fraca",
  "acao_sugerida": "manter" | "aumentar" | "reduzir" | "revisar",
  "confianca": <inteiro 0-100>,
  "score_qualitativo": <inteiro 0-100>,
  "resumo": "<síntese 3-5 linhas: tese de investimento atual>",
  "alerta_principal": "<maior risco ou preocupação em 1 linha>",
  "proxima_acao": "<o que monitorar no próximo ciclo, 1 linha>",
  "riscos": ["<risco 1>", "<risco 2>", "<risco 3>"],
  "catalisadores": ["<catalisador 1>", "<catalisador 2>"],
  "sensibilidade_macro": ["<fator macro relevante 1>", "<fator macro relevante 2>"],
  "alocacao_sugerida_pct": <float 0.0–25.0, percentual sugerido no portfólio>,
  "justificativa_alocacao": "<1-2 linhas justificando o peso sugerido>",
  "tese_final": "<conclusão integrada 2-3 linhas>"
}}
"""

_PROMPT_PORTFOLIO = """\
Você é um gestor de portfólio institucional com visão macro-setorial do mercado brasileiro. \
Analise o portfólio abaixo e produza um relatório consolidado que integre a dimensão quantitativa, \
qualitativa e macroeconômica. Considere a perspectiva de casas de análise especializadas (XP, BTG, Itaú BBA) \
para embasar sua avaliação da carteira como um todo.

=== COMPOSIÇÃO DO PORTFÓLIO ===
{composicao}

=== SÍNTESE DAS ANÁLISES INDIVIDUAIS ===
{resumos}

=== CENÁRIO MACROECONÔMICO ===
{macro}

Gere o relatório em JSON com EXATAMENTE este schema:

{{
  "qualidade_carteira": "alta" | "media" | "baixa",
  "perspectiva_12m": "construtiva" | "equilibrada" | "cautelosa",
  "confianca_media": <inteiro 0-100>,
  "score_medio": <inteiro 0-100>,
  "cobertura": "alta" | "media" | "baixa",
  "resumo_executivo": "<síntese 4-6 linhas do portfólio como um todo>",
  "relatorio_estrategico": "<análise estratégica detalhada 6-10 linhas: posicionamento setorial, riscos sistêmicos, oportunidades>",
  "papel_dos_ativos": "<como os ativos se complementam em termos de risco/retorno e setores, 3-5 linhas>",
  "pontos_fortes": ["<força 1>", "<força 2>", "<força 3>"],
  "pontos_fracos": ["<fraqueza 1>", "<fraqueza 2>"],
  "sintese_alocacao": "<como a redistribuição ponderada melhora o portfólio, 2-3 linhas>",
  "conclusao_estrategica": "<conclusão 4-6 linhas com gatilhos de monitoramento e horizonte de revisão>"
}}
"""


# ─────────────────────────────────────────────────────────────────────────────
# API pública
# ─────────────────────────────────────────────────────────────────────────────

def analisar_empresa(
    ticker: str,
    nome: str,
    setor: str,
    segmento: str,
    peso_pct: float,
    score: float,
    alpha_selic: float,
    df_mult: pd.DataFrame,
    df_fin: pd.DataFrame,
    macro_hist: dict,
    portfolio_ctx: str,
    rag_context: str = "",
    model: str = _MODEL_DEFAULT,
) -> dict:
    """Chama LLM e retorna dict com análise individual da empresa."""
    prompt = _PROMPT_EMPRESA.format(
        ticker=ticker, nome=nome, setor=setor or "N/D", segmento=segmento or "N/D",
        peso_pct=peso_pct, score=score, alpha_selic=alpha_selic,
        multiplos=_fmt_multiplos(df_mult),
        dre=_fmt_dre(df_fin),
        macro=_fmt_macro(macro_hist),
        rag_context=rag_context or "  Nenhum documento CVM disponível para este ativo.",
        portfolio_ctx=portfolio_ctx,
    )
    raw = _call_llm(prompt, model=model)
    return _parse_json(raw, _fallback_empresa(ticker, peso_pct))


def analisar_portfolio(
    items_analisados: list[dict],
    macro_hist: dict,
    model: str = _MODEL_DEFAULT,
) -> dict:
    """Chama LLM e retorna dict com análise consolidada do portfólio."""
    comp_lines, res_lines = [], []
    for it in items_analisados:
        tk  = it.get("ticker", "")
        w   = it.get("peso_pct", 0.0)
        sc  = it.get("score", 0.0)
        an  = it.get("analise", {})
        persp = an.get("perspectiva", "N/D")
        resumo = an.get("resumo", "")[:180]
        comp_lines.append(f"  {tk}: peso={w:.1f}% score={sc:.1f} perspectiva={persp}")
        if resumo:
            res_lines.append(f"  {tk}: {resumo}")

    prompt = _PROMPT_PORTFOLIO.format(
        composicao="\n".join(comp_lines) or "  (vazio)",
        resumos="\n".join(res_lines) or "  (sem resumos)",
        macro=_fmt_macro(macro_hist),
    )
    raw = _call_llm(prompt, model=model)
    return _parse_json(raw, _fallback_portfolio())


# ─────────────────────────────────────────────────────────────────────────────
# Redistribuição de pesos quanti + quali
# ─────────────────────────────────────────────────────────────────────────────

def redistribuir_pesos(
    items_analisados: list[dict],
    mode: str = "Rígida",
) -> dict[str, float]:
    """
    Combina score quantitativo + score qualitativo LLM.
    mode="Rígida": exclui empresas com perspectiva "fraca".
    mode="Flexível": mantém todas, peso mínimo 2%.
    """
    scores: dict[str, float] = {}
    for it in items_analisados:
        tk   = it.get("ticker", "")
        an   = it.get("analise", {})
        persp = an.get("perspectiva", "moderada")

        if mode == "Rígida" and persp == "fraca":
            continue

        score_q   = float(it.get("score", 50)) / 100.0
        score_llm = float(an.get("score_qualitativo", 50)) / 100.0
        confianca = float(an.get("confianca", 50)) / 100.0
        alpha_n   = min(max(float(it.get("alpha_selic", 0)) / 100.0, -0.5), 1.0)

        mult = {"forte": 1.20, "moderada": 1.00, "fraca": 0.65}.get(persp, 1.00)
        combined = (score_q * 0.60 + score_llm * 0.40) * (1 + alpha_n * 0.15) * mult * confianca

        scores[tk] = max(combined, 1e-6)

    if not scores:
        return {}

    total = sum(scores.values())
    raw = {tk: v / total for tk, v in scores.items()}

    min_w = 0.03 if mode == "Rígida" else 0.02
    max_w = 0.25
    for _ in range(15):
        clipped = {tk: min(max(v, min_w), max_w) for tk, v in raw.items()}
        t = sum(clipped.values())
        raw = {tk: v / t for tk, v in clipped.items()}

    return raw


# ─────────────────────────────────────────────────────────────────────────────
# Fallbacks
# ─────────────────────────────────────────────────────────────────────────────

def _fallback_empresa(ticker: str, peso_pct: float) -> dict:
    return {
        "perspectiva": "moderada",
        "acao_sugerida": "revisar",
        "confianca": 50,
        "score_qualitativo": 50,
        "resumo": f"Análise de {ticker} indisponível (erro de parsing).",
        "alerta_principal": "Verificar dados manualmente.",
        "proxima_acao": "Acompanhar resultados trimestrais.",
        "riscos": ["Dados insuficientes para análise completa"],
        "catalisadores": [],
        "sensibilidade_macro": ["juros", "câmbio"],
        "alocacao_sugerida_pct": round(peso_pct, 1),
        "justificativa_alocacao": "Mantém peso quantitativo original por falta de dados qualitativos.",
        "tese_final": "Análise pendente — manter posição até revisão.",
    }


def _fallback_portfolio() -> dict:
    return {
        "qualidade_carteira": "media",
        "perspectiva_12m": "equilibrada",
        "confianca_media": 50,
        "score_medio": 50,
        "cobertura": "media",
        "resumo_executivo": "Relatório indisponível.",
        "relatorio_estrategico": "",
        "papel_dos_ativos": "",
        "pontos_fortes": [],
        "pontos_fracos": [],
        "sintese_alocacao": "",
        "conclusao_estrategica": "",
    }
