"""De-para entre o nome canonico de um indicador e a chave real de cada classe.

Cada classe gravou o mesmo conceito com nome proprio: o P/L e "P/L" no B3 e
"pe" nos EUA. Sem esta camada, todo consumidor reimplementaria o de-para
e eles divergiriam.

Campo ausente ou nao aplicavel devolve None, nunca 0 — zero seria confundido
com valor real e contaminaria qualquer media.

Coberto por tests/test_global_fields.py.
"""
from __future__ import annotations

CAMPOS: tuple[str, ...] = (
    "crescimento_r2", "crescimento_receita", "dy", "market_cap", "payout",
    "pe", "pvp", "roe",
)

# campo canonico -> {classe: chave dentro de payload["fundamentals"]}
# Ausencia da classe no dicionario interno significa "nao aplicavel".
_ORIGEM: dict[str, dict[str, str]] = {
    # us: chave real de compute_company_metrics, nao "pe_ratio".
    "pe": {"b3": "P/L", "us": "pe"},
    # us ausente: us_metrics nao calcula P/B.
    "pvp": {"b3": "P/VP", "fii": "pvp"},
    # us: dividendo DESEMBOLSADO no ultimo exercicio (EDGAR) sobre o valor
    # de mercado — defasa ate um ano e nao capta mudanca recente de politica.
    # A ausencia da linha no EDGAR vira None, nunca 0: medido em 08/09/2026,
    # 42% dos que nao tinham a linha pagavam dividendo. Ver us_metrics.
    "dy": {"b3": "DY", "fii": "dy_12m", "us": "dividend_yield"},
    # Crescimento medido sobre RECEITA, nao lucro. So a classe us: b3 e fii
    # tem serie historica no payload e calculam a taxa na propria regra
    # (LPA anual e VPA mensal).
    #
    # A chave e `revenue_trend_5y` — a INCLINACAO da regressao de ln(receita)
    # no ano, nao o CAGR de ponta a ponta que estava aqui antes. CAGR le dois
    # pontos e ignora o caminho entre eles; a regressao usa a janela inteira.
    # O R2 que a acompanha (`revenue_trend_r2_5y`) diz se a reta descreve a
    # serie, e entra no texto da evidencia — sem ele, uma taxa de 12% com R2
    # de 0,05 seria lida como tendencia quando e ruido.
    "crescimento_receita": {"us": "revenue_trend_5y"},
    "crescimento_r2": {"us": "revenue_trend_r2_5y"},
    # us: fracao do lucro distribuida no ultimo exercicio. b3 ausente de
    # proposito — la o payout vem como SERIE (multiplos_anuais), e a regra de
    # renda mede o desvio dela, nao o nivel de um ano.
    "payout": {"us": "payout_ratio"},
    "roe": {"b3": "ROE", "us": "roe"},
    # b3 ausente: "Valor de mercado" nao esta em _MULT_COLS.
    # us: a chave real leva underscore (campo de contexto em us_metrics).
    "market_cap": {"us": "_market_cap", "fii": "patrimonio_liquido"},
}


def valor(payload: dict, asset_class: str, campo: str) -> float | None:
    """Valor numerico do campo canonico, ou None se ausente/nao aplicavel."""
    if campo not in _ORIGEM:
        raise KeyError(f"campo canonico desconhecido: {campo!r}")

    chave = _ORIGEM[campo].get(str(asset_class or "").strip().lower())
    if not chave:
        return None

    bruto = (payload or {}).get("fundamentals", {}).get(chave)
    if bruto is None or isinstance(bruto, bool):
        return None
    try:
        return float(bruto)
    except (TypeError, ValueError):
        return None


def disponivel(payload: dict, asset_class: str, campo: str) -> bool:
    """True quando o campo tem valor numerico utilizavel."""
    return valor(payload, asset_class, campo) is not None
