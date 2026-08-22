"""
core/status_invest.py
Scraping de indicadores fundamentalistas do Status Invest.

URLs:
  Ações:  https://statusinvest.com.br/acoes/{ticker}
  FIIs:   https://statusinvest.com.br/fundos-imobiliarios/{ticker}

Valores retornados:
  • Campos percentuais (DY, ROE, ROIC, Margens, Payout): decimal (0.15 = 15%)
    → compatível com o banco de dados (mesma escala)
  • Campos ratio (P/L, P/VP, EV/EBIT, …): valor direto (15.0 = 15×)

Funções públicas:
  fetch_stock_si(ticker) → dict   (indicadores de ação)
  fetch_fii_si(ticker)   → dict   (indicadores de FII)
"""
from __future__ import annotations

import requests
import streamlit as st

_SI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://statusinvest.com.br/",
}


def _parse_si_number(s: str) -> float | None:
    """Parse número no formato do Status Invest (decimal BR, pode ter %)."""
    if not s or s.strip() in ("", "-", "—", "?", "N/D", "N/A"):
        return None
    try:
        clean = s.strip().replace("%", "").replace("R$", "").strip()
        # Formato BR: separador de milhar = ponto, decimal = vírgula
        # Ex: "1.234,56" → "1234.56"
        clean = clean.replace(".", "").replace(",", ".")
        return float(clean)
    except (ValueError, AttributeError):
        return None


def _extract_si_data(html_content: bytes) -> dict[str, str]:
    """Extrai pares label→valor dos blocos .info do Status Invest."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return {}
    soup = BeautifulSoup(html_content, "html.parser")
    data: dict[str, str] = {}

    # Padrão principal: <div class="info"> com title + value
    for block in soup.find_all("div", class_="info"):
        title_el = (
            block.find(class_="title") or
            block.find("h3") or
            block.find("span", class_="sub-title")
        )
        value_el = (
            block.find("strong", class_="value") or
            block.find(class_="value") or
            block.find("strong")
        )
        if title_el and value_el:
            label = title_el.get_text(strip=True).upper()
            value = value_el.get_text(strip=True)
            if label and value and label not in data:
                data[label] = value

    return data


def _get_value(data: dict[str, str], *keys: str) -> float | None:
    """Busca um valor no dicionário, tentando múltiplas chaves com fuzzy match."""
    for key in keys:
        key_upper = key.upper()
        if key_upper in data:
            return _parse_si_number(data[key_upper])
        # Fuzzy: chave normalizada
        key_norm = key_upper.replace("/", "").replace(" ", "").replace(".", "").replace("Á","A").replace("Í","I").replace("Ê","E").replace("Ç","C").replace("Ã","A")
        for k, v in data.items():
            k_norm = k.replace("/", "").replace(" ", "").replace(".", "").replace("Á","A").replace("Í","I").replace("Ê","E").replace("Ç","C").replace("Ã","A")
            if key_norm and (key_norm in k_norm or k_norm in key_norm):
                return _parse_si_number(v)
    return None


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_stock_si(ticker: str) -> dict:
    """
    Scraping de indicadores de ação no Status Invest.

    Retorna dict com chaves canônicas do BD.
    Percentuais como decimais (0.15 = 15%).
    Retorna {} se falhar ou não encontrar dados.
    """
    tk = ticker.strip().upper().replace(".SA", "")
    url = f"https://statusinvest.com.br/acoes/{tk.lower()}"
    try:
        resp = requests.get(url, headers=_SI_HEADERS, timeout=15)
        if resp.status_code != 200:
            return {}
        data = _extract_si_data(resp.content)
        if not data:
            return {}

        def g(*keys: str) -> float | None:
            return _get_value(data, *keys)

        def pct(*keys: str) -> float | None:
            """Converte % bruto (15.0) para decimal (0.15)."""
            v = g(*keys)
            return v / 100.0 if v is not None else None

        return {
            "P/L":               g("P/L", "PL"),
            "P/VP":              g("P/VP", "PVP"),
            "DY":                pct("DY", "D.Y.", "DIVIDEND YIELD", "DIV. YIELD"),
            "ROE":               pct("ROE"),
            "ROIC":              pct("ROIC"),
            "ROA":               pct("ROA"),
            "Margem_Liquida":    pct("MARG. LÍQUIDA", "MARG LÍQUIDA", "MARGEM LÍQUIDA",
                                     "MARG.LIQ", "MARG LIQ"),
            "Margem_Operacional": pct("MARG. EBIT", "MARG EBIT", "MARGEM EBIT",
                                      "MARGEM OPERACIONAL", "MARG. OPERACIONAL"),
            "EV_EBIT":           g("EV/EBIT", "EV EBIT"),
            "Liquidez_Corrente": g("LIQ. CORRENTE", "LIQUIDEZ CORRENTE", "LIQ CORRENTE"),
            "Endividamento_Total": g("DÍV.BRUTA/PATRIM.", "DÍV. BRUTA/PATRIM.",
                                     "DÍVIDA BRUTA/PATRIM", "DIVBRUTA/PATRIM"),
            "P_FCO":             g("P/FCO", "P/FCF"),
            "Payout":            pct("PAYOUT", "PAYOUT MÉDIO"),
        }
    except Exception:
        return {}


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_fii_si(ticker: str) -> dict:
    """
    Scraping de indicadores de FII no Status Invest.

    Retorna dict com chaves canônicas do BD.
    Percentuais como decimais.
    Retorna {} se falhar.
    """
    tk = ticker.strip().upper().replace(".SA", "")
    url = f"https://statusinvest.com.br/fundos-imobiliarios/{tk.lower()}"
    try:
        resp = requests.get(url, headers=_SI_HEADERS, timeout=15)
        if resp.status_code != 200:
            return {}
        data = _extract_si_data(resp.content)
        if not data:
            return {}

        def g(*keys: str) -> float | None:
            return _get_value(data, *keys)

        def pct(*keys: str) -> float | None:
            v = g(*keys)
            return v / 100.0 if v is not None else None

        return {
            "P/VP":    g("P/VP", "PVP"),
            "DY":      pct("DY", "D.Y.", "DIVIDEND YIELD", "DIV. YIELD"),
            "Vacancia": pct("VACÂNCIA MÉDIA", "VACÂNCIA FÍSICA", "VACÂNCIA"),
        }
    except Exception:
        return {}
