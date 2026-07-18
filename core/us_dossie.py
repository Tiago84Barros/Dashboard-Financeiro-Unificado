"""
core/us_dossie.py
Dossiê determinístico por empresa (EUA) — espelha core/dossie_b3.

Tudo que é NÚMERO é calculado em código (core.us_metrics). A classificação e as
red flags são regras determinísticas e testáveis. Um LLM pode narrar depois, mas
não recalcula nem inventa métricas (dossie_to_text serializa o dossiê pronto).

Classes: consolidada | crescimento | turnaround | ciclica | assimetrica | inadequada.
Coberto por tests/test_us_dossie.py.
"""
from __future__ import annotations

import logging
from typing import Optional, Sequence

from core.us_metrics import compute_company_metrics

logger = logging.getLogger("us_dossie")

# setores estruturalmente cíclicos (pista; a decisão pondera fundamentos)
_CYCLICAL_SECTORS = frozenset({"Energy", "Basic Materials", "Materials", "Industrials"})


def classify_company(m: dict, sector: str | None = None) -> tuple[str, str]:
    """Classifica a empresa a partir do snapshot de métricas. (classe, motivo)."""
    years = m.get("_years") or 0
    net_income = m.get("_net_income")
    fcf = m.get("_fcf")
    equity = m.get("_equity")

    if years < 3 or net_income is None or equity is None:
        return "inadequada", "histórico/dados insuficientes para análise confiável"
    if equity < 0:
        return "inadequada", "patrimônio líquido negativo"

    g = m.get("revenue_cagr_3y")
    op_margin = m.get("operating_margin")
    net_margin = m.get("net_margin")
    ndte = m.get("net_debt_ebitda")
    roic = m.get("roic")

    # Assimétrica: crescimento alto e persistente + rentabilidade operacional + baixa alavancagem
    if (g is not None and g >= 0.25 and (op_margin is None or op_margin > 0)
            and (ndte is None or ndte < 3)):
        return "assimetrica", f"crescimento de receita ~{g*100:.0f}%/ano com operação rentável"

    # Turnaround: prejuízo recente virando (margem líquida negativa ou muito baixa,
    # mas FCF ou operação melhorando)
    if net_margin is not None and net_margin < 0:
        if fcf is not None and fcf > 0:
            return "turnaround", "prejuízo contábil, porém geração de caixa positiva"
        return "inadequada", "prejuízo persistente sem geração de caixa"

    # Crescimento: crescimento sólido, rentável
    if g is not None and g >= 0.12 and (net_margin is None or net_margin > 0):
        return "crescimento", f"crescimento de receita ~{g*100:.0f}%/ano rentável"

    # Cíclica: setor cíclico e margem/retorno voláteis (proxy: baixo ROIC atual)
    if sector in _CYCLICAL_SECTORS and (roic is None or roic < 0.10):
        return "ciclica", f"setor cíclico ({sector}) com retorno sobre capital moderado/baixo"

    # Consolidada: rentável, caixa positivo, alavancagem controlada
    if (net_margin is not None and net_margin > 0 and (fcf is None or fcf > 0)
            and (ndte is None or ndte < 3)):
        return "consolidada", "rentável, gera caixa e alavancagem sob controle"

    return "inadequada", "perfil não se encaixa em tese clara com os dados atuais"


def red_flags(m: dict) -> list[str]:
    """Sinais de alerta determinísticos (regra: nulo não gera flag)."""
    flags: list[str] = []
    ndte = m.get("net_debt_ebitda")
    if ndte is not None and ndte > 4:
        flags.append(f"Alavancagem alta: dívida líquida/EBITDA = {ndte:.1f}×")
    ic = m.get("interest_coverage")
    if ic is not None and ic < 2:
        flags.append(f"Cobertura de juros baixa: {ic:.1f}× (< 2×)")
    fcf = m.get("_fcf")
    if fcf is not None and fcf < 0:
        flags.append("Fluxo de caixa livre negativo")
    cc = m.get("cash_conversion")
    if cc is not None and cc < 0.5 and (m.get("_net_income") or 0) > 0:
        flags.append(f"Baixa conversão de lucro em caixa: {cc:.0%}")
    de = m.get("debt_to_equity")
    if de is not None and de > 2:
        flags.append(f"Dívida/patrimônio elevada: {de:.1f}×")
    eq = m.get("_equity")
    if eq is not None and eq < 0:
        flags.append("Patrimônio líquido negativo")
    return flags


def investment_notes(m: dict, label: str) -> dict:
    """Tese/condições de invalidação determinísticas por classe."""
    tese, invalidacao = [], []
    if label in ("crescimento", "assimetrica"):
        tese.append("Crescimento de receita acima da média com operação rentável.")
        invalidacao.append("Desaceleração persistente da receita.")
        invalidacao.append("Deterioração de margem operacional.")
        invalidacao.append("Aumento relevante de dívida ou diluição.")
    elif label == "consolidada":
        tese.append("Rentabilidade estável, geração de caixa e retorno ao acionista.")
        invalidacao.append("Queda estrutural de margem ou ROIC.")
        invalidacao.append("Alavancagem subindo sem retorno correspondente.")
    elif label == "turnaround":
        tese.append("Recuperação operacional em curso (caixa antes do lucro contábil).")
        invalidacao.append("Caixa operacional volta a ficar negativo.")
        invalidacao.append("Necessidade de capital externo (diluição/dívida).")
    elif label == "ciclica":
        tese.append("Exposição a ciclo setorial; avaliar no ponto do ciclo.")
        invalidacao.append("Queda de demanda/preços do setor.")
    else:
        tese.append("Sem tese clara com os dados atuais.")
    return {"tese": tese, "condicoes_invalidacao": invalidacao}


def assemble_dossie(symbol: str, *, name: str | None, sector: str | None,
                    industry: str | None, income: Sequence[dict],
                    balance: Sequence[dict], cashflow: Sequence[dict],
                    price: Optional[float] = None,
                    market_cap: Optional[float] = None,
                    score_row: Optional[dict] = None) -> dict:
    """Monta o dossiê determinístico (puro) de uma empresa."""
    m = compute_company_metrics(income, balance, cashflow, price=price,
                                market_cap=market_cap)
    label, motivo = classify_company(m, sector)
    return {
        "symbol": (symbol or "").upper(),
        "name": name, "sector": sector, "industry": industry,
        "classification": label, "classification_reason": motivo,
        "metrics": m,
        "red_flags": red_flags(m),
        "notes": investment_notes(m, label),
        "score": (score_row or {}).get("score"),
        "series_years": m.get("_years"),
    }


def dossie_to_text(d: dict) -> str:
    """Serializa o dossiê para narração por LLM (sem recálculo de números)."""
    from core.market_companies import translate_us_industry, translate_us_sector

    if d.get("erro"):
        return f"DOSSIÊ INDISPONÍVEL: {d['erro']}"
    m = d.get("metrics", {})
    setor = translate_us_sector(d.get("sector"), d.get("industry"))
    industria = translate_us_industry(d.get("industry") or d.get("sector"))

    def pct(x):
        return "—" if x is None else f"{x*100:.1f}%"

    def num(x, mult=False):
        return "—" if x is None else (f"{x:.2f}" if not mult else f"{x:.2f}×")

    L = [
        f"EMPRESA: {d['symbol']} — {d.get('name')} | Setor: {setor} / {industria}",
        f"CLASSIFICAÇÃO: {d.get('classification')} ({d.get('classification_reason')})"
        + (f" | Pontuação: {d['score']}" if d.get("score") is not None else ""),
        "\nQUALIDADE — margem bruta {} | operacional {} | líquida {} | FCF {} | "
        "ROE {} | ROIC {}".format(
            pct(m.get("gross_margin")), pct(m.get("operating_margin")),
            pct(m.get("net_margin")), pct(m.get("fcf_margin")),
            pct(m.get("roe")), pct(m.get("roic"))),
        "CRESCIMENTO — receita 3a {} | 5a {} | EPS 3a {} | FCF 3a {}".format(
            pct(m.get("revenue_cagr_3y")), pct(m.get("revenue_cagr_5y")),
            pct(m.get("eps_cagr_3y")), pct(m.get("fcf_cagr_3y"))),
        "SOLIDEZ — dív.líq/EBITDA {} | cobertura juros {} | liquidez corrente {}".format(
            num(m.get("net_debt_ebitda"), True), num(m.get("interest_coverage"), True),
            num(m.get("current_ratio"), True)),
        "AVALIAÇÃO — P/L {} | EV/EBIT {} | EV/EBITDA {} | P/FCL {} | retorno do FCL {}".format(
            num(m.get("pe"), True), num(m.get("ev_ebit"), True),
            num(m.get("ev_ebitda"), True), num(m.get("p_fcf"), True),
            pct(m.get("fcf_yield"))),
        "RETORNO AO ACIONISTA — retorno total ao acionista {}".format(
            pct(m.get("shareholder_yield"))),
    ]
    if d.get("red_flags"):
        L.append("\nSINAIS DE ALERTA:")
        L.extend(f"  - {f}" for f in d["red_flags"])
    notes = d.get("notes", {})
    if notes.get("tese"):
        L.append("\nTESE:")
        L.extend(f"  - {t}" for t in notes["tese"])
    if notes.get("condicoes_invalidacao"):
        L.append("CONDIÇÕES DE INVALIDAÇÃO:")
        L.extend(f"  - {c}" for c in notes["condicoes_invalidacao"])
    return "\n".join(L)


def build_dossie(symbol: str) -> dict:
    """Dossiê a partir do warehouse local (best-effort; nunca levanta para a UI)."""
    sym = (symbol or "").strip().upper()
    try:
        import core.us_read as ur
        bundle = ur.load_company_bundle(sym)
        if not bundle or not bundle.get("income"):
            return {"symbol": sym, "erro": "sem dados locais para esta empresa"}
        return assemble_dossie(
            sym, name=bundle.get("name"), sector=bundle.get("sector"),
            industry=bundle.get("industry"), income=bundle["income"],
            balance=bundle.get("balance", []), cashflow=bundle.get("cashflow", []),
            price=bundle.get("price"), market_cap=bundle.get("market_cap"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("build_dossie(%s) falhou: %s", sym, exc)
        return {"symbol": sym, "erro": str(exc)[:300]}
