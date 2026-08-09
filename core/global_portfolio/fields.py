"""De-para entre o nome canonico de um indicador e a chave real de cada classe.

Cada classe gravou o mesmo conceito com nome proprio: o P/L e "P/L" no B3 e
"pe" nos EUA. Sem esta camada, todo consumidor reimplementaria o de-para
e eles divergiriam.

Campo ausente ou nao aplicavel devolve None, nunca 0 — zero seria confundido
com valor real e contaminaria qualquer media.

Coberto por tests/test_global_fields.py.
"""
from __future__ import annotations

CAMPOS: tuple[str, ...] = ("dy", "market_cap", "pe", "pvp", "roe")

# campo canonico -> {classe: chave dentro de payload["fundamentals"]}
# Ausencia da classe no dicionario interno significa "nao aplicavel".
_ORIGEM: dict[str, dict[str, str]] = {
    # us: chave real de compute_company_metrics, nao "pe_ratio".
    "pe": {"b3": "P/L", "us": "pe"},
    # us ausente: us_metrics nao calcula P/B.
    "pvp": {"b3": "P/VP", "fii": "pvp"},
    # us ausente: us_metrics nao calcula dividend yield. payout_ratio e
    # shareholder_yield existem, mas sao outra coisa — nao servem de proxy.
    "dy": {"b3": "DY", "fii": "dy_12m"},
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
