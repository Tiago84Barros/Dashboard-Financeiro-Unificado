"""
core/fundamentus.py
Dados fundamentalistas de ações e FIIs via scraping do Fundamentus.
Dados de preço e histórico via yfinance.

Funções públicas:
    batch_stocks(tickers)   → dict[ticker, fundamentus_dict]
    batch_fiis(tickers)     → dict[ticker, fundamentus_dict]
    fetch_price_data(tickers) → dict[ticker, {price, change_pct, hist}]
    alerts_stock(pos, fd)   → list[(css_cls, lbl_cls, label, msg)]
    alerts_fii(pos, fd)     → list[(css_cls, lbl_cls, label, msg)]

Todos os dados são cacheados via st.cache_data (Streamlit).
Ações: TTL=3600s (1h) | Preços: TTL=600s (10min)
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import requests
import streamlit as st

# ── Thresholds de alerta ──────────────────────────────────────────────────────
THR = {
    "stock_pl_alto":      25.0,
    "stock_pl_baixo":      6.0,
    "stock_pvp_alto":      3.0,
    "stock_roe_baixo":    10.0,
    "stock_roic_baixo":    8.0,
    "stock_marg_neg":      0.0,
    "stock_divida_alta":   3.0,
    "stock_dy_alto":      12.0,
    "stock_conc_max":     10.0,
    "fii_pvp_premium":    1.15,
    "fii_pvp_desconto":   0.88,
    "fii_vacancia_alta":  10.0,
    "fii_dy_alto":        14.0,
    "fii_conc_max":       10.0,
}

_FUND_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.fundamentus.com.br/",
}

# ── Helpers de conversão ──────────────────────────────────────────────────────

def _parse_br_number(s: str) -> float | None:
    if not s or s.strip() in ("", "-", "—", "?"):
        return None
    try:
        clean = s.strip().replace("%", "").replace("R$", "").strip()
        clean = clean.replace(".", "").replace(",", ".")
        return float(clean)
    except (ValueError, AttributeError):
        return None


def _safe_f(val: Any, default: float = 0.0) -> float:
    try:
        v = float(val)
        return v if v == v else default
    except (TypeError, ValueError):
        return default


def _parse_fundamentus_html(content: bytes) -> dict[str, str]:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return {}
    soup = BeautifulSoup(content, "html.parser")
    data: dict[str, str] = {}
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            i = 0
            while i < len(cells) - 1:
                label = cells[i].get_text(" ", strip=True)
                value = cells[i + 1].get_text(" ", strip=True)
                if label and value and label not in ("?", ""):
                    data[label] = value
                i += 2
    return data


def _norm(s: str) -> str:
    return s.lower().replace(" ", "").replace(".", "").replace("/", "")


def _make_getters(raw: dict[str, str]):
    def g(key: str) -> float | None:
        for k in (key, key.replace(".", ""), key.lower()):
            if k in raw:
                return _parse_br_number(raw[k])
        key_n = _norm(key)
        for k, v in raw.items():
            if key_n and key_n in _norm(k):
                return _parse_br_number(v)
        return None

    def gs(key: str) -> str:
        for k in (key, key.replace(".", ""), key.lower()):
            if k in raw:
                return raw[k]
        key_n = _norm(key)
        for k, v in raw.items():
            if key_n and key_n in _norm(k):
                return v
        return ""

    return g, gs


# ── Scraping Ações ────────────────────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def _scrape_stock(ticker: str) -> dict:
    try:
        url = f"https://www.fundamentus.com.br/detalhes.php?papel={ticker}"
        resp = requests.get(url, headers=_FUND_HEADERS, timeout=15)
        if resp.status_code != 200:
            return {}
        raw = _parse_fundamentus_html(resp.content)
        g, gs = _make_getters(raw)
        return {
            "empresa":        gs("Empresa"),
            "setor":          gs("Setor"),
            "subsetor":       gs("Subsetor"),
            "cotacao":        g("Cotação"),
            "pl":             g("P/L"),
            "pvp":            g("P/VP"),
            "psr":            g("PSR"),
            "p_ebit":         g("P/EBIT"),
            "ev_ebit":        g("EV/EBIT"),
            "ev_ebitda":      g("EV/EBITDA"),
            "dy":             g("Div.Yield") or g("Div. Yield") or g("Dividend Yield"),
            "roe":            g("ROE"),
            "roic":           g("ROIC"),
            "marg_bruta":     g("Mrg Bruta") or g("Mrg. Bruta"),
            "marg_ebit":      g("Mrg EBIT") or g("Mrg. EBIT"),
            "marg_liq":       (g("Mrg. Líq.") or g("Mrg.Líq.") or
                               g("Mrg Líq") or g("Margem Líquida")),
            "giro_ativos":    g("Giro Ativos"),
            "liq_corr":       g("Liq. Corr."),
            "div_brut_patrim": g("Dív.Brut/ Patrim."),
            "patrim_liq":     g("Patrim. Líq"),
            "div_bruta":      g("Dív. Bruta"),
            "div_liquida":    g("Dív. Líquida"),
            "cresc_rec_5a":   g("Cres. Rec"),
            "receita_liq":    g("Receita Líquida"),
            "ebit":           g("EBIT"),
            "lucro_liq":      g("Lucro Líquido"),
            "liq_2m":         g("Liq.2meses"),
        }
    except Exception:
        return {}


def batch_stocks(tickers: tuple[str, ...]) -> dict[str, dict]:
    if not tickers:
        return {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(_scrape_stock, t): t for t in tickers}
        return {futures[f]: f.result() for f in futures}


# ── Scraping FIIs ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def _scrape_fii(ticker: str) -> dict:
    try:
        url = f"https://www.fundamentus.com.br/fii_detalhes.php?papel={ticker}"
        resp = requests.get(url, headers=_FUND_HEADERS, timeout=15)
        if resp.status_code != 200:
            return {}
        raw = _parse_fundamentus_html(resp.content)
        g, gs = _make_getters(raw)
        return {
            "segmento":       gs("Segmento") or gs("Mandato") or gs("Tipo"),
            "gestao":         gs("Gestão"),
            "cotacao":        g("Cotação"),
            "pvp":            g("P/VP"),
            "dy":             g("DY") or g("Div. Yield") or g("Div.Yield") or g("Dividend Yield"),
            "vacancia_media": g("Vacância Média"),
            "vacancia_fisica": g("Vacância Física"),
            "vacancia_financ": g("Vacância Financeira"),
            "qtd_imoveis":    g("Qtd de Imóveis") or g("Nro. Imóveis"),
            "qtd_cotistas":   g("Qtd de Cotistas") or g("Nro. Cotistas"),
            "qtd_ativos":     g("Qtd de Ativos"),
            "patrim_liq":     g("Patrim Líq") or g("Patrimônio Líq"),
            "vp_cota":        g("VP/Cota"),
            "valor_mercado":  g("Valor de mercado"),
            "nro_cotas":      g("Nro. Cotas"),
            "ult_rendimento": g("Último Rendimento"),
            "data_rendimento": gs("Data Últ. Rendim."),
            "liq_diaria":     g("Liq. Diária") or g("Vol $ méd"),
        }
    except Exception:
        return {}


@st.cache_data(ttl=3600, show_spinner=False)
def _scrape_fii_lista() -> dict[str, dict]:
    try:
        from bs4 import BeautifulSoup
        url = "https://www.fundamentus.com.br/fii_resultado.php"
        resp = requests.get(url, headers=_FUND_HEADERS, timeout=20)
        if resp.status_code != 200:
            return {}
        soup = BeautifulSoup(resp.content, "html.parser")
        table = soup.find("table", id="tabelaResultado")
        if not table:
            tables = soup.find_all("table")
            table = max(tables, key=lambda t: len(t.find_all("tr")), default=None)
        if not table:
            return {}
        header_row = table.find("tr")
        if not header_row:
            return {}
        headers = [th.get_text(strip=True) for th in header_row.find_all(["th", "td"])]
        result: dict[str, dict] = {}
        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            ticker = cells[0].get_text(strip=True).upper()
            if not ticker or len(ticker) < 4:
                continue
            rd = {headers[i]: cells[i].get_text(strip=True)
                  for i in range(min(len(headers), len(cells)))}

            def gv(key: str) -> float | None:
                if key in rd:
                    return _parse_br_number(rd[key])
                kn = key.lower().replace(" ", "").replace(".", "")
                for k, v in rd.items():
                    if kn in k.lower().replace(" ", "").replace(".", ""):
                        return _parse_br_number(v)
                return None

            cotacao = gv("Cotação")
            pvp = gv("P/VP")
            result[ticker] = {
                "segmento":       rd.get("Segmento", ""),
                "gestao":         rd.get("Gestão", ""),
                "cotacao":        cotacao,
                "pvp":            pvp,
                "dy":             gv("DY"),
                "vacancia_media": gv("Vacância Média") or gv("Vacância"),
                "qtd_imoveis":    gv("Qtd de Imóveis") or gv("Qtd Imóveis") or gv("Nro. Imóveis"),
                "patrim_liq":     gv("Patrim. Líq") or gv("Valor Patrimonial") or gv("PL"),
                "liq_diaria":     gv("Liquidez") or gv("Liq. Diária"),
                "vp_cota": (round(cotacao / pvp, 2) if cotacao and pvp and pvp > 0 else None),
            }
        return result
    except Exception:
        return {}


def batch_fiis(tickers: tuple[str, ...]) -> dict[str, dict]:
    if not tickers:
        return {}
    lista = _scrape_fii_lista()
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(_scrape_fii, t): t for t in tickers}
        individual = {futures[f]: f.result() for f in futures}
    _LISTA_KEYS = {"segmento", "gestao", "cotacao", "pvp", "dy",
                   "vacancia_media", "qtd_imoveis", "patrim_liq", "liq_diaria", "vp_cota"}
    result: dict[str, dict] = {}
    for t in tickers:
        base = dict(lista.get(t, {}))
        ind = dict(individual.get(t, {}))
        merged: dict = {}
        for k in _LISTA_KEYS:
            v = base.get(k)
            merged[k] = v if (v is not None and v != "") else ind.get(k)
        for k in ("ult_rendimento", "data_rendimento", "qtd_cotistas",
                  "vacancia_fisica", "vacancia_financ", "valor_mercado"):
            merged[k] = ind.get(k)
        result[t] = merged
    return result


# ── Classificação FII ─────────────────────────────────────────────────────────

_FUND_SEGMENTO_TO_TIPO: dict[str, str] = {
    "Papéis": "Papel", "Papel": "Papel", "CRI": "Papel",
    "Recebíveis": "Papel", "Títulos e Val. Mob.": "Papel", "Títulos": "Papel",
    "Logística": "Tijolo · Logística", "Industrial": "Tijolo · Logística",
    "Industrial/Logístico": "Tijolo · Logística", "Galpões": "Tijolo · Logística",
    "Lajes Corporativas": "Tijolo · Escritórios", "Escritórios": "Tijolo · Escritórios",
    "Comercial": "Tijolo · Escritórios", "Outros": "Tijolo · Escritórios",
    "Shoppings": "Shopping", "Shopping": "Shopping",
    "Shopping / Varejo": "Shopping", "Varejo": "Shopping",
    "Fundo de Fundos": "Fundo de Fundos", "FoF": "Fundo de Fundos",
    "Híbrido": "Híbrido", "Diversificado": "Híbrido",
    "Residencial": "Residencial", "Imóveis Residenciais": "Residencial",
    "Hospital": "Hospital", "Saúde": "Hospital", "Hospitalar": "Hospital",
    "Hotel": "Hotel", "Hotelaria": "Hotel",
    "Agro": "Agro", "Rural": "Agro", "Agronegócio": "Agro",
    "Educação": "Educação", "Educacional": "Educação",
}

FII_TIPO_MAP: dict[str, str] = {
    "MXRF11": "Papel", "KNCR11": "Papel", "IRDM11": "Papel",
    "HGCR11": "Papel", "RECR11": "Papel", "VRTA11": "Papel",
    "RZTR11": "Papel", "CPTS11": "Papel", "DEVA11": "Papel",
    "HGLG11": "Tijolo · Logística", "BTLG11": "Tijolo · Logística",
    "XPLG11": "Tijolo · Logística", "BRCO11": "Tijolo · Logística",
    "LVBI11": "Tijolo · Logística", "VILG11": "Tijolo · Logística",
    "BRCR11": "Tijolo · Escritórios", "PVBI11": "Tijolo · Escritórios",
    "VINO11": "Tijolo · Escritórios", "JSRE11": "Tijolo · Escritórios",
    "VISC11": "Shopping", "XPML11": "Shopping", "MALL11": "Shopping",
    "HSML11": "Shopping", "HGRU11": "Shopping",
    "BCFF11": "Fundo de Fundos", "KFOF11": "Fundo de Fundos",
    "RBRF11": "Fundo de Fundos", "HFOF11": "Fundo de Fundos",
    "KNRI11": "Híbrido",
}


def get_fii_tipo(ticker: str, segmento: str = "") -> str:
    if segmento and segmento != "—":
        for key, tipo in _FUND_SEGMENTO_TO_TIPO.items():
            if key.lower() in segmento.lower():
                return tipo
    return FII_TIPO_MAP.get(ticker.upper().strip(), "")


# ── yfinance — preços e histórico ─────────────────────────────────────────────

@st.cache_data(ttl=600, show_spinner=False)
def fetch_price_data(tickers: tuple[str, ...]) -> dict[str, dict]:
    """Cotação atual (change_pct) + histórico mensal (hist Series) por ticker."""
    if not tickers:
        return {}
    try:
        import pandas as pd
        import yfinance as yf

        symbols = [f"{t}.SA" for t in tickers]
        sym_str = " ".join(symbols)

        # Preços diários (5d) para cotação atual e variação diária
        df_d = yf.download(sym_str, period="5d", interval="1d",
                           auto_adjust=True, progress=False)
        # Histórico mensal (14mo) para var. mês e 12m
        df_m = yf.download(sym_str, period="14mo", interval="1mo",
                           auto_adjust=True, progress=False)

        is_multi = isinstance(df_d.columns, pd.MultiIndex)
        result: dict[str, dict] = {}
        for t, sym in zip(tickers, symbols):
            try:
                closes_d = (df_d["Close"][sym] if is_multi else df_d["Close"]).dropna()
                closes_m = (df_m["Close"][sym] if is_multi else df_m["Close"]).dropna() \
                           if not df_m.empty else pd.Series(dtype=float)
                price = float(closes_d.iloc[-1]) if len(closes_d) > 0 else None
                prev = float(closes_d.iloc[-2]) if len(closes_d) >= 2 else price
                chg = (price - prev) / prev * 100 if price and prev else 0.0
                result[t] = {"price": price, "change_pct": chg, "hist": closes_m}
            except Exception:
                result[t] = {"price": None, "change_pct": 0.0, "hist": None}
        return result
    except Exception:
        return {}


def var_mes(hist) -> float | None:
    if hist is None or len(hist) < 2:
        return None
    try:
        c = hist.dropna()
        return (float(c.iloc[-1]) - float(c.iloc[-2])) / float(c.iloc[-2]) * 100
    except Exception:
        return None


def var_12m(hist) -> float | None:
    if hist is None or len(hist) < 12:
        return None
    try:
        c = hist.dropna()
        if len(c) < 12:
            return None
        return (float(c.iloc[-1]) - float(c.iloc[-12])) / float(c.iloc[-12]) * 100
    except Exception:
        return None


# ── Geração de alertas ────────────────────────────────────────────────────────

Alert = tuple[str, str, str, str]


def alerts_stock(pos: dict, fd: dict) -> list[Alert]:
    alerts: list[Alert] = []
    t    = pos["ticker"]
    peso = _safe_f(pos.get("pct_carteira", 0))
    pl   = fd.get("pl");   pvp  = fd.get("pvp")
    roe  = fd.get("roe");  roic = fd.get("roic")
    marg = fd.get("marg_liq")
    divid = fd.get("div_brut_patrim");  dy = fd.get("dy")

    if peso > THR["stock_conc_max"]:
        alerts.append(("yellow", "warn", "Concentração elevada",
            f"{t} representa {peso:.1f}% da carteira. "
            f"Posições acima de {THR['stock_conc_max']:.0f}% aumentam o risco específico."))
    if pl:
        if pl > THR["stock_pl_alto"]:
            alerts.append(("yellow", "warn", "Valuation esticado",
                f"{t} — P/L de {pl:.1f}x acima de {THR['stock_pl_alto']:.0f}x."))
        elif 0 < pl < THR["stock_pl_baixo"]:
            alerts.append(("green", "opp", "P/L baixo — possível desconto",
                f"{t} — P/L de {pl:.1f}x abaixo de {THR['stock_pl_baixo']:.0f}x."))
    if pvp and pvp > THR["stock_pvp_alto"]:
        alerts.append(("yellow", "warn", "P/VP elevado",
            f"{t} — P/VP de {pvp:.2f}x acima de {THR['stock_pvp_alto']:.1f}x."))
    if roe is not None and 0 < roe < THR["stock_roe_baixo"]:
        alerts.append(("yellow", "warn", "ROE baixo",
            f"{t} — ROE de {roe:.1f}% abaixo de {THR['stock_roe_baixo']:.0f}%."))
    if roic is not None and 0 < roic < THR["stock_roic_baixo"]:
        alerts.append(("yellow", "warn", "ROIC baixo",
            f"{t} — ROIC de {roic:.1f}% abaixo de {THR['stock_roic_baixo']:.0f}%."))
    if marg is not None:
        if marg < THR["stock_marg_neg"]:
            alerts.append(("red", "risk", "Margem líquida negativa",
                f"{t} — margem: {marg:.1f}%. Verifique se é situação transitória."))
        elif 0 < marg < 5:
            alerts.append(("yellow", "warn", "Margem pressionada",
                f"{t} — margem líquida de {marg:.1f}%."))
    if divid and divid > THR["stock_divida_alta"]:
        alerts.append(("red", "risk", "Endividamento elevado",
            f"{t} — Dív.Bruta/Patrim. de {divid:.2f}x. Alavancagem elevada."))
    if dy and dy > THR["stock_dy_alto"]:
        alerts.append(("yellow", "warn", "Dividend Yield muito elevado",
            f"{t} — DY de {dy:.1f}%. Verifique sustentabilidade."))
    return alerts


def alerts_fii(pos: dict, fd: dict) -> list[Alert]:
    alerts: list[Alert] = []
    t    = pos["ticker"]
    peso = _safe_f(pos.get("pct_carteira", 0))
    pvp  = fd.get("pvp");  dy  = fd.get("dy")
    vac  = fd.get("vacancia_media") or fd.get("vacancia_fisica")
    qtd  = fd.get("qtd_imoveis")

    if peso > THR["fii_conc_max"]:
        alerts.append(("yellow", "warn", "Concentração elevada",
            f"{t} representa {peso:.1f}% da carteira."))
    if pvp:
        if pvp > THR["fii_pvp_premium"]:
            alerts.append(("yellow", "warn", "P/VP com prêmio",
                f"{t} — P/VP de {pvp:.2f}x. Cota pode estar cara."))
        elif pvp < THR["fii_pvp_desconto"]:
            alerts.append(("green", "opp", "P/VP com desconto",
                f"{t} — P/VP de {pvp:.2f}x. Analise motivo."))
    if vac is not None and vac > THR["fii_vacancia_alta"]:
        alerts.append(("red", "risk", "Vacância elevada",
            f"{t} — vacância de {vac:.1f}%."))
    elif vac is not None and vac == 0:
        alerts.append(("green", "opp", "Vacância zero", f"{t} — 100% ocupado."))
    if dy and dy > THR["fii_dy_alto"]:
        alerts.append(("yellow", "warn", "Yield muito elevado",
            f"{t} — DY de {dy:.1f}%. Pode indicar queda de cota."))
    if qtd is not None and qtd <= 3:
        alerts.append(("yellow", "warn", "Concentração em poucos imóveis",
            f"{t} — apenas {int(qtd)} imóvel(is)."))
    return alerts
