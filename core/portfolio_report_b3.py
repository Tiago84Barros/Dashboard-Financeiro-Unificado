"""Relatório institucional exclusivo da Avaliação de Portfólio B3.

Este módulo isola a evolução analítica solicitada para
``views/analise_portfolio_b3.py``. Ele reaproveita o dossiê determinístico,
os loaders e a cadeia de provedores existentes, mas não altera o prompt do
gate qualitativo, do Score, da análise individual B3 ou das empresas dos EUA.

Responsabilidades:
* priorizar pares setoriais presentes na carteira e completar com o universo B3;
* fornecer histórico e qualidade do caixa já calculados para a LLM interpretar;
* impor um contrato institucional, causal e sem recomendações simplistas;
* validar cenários, notas e score ponderado antes de devolver o relatório à UI.
"""
from __future__ import annotations

import copy
import logging
import re
from typing import Any

import numpy as np
import pandas as pd

from core.dossie_b3 import build_dossie, dossie_to_text
from core.llm_b3 import _call_llm, _parse_json, _report_model
from core.llm_context_b3 import (
    compute_segment_peers,
    get_company_fundamentals_context,
    get_sector_comparison_context,
)

logger = logging.getLogger(__name__)


QUALITATIVE_WEIGHTS: dict[str, tuple[str, int]] = {
    "modelo_negocio": ("Modelo de Negócio", 10),
    "vantagem_competitiva": ("Vantagem Competitiva", 12),
    "governanca": ("Governança", 8),
    "eficiencia_operacional": ("Eficiência Operacional", 10),
    "saude_financeira": ("Saúde Financeira", 12),
    "crescimento": ("Crescimento", 10),
    "geracao_caixa": ("Geração de Caixa", 12),
    "rentabilidade": ("Rentabilidade", 10),
    "qualidade_resultados": ("Qualidade dos Resultados", 10),
    "valuation": ("Valuation", 6),
}

_FORBIDDEN_RECOMMENDATIONS = (
    (re.compile(r"\bvale\s+comprar\b", re.I), "é compatível com o perfil descrito"),
    (re.compile(r"\bpode\s+entrar\s+na\s+carteira\b", re.I), "pode ser compatível com a carteira descrita"),
    (re.compile(r"\bsubstitua(?:-o|-a)?\s+por\b", re.I), "compare complementarmente com"),
    (re.compile(r"\b(?:compre|venda)\b", re.I), "avalie"),
)


def prioritize_peer_tickers(
    candidates: list[str],
    portfolio_tickers: list[str] | tuple[str, ...],
    target: str,
    max_peers: int = 8,
) -> tuple[list[str], list[str]]:
    """Ordena pares válidos: primeiro os que já estão na carteira, depois B3."""
    tk = str(target).strip().upper().replace(".SA", "")
    portfolio = {
        str(v).strip().upper().replace(".SA", "")
        for v in portfolio_tickers if v
    }
    clean: list[str] = []
    for candidate in candidates:
        peer = str(candidate).strip().upper().replace(".SA", "")
        if not peer or peer == tk or peer in clean:
            continue
        clean.append(peer)
    in_portfolio = [p for p in clean if p in portfolio][:max_peers]
    remaining = max(0, max_peers - len(in_portfolio))
    from_universe = [p for p in clean if p not in portfolio][:remaining]
    return in_portfolio, from_universe


def build_peer_context(
    ticker: str,
    portfolio_tickers: list[str] | tuple[str, ...],
    *,
    sector: str | None = None,
    max_peers: int = 8,
) -> str:
    """Monta comparação primária por pares e deixa a carteira como suplemento."""
    tk = str(ticker).strip().upper().replace(".SA", "")
    candidates, comparison_level = compute_segment_peers(tk, max_peers=max(12, max_peers))
    in_portfolio, from_universe = prioritize_peer_tickers(
        candidates, portfolio_tickers, tk, max_peers=max_peers,
    )
    selected = in_portfolio + from_universe
    fundamentals = get_company_fundamentals_context([tk, *selected], max_n=max_peers + 1)
    sector_medians = get_sector_comparison_context(
        segments=[sector] if sector else None,
        portfolio_tickers=[tk],
    )
    lines = [
        f"HIERARQUIA DE PARES DE {tk}: comparação primária por {comparison_level or 'setor B3'}.",
        "  PARES DO MESMO SETOR JÁ NA CARTEIRA (prioridade): "
        + (", ".join(in_portfolio) if in_portfolio else "nenhum"),
        "  PARES COMPLEMENTARES DO UNIVERSO B3: "
        + (", ".join(from_universe) if from_universe else "nenhum com dados suficientes"),
        "  REGRA: comparação com ativos de outros setores da carteira é apenas suplementar; "
        "não a use para concluir se o ativo está caro ou barato.",
        fundamentals or "FUNDAMENTOS DOS PARES: indisponíveis.",
        sector_medians or "MEDIANAS SETORIAIS: indisponíveis.",
    ]
    return "\n".join(lines)


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _format_number(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "N/D"
    if abs(number) >= 1_000_000_000:
        return f"{number / 1_000_000_000:.2f} bi"
    if abs(number) >= 1_000_000:
        return f"{number / 1_000_000:.1f} mi"
    return f"{number:.2f}"


def build_financial_history_context(df_fin: pd.DataFrame | None) -> str:
    """Serializa até 10 anos e calcula razões de qualidade do lucro em Python."""
    if df_fin is None or df_fin.empty:
        return (
            "HISTÓRICO FINANCEIRO: indisponível. Não infira tendência, FCF ou "
            "conversão de caixa sem dados."
        )
    frame = df_fin.copy()
    if "Data" in frame.columns:
        frame["Data"] = pd.to_datetime(frame["Data"], errors="coerce")
        frame = frame.dropna(subset=["Data"]).sort_values("Data").tail(10)
    else:
        frame = frame.tail(10)
    cols = [
        c for c in (
            "Receita_Liquida", "EBITDA", "Lucro_Liquido", "Patrimonio_Liquido",
            "Divida_Liquida", "FCO", "FCF",
        ) if c in frame.columns
    ]
    lines = [
        "HISTÓRICO FINANCEIRO (até 10 anos; valores absolutos na moeda da base):",
    ]
    for idx, row in frame.iterrows():
        year = str(idx)
        if "Data" in row and pd.notna(row.get("Data")):
            year = str(pd.Timestamp(row["Data"]).year)
        parts = [f"{col}={_format_number(row.get(col))}" for col in cols]
        revenue = _safe_float(row.get("Receita_Liquida"))
        ebitda = _safe_float(row.get("EBITDA"))
        profit = _safe_float(row.get("Lucro_Liquido"))
        fco = _safe_float(row.get("FCO"))
        if revenue:
            if ebitda is not None:
                parts.append(f"Margem_EBITDA={ebitda / revenue * 100:.1f}%")
            if profit is not None:
                parts.append(f"Margem_Liquida={profit / revenue * 100:.1f}%")
        if profit and fco is not None:
            parts.append(f"FCO_Lucro={fco / profit:.2f}x")
        lines.append(f"  {year}: " + " | ".join(parts))

    def _latest_ratio(numerator: str, denominator: str) -> str:
        if numerator not in frame.columns or denominator not in frame.columns:
            return "N/D"
        for _, row in frame.iloc[::-1].iterrows():
            n, d = _safe_float(row.get(numerator)), _safe_float(row.get(denominator))
            if n is not None and d not in (None, 0.0):
                return f"{n / d:.2f}x"
        return "N/D"

    positive_fco = None
    if "FCO" in frame.columns:
        values = pd.to_numeric(frame["FCO"], errors="coerce").dropna()
        positive_fco = f"{int((values > 0).sum())}/{len(values)} anos" if len(values) else None
    positive_fcf = None
    if "FCF" in frame.columns:
        values = pd.to_numeric(frame["FCF"], errors="coerce").dropna()
        positive_fcf = f"{int((values > 0).sum())}/{len(values)} anos" if len(values) else None

    lines.extend([
        "QUALIDADE DO RESULTADO — cálculos determinísticos:",
        f"  Conversão FCO/lucro mais recente: {_latest_ratio('FCO', 'Lucro_Liquido')}",
        f"  Conversão FCF/lucro mais recente: {_latest_ratio('FCF', 'Lucro_Liquido')}",
        f"  FCO positivo: {positive_fco or 'N/D'} | FCF positivo: {positive_fcf or 'N/D'}",
        "  FCO não é FCF. Se FCF estiver N/D, declare a limitação; não use FCO como substituto.",
    ])
    return "\n".join(lines)


def build_multiples_history_context(df_mult: pd.DataFrame | None) -> str:
    """Histórico de rentabilidade, margens, alavancagem e múltiplos."""
    if df_mult is None or df_mult.empty:
        return (
            "HISTÓRICO DE INDICADORES: indisponível. Não conclua tendência de "
            "ROIC, margens ou múltiplos a partir de um único snapshot."
        )
    frame = df_mult.copy()
    date_col = next((c for c in ("Data", "data") if c in frame.columns), None)
    if date_col:
        frame[date_col] = pd.to_datetime(frame[date_col], errors="coerce")
        frame = frame.dropna(subset=[date_col]).sort_values(date_col).tail(10)
    else:
        frame = frame.tail(10)
    percent_cols = {
        "ROE", "ROIC", "Margem_Liquida", "Margem_Operacional", "DY", "Payout",
    }
    cols = [
        c for c in (
            "ROE", "ROIC", "Margem_Liquida", "Margem_Operacional",
            "Endividamento_Total", "Liquidez_Corrente", "P/L", "P/VP",
            "EV_EBIT", "P_FCO", "DY", "Payout",
        ) if c in frame.columns
    ]
    lines = ["HISTÓRICO DE INDICADORES E MÚLTIPLOS (até 10 anos):"]
    for idx, row in frame.iterrows():
        year = str(idx)
        if date_col and pd.notna(row.get(date_col)):
            year = str(pd.Timestamp(row[date_col]).year)
        parts: list[str] = []
        for col in cols:
            value = _safe_float(row.get(col))
            if value is None:
                continue
            if col in percent_cols:
                value = value * 100 if abs(value) <= 2.0 else value
                parts.append(f"{col}={value:.1f}%")
            else:
                parts.append(f"{col}={value:.2f}x")
        if parts:
            lines.append(f"  {year}: " + " | ".join(parts))
    return "\n".join(lines) if len(lines) > 1 else "HISTÓRICO DE INDICADORES: sem valores utilizáveis."


def _format_macro(macro_hist: dict | None) -> str:
    if not macro_hist:
        return "CENÁRIO MACRO: indisponível."
    lines = ["CENÁRIO MACRO BRASIL:"]
    for year in sorted(macro_hist)[-3:]:
        data = macro_hist[year] or {}
        parts: list[str] = []
        for key, label, scale in (
            ("selic", "Selic", 100), ("ipca", "IPCA", 100), ("pib", "PIB", 100),
        ):
            value = _safe_float(data.get(key))
            if value is not None:
                parts.append(f"{label}={value * scale:.2f}%")
        fx = _safe_float(data.get("cambio"))
        if fx is not None:
            parts.append(f"USD/BRL={fx:.2f}")
        if parts:
            lines.append(f"  {year}: " + " | ".join(parts))
    return "\n".join(lines)


def _weights_contract() -> str:
    return ", ".join(
        f'"{key}": {weight}% ({label})'
        for key, (label, weight) in QUALITATIVE_WEIGHTS.items()
    )


_PROMPT_COMPANY_PORTFOLIO = """\
Você é um analista sênior de ações brasileiras preparando uma nota de diligência para um gestor.
Esta chamada pertence EXCLUSIVAMENTE à aba Avaliação de Portfólio B3. Produza interpretação causal,
não uma enumeração de indicadores. Todo fato ou número deve vir do contexto; inferências devem ser
marcadas como inferência. Se a evidência não sustentar uma causa, diga "causa não confirmada nos dados".

EMPRESA: {ticker} — {name}
SETOR: {sector} | SUBSETOR: {subsector} | SEGMENTO: {segment}

=== DOSSIÊ DETERMINÍSTICO ===
{dossier}

=== HISTÓRICO, TENDÊNCIAS E QUALIDADE DO RESULTADO ===
{financial_history}

{multiples_history}

=== PEER ANALYSIS — ORDEM OBRIGATÓRIA ===
{peer_context}

=== MACRO BRASIL ===
{macro}

=== EVENTOS E DOCUMENTOS CVM/IPE ===
{rag_context}

=== CONTEXTO SUPLEMENTAR DA CARTEIRA ===
{portfolio_context}

REGRAS ANALÍTICAS OBRIGATÓRIAS:
1. Compare valuation prioritariamente com pares do mesmo setor/segmento. Pares da carteira têm
   prioridade quando são setorialmente comparáveis; complete com o universo B3. Outros setores são
   apenas contexto de diversificação.
2. Explique por que os múltiplos podem estar baixos/altos: conecte expectativa, tendência operacional,
   balanço, qualidade do lucro, governança, regulação e eventos. Não atribua opinião ao "mercado" sem
   evidência; nesse caso use "os múltiplos sugerem".
3. Em Qualidade dos Resultados, trate lucro, FCO, FCF, conversão caixa/lucro, recorrência e itens
   extraordinários. FCO não é FCF. Ausência de dado reduz confiança e nota, não autoriza invenção.
4. Classifique cada tendência relevante como acelerando, desacelerando, estável ou deteriorando e
   explique o mecanismo. Não conclua tendência com apenas um ponto.
5. Catalisadores e riscos devem ser específicos, ligados a uma métrica, evento, janela de evidência ou
   transmissão econômica. É proibido usar listas genéricas sem explicar o efeito.
6. Não escreva "vale comprar", "compre", "venda", "substitua por" ou "pode entrar na carteira".
   Descreva perfil de investidor, horizonte, tolerância a volatilidade e condições de adequação.
7. Os cenários Otimista/Base/Pessimista devem somar 100%, explicar probabilidade e mecanismo de impacto.
   Não invente preço-alvo. Use impacto qualitativo quando não houver modelo de preço.
8. Eventos de fraude, governança, revisão contábil, regulação, M&A ou estratégia só podem ser citados
   quando estiverem no dossiê/RAG; diferencie fato documentado de inferência.
9. A conclusão deve responder: cara/justa/barata; desconto justificável; pessimismo/otimismo implícito;
   risco-retorno; principal positivo; principal risco. Termine com resumo executivo de até cinco linhas.
10. Score qualitativo: notas 0–10, justificativa causal e evidência/lacuna para cada dimensão. Pesos:
    {weights_contract}. O código recalculará a média ponderada; não manipule a nota para recomendar ação.

Responda somente JSON válido, sem markdown, com exatamente esta estrutura principal (campos internos
descritos são obrigatórios):
{{
  "perspectiva": "forte|moderada|fraca",
  "confianca": <inteiro de 0 a 100 — NUNCA fração; 85 significa 85%, 0.85 é inválido>,
  "resumo": "síntese analítica de até cinco linhas",
  "relatorio": {{
    "empresa_hoje": "modelo econômico e fonte de valor",
    "analise_pares": "comparação setorial e leitura dos descontos/prêmios",
    "valuation_interpretado": "múltiplos, causas prováveis, expectativas e justificativa",
    "tendencias": "receita, EBITDA, lucro, margens, ROE, ROIC, dívida e caixa",
    "qualidade_resultados": "lucro, FCO, FCF, conversão, recorrência e extraordinários",
    "governanca_controlador": "governança e alocação de capital com evidência disponível",
    "eventos_relevantes": "fatos documentados e seu efeito potencial",
    "qualidade_dados": "lacunas que limitam a leitura"
  }},
  "riscos": [{{"risco": "", "mecanismo": "", "indicador_monitorado": ""}}],
  "catalisadores": [{{"catalisador": "", "mecanismo": "", "janela_ou_gatilho": ""}}],
  "sensibilidade_macro": ["fator e transmissão específica"],
  "cenarios": [
    {{"cenario": "Otimista", "probabilidade_pct": 25, "impacto_esperado": "", "fundamentacao": ""}},
    {{"cenario": "Base", "probabilidade_pct": 50, "impacto_esperado": "", "fundamentacao": ""}},
    {{"cenario": "Pessimista", "probabilidade_pct": 25, "impacto_esperado": "", "fundamentacao": ""}}
  ],
  "score_qualitativo_detalhado": {{
    "modelo_negocio": {{"nota": 0, "justificativa": "", "evidencia_ou_lacuna": ""}},
    "vantagem_competitiva": {{"nota": 0, "justificativa": "", "evidencia_ou_lacuna": ""}},
    "governanca": {{"nota": 0, "justificativa": "", "evidencia_ou_lacuna": ""}},
    "eficiencia_operacional": {{"nota": 0, "justificativa": "", "evidencia_ou_lacuna": ""}},
    "saude_financeira": {{"nota": 0, "justificativa": "", "evidencia_ou_lacuna": ""}},
    "crescimento": {{"nota": 0, "justificativa": "", "evidencia_ou_lacuna": ""}},
    "geracao_caixa": {{"nota": 0, "justificativa": "", "evidencia_ou_lacuna": ""}},
    "rentabilidade": {{"nota": 0, "justificativa": "", "evidencia_ou_lacuna": ""}},
    "qualidade_resultados": {{"nota": 0, "justificativa": "", "evidencia_ou_lacuna": ""}},
    "valuation": {{"nota": 0, "justificativa": "", "evidencia_ou_lacuna": ""}}
  }},
  "adequacao_investidor": {{"perfil": "", "horizonte": "", "tolerancia_volatilidade": "", "condicoes": ""}},
  "conclusao": {{
    "faixa_valor": "cara|justa|barata|indeterminada",
    "desconto_justificavel": "",
    "percepcao_mercado": "pessimista|neutra|otimista|indeterminada",
    "risco_retorno": "",
    "principal_positivo": "",
    "principal_risco": "",
    "resumo_executivo": "até cinco linhas"
  }}
}}
"""


_PROMPT_PORTFOLIO = """\
Você é um gestor de ações brasileiras revisando uma carteira como conjunto. Use somente as análises
individuais e o macro abaixo. Explique causa e efeito, concentração, complementaridade, transmissão de
riscos e condições de adequação. Não dê ordens de compra, venda ou substituição. A comparação de
valuation de cada empresa já foi feita contra pares setoriais; não compare múltiplos entre setores.

=== COMPOSIÇÃO E LEITURAS INDIVIDUAIS ===
{items_context}

=== MACRO BRASIL ===
{macro}

=== SEGUNDA FONTE (WEB) SOBRE OS MESMOS FUNDAMENTOS ===
{web_context}

Responda somente JSON válido com este schema. Preserve os campos legados porque a interface os consome:
{{
  "qualidade_carteira": "alta|media|baixa",
  "perspectiva_12m": "construtiva|equilibrada|cautelosa",
  "confianca_media": 0,
  "score_medio": 0,
  "cobertura": "alta|media|baixa",
  "resumo_executivo": "até cinco linhas, decisão central e principal risco",
  "relatorio_estrategico": "leitura causal do conjunto, sem recomendação simplista",
  "papel_dos_ativos": "como exposições se complementam ou concentram",
  "pontos_fortes": ["força específica e mecanismo"],
  "pontos_fracos": ["fragilidade específica e mecanismo"],
  "sintese_alocacao": "como o método quanti+quali altera exposições; não dê ordem de negociação",
  "diagnostico_causal": "choques -> transmissão -> impacto na carteira",
  "riscos_transmissao": [{{"risco": "", "ativos_expostos": [""], "mecanismo": "", "monitoramento": ""}}],
  "catalisadores_portfolio": [{{"catalisador": "", "ativos_expostos": [""], "mecanismo": ""}}],
  "adequacao_carteira": {{"perfil": "", "horizonte": "", "volatilidade": "", "condicoes": ""}},
  "conclusao_estrategica": "conclusão em até cinco linhas com risco-retorno e gatilhos de revisão"
}}
"""


def build_company_prompt(
    ticker: str,
    dossier: dict,
    df_fin: pd.DataFrame | None,
    df_mult: pd.DataFrame | None,
    macro_hist: dict | None,
    peer_context: str,
    rag_context: str,
    portfolio_context: str,
) -> str:
    # O dossiê B3 atual mantém identidade no topo; o fallback aninhado preserva
    # compatibilidade com snapshots auxiliares usados em testes/vitrines.
    identity = dossier.get("identificacao") or dossier
    try:
        dossier_text = dossie_to_text(dossier)
    except (KeyError, TypeError):
        dossier_text = str(dossier)
    return _PROMPT_COMPANY_PORTFOLIO.format(
        ticker=ticker,
        name=identity.get("nome") or ticker,
        sector=identity.get("setor") or "N/D",
        subsector=identity.get("subsetor") or "N/D",
        segment=identity.get("segmento") or "N/D",
        dossier=dossier_text,
        financial_history=build_financial_history_context(df_fin),
        multiples_history=build_multiples_history_context(df_mult),
        peer_context=peer_context or "PARES: indisponíveis; não conclua prêmio/desconto setorial.",
        macro=_format_macro(macro_hist),
        rag_context=rag_context or "Nenhum trecho CVM/IPE recuperado; não invente eventos.",
        portfolio_context=portfolio_context or "Sem contexto suplementar da carteira.",
        weights_contract=_weights_contract(),
    )


def _fallback_company(ticker: str, reason: str) -> dict:
    return {
        "perspectiva": "moderada",
        "confianca": 0,
        "score_qualitativo": 50,
        "score_qualitativo_ponderado": 5.0,
        "resumo": f"Relatório institucional de {ticker} indisponível: {reason}.",
        "relatorio": {},
        "riscos": [],
        "catalisadores": [],
        "sensibilidade_macro": [],
        "cenarios": [],
        "score_qualitativo_detalhado": {},
        "adequacao_investidor": {},
        "conclusao": {
            "faixa_valor": "indeterminada",
            "desconto_justificavel": "Dados insuficientes.",
            "percepcao_mercado": "indeterminada",
            "risco_retorno": "Não mensurável com segurança.",
            "principal_positivo": "Não determinado.",
            "principal_risco": "Cobertura insuficiente.",
            "resumo_executivo": "Relatório indisponível; revisar dados e provedores.",
        },
    }


def _sanitize_text(text: Any) -> str:
    value = "" if text is None else str(text).strip()
    for pattern, replacement in _FORBIDDEN_RECOMMENDATIONS:
        value = pattern.sub(replacement, value)
    return value


def _sanitize_recursive(value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, list):
        return [_sanitize_recursive(v) for v in value]
    if isinstance(value, dict):
        return {k: _sanitize_recursive(v) for k, v in value.items()}
    return value


def _normalize_scenarios(raw: Any) -> list[dict]:
    by_name: dict[str, dict] = {}
    for row in raw if isinstance(raw, list) else []:
        if not isinstance(row, dict):
            continue
        name = _sanitize_text(row.get("cenario")).lower()
        canonical = next(
            (c for c in ("Otimista", "Base", "Pessimista") if c.lower() in name), None,
        )
        if canonical:
            by_name[canonical] = dict(row)
    defaults = {"Otimista": 25.0, "Base": 50.0, "Pessimista": 25.0}
    rows: list[dict] = []
    probabilities: list[float] = []
    for name in ("Otimista", "Base", "Pessimista"):
        row = by_name.get(name, {})
        probability = _safe_float(row.get("probabilidade_pct"))
        probabilities.append(max(0.0, min(100.0, probability if probability is not None else defaults[name])))
        rows.append({
            "cenario": name,
            "probabilidade_pct": 0.0,
            "impacto_esperado": _sanitize_text(row.get("impacto_esperado")),
            "fundamentacao": _sanitize_text(row.get("fundamentacao")),
        })
    total = sum(probabilities)
    if total <= 0:
        probabilities = [25.0, 50.0, 25.0]
        total = 100.0
    normalized = [round(v / total * 100, 1) for v in probabilities]
    normalized[1] = round(normalized[1] + 100.0 - sum(normalized), 1)
    for row, probability in zip(rows, normalized):
        row["probabilidade_pct"] = probability
    return rows


def _normalize_scores(raw: Any) -> tuple[dict, float, int]:
    source = raw if isinstance(raw, dict) else {}
    normalized: dict[str, dict] = {}
    weighted_sum = 0.0
    total_weight = 0
    for key, (label, weight) in QUALITATIVE_WEIGHTS.items():
        item = source.get(key) if isinstance(source.get(key), dict) else {}
        note = _safe_float(item.get("nota"))
        note = max(0.0, min(10.0, note if note is not None else 5.0))
        normalized[key] = {
            "label": label,
            "nota": round(note, 1),
            "peso_pct": weight,
            "justificativa": _sanitize_text(item.get("justificativa")),
            "evidencia_ou_lacuna": _sanitize_text(item.get("evidencia_ou_lacuna")),
        }
        weighted_sum += note * weight
        total_weight += weight
    score_10 = round(weighted_sum / total_weight, 2) if total_weight else 5.0
    return normalized, score_10, int(round(score_10 * 10))


def _normalize_confidence(raw: Any) -> int:
    """Confiança em 0–100, tolerando a LLM responder em fração.

    O contrato do prompt pede ``<int 0-100>``, mas "confiança" é palavra que
    puxa o modelo para probabilidade: ele devolve 0.85 e o arredondamento
    direto virava **1** no card — o mesmo 1 que um analista leria como "1% de
    confiança". Como 0 < v <= 1 é indistinguível de uma confiança de 1%, que
    nenhum relatório útil emite, a leitura por fração é a correta.

    Zero continua zero de propósito: é o valor dos fallbacks e a UI o usa para
    detectar que o relatório não foi gerado.
    """
    value = _safe_float(raw)
    if value is None or value <= 0:
        return 0
    if value <= 1.0:
        value *= 100.0
    return int(round(min(100.0, value)))


def sanitize_company_report(raw: Any, ticker: str) -> dict:
    """Valida invariantes e mantém chaves legadas usadas pela tela/alocação."""
    if not isinstance(raw, dict):
        return _fallback_company(ticker, "resposta não interpretável")
    report = _sanitize_recursive(copy.deepcopy(raw))
    report["perspectiva"] = (
        report.get("perspectiva") if report.get("perspectiva") in {"forte", "moderada", "fraca"}
        else "moderada"
    )
    report["confianca"] = _normalize_confidence(report.get("confianca"))
    report["cenarios"] = _normalize_scenarios(report.get("cenarios"))
    detail, weighted_10, weighted_100 = _normalize_scores(report.get("score_qualitativo_detalhado"))
    report["score_qualitativo_detalhado"] = detail
    report["score_qualitativo_ponderado"] = weighted_10
    report["score_qualitativo"] = weighted_100
    report.setdefault("relatorio", {})
    report.setdefault("riscos", [])
    report.setdefault("catalisadores", [])
    report.setdefault("sensibilidade_macro", [])
    report.setdefault("adequacao_investidor", {})
    conclusion = report.get("conclusao") if isinstance(report.get("conclusao"), dict) else {}
    conclusion["faixa_valor"] = (
        conclusion.get("faixa_valor")
        if conclusion.get("faixa_valor") in {"cara", "justa", "barata", "indeterminada"}
        else "indeterminada"
    )
    conclusion["percepcao_mercado"] = (
        conclusion.get("percepcao_mercado")
        if conclusion.get("percepcao_mercado") in {"pessimista", "neutra", "otimista", "indeterminada"}
        else "indeterminada"
    )
    report["conclusao"] = conclusion
    report["resumo"] = _sanitize_text(
        report.get("resumo") or conclusion.get("resumo_executivo") or "Síntese não fornecida.",
    )
    report["alerta_principal"] = _sanitize_text(conclusion.get("principal_risco"))
    report["proxima_acao"] = "Monitorar os indicadores e gatilhos descritos no relatório."
    report["tese_final"] = _sanitize_text(conclusion.get("resumo_executivo") or report["resumo"])
    # Não existe recomendação direta nem peso sugerido pela LLM neste contrato.
    report.pop("acao_sugerida", None)
    report.pop("alocacao_sugerida_pct", None)
    report.pop("justificativa_alocacao", None)
    report.pop("classificacao_selecao", None)
    report.pop("motivo_selecao", None)
    return report


def generate_company_portfolio_report(
    ticker: str,
    *,
    df_fin: pd.DataFrame | None = None,
    df_mult: pd.DataFrame | None = None,
    macro_hist: dict | None = None,
    portfolio_tickers: list[str] | tuple[str, ...] = (),
    portfolio_context: str = "",
    rag_context: str = "",
    model: str | None = None,
) -> tuple[dict, dict]:
    """Gera a nota institucional da empresa sem tocar no parecer compartilhado."""
    tk = str(ticker).strip().upper().replace(".SA", "")
    dossier = build_dossie(tk)
    if dossier.get("erro"):
        return _fallback_company(tk, f"dossiê indisponível: {dossier['erro']}"), dossier
    identity = dossier.get("identificacao") or dossier
    try:
        peer_context = build_peer_context(
            tk, portfolio_tickers, sector=identity.get("setor"),
        )
    except Exception as exc:
        logger.warning("Pares institucionais de %s indisponíveis: %s", tk, exc)
        peer_context = "PARES: indisponíveis; não conclua prêmio/desconto setorial."
    prompt = build_company_prompt(
        tk, dossier, df_fin, df_mult, macro_hist, peer_context, rag_context, portfolio_context,
    )
    try:
        raw = _call_llm(prompt, model=model or _report_model())
        parsed = _parse_json(raw, _fallback_company(tk, "JSON inválido"))
        return sanitize_company_report(parsed, tk), dossier
    except Exception as exc:
        logger.warning("Relatório institucional de %s falhou: %s", tk, exc)
        return _fallback_company(tk, str(exc)[:200]), dossier


def _fallback_portfolio(reason: str = "LLM indisponível") -> dict:
    return {
        "qualidade_carteira": "media",
        "perspectiva_12m": "equilibrada",
        "confianca_media": 0,
        "score_medio": 50,
        "cobertura": "baixa",
        "resumo_executivo": f"Relatório consolidado indisponível: {reason}.",
        "relatorio_estrategico": "",
        "papel_dos_ativos": "",
        "pontos_fortes": [],
        "pontos_fracos": [],
        "sintese_alocacao": "",
        "diagnostico_causal": "",
        "riscos_transmissao": [],
        "catalisadores_portfolio": [],
        "adequacao_carteira": {},
        "conclusao_estrategica": "",
    }


def _company_summary_for_portfolio(item: dict) -> str:
    analysis = item.get("analise") or {}
    conclusion = analysis.get("conclusao") or {}
    scenarios = analysis.get("cenarios") or []
    scenario_text = ", ".join(
        f"{s.get('cenario')}={s.get('probabilidade_pct')}% ({s.get('impacto_esperado', '')})"
        for s in scenarios if isinstance(s, dict)
    )
    risks = analysis.get("riscos") or []
    catalysts = analysis.get("catalisadores") or []
    return (
        f"{item.get('ticker')}: peso={float(item.get('peso_pct') or 0):.1f}% | "
        f"score quant={float(item.get('score') or 0):.1f} | score quali={analysis.get('score_qualitativo', 50)} | "
        f"valuation={conclusion.get('faixa_valor', 'indeterminada')} | "
        f"mercado={conclusion.get('percepcao_mercado', 'indeterminada')} | "
        f"resumo={analysis.get('resumo', '')} | cenários={scenario_text or 'N/D'} | "
        f"riscos={risks[:3]} | catalisadores={catalysts[:3]}"
    )


def sanitize_portfolio_report(raw: Any, items: list[dict]) -> dict:
    if not isinstance(raw, dict):
        return _fallback_portfolio("resposta não interpretável")
    report = _sanitize_recursive(copy.deepcopy(raw))
    defaults = _fallback_portfolio()
    for key, value in defaults.items():
        report.setdefault(key, value)
    report["qualidade_carteira"] = (
        report["qualidade_carteira"] if report["qualidade_carteira"] in {"alta", "media", "baixa"}
        else "media"
    )
    report["perspectiva_12m"] = (
        report["perspectiva_12m"]
        if report["perspectiva_12m"] in {"construtiva", "equilibrada", "cautelosa"}
        else "equilibrada"
    )
    confidences = [
        _safe_float((item.get("analise") or {}).get("confianca"))
        for item in items
    ]
    scores = [
        _safe_float((item.get("analise") or {}).get("score_qualitativo"))
        for item in items
    ]
    report["confianca_media"] = int(round(np.mean([v for v in confidences if v is not None]))) if any(v is not None for v in confidences) else 0
    report["score_medio"] = int(round(np.mean([v for v in scores if v is not None]))) if any(v is not None for v in scores) else 50
    return report


def analyze_portfolio_report(
    items_analyzed: list[dict],
    macro_hist: dict | None,
    *,
    model: str | None = None,
    web_context: str = "",
) -> dict:
    """Síntese consolidada exclusiva da aba, preservando o schema da UI.

    ``web_context`` traz a reconciliação banco × Fundamentus/Status Invest da
    carteira. Vazio quando a rede falha — a síntese sai só com o banco.
    """
    prompt = _PROMPT_PORTFOLIO.format(
        items_context="\n".join(_company_summary_for_portfolio(item) for item in items_analyzed)
        or "Carteira vazia.",
        macro=_format_macro(macro_hist),
        web_context=web_context or "Sem segunda fonte disponível nesta execução.",
    )
    try:
        raw = _call_llm(prompt, model=model or _report_model())
        parsed = _parse_json(raw, _fallback_portfolio("JSON inválido"))
        return sanitize_portfolio_report(parsed, items_analyzed)
    except Exception as exc:
        logger.warning("Relatório institucional consolidado falhou: %s", exc)
        return _fallback_portfolio(str(exc)[:200])
