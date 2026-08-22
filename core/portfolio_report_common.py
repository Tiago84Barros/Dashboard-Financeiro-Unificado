"""Maquinaria compartilhada dos relatórios institucionais de portfólio.

O que vive aqui é o que NÃO depende do mercado: o contrato de saída que a
interface consome, os pesos do score qualitativo, a normalização de cenários,
notas e confiança, e o saneamento de linguagem de recomendação.

O que fica FORA daqui, em ``portfolio_report_b3`` e ``portfolio_report_us``, é
o que é intrínseco a cada mercado: os prompts, a hierarquia de pares
(segmento B3 × indústria SEC), o formato do macro (Selic/IPCA/câmbio ×
Fed/CPI/Treasury) e a origem dos fundamentos.

A separação existe porque as duas telas prometem ao usuário a MESMA leitura —
mesmas dimensões de score, mesmos cenários, mesma escala de confiança — e
duplicar essa maquinaria garantiria que uma corrigisse defeitos que a outra
mantém. O defeito de confiança em fração (0.85 exibido como "1") é exatamente
disso: nasceu numa e teria de ser caçado na outra.
"""
from __future__ import annotations

import copy
import re
from typing import Any

import numpy as np

# Dimensões e pesos do score qualitativo. Valem para os dois mercados: são
# perguntas sobre o negócio, não sobre a bolsa em que ele é listado.
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

# O relatório descreve adequação, não emite ordem. Estes padrões reescrevem a
# linguagem imperativa que a LLM ocasionalmente produz apesar do contrato.
FORBIDDEN_RECOMMENDATIONS = (
    (re.compile(r"\bvale\s+comprar\b", re.I), "é compatível com o perfil descrito"),
    (re.compile(r"\bpode\s+entrar\s+na\s+carteira\b", re.I), "pode ser compatível com a carteira descrita"),
    (re.compile(r"\bsubstitua(?:-o|-a)?\s+por\b", re.I), "compare complementarmente com"),
    (re.compile(r"\b(?:compre|venda)\b", re.I), "avalie"),
)


def safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def format_number(value: Any) -> str:
    number = safe_float(value)
    if number is None:
        return "N/D"
    if abs(number) >= 1_000_000_000:
        return f"{number / 1_000_000_000:.2f} bi"
    if abs(number) >= 1_000_000:
        return f"{number / 1_000_000:.1f} mi"
    return f"{number:.2f}"


def weights_contract() -> str:
    return ", ".join(
        f'"{key}": {weight}% ({label})'
        for key, (label, weight) in QUALITATIVE_WEIGHTS.items()
    )


def prioritize_peer_tickers(
    candidates: list[str],
    portfolio_tickers: list[str] | tuple[str, ...],
    target: str,
    max_peers: int = 8,
) -> tuple[list[str], list[str]]:
    """Ordena pares válidos: primeiro os que já estão na carteira, depois o universo.

    Vale para os dois mercados — o que muda é quem produz a lista de candidatos
    (segmento na B3, indústria SEC nos EUA), não o critério de prioridade.
    """
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


# ── Saneamento e normalização do retorno da LLM ──────────────────────────────

def sanitize_text(text: Any) -> str:
    value = "" if text is None else str(text).strip()
    for pattern, replacement in FORBIDDEN_RECOMMENDATIONS:
        value = pattern.sub(replacement, value)
    return value


def sanitize_recursive(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, list):
        return [sanitize_recursive(v) for v in value]
    if isinstance(value, dict):
        return {k: sanitize_recursive(v) for k, v in value.items()}
    return value


def normalize_scenarios(raw: Any) -> list[dict]:
    """Otimista/Base/Pessimista somando exatamente 100%."""
    by_name: dict[str, dict] = {}
    for row in raw if isinstance(raw, list) else []:
        if not isinstance(row, dict):
            continue
        name = sanitize_text(row.get("cenario")).lower()
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
        probability = safe_float(row.get("probabilidade_pct"))
        probabilities.append(max(0.0, min(100.0, probability if probability is not None else defaults[name])))
        rows.append({
            "cenario": name,
            "probabilidade_pct": 0.0,
            "impacto_esperado": sanitize_text(row.get("impacto_esperado")),
            "fundamentacao": sanitize_text(row.get("fundamentacao")),
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


def normalize_scores(raw: Any) -> tuple[dict, float, int]:
    """(detalhe por dimensão, nota ponderada 0–10, nota ponderada 0–100)."""
    source = raw if isinstance(raw, dict) else {}
    normalized: dict[str, dict] = {}
    weighted_sum = 0.0
    total_weight = 0
    for key, (label, weight) in QUALITATIVE_WEIGHTS.items():
        item = source.get(key) if isinstance(source.get(key), dict) else {}
        note = safe_float(item.get("nota"))
        note = max(0.0, min(10.0, note if note is not None else 5.0))
        normalized[key] = {
            "label": label,
            "nota": round(note, 1),
            "peso_pct": weight,
            "justificativa": sanitize_text(item.get("justificativa")),
            "evidencia_ou_lacuna": sanitize_text(item.get("evidencia_ou_lacuna")),
        }
        weighted_sum += note * weight
        total_weight += weight
    score_10 = round(weighted_sum / total_weight, 2) if total_weight else 5.0
    return normalized, score_10, int(round(score_10 * 10))


def normalize_confidence(raw: Any) -> int:
    """Confiança em 0–100, tolerando a LLM responder em fração.

    O contrato do prompt pede ``<int 0-100>``, mas "confiança" é palavra que
    puxa o modelo para probabilidade: ele devolve 0.85 e o arredondamento
    direto virava **1** no card — o mesmo 1 que um analista leria como "1% de
    confiança". Como 0 < v <= 1 é indistinguível de uma confiança de 1%, que
    nenhum relatório útil emite, a leitura por fração é a correta.

    Zero continua zero de propósito: é o valor dos fallbacks e a UI o usa para
    detectar que o relatório não foi gerado.
    """
    value = safe_float(raw)
    if value is None or value <= 0:
        return 0
    if value <= 1.0:
        value *= 100.0
    return int(round(min(100.0, value)))


def fallback_company(ticker: str, reason: str) -> dict:
    """Relatório neutro quando a LLM falha — mesmo schema do caminho feliz."""
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


def fallback_portfolio(reason: str = "LLM indisponível") -> dict:
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


def sanitize_company_report(raw: Any, ticker: str) -> dict:
    """Valida invariantes e mantém as chaves que a interface consome."""
    if not isinstance(raw, dict):
        return fallback_company(ticker, "resposta não interpretável")
    report = sanitize_recursive(copy.deepcopy(raw))
    report["perspectiva"] = (
        report.get("perspectiva") if report.get("perspectiva") in {"forte", "moderada", "fraca"}
        else "moderada"
    )
    report["confianca"] = normalize_confidence(report.get("confianca"))
    report["cenarios"] = normalize_scenarios(report.get("cenarios"))
    detail, weighted_10, weighted_100 = normalize_scores(report.get("score_qualitativo_detalhado"))
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
    report["resumo"] = sanitize_text(
        report.get("resumo") or conclusion.get("resumo_executivo") or "Síntese não fornecida.",
    )
    report["alerta_principal"] = sanitize_text(conclusion.get("principal_risco"))
    report["proxima_acao"] = "Monitorar os indicadores e gatilhos descritos no relatório."
    report["tese_final"] = sanitize_text(conclusion.get("resumo_executivo") or report["resumo"])
    # Não existe recomendação direta nem peso sugerido pela LLM neste contrato.
    report.pop("acao_sugerida", None)
    report.pop("alocacao_sugerida_pct", None)
    report.pop("justificativa_alocacao", None)
    report.pop("classificacao_selecao", None)
    report.pop("motivo_selecao", None)
    return report


def sanitize_portfolio_report(raw: Any, items: list[dict]) -> dict:
    """Consolida o relatório da carteira. Confiança e score vêm dos itens.

    Deliberadamente ignora ``confianca_media``/``score_medio`` devolvidos pela
    LLM: são médias que ela estimaria de cabeça, e o código já tem os valores
    exatos por empresa.
    """
    if not isinstance(raw, dict):
        return fallback_portfolio("resposta não interpretável")
    report = sanitize_recursive(copy.deepcopy(raw))
    defaults = fallback_portfolio()
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
        safe_float((item.get("analise") or {}).get("confianca"))
        for item in items
    ]
    scores = [
        safe_float((item.get("analise") or {}).get("score_qualitativo"))
        for item in items
    ]
    report["confianca_media"] = (
        int(round(np.mean([v for v in confidences if v is not None])))
        if any(v is not None for v in confidences) else 0
    )
    report["score_medio"] = (
        int(round(np.mean([v for v in scores if v is not None])))
        if any(v is not None for v in scores) else 50
    )
    return report


def company_summary_for_portfolio(item: dict) -> str:
    """Uma linha por empresa para o prompt consolidado."""
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
