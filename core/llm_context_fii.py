"""Contexto determinístico e auditável para o chat de FIIs."""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Iterable

import pandas as pd

_DETAIL_METRICS = (
    "vacancia_fisica", "vacancia_financeira", "wault_anos",
    "tenant_concentration", "lease_expiry_concentration_24m", "leverage",
    "duration_anos", "ltv", "credit_spread", "rating_quality",
    "subordination_protection", "delinquency",
    "debtor_diversification", "indexer_diversification", "issuance_concentration",
    "nav_discount", "double_fee_burden", "holdings_overlap",
    "invested_portfolio_liquidity", "holdings_quality",
    "income_growth_per_share_3y", "income_recurrence", "management_efficiency",
    "fee_efficiency", "mandate_adherence", "conflict_alignment",
)


def _num(value: Any) -> float | None:
    try:
        number = float(value)
        return number if pd.notna(number) else None
    except (TypeError, ValueError):
        return None


def _fmt(value: Any, *, percent: bool = False) -> str:
    number = _num(value)
    if number is None:
        return "ausente"
    if percent:
        if abs(number) > 1:
            number /= 100
        return f"{number:.2%}"
    return f"{number:.4g}"


def _top_mapping(value: Any, limit: int = 6) -> str:
    if not isinstance(value, dict) or not value:
        return "ausente"
    clean = []
    for name, weight in value.items():
        number = _num(weight)
        if number is not None:
            clean.append((str(name), number / 100 if abs(number) > 1 else number))
    clean.sort(key=lambda pair: pair[1], reverse=True)
    return ", ".join(f"{name}={weight:.1%}" for name, weight in clean[:limit]) or "ausente"


def _reference_summary(row: dict) -> str:
    metadata = row.get("metric_metadata") or {}
    refs = []
    for metric, meta in metadata.items():
        if not isinstance(meta, dict):
            continue
        ref = meta.get("reference_date")
        source = meta.get("source")
        if ref and str(ref) not in ("None", "NaT"):
            refs.append(f"{metric}:{str(ref)[:10]}({source or 'fonte não informada'})")
    return "; ".join(refs[:12]) or f"snapshot:{str(row.get('updated_at') or 'não informado')[:10]}"


def _fund_block(row: dict, selected: bool) -> str:
    lines = [
        f"FII {row.get('ticker')} | selecionado={'sim' if selected else 'não'} | "
        f"tipo={row.get('tipo') or 'ausente'} | segmento={row.get('sector') or 'ausente'}",
        "  mercado: "
        f"score={_fmt(row.get('type_score'))}; confiança={_fmt(row.get('confidence'), percent=True)}; "
        f"cobertura={_fmt(row.get('coverage'), percent=True)}; DY12m={_fmt(row.get('dy_12m'), percent=True)}; "
        f"P/VP={_fmt(row.get('pvp'))}; liquidez_dia={_fmt(row.get('liquidez_diaria'))}; "
        f"PL={_fmt(row.get('patrimonio_liquido'))}; cotistas={_fmt(row.get('num_cotistas'))}",
        "  patrimônio: "
        f"imóveis={_fmt(row.get('property_count'))}; regiões={_fmt(row.get('region_count'))}; "
        f"pct_imóveis={_fmt(row.get('pct_imoveis'), percent=True)}; "
        f"pct_papel={_fmt(row.get('pct_papel'), percent=True)}; "
        f"pct_fundos={_fmt(row.get('pct_fundos'), percent=True)}; "
        f"pct_caixa={_fmt(row.get('pct_caixa'), percent=True)}",
    ]
    observed = []
    for metric in _DETAIL_METRICS:
        if _num(row.get(metric)) is not None:
            percent = metric not in ("wault_anos", "duration_anos")
            observed.append(f"{metric}={_fmt(row.get(metric), percent=percent)}")
    lines.append("  métricas específicas observadas: " + ("; ".join(observed) or "nenhuma"))
    for label, key in (("locatários", "tenants"), ("devedores", "debtors"),
                       ("emissores", "issuers"), ("indexadores", "indexers"),
                       ("regiões", "regions"), ("fundos investidos", "holdings")):
        if isinstance(row.get(key), dict) and row.get(key):
            lines.append(f"  {label}: {_top_mapping(row.get(key))}")
    missing = row.get("missing_critical") or []
    if missing:
        lines.append("  métricas críticas ausentes: " + ", ".join(map(str, missing)))
    lines.append("  referências: " + _reference_summary(row))
    return "\n".join(lines)


def _correlation_context(prices: pd.DataFrame | None, selected_tickers: list[str]) -> str:
    if prices is None or prices.empty:
        return "Correlação: histórico indisponível."
    returns = prices.pct_change(fill_method=None)
    lines = []
    for ticker in selected_tickers:
        if ticker not in returns:
            continue
        for benchmark in ("XFIX11", "BOVA11"):
            if benchmark not in returns:
                continue
            common = returns[[ticker, benchmark]].dropna().tail(36)
            if len(common) < 12:
                continue
            corr = common[ticker].corr(common[benchmark])
            if pd.notna(corr):
                lines.append(f"{ticker} x {benchmark}: corr={corr:.3f}, meses={len(common)}")
    return "Correlação de retornos totais mensais:\n  " + "\n  ".join(lines) if lines else (
        "Correlação: menos de 12 meses coincidentes por par.")


def build_fii_chat_context(
    *, user_question: str, selected_items: Iterable[dict], scored_rows: Iterable[dict],
    methodology_rows: Iterable[dict], portfolio_result: dict, scenario: Any,
    reports: Iterable[dict] | None = None, prices: pd.DataFrame | None = None,
) -> str:
    """Compila carteira, fundos citados, pares, macro e rastreabilidade."""
    selected = [dict(row) for row in selected_items]
    scored = [dict(row) for row in scored_rows]
    methodology = [dict(row) for row in methodology_rows]
    scored_by_ticker = {str(row.get("ticker") or ""): row for row in scored}
    all_by_ticker = {str(row.get("ticker") or ""): row for row in methodology}
    for ticker, score_row in scored_by_ticker.items():
        all_by_ticker[ticker] = {**all_by_ticker.get(ticker, {}), **score_row}
    selected_tickers = [str(row.get("ticker") or "") for row in selected]
    cited = re.findall(r"\b[A-Z]{4}11\b", (user_question or "").upper())
    detail_tickers = list(dict.fromkeys(selected_tickers + cited))

    current_output = (
        "Carteira Modelo aprovada pelos gates vigentes; resultado quantitativo "
        "não constitui garantia de retorno."
        if portfolio_result.get("can_publish")
        else "lista de diligência; há gates ou pré-requisitos pendentes."
    )
    lines = [
        "STATUS E ESCOPO:",
        f"  Saída atual: {current_output}",
        f"  FIIs selecionados={len(selected)}; elegíveis={len(scored)}; "
        f"renda esperada={_fmt(portfolio_result.get('expected_yield'), percent=True)}; "
        f"número efetivo={_fmt(portfolio_result.get('effective_assets'))}; "
        f"publicável={'sim' if portfolio_result.get('can_publish') else 'não'}",
        "  bloqueios: " + ("; ".join(portfolio_result.get("blockers") or []) or "nenhum"),
        "",
        "CENÁRIO INFORMADO PELO USUÁRIO:",
        f"  Selic={_fmt(getattr(scenario, 'selic', None), percent=False)}%; "
        f"IPCA={_fmt(getattr(scenario, 'ipca', None), percent=False)}%; "
        f"CDI={_fmt(getattr(scenario, 'cdi', None), percent=False)}%; "
        f"ΔSelic12m={_fmt(getattr(scenario, 'selic_change_12m', None))} p.p.; "
        f"choque_vacância={_fmt(getattr(scenario, 'vacancy_shock', None), percent=True)}; "
        f"evento_crédito={_fmt(getattr(scenario, 'credit_event_rate', None), percent=True)}",
        "",
        "PESOS DA SELEÇÃO:",
        "  " + ", ".join(f"{row.get('ticker')}={_fmt(row.get('weight'), percent=True)}"
                          for row in selected),
        "",
        "DETALHES DOS FUNDOS:",
    ]
    selected_set = set(selected_tickers)
    for ticker in detail_tickers:
        row = {**all_by_ticker.get(ticker, {}),
               **next((item for item in selected if item.get("ticker") == ticker), {})}
        if row:
            lines.append(_fund_block(row, ticker in selected_set))
        else:
            lines.append(f"FII {ticker}: não localizado no contexto carregado.")

    peers_by_type: dict[str, list[dict]] = defaultdict(list)
    for row in scored:
        if row.get("ticker") not in selected_set:
            peers_by_type[str(row.get("tipo") or "outros")].append(row)
    lines += ["", "PARES ELEGÍVEIS MAIS BEM PONTUADOS (fora da seleção):"]
    for fii_type, rows in sorted(peers_by_type.items()):
        rows.sort(key=lambda row: _num(row.get("type_score")) or -1, reverse=True)
        summary = ", ".join(
            f"{row.get('ticker')}(score={_fmt(row.get('type_score'))}, "
            f"DY={_fmt(row.get('dy_12m'), percent=True)}, P/VP={_fmt(row.get('pvp'))}, "
            f"conf={_fmt(row.get('confidence'), percent=True)})" for row in rows[:8]
        )
        lines.append(f"  {fii_type}: {summary or 'nenhum'}")

    report_rows = {str(row.get("ticker") or ""): row for row in (reports or [])}
    if report_rows:
        lines += ["", "SÍNTESES DETERMINÍSTICAS EXIBIDAS NO APP:"]
        for ticker in selected_tickers:
            report = report_rows.get(ticker) or {}
            facts = list(report.get("facts") or []) + list(report.get("structure") or [])
            if facts:
                lines.append(f"  {ticker}: " + " | ".join(map(str, facts[:10])))

    lines += ["", _correlation_context(prices, selected_tickers), "",
              "LIMITAÇÕES GERAIS:",
              "  Métricas ausentes não foram imputadas. Correlações são retrospectivas. ",
              "  Scores são relativos ao universo elegível e não substituem leitura dos relatórios gerenciais."]
    return "\n".join(lines)
