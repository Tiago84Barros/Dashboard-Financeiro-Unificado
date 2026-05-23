"""
pages/investimentos.py  — v3 (layout original replicado)

Replica as 4 seções do app dashboard-investimentos.streamlit.app:
  Tabs: Dashboard | Histórico | Carteira | Análise

Dashboard:
  6 KPIs — Patrimônio Total, BR Brasil, Exterior, Resultado Acumulado,
            Renda Recebida 12M, Dividend Yield 12M
  Card   — Nº de Ativos + N Efetivo (diversificação)
  Visão Geral — Estado da Carteira (análise qualitativa) + Evolução Patrimonial
  Análise de Risco — Radar de Risco + Ações Sugeridas
  Distribuição — Donut por classe + Barras por classe

Dados: core/investimentos + core/proventos
"""
import html as _html
from datetime import date as _date, datetime as _datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.investimentos import get_carteira, get_cashflow_mensal, get_evolucao_patrimonial
from core.proventos import get_proventos
from core.utils import fmt_moeda, fmt_percentual
from design.componentes import badge_status
import core.fundamentus as _fund
import core.data_reconciliacao as _recon

# ── Paleta ────────────────────────────────────────────────────────────────────
_COR_POSITIVO = "#00C896"
_COR_NEGATIVO = "#FC5C7D"
_COR_INFO     = "#4A9EFF"
_COR_ALERTA   = "#F6C90E"
_COR_NEUTRO   = "#9CA3AF"
_COR_ROXO     = "#9B59B6"


_ICONES_B3_CDN = (
    "https://raw.githubusercontent.com/thefintz/icones-b3/main/icones"
)

# ── CSS dos cards fundamentalistas (injetado uma vez por sessão) ──────────────
_FUND_CSS = """
<style>
.fund-card {
    background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);
    border-radius:14px;padding:16px 18px;margin-bottom:10px;transition:border-color .2s;
}
.fund-card:hover { border-color:rgba(0,200,150,0.35); }
.fund-header { display:flex;justify-content:space-between;align-items:flex-start;
               margin-bottom:10px;gap:10px; }
.fund-ticker { font-size:1.0rem;font-weight:700;color:#E2E8F0; }
.fund-name   { font-size:0.71rem;color:#8b9ab0;margin-top:2px;max-width:200px; }
.fund-price  { font-size:1.0rem;font-weight:700;color:#00C896;text-align:right; }
.fund-chg-pos { font-size:0.70rem;color:#00C896;text-align:right; }
.fund-chg-neg { font-size:0.70rem;color:#FC5C7D;text-align:right; }
.fund-chg-neu { font-size:0.70rem;color:#9CA3AF;text-align:right; }
.fund-row { display:flex;justify-content:space-between;align-items:center;
            padding:4px 0;border-bottom:1px solid rgba(255,255,255,0.04); }
.fund-row:last-child { border-bottom:none; }
.fund-key     { font-size:0.67rem;color:#8b9ab0; }
.fund-val     { font-size:0.77rem;font-weight:600;color:#E2E8F0; }
.fund-val-pos { font-size:0.77rem;font-weight:600;color:#00C896; }
.fund-val-neg { font-size:0.77rem;font-weight:600;color:#FC5C7D; }
.fund-val-warn{ font-size:0.77rem;font-weight:600;color:#F6C90E; }
.fund-sec { font-size:0.63rem;font-weight:700;color:#8b9ab0;text-transform:uppercase;
            letter-spacing:.05em;padding:6px 0 2px;margin-top:4px;
            border-top:1px solid rgba(255,255,255,0.06); }
.f-chip { display:inline-block;font-size:0.69rem;font-weight:600;
          padding:3px 10px;border-radius:20px;margin:2px 3px 2px 0; }
.f-chip-green  { background:rgba(0,200,150,0.15);color:#00C896;border:1px solid rgba(0,200,150,0.3); }
.f-chip-yellow { background:rgba(246,201,14,0.15);color:#F6C90E;border:1px solid rgba(246,201,14,0.3); }
.f-chip-red    { background:rgba(252,92,125,0.15);color:#FC5C7D;border:1px solid rgba(252,92,125,0.3); }
.f-chip-blue   { background:rgba(74,158,255,0.15);color:#4A9EFF;border:1px solid rgba(74,158,255,0.3); }
.f-chip-purple { background:rgba(155,89,182,0.15);color:#9B59B6;border:1px solid rgba(155,89,182,0.3); }
.alert-item { border-left:3px solid;padding:10px 14px;margin-bottom:9px;
              border-radius:0 8px 8px 0;background:rgba(255,255,255,0.03);
              font-size:0.83rem;color:#c8d4e0;line-height:1.45; }
.alert-red    { border-color:#FC5C7D; }
.alert-yellow { border-color:#F6C90E; }
.alert-green  { border-color:#00C896; }
.alert-blue   { border-color:#4A9EFF; }
.alert-lbl { font-size:0.67rem;font-weight:700;text-transform:uppercase;
             letter-spacing:.05em;margin-bottom:2px; }
.lbl-risk { color:#FC5C7D; } .lbl-warn { color:#F6C90E; }
.lbl-opp  { color:#00C896; } .lbl-info { color:#4A9EFF; }
</style>
"""

# ── Helpers HTML para cards fundamentalistas ──────────────────────────────────

def _f_br(v, d: int = 2) -> str:
    try:
        s = f"{float(v):,.{d}f}"
        return s.replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "—"

def _f_brs(v, d: int = 2) -> str:
    return f"R$ {_f_br(v, d)}"

def _f_big(v) -> str:
    try:
        v = float(v)
        if abs(v) >= 1e9: return f"R$ {v/1e9:.2f}B"
        if abs(v) >= 1e6: return f"R$ {v/1e6:.2f}M"
        return _f_brs(v, 0)
    except Exception:
        return "—"

def _f_chip(label: str, color: str = "blue") -> str:
    return f'<span class="f-chip f-chip-{color}">{_html.escape(label)}</span>'

def _f_row(key: str, val: str, cls: str = "fund-val") -> str:
    return (f'<div class="fund-row"><span class="fund-key">{_html.escape(key)}</span>'
            f'<span class="{cls}">{val}</span></div>')

def _f_sec(title: str) -> str:
    return f'<div class="fund-sec">{_html.escape(title)}</div>'

def _f_color_pct(v, good_positive: bool = True) -> str:
    if v is None: return "fund-val"
    if good_positive:
        return "fund-val-pos" if v > 0.1 else ("fund-val-neg" if v < -0.1 else "fund-val")
    return "fund-val-neg" if v > 0.1 else ("fund-val-pos" if v < -0.1 else "fund-val")

def _f_logo(ticker: str) -> str:
    clean = ticker.removesuffix(".SA")
    base  = clean[:-1] if clean.endswith("F") and len(clean) > 4 else clean
    esc   = _html.escape(ticker)
    tid   = ticker.replace(" ", "_")
    url   = f"{_ICONES_B3_CDN}/{base}.png"
    return (
        f'<img src="{url}" '
        f'style="width:36px;height:36px;border-radius:8px;object-fit:contain;'
        f'background:rgba(255,255,255,0.08);padding:3px;flex-shrink:0;" '
        f'onerror="this.style.display=\'none\';'
        f'document.getElementById(\'ph_{tid}\').style.display=\'flex\';">'
        f'<div id="ph_{tid}" style="display:none;width:36px;height:36px;border-radius:8px;'
        f'background:rgba(0,200,150,0.2);align-items:center;justify-content:center;'
        f'font-size:0.82rem;font-weight:700;color:#00C896;flex-shrink:0;">{_html.escape(esc[:3])}</div>'
    )

def _f_alert_html(cls: str, lbl_cls: str, label: str, msg: str) -> str:
    return (f'<div class="alert-item alert-{cls}">'
            f'<div class="alert-lbl lbl-{lbl_cls}">{_html.escape(label)}</div>'
            f'{_html.escape(msg)}</div>')


def _get_logos(tickers: tuple) -> dict:
    """Constrói URLs de logo via CDN icones-b3 (GitHub público, sem auth).
    Tickers fracionários (PETR3F) mapeados para o ticker-base (PETR3).
    Logos inexistentes são tratados pelo onerror no img tag.
    """
    logos: dict[str, str] = {}
    for t in tickers:
        if not t:
            continue
        clean = t.removesuffix(".SA")
        base  = clean[:-1] if clean.endswith("F") and len(clean) > 4 else clean
        logos[t] = f"{_ICONES_B3_CDN}/{base}.png"
    return logos


# ── Fatores macro e coeficientes por classe ───────────────────────────────────
_MACRO_FATORES = [
    "Brasil / Risco Fiscal",
    "Selic / CDI / Juros",
    "Bolsa Brasil",
    "Câmbio / Dólar",
    "Renda Variável EUA",
    "Inflação / IPCA",
]
# Chaves em minúsculas (substring match contra cls["nome"].lower())
# Valores: [brasil, selic, bolsa_br, cambio, rv_eua, ipca]
_MACRO_COEF: dict[str, list] = {
    "ações":      [0.90, 0.20, 0.85, 0.10, 0.10, 0.30],
    "fii":        [0.85, 0.65, 0.25, 0.05, 0.05, 0.40],
    "fundo imob": [0.85, 0.65, 0.25, 0.05, 0.05, 0.40],
    "renda fixa": [0.60, 0.95, 0.05, 0.05, 0.05, 0.55],
    "tesouro":    [0.55, 0.95, 0.03, 0.03, 0.03, 0.65],
    "bdr":        [0.20, 0.10, 0.15, 0.80, 0.85, 0.10],
    "etf":        [0.20, 0.10, 0.20, 0.75, 0.80, 0.10],
    "cripto":     [0.20, 0.05, 0.15, 0.50, 0.60, 0.15],
    "default":    [0.55, 0.35, 0.35, 0.25, 0.20, 0.30],
}


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS — métricas e computed fields
# ══════════════════════════════════════════════════════════════════════════════

def _calc_n_efetivo(posicoes: list) -> float:
    """N efetivo = 1 / Σwi² (índice inverso de Herfindahl)."""
    if not posicoes:
        return 0.0
    hhi = sum((p["pct_carteira"] / 100) ** 2 for p in posicoes)
    return round(1 / hhi, 1) if hhi > 0 else 0.0


def _is_exterior_position(pos: dict) -> bool:
    pais = str(pos.get("pais") or pos.get("country") or "BR").upper()
    moeda = str(pos.get("moeda") or "BRL").upper()
    return pais not in ("", "BR") or moeda != "BRL"


def _split_br_ext(posicoes: list) -> tuple:
    """Retorna (valor_br, valor_ext), respeitando pais e moeda original."""
    br = sum(p["valor_mercado"] for p in posicoes if not _is_exterior_position(p))
    ext = sum(p["valor_mercado"] for p in posicoes if _is_exterior_position(p))
    return round(br, 2), round(ext, 2)


def _estado_carteira(carteira: dict, n_efetivo: float, pct_ext: float) -> list:
    """Gera análise qualitativa da carteira — lista de dicts {tipo, titulo, texto}."""
    analise  = []
    total    = carteira["total_mercado"]
    por_cls  = carteira.get("por_classe", [])
    rentab   = carteira["rentabilidade_total_pct"]

    # Diversificação
    if n_efetivo >= 10:
        analise.append({"tipo": "positivo",
                        "titulo": "POSITIVO",
                        "texto": f"Boa diversificação — N efetivo = {n_efetivo} ativos equivalentes."})
    elif n_efetivo >= 5:
        analise.append({"tipo": "info",
                        "titulo": "INFO",
                        "texto": f"Diversificação moderada — N efetivo = {n_efetivo} ativos equivalentes."})
    else:
        analise.append({"tipo": "alerta",
                        "titulo": "ATENÇÃO",
                        "texto": f"Baixa diversificação — N efetivo = {n_efetivo}. Considere ampliar para ≥ 10."})

    # Maior classe
    if por_cls:
        maior_cls = max(por_cls, key=lambda c: c["pct_carteira"])
        if maior_cls["pct_carteira"] <= 40:
            analise.append({"tipo": "info",
                            "titulo": "INFO",
                            "texto": f"Maior classe: {maior_cls['nome']} com "
                                     f"{maior_cls['pct_carteira']:.1f}% — dentro do limite de 40%."})
        else:
            analise.append({"tipo": "alerta",
                            "titulo": "ATENÇÃO",
                            "texto": f"Concentração alta: {maior_cls['nome']} com "
                                     f"{maior_cls['pct_carteira']:.1f}% (limite recomendado: 40%)."})

    # Exposição exterior
    if pct_ext > 0:
        analise.append({"tipo": "info",
                        "titulo": "INFO",
                        "texto": f"Exposição internacional: {pct_ext:.1f}% do patrimônio em moeda estrangeira."})

    # FIIs
    fii = next((c for c in por_cls if "FII" in c["nome"] or "fii" in c["nome"].lower()), None)
    if fii:
        if fii["pct_carteira"] > 0:
            analise.append({"tipo": "info",
                            "titulo": "INFO",
                            "texto": f"FIIs com {fii['pct_carteira']:.2f}% de peso — participação no portfólio."})

    # Resultado
    if rentab > 0:
        analise.append({"tipo": "positivo",
                        "titulo": "POSITIVO",
                        "texto": f"Portfólio com resultado positivo de +{rentab:.2f}% sobre o custo."})
    elif rentab < 0:
        analise.append({"tipo": "negativo",
                        "titulo": "ATENÇÃO",
                        "texto": f"Portfólio em queda de {rentab:.2f}% sobre o custo histórico."})
    else:
        analise.append({"tipo": "info",
                        "titulo": "INFO",
                        "texto": "Rentabilidade em 0% — importe cotações em Configurações > Atualização de Dados."})

    return analise


def _acoes_sugeridas(carteira: dict, n_efetivo: float, dy: float) -> list:
    """Gera lista de ações sugeridas baseadas nos dados do portfólio."""
    sugestoes = []
    por_cls   = carteira.get("por_classe", [])

    if carteira.get("posicoes"):
        sugestoes.append({
            "cor":   _COR_INFO,
            "tag":   "📋",
            "titulo": "Acompanhe os fundamentos dos ativos com maior peso",
            "texto":  "Revise regularmente os resultados trimestrais, payout e perspectivas dos ativos de maior participação.",
        })

    if not carteira["cotacoes_disponiveis"]:
        sugestoes.append({
            "cor":   _COR_ALERTA,
            "tag":   "⚡",
            "titulo": "Importe cotações para calcular rentabilidade real",
            "texto":  "Acesse Configurações > Atualização de Dados e execute a atualização.",
        })

    if n_efetivo < 10:
        sugestoes.append({
            "cor":   _COR_ALERTA,
            "tag":   "📊",
            "titulo": "Considere aumentar a diversificação",
            "texto":  f"N efetivo atual: {n_efetivo}. Meta recomendada: ≥ 10 ativos equivalentes.",
        })

    if dy < 3 and dy >= 0:
        sugestoes.append({
            "cor":   _COR_NEUTRO,
            "tag":   "💵",
            "titulo": "Dividend Yield abaixo de 3%",
            "texto":  "Avalie incluir mais ativos pagadores de dividendos (FIIs, ações dividendeiras).",
        })

    if not sugestoes:
        sugestoes.append({
            "cor":   _COR_POSITIVO,
            "tag":   "✅",
            "titulo": "Portfólio bem estruturado",
            "texto":  "Continue monitorando e rebalanceando conforme a estratégia.",
        })

    return sugestoes


def _calc_dependencias_macro(por_classe: list) -> list:
    """Retorna exposição estimada (%) a cada fator macro, ponderada pela alocação."""
    if not por_classe:
        return []

    acumulado = [0.0] * len(_MACRO_FATORES)
    for cls in por_classe:
        nome_lower = cls["nome"].lower()
        coefs = _MACRO_COEF["default"]
        for key, vals in _MACRO_COEF.items():
            if key != "default" and key in nome_lower:
                coefs = vals
                break
        w = cls["pct_carteira"] / 100
        for i, c in enumerate(coefs):
            acumulado[i] += w * c * 100

    return [
        {"fator": f, "exposicao": round(acumulado[i], 1)}
        for i, f in enumerate(_MACRO_FATORES)
    ]


def _macro_coefs_for_class(classe: str) -> list:
    nome_lower = (classe or "").lower()
    for key, vals in _MACRO_COEF.items():
        if key != "default" and key in nome_lower:
            return vals
    return _MACRO_COEF["default"]


def _is_rf_ou_tesouro(classe: str) -> bool:
    nome_lower = (classe or "").lower()
    return any(k in nome_lower for k in ("tesouro", "renda fixa", "fundo rf", "fundo renda fixa"))


def _label_tesouro_codigo(ticker: str) -> str | None:
    codigo = (ticker or "").upper().strip()
    if not codigo:
        return None
    ano = "".join(ch for ch in codigo if ch.isdigit())
    ano = ano[-4:] if len(ano) >= 4 else ""
    if codigo.startswith("TSELIC"):
        return f"Tesouro Selic {ano}".strip()
    if codigo.startswith("TIPCA"):
        return f"Tesouro IPCA+ {ano}".strip()
    if codigo.startswith("TPRE"):
        return f"Tesouro Prefixado {ano}".strip()
    if codigo.startswith("TEDUCA"):
        return f"Tesouro Educa+ {ano}".strip()
    return None


def _asset_display_label(pos: dict) -> str:
    ticker = str(pos.get("ticker") or "").upper().strip()
    nome = str(pos.get("nome") or "").strip()
    classe = str(pos.get("classe") or "")
    if "tesouro" in classe.lower():
        return _label_tesouro_codigo(ticker) or nome or ticker
    if _is_rf_ou_tesouro(classe):
        if nome and nome.upper() != ticker:
            return nome
        return ticker
    return ticker or nome


def _short_asset_label(label: str, max_len: int = 48) -> str:
    texto = " ".join(str(label or "").split())
    if len(texto) <= max_len:
        return texto
    return texto[:max_len - 1].rstrip() + "..."


def _calc_dependencias_macro_ativos(posicoes: list) -> pd.DataFrame:
    """Exposicao macro por ativo, ponderada pelo peso real na carteira."""
    rows = []
    for pos in posicoes:
        peso_pct = float(pos.get("pct_carteira") or 0)
        if peso_pct <= 0:
            continue
        coefs = _macro_coefs_for_class(str(pos.get("classe") or ""))
        ativo_label = _asset_display_label(pos)
        row = {
            "Ticker": pos.get("ticker"),
            "Ativo": ativo_label,
            "Rotulo": _short_asset_label(ativo_label),
            "Nome": pos.get("nome"),
            "Classe": pos.get("classe"),
            "Peso (%)": round(peso_pct, 2),
            "Valor": float(pos.get("valor_mercado") or 0),
        }
        for fator, coef in zip(_MACRO_FATORES, coefs):
            row[fator] = round(peso_pct * float(coef), 2)
        row["Exposicao Total"] = round(sum(row[f] for f in _MACRO_FATORES), 2)
        rows.append(row)
    return pd.DataFrame(rows)


def _yf_symbol_for_pos(pos: dict) -> str | None:
    ticker = str(pos.get("ticker") or "").upper().strip()
    if not ticker:
        return None
    classe = str(pos.get("classe") or "").lower()
    pais = str(pos.get("pais") or "BR").upper().strip()
    if "tesouro" in classe or "renda fixa" in classe or "fundo rf" in classe:
        return None
    if pais not in ("", "BR"):
        return ticker
    if ticker.endswith("F") and len(ticker) > 4:
        ticker = ticker[:-1]
    if any(k in classe for k in ("aÃ§", "aç", "fii", "etf")):
        return f"{ticker}.SA"
    return None


@st.cache_data(ttl=3600, show_spinner=False)
def _load_corr_precos_db(symbol_map: tuple[tuple[str, str], ...], period: str = "2y") -> dict:
    """Fallback: usa asset_quotes/snapshots do banco quando yfinance nao esta disponivel."""
    tickers = [tk for tk, _ in symbol_map]
    if len(tickers) < 2:
        return {"corr": pd.DataFrame(), "returns": pd.DataFrame(), "symbols_ok": []}

    try:
        from sqlalchemy import bindparam, text
        from core.config import settings
        from core.database import get_engine
    except Exception:
        return {"corr": pd.DataFrame(), "returns": pd.DataFrame(), "symbols_ok": []}

    engine = get_engine()
    owner = getattr(settings, "OWNER_USER_ID", None)
    if engine is None or not owner:
        return {"corr": pd.DataFrame(), "returns": pd.DataFrame(), "symbols_ok": []}

    quote_frame = pd.DataFrame()
    frames = []
    with engine.connect() as conn:
        try:
            q_quotes = text("""
                SELECT a.ticker, aq.timestamp::date AS data, aq.close AS preco
                FROM asset_quotes aq
                JOIN assets a ON a.id = aq.asset_id
                WHERE a.ticker IN :tickers
                  AND aq.close IS NOT NULL
                  AND aq.timestamp >= CURRENT_DATE - INTERVAL '3 years'
                ORDER BY aq.timestamp
            """).bindparams(bindparam("tickers", expanding=True))
            rows = conn.execute(q_quotes, {"tickers": tickers}).mappings().all()
            if rows:
                quote_frame = pd.DataFrame(rows)
        except Exception:
            pass

        if not quote_frame.empty:
            dfq = quote_frame.copy()
            dfq["data"] = pd.to_datetime(dfq["data"], errors="coerce")
            dfq["preco"] = pd.to_numeric(dfq["preco"], errors="coerce")
            dfq = dfq.dropna(subset=["data", "ticker", "preco"])
            daily = (
                dfq.sort_values("data")
                .drop_duplicates(["data", "ticker"], keep="last")
                .pivot(index="data", columns="ticker", values="preco")
                .sort_index()
                .dropna(axis=1, thresh=12)
            )
            daily_returns = daily.pct_change(fill_method=None).dropna(how="all").dropna(axis=1, thresh=10)
            if daily_returns.shape[1] >= 2:
                corr = daily_returns.corr(min_periods=10).dropna(how="all").dropna(axis=1, how="all").round(3)
                if corr.shape[1] >= 2:
                    return {"corr": corr, "returns": daily_returns, "symbols_ok": list(corr.columns)}

        try:
            q_snap = text("""
                SELECT
                    a.ticker,
                    pps.report_date::date AS data,
                    COALESCE(
                        NULLIF(pps.market_price, 0),
                        NULLIF(pps.market_value, 0) / NULLIF(pps.quantity, 0)
                    ) AS preco
                FROM portfolio_position_snapshots pps
                JOIN assets a ON a.id = pps.asset_id
                WHERE pps.user_id = :uid
                  AND a.ticker IN :tickers
                  AND pps.report_date >= CURRENT_DATE - INTERVAL '5 years'
                  AND COALESCE(
                        NULLIF(pps.market_price, 0),
                        NULLIF(pps.market_value, 0) / NULLIF(pps.quantity, 0)
                  ) IS NOT NULL
                ORDER BY pps.report_date
            """).bindparams(bindparam("tickers", expanding=True))
            rows = conn.execute(q_snap, {"uid": owner, "tickers": tickers}).mappings().all()
            if rows:
                frames.append(pd.DataFrame(rows))
        except Exception:
            pass

    if not frames:
        return {"corr": pd.DataFrame(), "returns": pd.DataFrame(), "symbols_ok": []}

    df = pd.concat(frames, ignore_index=True)
    df["data"] = pd.to_datetime(df["data"], errors="coerce")
    df["preco"] = pd.to_numeric(df["preco"], errors="coerce")
    df = df.dropna(subset=["data", "ticker", "preco"])
    if df.empty:
        return {"corr": pd.DataFrame(), "returns": pd.DataFrame(), "symbols_ok": []}

    close = (
        df.sort_values("data")
        .drop_duplicates(["data", "ticker"], keep="last")
        .pivot(index="data", columns="ticker", values="preco")
        .sort_index()
    )
    close = close.resample("ME").last().dropna(axis=1, thresh=7)
    returns = close.pct_change(fill_method=None).replace([float("inf"), float("-inf")], pd.NA).dropna(how="all")
    returns = returns.dropna(axis=1, thresh=6)
    if returns.shape[1] < 2:
        return {"corr": pd.DataFrame(), "returns": returns, "symbols_ok": list(returns.columns)}
    corr = returns.corr(min_periods=6).dropna(how="all").dropna(axis=1, how="all").round(3)
    return {"corr": corr, "returns": returns, "symbols_ok": list(corr.columns)}


@st.cache_data(ttl=3600, show_spinner=False)
def _load_corr_precos(symbol_map: tuple[tuple[str, str], ...], period: str = "2y") -> dict:
    """Baixa precos e retorna correlacao de retornos mensais para ativos negociaveis."""
    if len(symbol_map) < 2:
        return {"corr": pd.DataFrame(), "returns": pd.DataFrame(), "symbols_ok": []}
    try:
        import yfinance as yf
    except Exception:
        return _load_corr_precos_db(symbol_map, period)

    tickers = [sym for _, sym in symbol_map]
    symbol_to_ticker = {sym: tk for tk, sym in symbol_map}

    def _close_from_download(raw_data: pd.DataFrame) -> pd.DataFrame:
        if raw_data is None or raw_data.empty:
            return pd.DataFrame()
        if isinstance(raw_data.columns, pd.MultiIndex):
            if "Close" in raw_data.columns.get_level_values(0):
                close_data = raw_data["Close"]
            elif "Adj Close" in raw_data.columns.get_level_values(0):
                close_data = raw_data["Adj Close"]
            else:
                close_data = raw_data.xs(raw_data.columns.get_level_values(0)[0], axis=1, level=0)
        else:
            close_data = raw_data[["Close"]] if "Close" in raw_data.columns else raw_data
            if len(tickers) == 1:
                close_data.columns = tickers
        return close_data.rename(columns=symbol_to_ticker)

    try:
        raw = yf.download(tickers, period=period, interval="1d", auto_adjust=True,
                          progress=False, threads=True)
    except Exception:
        return _load_corr_precos_db(symbol_map, period)

    close = _close_from_download(raw)
    if close.empty or close.dropna(axis=1, thresh=30).shape[1] < 2:
        series = {}
        for ticker, symbol in symbol_map:
            try:
                hist = yf.download(symbol, period=period, interval="1d", auto_adjust=True,
                                   progress=False, threads=False)
                item_close = _close_from_download(hist)
                if not item_close.empty:
                    series[ticker] = item_close.iloc[:, 0]
            except Exception:
                continue
        close = pd.DataFrame(series)

    close = close.dropna(axis=1, thresh=30)
    if close.shape[1] < 2:
        return _load_corr_precos_db(symbol_map, period)

    close = close.resample("ME").last().dropna(how="all")
    returns = close.pct_change(fill_method=None).replace([float("inf"), float("-inf")], pd.NA).dropna(how="all")
    returns = returns.dropna(axis=1, thresh=6)
    if returns.shape[1] < 2:
        db_data = _load_corr_precos_db(symbol_map, period)
        return db_data if not db_data.get("corr", pd.DataFrame()).empty else {
            "corr": pd.DataFrame(), "returns": returns, "symbols_ok": list(returns.columns)
        }
    corr = returns.corr(min_periods=6).dropna(how="all").dropna(axis=1, how="all").round(3)
    if corr.shape[1] < 2:
        db_data = _load_corr_precos_db(symbol_map, period)
        return db_data if not db_data.get("corr", pd.DataFrame()).empty else {
            "corr": corr, "returns": returns, "symbols_ok": list(corr.columns)
        }
    return {"corr": corr, "returns": returns, "symbols_ok": list(corr.columns)}


def _build_corr_data(posicoes: list) -> dict:
    symbol_map = []
    seen = set()
    skipped = []
    for pos in sorted(posicoes, key=lambda p: float(p.get("valor_mercado") or 0), reverse=True):
        tk = str(pos.get("ticker") or "").upper().strip()
        sym = _yf_symbol_for_pos(pos)
        if sym:
            if tk not in seen:
                symbol_map.append((tk, sym))
                seen.add(tk)
        elif tk:
            skipped.append(tk)
    data = _load_corr_precos(tuple(symbol_map[:28]))
    data["skipped"] = skipped
    data["requested"] = [tk for tk, _ in symbol_map[:28]]
    return data


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS — Cards CSS (sem comentários HTML)
# ══════════════════════════════════════════════════════════════════════════════

def _kpi_macro(titulo: str, valor: str, sub: str, cor: str) -> str:
    """Card compacto para 7 colunas (macro)."""
    return (
        f'<div style="background:#12151E;border:1px solid #1E2533;'
        f'border-radius:8px;padding:13px 11px 9px;height:100%;">'
        f'<div style="font-size:0.54rem;font-weight:800;text-transform:uppercase;'
        f'letter-spacing:0.11em;color:#4A5568;margin-bottom:5px;">{titulo}</div>'
        f'<div style="font-size:1.15rem;font-weight:800;color:{cor};'
        f'letter-spacing:-0.02em;line-height:1.1;margin-bottom:3px;'
        f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{valor}</div>'
        f'<div style="font-size:0.62rem;color:#4A5568;line-height:1.3;">{sub}</div>'
        f'</div>'
    )


def _kpi(titulo: str, valor: str, sub: str, cor: str, tag: str = "") -> str:
    tag_html = (
        f'<span style="font-size:0.65rem;font-weight:700;padding:1px 5px;'
        f'border-radius:3px;background:rgba(0,200,150,0.15);'
        f'color:{_COR_POSITIVO};margin-bottom:4px;display:inline-block;">{tag}</span><br>'
        if tag else ""
    )
    return (
        f'<div style="background:#12151E;border:1px solid #1E2533;'
        f'border-radius:10px;padding:18px 16px 14px;height:100%;">'
        f'<div style="font-size:0.60rem;font-weight:800;text-transform:uppercase;'
        f'letter-spacing:0.13em;color:#4A5568;margin-bottom:8px;">{titulo}</div>'
        f'{tag_html}'
        f'<div style="font-size:1.60rem;font-weight:800;color:{cor};'
        f'letter-spacing:-0.02em;line-height:1.1;margin-bottom:6px;">{valor}</div>'
        f'<div style="font-size:0.72rem;color:#4A5568;line-height:1.3;">{sub}</div>'
        f'</div>'
    )


def _estado_item(tipo: str, titulo: str, texto: str) -> str:
    cores = {
        "positivo": (_COR_POSITIVO, "✓"),
        "negativo": (_COR_NEGATIVO, "✗"),
        "alerta":   (_COR_ALERTA,   "⚠"),
        "info":     (_COR_INFO,     "i"),
    }
    cor, icone = cores.get(tipo, (_COR_NEUTRO, "·"))
    return (
        f'<div style="border-left:3px solid {cor};padding:8px 12px;'
        f'margin-bottom:8px;background:rgba(255,255,255,0.02);border-radius:0 6px 6px 0;">'
        f'<div style="font-size:0.65rem;font-weight:800;text-transform:uppercase;'
        f'letter-spacing:0.1em;color:{cor};margin-bottom:3px;">'
        f'{icone} {titulo}</div>'
        f'<div style="font-size:0.80rem;color:#CBD5E0;">{texto}</div>'
        f'</div>'
    )


def _secao_titulo_orig(icone: str, titulo: str, sub: str = "") -> None:
    """Título de seção estilo app original (ícone grande + texto)."""
    sub_html = (
        f'<div style="font-size:0.80rem;color:#718096;margin-top:2px;">{sub}</div>'
        if sub else ""
    )
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:10px;'
        f'margin-top:24px;margin-bottom:12px;">'
        f'<span style="font-size:1.5rem">{icone}</span>'
        f'<div>'
        f'<span style="font-size:1.30rem;font-weight:800;color:#E2E8F0;">{titulo}</span>'
        f'{sub_html}'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS — Gráficos
# ══════════════════════════════════════════════════════════════════════════════

def _fig_evolucao(cashflow: list, total_atual: float) -> go.Figure:
    """Simula evolução patrimonial acumulando saldos mensais de trás para frente."""
    h = list(reversed(cashflow))      # mais recente primeiro
    vals = [total_atual]
    for c in h[:-1]:
        vals.append(vals[-1] - c["saldo"])  # desfaz cada mês

    vals  = list(reversed(vals))
    h     = list(reversed(h))
    meses = [c["label"] for c in h]

    fig = go.Figure(go.Scatter(
        x=meses, y=vals,
        mode="lines",
        line={"color": _COR_POSITIVO, "width": 2.5},
        fill="tozeroy",
        fillcolor="rgba(0,200,150,0.07)",
        hovertemplate="<b>%{x}</b><br>R$ %{y:,.0f}<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        margin={"t": 8, "b": 0, "l": 0, "r": 0},
        height=280,
        yaxis={"showgrid": True, "gridcolor": "#1E2533",
               "tickformat": ",.0f", "tickprefix": "R$ "},
        xaxis={"showgrid": False},
        showlegend=False,
    )
    return fig


def _fig_radar(carteira: dict, n_efetivo: float, pct_ext: float) -> go.Figure:
    """Radar de risco com 5 dimensões."""
    posicoes = carteira.get("posicoes", [])
    total    = carteira["total_mercado"]
    por_cls  = carteira.get("por_classe", [])

    # Maior posição individual
    max_pos = max((p["pct_carteira"] for p in posicoes), default=0)
    # Maior classe
    max_cls = max((c["pct_carteira"] for c in por_cls), default=0)
    # FII pct
    fii = next((c for c in por_cls if "FII" in c["nome"]), None)
    fii_pct = fii["pct_carteira"] if fii else 0

    # Normaliza 0-10 (10 = máximo risco para concentração, 10 = máximo diversificação para n_efetivo)
    dim_conc_ativo  = min(max_pos / 40 * 10, 10)          # alto = risco
    dim_conc_setor  = min(max_cls / 50 * 10, 10)           # alto = risco
    dim_cambial     = min(pct_ext / 30 * 10, 10)           # alto = exposição
    dim_fii         = min(fii_pct / 30 * 10, 10)           # informativo
    dim_diversif    = min(n_efetivo / 20 * 10, 10)         # alto = menos risco

    categorias = [
        "Concentração<br>Ativo Individual",
        "Concentração<br>Setorial",
        "Exposição<br>Cambial",
        "Exposição<br>FIIs",
        "Diversificação",
    ]
    valores = [dim_conc_ativo, dim_conc_setor, dim_cambial, dim_fii, dim_diversif]
    valores_closed = valores + [valores[0]]   # fecha o polígono
    cats_closed    = categorias + [categorias[0]]

    fig = go.Figure(go.Scatterpolar(
        r=valores_closed,
        theta=cats_closed,
        fill="toself",
        fillcolor="rgba(74,158,255,0.15)",
        line={"color": _COR_INFO, "width": 2},
        hovertemplate="<b>%{theta}</b><br>%{r:.1f}/10<extra></extra>",
    ))
    fig.update_layout(
        polar={
            "radialaxis": {
                "visible": True, "range": [0, 10],
                "gridcolor": "#1E2533", "linecolor": "#1E2533",
                "tickcolor": _COR_NEUTRO, "tickfont": {"size": 9},
            },
            "angularaxis": {"linecolor": "#1E2533", "gridcolor": "#1E2533",
                            "tickfont": {"size": 9, "color": _COR_NEUTRO}},
            "bgcolor": "rgba(0,0,0,0)",
        },
        paper_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        margin={"t": 20, "b": 20, "l": 30, "r": 30},
        height=280,
        showlegend=False,
    )
    return fig


def _fig_donut_classes(por_classe: list) -> go.Figure:
    nomes = [c["nome"]          for c in por_classe]
    vals  = [c["valor_mercado"] for c in por_classe]
    cores = [c["cor"]           for c in por_classe]

    fig = go.Figure(go.Pie(
        labels=nomes, values=vals, hole=0.52,
        marker={"colors": cores, "line": {"color": "#0E1117", "width": 2}},
        textinfo="percent", textfont={"size": 11},
        hovertemplate="<b>%{label}</b><br>R$ %{value:,.0f}<br>%{percent}<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        legend={"font": {"size": 10}, "bgcolor": "rgba(0,0,0,0)"},
        margin={"t": 8, "b": 8, "l": 0, "r": 0},
        height=320,
    )
    return fig


def _fig_barras_classes(por_classe: list) -> go.Figure:
    nomes = [c["nome"]          for c in por_classe]
    vals  = [c["valor_mercado"] for c in por_classe]
    cores = [c["cor"]           for c in por_classe]

    # Rótulos sobre as barras
    texts = [f"R$ {v/1000:.0f}k" if v >= 1000 else fmt_moeda(v) for v in vals]

    fig = go.Figure(go.Bar(
        x=nomes, y=vals, marker_color=cores,
        text=texts, textposition="outside",
        textfont={"size": 10, "color": "#E2E8F0"},
        hovertemplate="<b>%{x}</b><br>R$ %{y:,.0f}<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        margin={"t": 30, "b": 10, "l": 0, "r": 0}, height=320,
        xaxis={"showgrid": False, "tickangle": -30},
        yaxis={"showgrid": True, "gridcolor": "#1E2533",
               "tickformat": ",.0f", "tickprefix": "R$ "},
        showlegend=False,
    )
    return fig


def _fig_cashflow_hist(cashflow: list) -> go.Figure:
    labels   = [c["label"]    for c in cashflow]
    receitas = [c["receitas"] for c in cashflow]
    despesas = [c["despesas"] for c in cashflow]
    saldos   = [c["saldo"]    for c in cashflow]

    fig = go.Figure()
    fig.add_trace(go.Bar(name="Receitas", x=labels, y=receitas,
                         marker_color=_COR_POSITIVO, opacity=0.85,
                         hovertemplate="<b>Receitas %{x}</b><br>R$ %{y:,.2f}<extra></extra>"))
    fig.add_trace(go.Bar(name="Despesas", x=labels, y=despesas,
                         marker_color=_COR_NEGATIVO, opacity=0.85,
                         hovertemplate="<b>Despesas %{x}</b><br>R$ %{y:,.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(name="Saldo", x=labels, y=saldos,
                             mode="lines+markers",
                             line={"color": _COR_INFO, "width": 2},
                             marker={"size": 6},
                             yaxis="y2",
                             hovertemplate="<b>Saldo %{x}</b><br>R$ %{y:,.2f}<extra></extra>"))
    fig.update_layout(
        barmode="group",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        legend={"orientation": "h", "y": -0.18, "font": {"size": 11},
                "bgcolor": "rgba(0,0,0,0)"},
        margin={"t": 10, "b": 10, "l": 0, "r": 0}, height=320,
        yaxis={"showgrid": True, "gridcolor": "#1E2533",
               "tickformat": ",.0f", "tickprefix": "R$ "},
        yaxis2={"overlaying": "y", "side": "right", "showgrid": False,
                "tickformat": ",.0f", "tickprefix": "R$ "},
        xaxis={"showgrid": False},
    )
    return fig


def _fig_aportes(fluxo: list, visao: str) -> go.Figure:
    """Barras de aportes mensais (últimos 12) ou anuais (histórico completo)."""
    if visao == "Mensal":
        data   = fluxo[-12:]
        labels = [d["label"]  for d in data]
        vals   = [d["aporte"] for d in data]
    else:
        agg: dict[int, float] = {}
        for d in fluxo:
            agg[d["ano"]] = round(agg.get(d["ano"], 0.0) + d["aporte"], 2)
        labels = [str(k) for k in sorted(agg)]
        vals   = [agg[k] for k in sorted(agg)]

    cores = [_COR_POSITIVO if v >= 0 else _COR_NEGATIVO for v in vals]
    texts = [
        f"R$ {v/1000:.1f}k" if abs(v) >= 1000 else f"R$ {v:.0f}"
        for v in vals
    ]

    fig = go.Figure(go.Bar(
        x=labels, y=vals,
        marker_color=cores,
        text=texts,
        textposition="outside",
        textfont={"size": 10, "color": "#E2E8F0"},
        hovertemplate="<b>%{x}</b><br>Aporte líquido: R$ %{y:,.0f}<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        margin={"t": 30, "b": 0, "l": 0, "r": 0},
        height=300,
        xaxis={"showgrid": False, "tickangle": -30 if visao == "Mensal" else 0},
        yaxis={"showgrid": True, "gridcolor": "#1E2533",
               "tickformat": ",.0f", "tickprefix": "R$ "},
        showlegend=False,
    )
    return fig


def _fig_evolucao_patrimonial(snapshots: list) -> go.Figure:
    """Três linhas: Valor de Mercado, Com Dividendos, Valor Investido."""
    labels    = [s["label"]                for s in snapshots]
    investido = [s["valor_investido"]      for s in snapshots]
    mercado   = [s["valor_mercado"]        for s in snapshots]
    com_div   = [s["valor_com_dividendos"] for s in snapshots]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=labels, y=com_div,
        name="Com Dividendos",
        mode="lines",
        line={"color": _COR_ALERTA, "width": 2},
        hovertemplate="<b>%{x}</b><br>c/ Dividendos: R$ %{y:,.0f}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=labels, y=mercado,
        name="Valor de Mercado",
        mode="lines",
        line={"color": _COR_POSITIVO, "width": 2.5},
        fill="tozeroy",
        fillcolor="rgba(0,200,150,0.06)",
        hovertemplate="<b>%{x}</b><br>Mercado: R$ %{y:,.0f}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=labels, y=investido,
        name="Valor Investido",
        mode="lines",
        line={"color": _COR_NEUTRO, "width": 1.5, "dash": "dot"},
        hovertemplate="<b>%{x}</b><br>Investido: R$ %{y:,.0f}<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        legend={"orientation": "h", "y": -0.15, "font": {"size": 11},
                "bgcolor": "rgba(0,0,0,0)"},
        margin={"t": 10, "b": 10, "l": 0, "r": 0},
        height=380,
        yaxis={"showgrid": True, "gridcolor": "#1E2533",
               "tickformat": ",.0f", "tickprefix": "R$ "},
        xaxis={"showgrid": False},
    )
    return fig


def _fig_dependencias_macro(deps: list) -> go.Figure:
    """Gráfico de barras horizontais — exposição macro do portfólio."""
    fatores = [d["fator"]    for d in deps]
    valores = [d["exposicao"] for d in deps]
    cores   = [
        _COR_NEGATIVO if v >= 70 else
        _COR_ALERTA   if v >= 50 else
        _COR_INFO
        for v in valores
    ]

    fig = go.Figure(go.Bar(
        x=valores, y=fatores,
        orientation="h",
        marker_color=cores,
        text=[f"{v:.1f}%" for v in valores],
        textposition="outside",
        textfont={"size": 11, "color": "#E2E8F0"},
        hovertemplate="<b>%{y}</b><br>Exposição: %{x:.1f}%<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        margin={"t": 10, "b": 10, "l": 0, "r": 70},
        height=260,
        xaxis={"showgrid": True, "gridcolor": "#1E2533",
               "range": [0, 115], "ticksuffix": "%"},
        yaxis={"showgrid": False, "automargin": True},
        showlegend=False,
    )
    return fig


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS — Dados externos (BCB + yfinance, cache 30 min)
# ══════════════════════════════════════════════════════════════════════════════

def _fig_macro_ativos(df_macro: pd.DataFrame, fator: str) -> go.Figure:
    """Mostra quais ativos mais contribuem para um fator macro."""
    if df_macro.empty or fator not in df_macro.columns:
        return go.Figure()

    df = df_macro.sort_values(fator, ascending=False).head(12).iloc[::-1]
    valores = df[fator].tolist()
    cores = [
        _COR_NEGATIVO if v >= 10 else
        _COR_ALERTA if v >= 5 else
        _COR_INFO
        for v in valores
    ]

    fig = go.Figure(go.Bar(
        x=valores,
        y=df["Rotulo"] if "Rotulo" in df.columns else df["Ativo"],
        orientation="h",
        marker_color=cores,
        text=[f"{v:.1f} p.p." for v in valores],
        textposition="outside",
        textfont={"size": 11, "color": "#E2E8F0"},
        customdata=df[["Ativo", "Ticker", "Classe", "Peso (%)"]].to_numpy(),
        hovertemplate=(
            "<b>%{y}</b><br>"
            "Ativo: %{customdata[0]}<br>"
            "Ticker: %{customdata[1]}<br>"
            "Classe: %{customdata[2]}<br>"
            "Peso: %{customdata[3]:.2f}%<br>"
            f"Contrib. {fator}: " + "%{x:.2f} p.p.<extra></extra>"
        ),
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        margin={"t": 10, "b": 10, "l": 0, "r": 90},
        height=max(320, 26 * len(df) + 90),
        xaxis={"showgrid": True, "gridcolor": "#1E2533", "ticksuffix": " p.p."},
        yaxis={"showgrid": False, "automargin": True},
        showlegend=False,
    )
    return fig


def _fig_corr_heatmap(corr: pd.DataFrame) -> go.Figure:
    """Mapa de calor da correlacao entre retornos mensais."""
    if corr.empty:
        return go.Figure()

    fig = go.Figure(go.Heatmap(
        z=corr.values,
        x=corr.columns,
        y=corr.index,
        zmin=-1,
        zmax=1,
        colorscale=[
            [0.00, "#2563EB"],
            [0.35, "#0F172A"],
            [0.50, "#1E293B"],
            [0.65, "#FACC15"],
            [1.00, "#F43F5E"],
        ],
        colorbar={"title": "corr."},
        text=corr.round(2).astype(str).values,
        texttemplate="%{text}",
        textfont={"size": 10, "color": "#F8FAFC"},
        hovertemplate="<b>%{y} x %{x}</b><br>Correlacao: %{z:.2f}<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        margin={"t": 10, "b": 70, "l": 0, "r": 0},
        height=max(430, 24 * len(corr.columns) + 180),
        xaxis={"tickangle": -45, "automargin": True},
        yaxis={"automargin": True},
    )
    return fig


def _corr_pairs(corr: pd.DataFrame) -> pd.DataFrame:
    """Lista pares de ativos por intensidade de correlacao."""
    if corr.empty:
        return pd.DataFrame()

    rows = []
    cols = list(corr.columns)
    for i, ativo_a in enumerate(cols):
        for ativo_b in cols[i + 1:]:
            val = corr.loc[ativo_a, ativo_b]
            if pd.isna(val):
                continue
            abs_val = abs(float(val))
            leitura = "Alta" if abs_val >= 0.70 else "Moderada" if abs_val >= 0.40 else "Baixa"
            rows.append({
                "Par": f"{ativo_a} x {ativo_b}",
                "Correlacao": round(float(val), 2),
                "|Correlacao|": round(abs_val, 2),
                "Leitura": leitura,
            })
    return pd.DataFrame(rows).sort_values("|Correlacao|", ascending=False)


@st.cache_data(ttl=1800)
def _get_macro_dados() -> dict:
    """Busca indicadores macro: BCB (SELIC, IPCA) + yfinance (câmbio, bolsas)."""
    import requests  # já é dep do streamlit
    try:
        import yfinance as yf
    except Exception:
        yf = None

    dados = {
        "selic":     14.75,
        "ipca_12m":  4.80,
        "cdi_12m":   14.65,
        "usdbrl":    5.75,
        "ibovespa":  130000.0,
        "sp500":     5500.0,
        "ifix":      3200.0,
    }

    # BCB: Meta SELIC (série 4189)
    try:
        r = requests.get(
            "https://api.bcb.gov.br/dados/serie/bcdata.sgs.4189/dados/ultimos/1?formato=json",
            timeout=5,
        )
        if r.ok and r.json():
            dados["selic"] = float(r.json()[0]["valor"].replace(",", "."))
    except Exception:
        pass

    # BCB: IPCA acumulado 12M (série 13522)
    try:
        r = requests.get(
            "https://api.bcb.gov.br/dados/serie/bcdata.sgs.13522/dados/ultimos/1?formato=json",
            timeout=5,
        )
        if r.ok and r.json():
            dados["ipca_12m"] = float(r.json()[0]["valor"].replace(",", "."))
    except Exception:
        pass

    # CDI ≈ SELIC − 0,10 p.p.
    dados["cdi_12m"] = round(dados["selic"] - 0.10, 2)

    # yfinance: câmbio e bolsas
    for sym, key in [
        ("USDBRL=X", "usdbrl"),
        ("^BVSP",    "ibovespa"),
        ("^GSPC",    "sp500"),
    ]:
        if yf is None:
            break
        try:
            hist = yf.Ticker(sym).history(period="5d")
            if not hist.empty:
                dados[key] = round(float(hist["Close"].iloc[-1]), 2)
        except Exception:
            pass

    return dados


# ══════════════════════════════════════════════════════════════════════════════
# TABS
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# Desempenho Histórico — Top 10 ações (linhas com retorno % normalizado)
# ══════════════════════════════════════════════════════════════════════════════

# Períodos suportados no selectbox + janela de dados a carregar
_PERFORMANCE_PERIODS: dict[str, dict] = {
    "3M":  {"days":   95, "yf": "3mo",  "label": "3 meses"},
    "6M":  {"days":  190, "yf": "6mo",  "label": "6 meses"},
    "12M": {"days":  370, "yf": "1y",   "label": "12 meses"},
    "24M": {"days":  740, "yf": "2y",   "label": "24 meses"},
    "YTD": {"days": None, "yf": "ytd",  "label": "YTD (no ano)"},
    "5Y":  {"days": 1850, "yf": "5y",   "label": "5 anos"},
}

# Paleta para até 10 séries (cores distintas, contraste com fundo escuro)
_PERF_PALETTE = [
    "#4A9EFF", "#00C896", "#F6C90E", "#9B59B6", "#FC5C7D",
    "#FF6B35", "#48BB78", "#ED8936", "#38B2AC", "#E94560",
]


def _select_top_n_acoes(posicoes: list, n: int = 10) -> list[dict]:
    """Top N posições por valor_mercado, filtrando apenas ações negociáveis
    com cotação diária (classe stock/ETF, exclui FII / RF / Tesouro / Cripto).

    Retorna lista [{ticker, nome, valor_mercado, classe}, ...] já ordenada.
    """
    candidatas = []
    for p in posicoes:
        classe = str(p.get("classe") or "").lower()
        ticker = str(p.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        # Filtra só ações negociáveis (exclui Tesouro, CDB, fundos RF, etc.)
        if any(k in classe for k in ("tesouro", "renda fixa", "fundo rf", "cripto")):
            continue
        candidatas.append({
            "ticker":        ticker,
            "nome":          p.get("nome") or ticker,
            "valor_mercado": float(p.get("valor_mercado") or 0),
            "classe":        p.get("classe"),
            "pais":          p.get("pais") or "BR",
        })
    candidatas.sort(key=lambda x: x["valor_mercado"], reverse=True)
    return candidatas[:n]


@st.cache_data(ttl=3600, show_spinner=False)
def _load_performance_history(
    symbol_map: tuple[tuple[str, str], ...],
    period_key: str = "12M",
) -> "pd.DataFrame":
    """Carrega preços de fechamento históricos (Close) para os tickers.

    `symbol_map` é tupla de (ticker_local, yf_symbol). Tenta yfinance
    primeiro; em caso de falha, usa asset_quotes do banco.
    Retorna DataFrame com index=DatetimeIndex, columns=ticker_local, values=preço.
    """
    if not symbol_map:
        return pd.DataFrame()

    period_cfg = _PERFORMANCE_PERIODS.get(period_key, _PERFORMANCE_PERIODS["12M"])
    tickers_local = [tk for tk, _ in symbol_map]
    symbol_to_ticker = {sym: tk for tk, sym in symbol_map}

    # 1) Tenta yfinance (1 chamada batch)
    try:
        import yfinance as yf
        raw = yf.download(
            [sym for _, sym in symbol_map],
            period=period_cfg["yf"],
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        if raw is not None and not raw.empty:
            if isinstance(raw.columns, pd.MultiIndex):
                level0 = raw.columns.get_level_values(0)
                if "Close" in level0:
                    close = raw["Close"]
                elif "Adj Close" in level0:
                    close = raw["Adj Close"]
                else:
                    close = raw.xs(level0[0], axis=1, level=0)
            else:
                close = raw[["Close"]] if "Close" in raw.columns else raw
                if len(symbol_map) == 1:
                    close.columns = [symbol_map[0][1]]
            close = close.rename(columns=symbol_to_ticker)
            close = close.dropna(axis=1, how="all").sort_index()
            if not close.empty and close.shape[1] >= 1:
                return close
    except Exception:
        pass  # cai no fallback

    # 2) Fallback: asset_quotes
    try:
        from sqlalchemy import bindparam, text
        from core.config import settings
        from core.database import get_engine
        engine = get_engine()
        if engine is None or not tickers_local:
            return pd.DataFrame()
        days = period_cfg["days"] or 365  # YTD aproxima 1y
        with engine.connect() as conn:
            q = text("""
                SELECT a.ticker, aq.timestamp::date AS data, aq.close AS preco
                FROM asset_quotes aq
                JOIN assets a ON a.id = aq.asset_id
                WHERE a.ticker IN :tickers
                  AND aq.close IS NOT NULL
                  AND aq.timestamp >= CURRENT_DATE - (:days || ' days')::INTERVAL
                ORDER BY aq.timestamp
            """).bindparams(bindparam("tickers", expanding=True))
            rows = conn.execute(q, {"tickers": tickers_local, "days": str(days)}).mappings().all()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["data"] = pd.to_datetime(df["data"], errors="coerce")
        df["preco"] = pd.to_numeric(df["preco"], errors="coerce")
        df = df.dropna(subset=["data", "ticker", "preco"])
        if df.empty:
            return pd.DataFrame()
        pivot = (
            df.sort_values("data")
            .drop_duplicates(["data", "ticker"], keep="last")
            .pivot(index="data", columns="ticker", values="preco")
            .sort_index()
        )
        return pivot
    except Exception:
        return pd.DataFrame()


def _fig_performance_historico(
    prices: "pd.DataFrame",
    name_map: dict[str, str],
    period_label: str,
) -> go.Figure:
    """Plota retorno % normalizado: primeira observação = 0%."""
    if prices.empty or prices.shape[1] == 0:
        return go.Figure()

    # Para YTD, filtra do início do ano corrente
    if period_label.startswith("YTD"):
        from datetime import date as _d
        start = pd.Timestamp(_d.today().year, 1, 1)
        prices = prices.loc[prices.index >= start]
        if prices.empty:
            return go.Figure()

    # Normaliza: cada coluna vira (preço / primeiro_preço_válido - 1) * 100
    norm = prices.copy()
    for col in norm.columns:
        first_valid_idx = norm[col].first_valid_index()
        if first_valid_idx is None:
            continue
        base = norm.loc[first_valid_idx, col]
        if base and base != 0:
            norm[col] = (norm[col] / base - 1) * 100

    # Ordena tickers pelo retorno final (maior primeiro) — colore Top primeiro
    final_returns = {
        col: float(norm[col].dropna().iloc[-1])
        for col in norm.columns
        if not norm[col].dropna().empty
    }
    ordered_tickers = sorted(final_returns, key=final_returns.get, reverse=True)

    fig = go.Figure()
    for i, ticker in enumerate(ordered_tickers):
        serie = norm[ticker].dropna()
        if serie.empty:
            continue
        cor = _PERF_PALETTE[i % len(_PERF_PALETTE)]
        nome_curto = (name_map.get(ticker) or ticker)[:30]
        final_ret = final_returns[ticker]
        signo = "+" if final_ret >= 0 else ""
        legend_lbl = f"{ticker} ({signo}{final_ret:.1f}%)"
        fig.add_trace(go.Scatter(
            x=serie.index,
            y=serie.values,
            mode="lines",
            name=legend_lbl,
            line={"color": cor, "width": 1.8},
            customdata=[[nome_curto, ticker]] * len(serie),
            hovertemplate=(
                "<b>%{customdata[1]}</b> · %{customdata[0]}<br>"
                "%{x|%d/%m/%Y}: %{y:+.2f}%<extra></extra>"
            ),
        ))

    # Linha zero (baseline) para referência visual
    fig.add_hline(
        y=0, line_dash="dot", line_color="#4A5568", line_width=1, opacity=0.7,
    )

    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        legend={
            "orientation": "v",
            "x": 1.02, "y": 1.0,
            "font": {"size": 10},
            "bgcolor": "rgba(0,0,0,0)",
        },
        margin={"t": 10, "b": 10, "l": 0, "r": 0},
        height=420,
        xaxis={
            "showgrid": True, "gridcolor": "#1E2533",
            "title": None,
        },
        yaxis={
            "showgrid": True, "gridcolor": "#1E2533",
            "ticksuffix": "%", "title": "Retorno desde início do período",
            "tickfont": {"size": 10},
        },
        hovermode="x unified",
    )
    return fig


def _tab_dashboard(carteira: dict, proventos: dict, cashflow: list, evolucao: dict) -> None:
    posicoes   = carteira.get("posicoes", [])
    por_classe = carteira.get("por_classe", [])

    n_efetivo   = _calc_n_efetivo(posicoes)
    br, ext     = _split_br_ext(posicoes)
    total       = carteira["total_mercado"]
    invest      = carteira["total_investido"]
    resultado   = round(total - invest, 2)
    rentab_pct  = carteira["rentabilidade_total_pct"]
    pct_br      = round(br  / total * 100, 2) if total > 0 else 0
    pct_ext     = round(ext / total * 100, 2) if total > 0 else 0
    renda_12m   = proventos.get("total_12m", proventos.get("total_ano", 0.0))
    dy          = round(renda_12m / total * 100, 2) if total > 0 else 0

    cor_res = _COR_POSITIVO if resultado >= 0 else _COR_NEGATIVO

    # ── Linha 1: 4 KPI cards ─────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4, gap="small")
    with c1:
        st.markdown(_kpi(
            "Patrimônio Total", fmt_moeda(total),
            f"{carteira['num_ativos']} posições ativas",
            "#E2E8F0",
        ), unsafe_allow_html=True)
    with c2:
        st.markdown(_kpi(
            "BR Brasil", fmt_moeda(br),
            f"{pct_br:.2f}% do patrimônio",
            _COR_POSITIVO,
        ), unsafe_allow_html=True)
    with c3:
        st.markdown(_kpi(
            "Exterior", fmt_moeda(ext),
            f"{pct_ext:.2f}% do patrimônio",
            _COR_INFO,
        ), unsafe_allow_html=True)
    with c4:
        st.markdown(_kpi(
            "Resultado Acumulado",
            f"{'+' if resultado >= 0 else ''}{fmt_moeda(resultado)}",
            f"{'+' if rentab_pct >= 0 else ''}{rentab_pct:.2f}%",
            cor_res,
        ), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Linha 2: 3 KPI cards ─────────────────────────────────────────────────
    c5, c6, c7 = st.columns(3, gap="small")
    with c5:
        st.markdown(_kpi(
            "Renda Recebida (12M)", fmt_moeda(renda_12m),
            "Proventos dos últimos 12 meses",
            _COR_ALERTA,
        ), unsafe_allow_html=True)
    with c6:
        st.markdown(_kpi(
            "Dividend Yield (12M)",
            fmt_percentual(dy, sinal=False),
            "Renda 12m / Patrimônio atual",
            _COR_ROXO,
        ), unsafe_allow_html=True)
    with c7:
        st.markdown(_kpi(
            "Nº de Ativos",
            str(carteira["num_ativos"]),
            f"N efetivo: {n_efetivo}",
            "#E2E8F0",
        ), unsafe_allow_html=True)

    # ── Visão Geral ───────────────────────────────────────────────────────────
    _secao_titulo_orig("🗂️", "Visão Geral")

    col_estado, col_evolucao = st.columns([1, 1], gap="medium")

    with col_estado:
        st.markdown(
            '<div style="font-size:0.83rem;font-weight:700;color:#E2E8F0;'
            'margin-bottom:10px;">🌐 Estado da Carteira</div>',
            unsafe_allow_html=True,
        )
        analise = _estado_carteira(carteira, n_efetivo, pct_ext)
        for item in analise:
            st.markdown(_estado_item(item["tipo"], item["titulo"], item["texto"]),
                        unsafe_allow_html=True)

    with col_evolucao:
        st.markdown(
            '<div style="font-size:0.83rem;font-weight:700;color:#E2E8F0;'
            'margin-bottom:10px;">📐 Evolução Patrimonial</div>',
            unsafe_allow_html=True,
        )
        snapshots = evolucao.get("snapshots", []) if isinstance(evolucao, dict) else []
        if snapshots:
            st.plotly_chart(_fig_evolucao_patrimonial(snapshots),
                            use_container_width=True,
                            config={"displayModeBar": False},
                            key="dash_evolucao_snapshot")
            st.caption("Evolucao baseada nos snapshots importados do App2.")
        elif cashflow:
            st.plotly_chart(_fig_evolucao(cashflow, total),
                            use_container_width=True,
                            config={"displayModeBar": False},
                            key="dash_evolucao")
            st.caption("Evolução estimada com base no fluxo de caixa mensal acumulado.")
        else:
            st.caption("Sem dados de fluxo de caixa.")

    # ── Análise de Risco ──────────────────────────────────────────────────────
    _secao_titulo_orig("🎯", "Análise de Risco")

    col_radar, col_acoes = st.columns([1, 1], gap="medium")

    with col_radar:
        st.markdown(
            '<div style="font-size:0.83rem;font-weight:700;color:#E2E8F0;'
            'margin-bottom:8px;">🎯 Radar de Risco</div>',
            unsafe_allow_html=True,
        )
        st.plotly_chart(_fig_radar(carteira, n_efetivo, pct_ext),
                        use_container_width=True,
                        config={"displayModeBar": False},
                        key="dash_radar")
        st.caption("0 = mínimo · 10 = máximo para cada dimensão")

    with col_acoes:
        st.markdown(
            '<div style="font-size:0.83rem;font-weight:700;color:#E2E8F0;'
            'margin-bottom:8px;">✅ Ações Sugeridas</div>',
            unsafe_allow_html=True,
        )
        sugestoes = _acoes_sugeridas(carteira, n_efetivo, dy)
        for s in sugestoes:
            st.markdown(
                f'<div style="border-left:3px solid {s["cor"]};'
                f'padding:10px 12px;margin-bottom:10px;'
                f'background:rgba(255,255,255,0.02);border-radius:0 6px 6px 0;">'
                f'<div style="font-size:0.78rem;font-weight:700;color:{s["cor"]};'
                f'margin-bottom:3px;">{s["tag"]} {s["titulo"]}</div>'
                f'<div style="font-size:0.78rem;color:#9CA3AF;">{s["texto"]}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # ── Distribuição do Portfólio ─────────────────────────────────────────────
    _secao_titulo_orig(
        "🟠", "Distribuição do Portfólio",
        "Por tipo de investimento. Valores de mercado atuais.",
    )

    if por_classe:
        col_donut, col_barras = st.columns([1, 1], gap="medium")
        with col_donut:
            st.markdown(
                '<div style="font-size:0.83rem;color:#9CA3AF;'
                'margin-bottom:8px;">Por tipo de investimento</div>',
                unsafe_allow_html=True,
            )
            st.plotly_chart(_fig_donut_classes(por_classe),
                            use_container_width=True,
                            config={"displayModeBar": False},
                            key="dash_donut")
        with col_barras:
            st.markdown(
                '<div style="font-size:0.83rem;color:#9CA3AF;'
                'margin-bottom:8px;">Valores de mercado por tipo</div>',
                unsafe_allow_html=True,
            )
            st.plotly_chart(_fig_barras_classes(por_classe),
                            use_container_width=True,
                            config={"displayModeBar": False},
                            key="dash_barras_classes")
    else:
        st.caption("Sem dados de alocação por classe.")

    # ── Desempenho Histórico (Top 10 Ações) ───────────────────────────────────
    _secao_titulo_orig(
        "📈", "Desempenho Histórico das Ações",
        "Retorno % das 10 maiores posições, normalizado pelo primeiro dia do período",
    )

    top10 = _select_top_n_acoes(posicoes, n=10)

    if not top10:
        st.caption(
            "Sem ações negociáveis na carteira (FII / Tesouro / RF excluídos). "
            "Importe a Negociação B3 ou Relatório XP em Configurações."
        )
    else:
        col_periodo, col_info = st.columns([1, 4])
        with col_periodo:
            periodo_key = st.selectbox(
                "Período",
                list(_PERFORMANCE_PERIODS.keys()),
                index=2,  # 12M default
                key="dash_perf_periodo",
                label_visibility="collapsed",
                format_func=lambda k: _PERFORMANCE_PERIODS[k]["label"],
            )
        with col_info:
            tickers_str = ", ".join(p["ticker"] for p in top10)
            st.caption(
                f"Top {len(top10)} ações por valor de mercado: {tickers_str}"
            )

        # Monta symbol_map: ticker local → símbolo yfinance
        # (BR: APPEND .SA, normaliza fracionário; Exterior: ticker puro)
        symbol_map: list[tuple[str, str]] = []
        for p in top10:
            t = p["ticker"]
            pais = str(p.get("pais") or "BR").upper()
            # Remove sufixo F de fracionário (BBAS3F → BBAS3)
            base = t[:-1] if t.endswith("F") and len(t) > 4 else t
            if pais in ("", "BR"):
                symbol_map.append((t, f"{base}.SA"))
            else:
                symbol_map.append((t, base))
        name_map = {p["ticker"]: p["nome"] for p in top10}

        with st.spinner("Carregando cotações históricas..."):
            prices = _load_performance_history(
                tuple(symbol_map), period_key=periodo_key,
            )

        if prices is None or prices.empty:
            st.warning(
                "Sem cotações disponíveis pra estes tickers no período. "
                "Verifique conexão com yfinance ou popule a tabela `asset_quotes`."
            )
        else:
            fig_perf = _fig_performance_historico(
                prices, name_map, _PERFORMANCE_PERIODS[periodo_key]["label"],
            )
            st.plotly_chart(
                fig_perf,
                use_container_width=True,
                config={"displayModeBar": False},
                key=f"dash_perf_{periodo_key}",
            )
            # Sumário compacto: ticker que mais subiu/caiu
            try:
                returns_summary = {}
                for col in prices.columns:
                    s = prices[col].dropna()
                    if len(s) >= 2:
                        returns_summary[col] = (s.iloc[-1] / s.iloc[0] - 1) * 100
                if returns_summary:
                    top_up = max(returns_summary, key=returns_summary.get)
                    top_dn = min(returns_summary, key=returns_summary.get)
                    st.caption(
                        f"🏆 Melhor: **{top_up}** ({returns_summary[top_up]:+.2f}%) · "
                        f"📉 Pior: **{top_dn}** ({returns_summary[top_dn]:+.2f}%)"
                    )
            except Exception:
                pass

    # ── Cenário Macroeconômico ─────────────────────────────────────────────────
    _secao_titulo_orig(
        "🌐", "Cenário Macroeconômico",
        "Dados atualizados a cada 30 minutos",
    )

    macro = _get_macro_dados()
    cm1, cm2, cm3, cm4, cm5, cm6, cm7 = st.columns(7, gap="small")
    with cm1:
        st.markdown(_kpi_macro("SELIC", f"{macro['selic']:.2f}%",
                               "Meta SELIC a.a.", _COR_NEGATIVO),
                    unsafe_allow_html=True)
    with cm2:
        st.markdown(_kpi_macro("IPCA 12M", f"{macro['ipca_12m']:.2f}%",
                               "Acumulado 12 meses", _COR_ALERTA),
                    unsafe_allow_html=True)
    with cm3:
        st.markdown(_kpi_macro("CDI 12M", f"{macro['cdi_12m']:.2f}%",
                               "Taxa CDI anual", _COR_ALERTA),
                    unsafe_allow_html=True)
    with cm4:
        st.markdown(_kpi_macro("USD / BRL", f"R$ {macro['usdbrl']:.4f}",
                               "Câmbio atual", _COR_INFO),
                    unsafe_allow_html=True)
    with cm5:
        ibov_k = macro["ibovespa"] / 1000
        st.markdown(_kpi_macro("IBOVESPA", f"{ibov_k:,.1f}k",
                               "Índice Bovespa (pts)", _COR_POSITIVO),
                    unsafe_allow_html=True)
    with cm6:
        sp_k = macro["sp500"] / 1000
        st.markdown(_kpi_macro("S&P 500", f"{sp_k:,.1f}k",
                               "Índice S&P 500 (pts)", _COR_POSITIVO),
                    unsafe_allow_html=True)
    with cm7:
        ifix_k = macro["ifix"] / 1000
        st.markdown(_kpi_macro("IFIX", f"{ifix_k:,.3f}k",
                               "Índice de FIIs", _COR_ROXO),
                    unsafe_allow_html=True)

    st.caption(
        "Fontes: BCB API (SELIC, IPCA) · Yahoo Finance (câmbio, bolsas). "
        "IFIX exibe valor de referência estático."
    )

    # ── Dependências Macro do Portfólio ────────────────────────────────────────
    _secao_titulo_orig(
        "📐", "Dependências Macro do Portfólio",
        "Exposição estimada por fator macroeconômico — baseado na alocação por classe",
    )

    deps = _calc_dependencias_macro(por_classe)
    if deps:
        fator_max = max(deps, key=lambda d: d["exposicao"])
        if fator_max["exposicao"] >= 60:
            st.warning(
                f"⚠️ Alta dependência em **{fator_max['fator']}** "
                f"({fator_max['exposicao']:.1f}%) — considere diversificar para reduzir concentração.",
            )

        col_chart, col_leg = st.columns([2, 1], gap="medium")
        with col_chart:
            st.plotly_chart(_fig_dependencias_macro(deps),
                            use_container_width=True,
                            config={"displayModeBar": False},
                            key="dash_macro_deps")
        with col_leg:
            st.markdown(
                '<div style="font-size:0.78rem;color:#718096;padding-top:16px;">'
                '<b style="color:#CBD5E0;">Como interpretar</b><br><br>'
                'Cada barra mostra quanto o portfólio pode ser afetado por um fator '
                'macroeconômico, ponderado pela composição por classe de ativo.<br><br>'
                f'<span style="color:{_COR_NEGATIVO};">■</span> ≥ 70% — alta exposição<br>'
                f'<span style="color:{_COR_ALERTA};">■</span> 50–70% — moderada<br>'
                f'<span style="color:{_COR_INFO};">■</span> &lt; 50% — controlada'
                '</div>',
                unsafe_allow_html=True,
            )
        st.caption(
            "Exposição estimada por coeficientes fixos por classe. "
            "Valores indicativos — não constituem recomendação de investimento."
        )
    else:
        st.caption("Sem dados de alocação para calcular dependências macro.")


    df_macro_ativos = _calc_dependencias_macro_ativos(posicoes)
    if not df_macro_ativos.empty:
        st.markdown("<br>", unsafe_allow_html=True)
        _secao_titulo_orig(
            "\U0001F9ED", "Ativos expostos aos fatores macro",
            "Mostra quais posições mais contribuem para cada dependência macroeconômica",
        )

        fator_padrao = 0
        if deps:
            fator_nome = max(deps, key=lambda d: d["exposicao"])["fator"]
            if fator_nome in _MACRO_FATORES:
                fator_padrao = _MACRO_FATORES.index(fator_nome)

        fator_sel = st.selectbox(
            "Fator macro",
            _MACRO_FATORES,
            index=fator_padrao,
            key="dash_macro_fator_ativo",
        )

        col_exp_chart, col_exp_table = st.columns([2, 1], gap="medium")
        with col_exp_chart:
            st.plotly_chart(
                _fig_macro_ativos(df_macro_ativos, fator_sel),
                use_container_width=True,
                config={"displayModeBar": False},
                key="dash_macro_ativos",
            )
        with col_exp_table:
            df_rank = (
                df_macro_ativos[["Ativo", "Ticker", "Classe", "Peso (%)", fator_sel, "Valor"]]
                .sort_values(fator_sel, ascending=False)
                .head(12)
                .rename(columns={fator_sel: "Contrib. (p.p.)"})
            )
            df_rank = df_rank.copy()
            df_rank["Valor"] = df_rank["Valor"].apply(fmt_moeda)
            st.dataframe(
                df_rank,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Peso (%)": st.column_config.NumberColumn(format="%.2f%%"),
                    "Contrib. (p.p.)": st.column_config.NumberColumn(format="%.2f"),
                    "Valor": st.column_config.TextColumn(),
                },
            )
        st.caption(
            "A contribuição usa o peso real do ativo multiplicado pelo coeficiente macro "
            "da sua classe. Assim, o usuário enxerga quais ativos puxam cada fator."
        )

    st.markdown("<br>", unsafe_allow_html=True)
    _secao_titulo_orig(
        "\U0001F9EC", "Correlação entre ativos",
        "Retornos dos ativos negociáveis para medir a força da diversificação",
    )
    corr_data = _build_corr_data(posicoes)
    corr = corr_data.get("corr", pd.DataFrame())
    if not corr.empty:
        pares = _corr_pairs(corr)
        if not pares.empty:
            media_abs = pares["|Correlacao|"].mean()
            max_pair = pares.iloc[0]
            baixa_pct = (pares["|Correlacao|"] < 0.40).mean() * 100
            if media_abs < 0.30:
                _corr_label, _corr_cor = "Boa", _COR_POSITIVO
            elif media_abs < 0.50:
                _corr_label, _corr_cor = "Razoável", _COR_ALERTA
            else:
                _corr_label, _corr_cor = "Ruim", _COR_NEGATIVO
            ck1, ck2, ck3 = st.columns(3, gap="small")
            with ck1:
                st.markdown(_kpi_macro(
                    "Correlação média",
                    f"{media_abs:.2f}",
                    f"Diversificação {_corr_label.lower()} · média absoluta dos pares", _corr_cor,
                ), unsafe_allow_html=True)
            with ck2:
                cor_max = _COR_NEGATIVO if max_pair["|Correlacao|"] >= 0.70 else _COR_ALERTA
                st.markdown(_kpi_macro(
                    "Maior par",
                    f"{max_pair['|Correlacao|']:.2f}",
                    max_pair["Par"], cor_max,
                ), unsafe_allow_html=True)
            with ck3:
                cor_baixa = _COR_POSITIVO if baixa_pct >= 60 else _COR_ALERTA
                st.markdown(_kpi_macro(
                    "Pares baixa corr.",
                    f"{baixa_pct:.0f}%",
                    "|corr| abaixo de 0.40", cor_baixa,
                ), unsafe_allow_html=True)

        col_heat, col_pairs = st.columns([2, 1], gap="medium")
        with col_heat:
            st.plotly_chart(
                _fig_corr_heatmap(corr),
                use_container_width=True,
                config={"displayModeBar": False},
                key="dash_corr_heatmap",
            )
        with col_pairs:
            if not pares.empty:
                st.markdown("**Pares mais correlacionados**")
                st.dataframe(
                    pares[["Par", "Correlacao", "Leitura"]].head(10),
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Correlacao": st.column_config.NumberColumn(format="%.2f"),
                    },
                )
                st.markdown("**Pares com menor correlação**")
                st.dataframe(
                    pares.sort_values("|Correlacao|")[["Par", "Correlacao", "Leitura"]].head(8),
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Correlacao": st.column_config.NumberColumn(format="%.2f"),
                    },
                )
        pulados = corr_data.get("skipped", [])
        if pulados:
            st.caption(
                "Fora da matriz: " + ", ".join(pulados[:12]) +
                ("..." if len(pulados) > 12 else "") +
                ". Normalmente são Tesouro, renda fixa ou ativos sem série de preços comparável."
            )
        st.caption(
            "Correlação calculada com retornos dos preços disponíveis: yfinance quando instalado, "
            "asset_quotes diário como fallback e snapshots históricos quando necessário. "
            "Quanto menor a correlação absoluta entre os ativos, maior tende a ser a diversificação estatística."
        )
    else:
        st.caption(
            "Não há séries suficientes para montar a matriz de correlação. "
            "Ativos de renda fixa e Tesouro entram na diversificação por classe, mas não possuem preço diário comparável."
        )

def _tab_historico(cashflow: list, proventos: dict, evolucao: dict) -> None:
    st.markdown("<br>", unsafe_allow_html=True)

    snapshots = evolucao.get("snapshots", [])

    # ── Evolução Patrimonial ──────────────────────────────────────────────────
    _secao_titulo_orig(
        "📈", "Evolução Patrimonial",
        "Valor de Mercado e total com Dividendos acumulados — histórico completo",
    )
    if snapshots:
        # KPI summary row
        ck1, ck2, ck3 = st.columns(3, gap="small")
        with ck1:
            st.markdown(_kpi(
                "Valor de Mercado Atual", fmt_moeda(evolucao["total_mercado"]),
                "Carteira consolidada atual", _COR_POSITIVO,
            ), unsafe_allow_html=True)
        with ck2:
            st.markdown(_kpi(
                "Total Com Dividendos",
                fmt_moeda(evolucao["total_mercado"] + evolucao["total_dividendos"]),
                "Mercado + proventos históricos acumulados", _COR_ALERTA,
            ), unsafe_allow_html=True)
        with ck3:
            st.markdown(_kpi(
                "Total Investido (custo)",
                fmt_moeda(evolucao["total_investido"]),
                "Custo consolidado da carteira atual", _COR_NEUTRO,
            ), unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)
        st.plotly_chart(_fig_evolucao_patrimonial(snapshots),
                        use_container_width=True,
                        config={"displayModeBar": False},
                        key="hist_evolucao_patrimonial")
        st.caption(
            "Snapshots XP (relatórios mensais). "
            "Ponto atual inclui posições internacionais (Nomad) consolidadas. "
            "Com Dividendos = Mercado + proventos históricos acumulados."
        )
    else:
        st.info("Sem dados históricos de transações para exibir.", icon="📈")

    st.markdown("<br>", unsafe_allow_html=True)

    # Proventos — seleção Mensal / Anual
    hist_prov = proventos.get("historico_mensal", [])

    col_prov_title, col_prov_vis = st.columns([3, 1])
    with col_prov_title:
        _secao_titulo_orig("💵", "Proventos", "Dividendos e rendimentos recebidos")
    with col_prov_vis:
        st.markdown("<br>", unsafe_allow_html=True)
        visao_prov = st.selectbox(
            "Visualização",
            ["Mensal", "Anual"],
            key="inv_hist_prov_visao",
            label_visibility="collapsed",
        )

    if hist_prov:
        if visao_prov == "Mensal":
            labels_p = [h["label"] for h in hist_prov]
            vals_p   = [h["total"] for h in hist_prov]
        else:
            # Agrega por ano
            agg: dict[int, float] = {}
            for h in hist_prov:
                agg[h["ano"]] = agg.get(h["ano"], 0.0) + h["total"]
            labels_p = [str(a) for a in sorted(agg)]
            vals_p   = [round(agg[a], 2) for a in sorted(agg)]

        fig_prov = go.Figure(go.Bar(
            x=labels_p, y=vals_p,
            marker_color=_COR_ALERTA, opacity=0.85,
            text=[f"R$ {v:,.0f}".replace(",", ".") for v in vals_p],
            textposition="outside",
            textfont={"size": 10, "color": "#E2E8F0"},
            hovertemplate="<b>%{x}</b><br>R$ %{y:,.2f}<extra></extra>",
        ))
        fig_prov.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font_color=_COR_NEUTRO,
            margin={"t": 30, "b": 0, "l": 0, "r": 0}, height=260,
            xaxis={"showgrid": False},
            yaxis={"showgrid": True, "gridcolor": "#1E2533",
                   "tickformat": ",.0f", "tickprefix": "R$ "},
            showlegend=False,
        )
        st.plotly_chart(fig_prov, use_container_width=True,
                        config={"displayModeBar": False},
                        key="hist_proventos_bar")

        # Totalizador por ano abaixo do gráfico (sempre visível)
        if visao_prov == "Anual":
            total_exibido = sum(vals_p)
            st.markdown(
                f'<div style="font-size:0.78rem;color:{_COR_ALERTA};'
                f'font-weight:700;text-align:right;margin-top:-8px;">'
                f'Total no período: R$ {total_exibido:,.2f}'.replace(",", "X").replace(".", ",").replace("X", ".") +
                f'</div>',
                unsafe_allow_html=True,
            )
        else:
            total_exibido = sum(vals_p)
            st.caption(
                f"Total no período: **R$ {total_exibido:,.2f}**".replace(",", "X").replace(".", ",").replace("X", ".")
                + f" · {len(hist_prov)} meses"
            )
    else:
        st.caption("Sem histórico de proventos.")

    # ── Aportes por Período ───────────────────────────────────────────────────
    fluxo_mensal = evolucao.get("fluxo_mensal", [])
    if fluxo_mensal:
        st.markdown("<br>", unsafe_allow_html=True)

        col_ap_t, col_ap_v = st.columns([3, 1])
        with col_ap_t:
            _secao_titulo_orig(
                "💰", "Aportes por Período",
                "Valor líquido investido por mês (últimos 12) ou por ano (histórico)",
            )
        with col_ap_v:
            st.markdown("<br>", unsafe_allow_html=True)
            visao_ap = st.selectbox(
                "Período",
                ["Mensal", "Anual"],
                key="hist_aportes_visao",
                label_visibility="collapsed",
            )

        st.plotly_chart(
            _fig_aportes(fluxo_mensal, visao_ap),
            use_container_width=True,
            config={"displayModeBar": False},
            key="hist_aportes",
        )

        # Totalizador
        if visao_ap == "Mensal":
            dados_vis = fluxo_mensal[-12:]
            total_vis = sum(d["aporte"] for d in dados_vis)
            st.caption(
                f"Total aportado nos últimos 12 meses: **{fmt_moeda(total_vis)}** "
                f"· Valores negativos indicam meses com resgates líquidos."
            )
        else:
            total_vis = sum(d["aporte"] for d in fluxo_mensal)
            anos = sorted({d["ano"] for d in fluxo_mensal})
            st.caption(
                f"Total aportado ({anos[0]}–{anos[-1]}): **{fmt_moeda(total_vis)}** "
                f"· {len(anos)} ano(s) de histórico."
            )


def _header_classe(cls_info: dict, renda_cls: float) -> None:
    cor  = cls_info.get("cor", "#718096")
    n    = cls_info["num_ativos"]
    vm   = cls_info["valor_mercado"]
    ti   = cls_info["total_investido"]
    pct  = cls_info["pct_carteira"]
    resultado = vm + renda_cls - ti
    rsc  = resultado / ti * 100 if ti > 0 else 0.0
    cor_rsc = _COR_POSITIVO if rsc >= 0 else _COR_NEGATIVO
    seta_rsc = "▲" if rsc >= 0 else "▼"
    st.markdown(
        f'<div style="border-left:4px solid {cor};padding:8px 16px;'
        f'background:rgba(255,255,255,0.02);border-radius:0 8px 8px 0;'
        f'margin:24px 0 14px;display:flex;align-items:center;gap:14px;flex-wrap:wrap;">'
        f'<span style="font-size:1.05rem;font-weight:800;color:#E2E8F0;">{cls_info["nome"]}</span>'
        f'<span style="font-size:0.75rem;color:#718096;">'
        f'{n} ativo{"s" if n != 1 else ""} · {fmt_moeda(vm)} · {pct:.1f}% da carteira · '
        f'<span style="color:{cor_rsc};">Ret. s/ custo {seta_rsc} {abs(rsc):.2f}%</span>'
        f'</span>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _card_ativo(pos: dict, renda: float, logo_url: str = "") -> str:
    cor      = pos["cor"]
    rentab   = pos["rentab_pct"]
    cor_r    = _COR_POSITIVO if rentab >= 0 else _COR_NEGATIVO
    seta_r   = "▲" if rentab >= 0 else "▼"
    cor_vm   = _COR_POSITIVO if pos["valor_mercado"] >= pos["total_investido"] else _COR_NEGATIVO
    dot_cor  = _COR_POSITIVO if pos["preco_atual"] >= pos["preco_medio"] else _COR_NEGATIVO
    ti       = pos["total_investido"]
    custo_fonte = pos.get("custo_fonte", "snapshot")
    custo_ausente = custo_fonte == "mercado_fallback"
    rsc      = (pos["valor_mercado"] + renda - ti) / ti * 100 if ti > 0 and not custo_ausente else 0.0
    cor_rsc  = _COR_POSITIVO if rsc >= 0 else _COR_NEGATIVO

    dot = (f'<span style="display:inline-block;width:7px;height:7px;border-radius:50%;'
           f'background:{dot_cor};margin-left:3px;vertical-align:middle;"></span>')

    custo_label = "Custo estimado" if custo_fonte == "preco_medio_estimado" else "Custo investido"
    custo_val = "Não informado" if custo_ausente else fmt_moeda(ti)
    resultado_val = "—" if custo_ausente else f"{seta_r} {abs(rentab):.2f}%"
    retorno_val = "—" if custo_ausente else f"{rsc:.2f}%"
    resultado_cor = "#718096" if custo_ausente else cor_r
    retorno_cor = "#718096" if custo_ausente else cor_rsc
    mercado_cor = "#CBD5E0" if custo_ausente else cor_vm

    metricas = [
        ("Peso na carteira",  f"{pos['pct_carteira']:.2f}%",       "#CBD5E0"),
        ("Quantidade",        f"{pos['quantidade']:,.0f}".replace(",", "."), "#CBD5E0"),
        (f"Preço atual {dot}", fmt_moeda(pos["preco_atual"]),        "#CBD5E0"),
        ("Preço médio",       fmt_moeda(pos["preco_medio"]),         "#CBD5E0"),
        (custo_label,          custo_val,                            "#CBD5E0"),
        ("Valor de mercado",  fmt_moeda(pos["valor_mercado"]),       mercado_cor),
        ("Resultado total",   resultado_val,                         resultado_cor),
        ("Renda recebida",    fmt_moeda(renda),                      _COR_ALERTA),
        ("Retorno s/ custo",  retorno_val,                           retorno_cor),
    ]
    rows_html = "".join(
        f'<div style="display:flex;justify-content:space-between;padding:4px 0;'
        f'border-bottom:1px solid #1A1F2E;font-size:0.76rem;">'
        f'<span style="color:#718096;">{lbl}</span>'
        f'<span style="color:{cv};font-weight:600;">{val}</span>'
        f'</div>'
        for lbl, val, cv in metricas
    )
    initials   = pos["ticker"][:5]
    nome_curto = pos["nome"][:22] if len(pos["nome"]) > 22 else pos["nome"]
    # Avatar: iniciais como fundo; logo sobreposta via position:absolute.
    # onerror oculta o <img> se o CDN retornar 404 → iniciais ficam visíveis.
    img_tag = (
        f'<img src="{logo_url}" '
        f'style="position:absolute;top:0;left:0;width:40px;height:40px;'
        f'border-radius:8px;object-fit:contain;background:#1E2533;" '
        f'onerror="this.style.display=\'none\'" '
        f'alt="{pos["ticker"]}">'
    ) if logo_url else ""
    avatar_html = (
        f'<div style="width:40px;height:40px;border-radius:8px;position:relative;'
        f'flex-shrink:0;background:{cor};display:flex;align-items:center;'
        f'justify-content:center;font-size:0.60rem;font-weight:800;color:#fff;">'
        f'{initials}{img_tag}'
        f'</div>'
    )
    return (
        f'<div style="background:#12151E;border:1px solid #1E2533;border-radius:12px;'
        f'padding:16px;margin-bottom:6px;">'
        f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">'
        f'{avatar_html}'
        f'<div style="overflow:hidden;">'
        f'<div style="font-size:0.83rem;font-weight:800;color:#E2E8F0;'
        f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{nome_curto}</div>'
        f'<div style="font-size:0.68rem;color:#718096;">{pos["ticker"]}</div>'
        f'</div></div>'
        f'<span style="font-size:0.58rem;font-weight:700;padding:2px 7px;border-radius:4px;'
        f'background:{cor}33;color:{cor};text-transform:uppercase;letter-spacing:0.08em;'
        f'display:inline-block;margin-bottom:8px;">{pos["classe"]}</span>'
        f'<div>{rows_html}</div>'
        f'</div>'
    )


def _tab_carteira(carteira: dict, proventos: dict) -> None:
    from collections import defaultdict as _dd
    posicoes   = carteira.get("posicoes", [])
    por_classe = carteira.get("por_classe", [])

    # Renda recebida por ticker
    renda_por_ticker: dict[str, float] = {
        a["ticker"]: a["total"]
        for a in proventos.get("por_ativo", [])
    }
    # Renda total por classe (para header)
    renda_por_classe: dict[str, float] = _dd(float)
    for p in posicoes:
        renda_por_classe[p["classe"]] += renda_por_ticker.get(p["ticker"], 0.0)

    st.markdown("<br>", unsafe_allow_html=True)

    if not carteira["cotacoes_disponiveis"]:
        st.info(
            "**Cotações não disponíveis** — acesse **Configurações > Atualização de Dados** "
            "para baixar cotações via yfinance.",
            icon="📊",
        )

    # ── KPIs resumo ──────────────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4, gap="small")
    with c1:
        st.markdown(_kpi("Total Investido", fmt_moeda(carteira["total_investido"]),
                         "Custo histórico acumulado.", "#E2E8F0"),
                    unsafe_allow_html=True)
    with c2:
        st.markdown(_kpi("Valor de Mercado", fmt_moeda(carteira["total_mercado"]),
                         "Valor atual da carteira.", _COR_INFO),
                    unsafe_allow_html=True)
    with c3:
        rent = carteira["rentabilidade_total_pct"]
        st.markdown(_kpi("Rentabilidade Total",
                         fmt_percentual(rent),
                         "(Mercado − Custo) / Custo.",
                         _COR_POSITIVO if rent >= 0 else _COR_NEGATIVO),
                    unsafe_allow_html=True)
    with c4:
        st.markdown(_kpi("Ativos na Carteira",
                         str(carteira["num_ativos"]),
                         f"N efetivo: {_calc_n_efetivo(posicoes)}",
                         _COR_NEUTRO),
                    unsafe_allow_html=True)

    if not posicoes:
        st.info("Nenhuma posição encontrada. Execute o ETL de posições.", icon="💼")
        return

    # Busca logos em lote (cache 24h) — falha silenciosa
    tickers_tuple = tuple(p["ticker"] for p in posicoes)
    logos = _get_logos(tickers_tuple)

    # ── Cards agrupados por classe ────────────────────────────────────────────
    pos_por_classe: dict[str, list] = _dd(list)
    for p in posicoes:
        pos_por_classe[p["classe"]].append(p)

    for cls_info in por_classe:
        cls_nome   = cls_info["nome"]
        cls_pos    = pos_por_classe.get(cls_nome, [])
        if not cls_pos:
            continue
        renda_cls  = renda_por_classe.get(cls_nome, 0.0)
        _header_classe(cls_info, renda_cls)

        for i in range(0, len(cls_pos), 4):
            chunk = cls_pos[i:i + 4]
            cols  = st.columns(4, gap="small")
            for j, pos in enumerate(chunk):
                renda_p = renda_por_ticker.get(pos["ticker"], 0.0)
                with cols[j]:
                    st.markdown(_card_ativo(pos, renda_p, logos.get(pos["ticker"], "")), unsafe_allow_html=True)
            if i + 4 < len(cls_pos):
                st.markdown("<br>", unsafe_allow_html=True)


def _cls_to_type(classe: str) -> str:
    c = classe.lower()
    if "fii" in c or "fundo imob" in c: return "fii"
    if "ação" in c or "ações" in c or "acoes" in c or "acao" in c: return "stock"
    if "etf" in c: return "etf"
    if "bdr" in c: return "bdr"
    return "stock"


def _stock_card_html(pos: dict, fd: dict, price_info: dict,
                     alerts: list) -> str:
    T = _fund.THR
    ticker = pos["ticker"]
    name   = (pos.get("nome") or ticker)[:45]
    peso   = float(pos.get("pct_carteira", 0))
    custo  = float(pos.get("total_investido", 0))
    pm     = float(pos.get("preco_medio", 0))

    preco   = fd.get("cotacao") or price_info.get("price")
    chg     = price_info.get("change_pct", 0.0)
    hist    = price_info.get("hist")
    vm_mes  = _fund.var_mes(hist)
    vm_12   = _fund.var_12m(hist)

    pl = fd.get("pl");   pvp = fd.get("pvp")
    psr = fd.get("psr"); p_ebit = fd.get("p_ebit")
    ev_ebit = fd.get("ev_ebit"); ev_ebitda = fd.get("ev_ebitda")
    roe = fd.get("roe"); roic = fd.get("roic")
    ml = fd.get("marg_liq"); cresc_r = fd.get("cresc_rec_5a")
    div_p = fd.get("div_brut_patrim"); div_liq = fd.get("div_liquida")
    liq_c = fd.get("liq_corr"); patrim = fd.get("patrim_liq")
    receita = fd.get("receita_liq"); lucro = fd.get("lucro_liq")
    dy = fd.get("dy"); setor = fd.get("setor") or "—"
    subsetor = fd.get("subsetor") or ""

    chips = []
    if pvp and pvp < 1.0:           chips.append(_f_chip("P/VP < 1", "green"))
    if pl  and pl  < 10:            chips.append(_f_chip("P/L baixo", "green"))
    if roe and roe > 20:            chips.append(_f_chip("ROE alto ▲", "green"))
    if roic and roic > 15:          chips.append(_f_chip("ROIC alto ▲", "green"))
    if ml  is not None and ml < 0:  chips.append(_f_chip("Prejuízo", "red"))
    if div_p and div_p > T["stock_divida_alta"]: chips.append(_f_chip("Alavancagem ⚠", "red"))
    if peso > T["stock_conc_max"]:  chips.append(_f_chip("Concentrada", "yellow"))
    if any(a[0] == "red" for a in alerts): chips.append(_f_chip("Revisar", "red"))
    if not chips:                   chips.append(_f_chip("Monitorando", "blue"))

    price_str = f"R$ {_f_br(preco)}" if preco else "—"
    if chg > 0.1:    chg_cls = "fund-chg-pos"; chg_str = f"▲ {chg:.2f}% hoje"
    elif chg < -0.1: chg_cls = "fund-chg-neg"; chg_str = f"▼ {abs(chg):.2f}% hoje"
    else:            chg_cls = "fund-chg-neu"; chg_str = f"{chg:.2f}% hoje"

    R = _f_row; S = _f_sec
    rows  = S("Posição")
    rows += R("Peso na carteira", f"{peso:.1f}%")
    rows += R("Custo investido", _f_brs(custo, 0))
    if pm: rows += R("Preço médio", _f_brs(pm))

    rows += S("Valuation  (Fundamentus)")
    if pl:    rows += R("P/L", f"{pl:.1f}x",
                  "fund-val-warn" if pl > T["stock_pl_alto"] else
                  "fund-val-pos"  if pl < T["stock_pl_baixo"] else "fund-val")
    if pvp:   rows += R("P/VP", f"{pvp:.2f}x",
                  "fund-val-warn" if pvp > T["stock_pvp_alto"] else "fund-val")
    if psr:   rows += R("PSR", f"{psr:.2f}x")
    if p_ebit: rows += R("P/EBIT", f"{p_ebit:.2f}x")
    if ev_ebitda: rows += R("EV/EBITDA", f"{ev_ebitda:.2f}x")
    if ev_ebit:   rows += R("EV/EBIT", f"{ev_ebit:.2f}x")
    if dy:    rows += R("Dividend Yield", f"{dy:.2f}%",
                  "fund-val-warn" if dy > T["stock_dy_alto"] else "fund-val-pos")

    rows += S("Rentabilidade  (Fundamentus)")
    if roe:   rows += R("ROE", f"{roe:.1f}%", _f_color_pct(roe - T["stock_roe_baixo"]))
    if roic:  rows += R("ROIC", f"{roic:.1f}%", _f_color_pct(roic - T["stock_roic_baixo"]))
    if fd.get("marg_bruta") is not None: rows += R("Margem Bruta", f"{fd['marg_bruta']:.1f}%", _f_color_pct(fd["marg_bruta"]))
    if ml is not None: rows += R("Margem Líq.", f"{ml:.1f}%",
                  "fund-val-neg" if ml < 0 else ("fund-val-warn" if ml < 5 else "fund-val-pos"))
    if cresc_r is not None: rows += R("Cresc. Receita 5a", f"{cresc_r:+.1f}%", _f_color_pct(cresc_r))

    rows += S("Endividamento e Liquidez  (Fundamentus)")
    if div_p: rows += R("Dív.Bruta/Patrim.", f"{div_p:.2f}x",
                  "fund-val-neg" if div_p > T["stock_divida_alta"] else "fund-val")
    if div_liq is not None: rows += R("Dívida Líquida", _f_big(div_liq),
                  "fund-val-neg" if div_liq > 0 else "fund-val-pos")
    if liq_c:  rows += R("Liq. Corrente", f"{liq_c:.2f}x",
                  "fund-val-pos" if liq_c > 1 else "fund-val-neg")
    if patrim: rows += R("Patrimônio Líq.", _f_big(patrim))

    rows += S("Demonstrativos LTM  (Fundamentus)")
    if receita: rows += R("Receita Líq.", _f_big(receita))
    if lucro is not None: rows += R("Lucro Líq.", _f_big(lucro),
                  "fund-val-pos" if lucro > 0 else "fund-val-neg")

    rows += S("Preço e Variação  (yfinance)")
    rows += R("Var. no Mês",   f"{vm_mes:+.1f}%" if vm_mes is not None else "—", _f_color_pct(vm_mes))
    rows += R("Var. 12 Meses", f"{vm_12:+.1f}%"  if vm_12  is not None else "—", _f_color_pct(vm_12))
    rows += R("Setor", setor)
    if subsetor: rows += R("Subsetor", subsetor)

    return (
        f'<div class="fund-card">'
        f'<div class="fund-header">'
        f'  <div style="display:flex;gap:10px;align-items:flex-start;">'
        f'    {_f_logo(ticker)}'
        f'    <div><div class="fund-ticker">{_html.escape(ticker)}</div>'
        f'    <div class="fund-name">{_html.escape(name)}</div></div>'
        f'  </div>'
        f'  <div><div class="fund-price">{price_str}</div>'
        f'  <div class="{chg_cls}">{chg_str}</div></div>'
        f'</div>'
        f'<div style="margin-bottom:8px;">{"".join(chips)}</div>'
        f'{rows}'
        f'<p style="font-size:0.63rem;color:#3d4a5c;margin:7px 0 0;">'
        f'Fundamentus · yfinance · {_datetime.now().strftime("%d/%m/%Y %H:%M")}</p>'
        f'</div>'
    )


def _fii_card_html(pos: dict, fd: dict, price_info: dict,
                   renda_recebida: float, alerts: list) -> str:
    T = _fund.THR
    ticker = pos["ticker"]
    name   = (pos.get("nome") or ticker)[:45]
    peso   = float(pos.get("pct_carteira", 0))
    custo  = float(pos.get("total_investido", 0))
    qty    = float(pos.get("quantidade", 0))

    preco  = fd.get("cotacao") or price_info.get("price")
    chg    = price_info.get("change_pct", 0.0)
    hist   = price_info.get("hist")
    vm_mes = _fund.var_mes(hist)
    vm_12  = _fund.var_12m(hist)

    pvp      = fd.get("pvp"); dy_f = fd.get("dy")
    vac      = fd.get("vacancia_media") or fd.get("vacancia_fisica")
    vac_fin  = fd.get("vacancia_financ")
    qtd_im   = fd.get("qtd_imoveis"); qtd_cot = fd.get("qtd_cotistas")
    vp_cota  = fd.get("vp_cota"); patrim = fd.get("patrim_liq")
    ult_rend = fd.get("ult_rendimento"); data_rend = fd.get("data_rendimento") or ""
    liq      = fd.get("liq_diaria")
    seg_raw  = fd.get("segmento") or ""
    fii_tipo = _fund.get_fii_tipo(ticker, seg_raw)
    yoc = (renda_recebida / custo * 100) if (renda_recebida > 0 and custo > 0) else None

    chips = []
    if fii_tipo:                                     chips.append(_f_chip(fii_tipo, "purple"))
    if pvp and pvp < T["fii_pvp_desconto"]:          chips.append(_f_chip("P/VP descontado", "green"))
    if pvp and pvp > T["fii_pvp_premium"]:           chips.append(_f_chip("P/VP c/ prêmio", "yellow"))
    if vac is not None and vac > T["fii_vacancia_alta"]: chips.append(_f_chip(f"Vacância {vac:.0f}%", "red"))
    if vac == 0:                                     chips.append(_f_chip("100% ocupado ✓", "green"))
    if dy_f and 8 <= dy_f <= 13:                     chips.append(_f_chip("DY saudável", "green"))
    if dy_f and dy_f > T["fii_dy_alto"]:             chips.append(_f_chip("DY elevado ⚠", "yellow"))
    if qtd_im is not None and qtd_im <= 3:           chips.append(_f_chip("Poucos imóveis ⚠", "yellow"))
    if peso > T["fii_conc_max"]:                     chips.append(_f_chip("Concentrado", "yellow"))
    if not chips:                                    chips.append(_f_chip("Monitorando", "blue"))

    price_str = f"R$ {_f_br(preco)}" if preco else "—"
    if chg > 0.1:    chg_cls = "fund-chg-pos"; chg_str = f"▲ {chg:.2f}% hoje"
    elif chg < -0.1: chg_cls = "fund-chg-neg"; chg_str = f"▼ {abs(chg):.2f}% hoje"
    else:            chg_cls = "fund-chg-neu"; chg_str = f"{chg:.2f}% hoje"

    R = _f_row; S = _f_sec
    rows  = S("Posição")
    rows += R("Peso na carteira", f"{peso:.1f}%")
    rows += R("Custo investido", _f_brs(custo, 0))
    rows += R("Cotas", _f_br(qty, 0))

    rows += S("Indicadores  (Fundamentus)")
    if pvp:   rows += R("P/VP", f"{pvp:.2f}x",
                  "fund-val-warn" if pvp > T["fii_pvp_premium"] else
                  "fund-val-pos"  if pvp < T["fii_pvp_desconto"] else "fund-val")
    if vp_cota: rows += R("VP/Cota", _f_brs(vp_cota))
    if dy_f:  rows += R("Dividend Yield", f"{dy_f:.2f}%",
                  "fund-val-warn" if dy_f > T["fii_dy_alto"] else "fund-val-pos")
    if ult_rend:
        lbl_rend = f"Últ. Rendimento{' (' + data_rend + ')' if data_rend else ''}"
        rows += R(lbl_rend, _f_brs(ult_rend, 4))
    if yoc:             rows += R("YoC (renda/custo)", f"{yoc:.1f}%", "fund-val-pos")
    if renda_recebida > 0: rows += R("Renda recebida", _f_brs(renda_recebida, 0), "fund-val-pos")

    rows += S("Ocupação e Diversificação  (Fundamentus)")
    if fii_tipo: rows += R("Tipo de FII", fii_tipo, "fund-val-pos")
    rows += R("Segmento", seg_raw or "—")
    if vac is not None: rows += R("Vacância Média", f"{vac:.1f}%",
                  "fund-val-neg" if vac > T["fii_vacancia_alta"] else
                  "fund-val-pos" if vac == 0 else "fund-val-warn" if vac > 5 else "fund-val")
    if vac_fin is not None: rows += R("Vacância Financeira", f"{vac_fin:.1f}%")
    if qtd_im is not None:  rows += R("Nº de Imóveis", str(int(qtd_im)),
                  "fund-val-warn" if qtd_im <= 3 else "fund-val")
    if qtd_cot is not None: rows += R("Nº de Cotistas", _f_br(qtd_cot, 0))
    if patrim:  rows += R("Patrimônio Líq.", _f_big(patrim))
    if liq:     rows += R("Liq. Diária", _f_big(liq))

    rows += S("Variação de Preço  (yfinance)")
    rows += R("Var. no Mês",   f"{vm_mes:+.1f}%" if vm_mes is not None else "—", _f_color_pct(vm_mes))
    rows += R("Var. 12 Meses", f"{vm_12:+.1f}%"  if vm_12  is not None else "—", _f_color_pct(vm_12))

    return (
        f'<div class="fund-card">'
        f'<div class="fund-header">'
        f'  <div style="display:flex;gap:10px;align-items:flex-start;">'
        f'    {_f_logo(ticker)}'
        f'    <div><div class="fund-ticker">{_html.escape(ticker)}</div>'
        f'    <div class="fund-name">{_html.escape(name)}</div></div>'
        f'  </div>'
        f'  <div><div class="fund-price">{price_str}</div>'
        f'  <div class="{chg_cls}">{chg_str}</div></div>'
        f'</div>'
        f'<div style="margin-bottom:8px;">{"".join(chips)}</div>'
        f'{rows}'
        f'<p style="font-size:0.63rem;color:#3d4a5c;margin:7px 0 0;">'
        f'Fundamentus · yfinance · {_datetime.now().strftime("%d/%m/%Y %H:%M")}</p>'
        f'</div>'
    )


def _fig_top15(posicoes: list) -> go.Figure:
    top = sorted(posicoes, key=lambda p: p["total_investido"], reverse=True)[:15]
    top = list(reversed(top))
    nomes = [p["nome"][:24] if len(p["nome"]) > 24 else p["nome"] for p in top]
    vals  = [p["total_investido"] for p in top]
    cores = [p["cor"] for p in top]
    texts = [f"R$ {v/1000:.1f}k" if v >= 1000 else fmt_moeda(v) for v in vals]

    fig = go.Figure(go.Bar(
        y=nomes, x=vals,
        orientation="h",
        marker_color=cores,
        text=texts,
        textposition="outside",
        textfont={"size": 10, "color": "#E2E8F0"},
        hovertemplate="<b>%{y}</b><br>Custo: R$ %{x:,.0f}<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        margin={"t": 10, "b": 10, "l": 0, "r": 60},
        height=420,
        xaxis={"showgrid": True, "gridcolor": "#1E2533",
               "tickformat": ",.0f", "tickprefix": "R$ "},
        yaxis={"showgrid": False, "automargin": True, "tickfont": {"size": 10}},
        showlegend=False,
    )
    return fig


def _kpi_classe(cls: dict) -> str:
    """Card compacto de classe de ativo para a linha de resumo."""
    cor  = cls["cor"]
    val  = f"{cls['pct_carteira']:.1f}%"
    sub  = (
        f"{cls['num_ativos']} ativo{'s' if cls['num_ativos'] != 1 else ''}"
        f" · R$ {cls['valor_mercado']/1000:.0f}k"
    )
    return (
        f'<div style="background:#12151E;border:1px solid #1E2533;'
        f'border-radius:10px;padding:14px 12px 10px;height:100%;">'
        f'<div style="font-size:0.55rem;font-weight:800;text-transform:uppercase;'
        f'letter-spacing:0.13em;color:#4A5568;margin-bottom:6px;">% {cls["nome"]}</div>'
        f'<div style="font-size:1.50rem;font-weight:800;color:{cor};'
        f'letter-spacing:-0.02em;line-height:1.1;margin-bottom:4px;">{val}</div>'
        f'<div style="font-size:0.68rem;color:#4A5568;">{sub}</div>'
        f'</div>'
    )


def _tab_analise(carteira: dict, proventos: dict) -> None:
    posicoes   = carteira.get("posicoes", [])
    por_classe = carteira.get("por_classe", [])
    por_setor  = carteira.get("por_setor",  [])
    n_efetivo  = _calc_n_efetivo(posicoes)

    total_inv = carteira["total_investido"]
    total_mkt = carteira["total_mercado"]
    rentab    = carteira["rentabilidade_total_pct"]
    renda_12m = proventos.get("total_12m", proventos.get("total_ano", 0.0))
    renda_por_ticker = {a["ticker"]: a["total"] for a in proventos.get("por_ativo", [])}

    # Injeta CSS dos cards (idempotente — Streamlit de-dups <style>)
    st.markdown(_FUND_CSS, unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)

    # ── Header ────────────────────────────────────────────────────────────────
    st.markdown(
        '<div style="display:flex;align-items:center;gap:10px;margin-bottom:6px;">'
        '<span style="font-size:2rem">🔍</span>'
        '<h2 style="font-size:1.80rem;font-weight:800;color:#E2E8F0;margin:0;">'
        'Análise do Portfólio</h2>'
        '</div>'
        '<p style="font-size:0.80rem;color:#9CA3AF;margin-bottom:20px;">'
        '📌 Indicadores quantitativos para apoio à tomada de decisão. '
        '<b style="color:#CBD5E0;">Não constitui recomendação de investimento.</b> '
        'Avalie sempre o contexto macro, a qualidade da gestão e seu perfil de risco.'
        '</p>',
        unsafe_allow_html=True,
    )

    # ── Sub-tabs ──────────────────────────────────────────────────────────────
    ta, tb, tc, td = st.tabs([
        "📋 Visão Geral", "📈 Ações", "🏢 FIIs", "🔔 Alertas",
    ])

    # ══════════════════════════════════════════════════════════════════════════
    # Visão Geral
    # ══════════════════════════════════════════════════════════════════════════
    with ta:
        st.markdown("<br>", unsafe_allow_html=True)
        _secao_titulo_orig("📋", "Resumo do Portfólio")

        c1, c2, c3, c4 = st.columns(4, gap="small")
        cor_r = _COR_POSITIVO if rentab >= 0 else _COR_NEGATIVO
        seta_r = "▲" if rentab >= 0 else "▼"
        with c1:
            st.markdown(_kpi("Valor Total Investido", fmt_moeda(total_inv),
                             f"{carteira['num_ativos']} ativos na carteira", "#E2E8F0"),
                        unsafe_allow_html=True)
        with c2:
            st.markdown(_kpi("Valor de Mercado", fmt_moeda(total_mkt),
                             "Ativos com cotação disponível", _COR_INFO),
                        unsafe_allow_html=True)
        with c3:
            st.markdown(_kpi("Rentabilidade Acumulada", f"{seta_r} {abs(rentab):.1f}%",
                             "Mercado vs custo (ativos com cotação)", cor_r),
                        unsafe_allow_html=True)
        with c4:
            st.markdown(_kpi("Renda Total Recebida (12M)", fmt_moeda(renda_12m),
                             "Dividendos + JCP + Rendimentos", _COR_ALERTA),
                        unsafe_allow_html=True)

        if por_classe:
            st.markdown("<br>", unsafe_allow_html=True)
            n_cls = min(len(por_classe), 6)
            cols_cls = st.columns(n_cls, gap="small")
            for i, cls in enumerate(por_classe[:n_cls]):
                with cols_cls[i]:
                    st.markdown(_kpi_classe(cls), unsafe_allow_html=True)

        # ── Destaques ─────────────────────────────────────────────────────────
        st.markdown("<br>", unsafe_allow_html=True)
        _secao_titulo_orig("🏆", "Destaques da Carteira")

        if posicoes:
            pos_com_rent  = [p for p in posicoes if p.get("rentab_pct") is not None]
            top_val       = sorted(pos_com_rent, key=lambda p: p["rentab_pct"], reverse=True)[:5]
            top_qda       = sorted(pos_com_rent, key=lambda p: p["rentab_pct"])[:5]
            top_peso_list = sorted(posicoes,     key=lambda p: p["pct_carteira"], reverse=True)[:5]

            def _dest_row(nome: str, valor_str: str, badge: str, cor: str) -> str:
                return (
                    f'<div style="display:flex;justify-content:space-between;align-items:center;'
                    f'padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.05);">'
                    f'<span style="font-size:0.82rem;font-weight:700;color:#E2E8F0;'
                    f'max-width:54%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">'
                    f'{_html.escape(nome)}</span>'
                    f'<span style="font-size:0.80rem;font-weight:700;color:{cor};white-space:nowrap;">'
                    f'{badge}&nbsp;<span style="font-size:0.72rem;color:#4A5568;font-weight:400;">'
                    f'{valor_str}</span></span>'
                    f'</div>'
                )

            col_dv, col_dq, col_dp = st.columns(3, gap="medium")

            with col_dv:
                st.markdown(
                    '<div style="font-size:0.83rem;font-weight:700;color:#E2E8F0;'
                    'margin-bottom:10px;">🏆 Maior Valorização</div>',
                    unsafe_allow_html=True,
                )
                for p in top_val:
                    r    = p["rentab_pct"]
                    nome = (p.get("nome") or p["ticker"])[:24]
                    seta = "▲" if r >= 0 else "▼"
                    cor  = _COR_POSITIVO if r >= 0 else _COR_NEGATIVO
                    st.markdown(
                        _dest_row(nome, fmt_moeda(p["valor_mercado"]),
                                  f"{seta} {abs(r):.1f}%", cor),
                        unsafe_allow_html=True,
                    )

            with col_dq:
                st.markdown(
                    '<div style="font-size:0.83rem;font-weight:700;color:#E2E8F0;'
                    'margin-bottom:10px;">📉 Maior Queda</div>',
                    unsafe_allow_html=True,
                )
                for p in top_qda:
                    r    = p["rentab_pct"]
                    nome = (p.get("nome") or p["ticker"])[:24]
                    seta = "▼" if r < 0 else "▲"
                    cor  = _COR_NEGATIVO if r < 0 else _COR_POSITIVO
                    st.markdown(
                        _dest_row(nome, fmt_moeda(p["valor_mercado"]),
                                  f"{seta} {abs(r):.1f}%", cor),
                        unsafe_allow_html=True,
                    )

            with col_dp:
                st.markdown(
                    '<div style="font-size:0.83rem;font-weight:700;color:#E2E8F0;'
                    'margin-bottom:10px;">⚖️ Maior Peso</div>',
                    unsafe_allow_html=True,
                )
                for p in top_peso_list:
                    pct  = p["pct_carteira"]
                    nome = (p.get("nome") or p["ticker"])[:24]
                    cor  = (_COR_NEGATIVO if pct >= 15 else
                            _COR_ALERTA   if pct >= 10 else "#CBD5E0")
                    st.markdown(
                        _dest_row(nome, fmt_moeda(p["valor_mercado"]),
                                  f"{pct:.1f}%", cor),
                        unsafe_allow_html=True,
                    )

        # ── Alertas de Concentração ───────────────────────────────────────────
        concentrados = [p for p in posicoes if p["pct_carteira"] > 10.0]
        if concentrados:
            st.markdown("<br>", unsafe_allow_html=True)
            _secao_titulo_orig("⚠️", "Alertas de Concentração")
            for p in concentrados:
                st.markdown(
                    f'<div style="border-left:4px solid {_COR_ALERTA};padding:10px 14px;'
                    f'margin-bottom:8px;background:rgba(246,201,14,0.05);'
                    f'border-radius:0 8px 8px 0;">'
                    f'<div style="font-size:0.67rem;font-weight:700;text-transform:uppercase;'
                    f'letter-spacing:0.08em;color:{_COR_ALERTA};margin-bottom:3px;">'
                    f'CONCENTRAÇÃO ELEVADA</div>'
                    f'<div style="font-size:0.82rem;color:#CBD5E0;">'
                    f'{_html.escape(p["ticker"])} ({_html.escape(p["classe"])}) representa '
                    f'{p["pct_carteira"]:.1f}% da carteira. '
                    f'Posições acima de 10% ampliam o risco específico.</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        st.markdown("<br>", unsafe_allow_html=True)
        col_donut, col_top15 = st.columns([1, 1], gap="medium")
        with col_donut:
            _secao_titulo_orig("🥧", "Distribuição por Classe de Ativo")
            if por_classe:
                st.plotly_chart(_fig_donut_classes(por_classe),
                                use_container_width=True,
                                config={"displayModeBar": False},
                                key="analise_donut")
            else:
                st.caption("Sem dados de alocação.")
        with col_top15:
            _secao_titulo_orig("🏆", "Top 15 Posições por Custo")
            if posicoes:
                st.plotly_chart(_fig_top15(posicoes),
                                use_container_width=True,
                                config={"displayModeBar": False},
                                key="analise_top15")
            else:
                st.caption("Sem posições.")

        st.markdown("<br>", unsafe_allow_html=True)
        _secao_titulo_orig("📊", "Concentração")
        col_cls, col_set = st.columns(2, gap="medium")
        with col_cls:
            st.markdown('<div style="font-size:0.83rem;font-weight:700;color:#E2E8F0;'
                        'margin-bottom:10px;">Por Classe de Ativo</div>',
                        unsafe_allow_html=True)
            for cls in por_classe:
                barra_cor = (_COR_NEGATIVO if cls["pct_carteira"] >= 50 else
                             _COR_ALERTA   if cls["pct_carteira"] >= 35 else _COR_POSITIVO)
                w = min(cls["pct_carteira"], 100)
                st.markdown(
                    f'<div style="margin-bottom:10px;">'
                    f'<div style="display:flex;justify-content:space-between;'
                    f'font-size:0.80rem;color:#CBD5E0;margin-bottom:4px;">'
                    f'<span style="display:flex;align-items:center;gap:6px;">'
                    f'<span style="width:8px;height:8px;border-radius:50%;'
                    f'background:{cls["cor"]};display:inline-block"></span>'
                    f'{cls["nome"]}</span>'
                    f'<span style="font-weight:700;color:{barra_cor}">'
                    f'{cls["pct_carteira"]:.1f}%</span></div>'
                    f'<div style="background:#1E2533;border-radius:3px;height:5px;">'
                    f'<div style="background:{cls["cor"]};width:{w:.0f}%;'
                    f'height:100%;border-radius:3px;"></div></div>'
                    f'<div style="font-size:0.70rem;color:#4A5568;text-align:right;'
                    f'margin-top:2px;">{fmt_moeda(cls["valor_mercado"])}'
                    f' · {cls["num_ativos"]} ativo{"s" if cls["num_ativos"] != 1 else ""}'
                    f'</div></div>', unsafe_allow_html=True)
        with col_set:
            st.markdown('<div style="font-size:0.83rem;font-weight:700;color:#E2E8F0;'
                        'margin-bottom:10px;">Por Setor</div>', unsafe_allow_html=True)
            if por_setor:
                total_setor = sum(s["valor_mercado"] for s in por_setor) or 1
                for s in por_setor[:8]:
                    pct_s = round(s["valor_mercado"] / total_setor * 100, 1)
                    w = min(pct_s, 100)
                    bc = (_COR_NEGATIVO if pct_s >= 40 else
                          _COR_ALERTA   if pct_s >= 25 else _COR_INFO)
                    st.markdown(
                        f'<div style="margin-bottom:10px;">'
                        f'<div style="display:flex;justify-content:space-between;'
                        f'font-size:0.80rem;color:#CBD5E0;margin-bottom:4px;">'
                        f'<span>{s["nome"]}</span>'
                        f'<span style="font-weight:700;color:{bc}">{pct_s:.1f}%</span></div>'
                        f'<div style="background:#1E2533;border-radius:3px;height:5px;">'
                        f'<div style="background:{bc};width:{w:.0f}%;'
                        f'height:100%;border-radius:3px;"></div></div>'
                        f'<div style="font-size:0.70rem;color:#4A5568;text-align:right;'
                        f'margin-top:2px;">{fmt_moeda(s["valor_mercado"])}</div></div>',
                        unsafe_allow_html=True)
            else:
                st.caption("Sem dados de setor.")

        st.markdown("<br>", unsafe_allow_html=True)
        _secao_titulo_orig("💵", "Proventos por Ativo")
        por_ativo = proventos.get("por_ativo", [])
        if por_ativo:
            df_prov = pd.DataFrame([{"Ticker": a["ticker"],
                                     "Proventos": fmt_moeda(a["total"]),
                                     "Eventos": a["num_eventos"],
                                     "Último": a.get("ultimo_pagamento") or "—"}
                                    for a in por_ativo[:20]])
            st.dataframe(df_prov,
                         column_config={
                             "Ticker":    st.column_config.TextColumn("Ticker"),
                             "Proventos": st.column_config.TextColumn("Proventos"),
                             "Eventos":   st.column_config.NumberColumn("Eventos", format="%d"),
                             "Último":    st.column_config.TextColumn("Último Pagamento"),
                         },
                         hide_index=True, use_container_width=True)
        else:
            st.caption("Sem dados de proventos por ativo.")

    # ══════════════════════════════════════════════════════════════════════════
    # Ações — cards fundamentalistas
    # ══════════════════════════════════════════════════════════════════════════
    with tb:
        st.markdown("<br>", unsafe_allow_html=True)

        def _base(t: str) -> str:
            return t[:-1] if t.endswith("F") and len(t) > 4 else t

        acoes = [p for p in posicoes
                 if "ação" in p["classe"].lower() or "ações" in p["classe"].lower()
                 or "acoes" in p["classe"].lower() or p["classe"].lower() == "ações br"]

        if not acoes:
            st.info("Sem posições de ações na carteira.", icon="📈")
        else:
            fund_tks = tuple({_base(p["ticker"]) for p in acoes})
            price_tks = fund_tks  # yfinance usa mesmos tickers-base

            with st.spinner(f"Buscando dados fundamentalistas para {len(fund_tks)} ações…"):
                # DB primário + Fundamentus validação/fallback (batch sem Status Invest)
                fd_all    = _recon.batch_fund_fmt(fund_tks)
                price_all = _fund.fetch_price_data(price_tks)

            for i in range(0, len(acoes), 3):
                chunk = acoes[i:i + 3]
                cols  = st.columns(3, gap="small")
                for j, pos in enumerate(chunk):
                    base_t = _base(pos["ticker"])
                    fd     = fd_all.get(base_t, {})
                    pi     = price_all.get(base_t, {})
                    alts   = _fund.alerts_stock(pos, fd)
                    with cols[j]:
                        st.markdown(_stock_card_html(pos, fd, pi, alts),
                                    unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════════
    # FIIs — cards fundamentalistas
    # ══════════════════════════════════════════════════════════════════════════
    with tc:
        st.markdown("<br>", unsafe_allow_html=True)

        fiis = [p for p in posicoes
                if "fii" in p["classe"].lower() or "fundo imob" in p["classe"].lower()]

        if not fiis:
            st.info("Sem posições de FIIs na carteira.", icon="🏢")
        else:
            fii_tks = tuple(p["ticker"] for p in fiis)

            with st.spinner(f"Buscando dados de {len(fii_tks)} FIIs…"):
                fd_fiis   = _fund.batch_fiis(fii_tks)
                price_fiis = _fund.fetch_price_data(fii_tks)

            for i in range(0, len(fiis), 3):
                chunk = fiis[i:i + 3]
                cols  = st.columns(3, gap="small")
                for j, pos in enumerate(chunk):
                    t     = pos["ticker"]
                    fd    = fd_fiis.get(t, {})
                    pi    = price_fiis.get(t, {})
                    renda = renda_por_ticker.get(t, 0.0)
                    alts  = _fund.alerts_fii(pos, fd)
                    with cols[j]:
                        st.markdown(_fii_card_html(pos, fd, pi, renda, alts),
                                    unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════════
    # Alertas — consolidado ações + FIIs
    # ══════════════════════════════════════════════════════════════════════════
    with td:
        st.markdown("<br>", unsafe_allow_html=True)
        st.caption("Alertas gerados com base nos dados do Fundamentus. "
                   "Carregue a aba Ações ou FIIs primeiro para atualizar os dados.")

        # Coleta alertas dos dados já cacheados (sem refetch)
        all_alerts: list[tuple] = []

        def _base_td(t: str) -> str:
            return t[:-1] if t.endswith("F") and len(t) > 4 else t

        acoes_td = [p for p in posicoes
                    if "ação" in p["classe"].lower() or "ações" in p["classe"].lower()
                    or "acoes" in p["classe"].lower() or p["classe"].lower() == "ações br"]
        for pos in acoes_td:
            # DB primário + Fundamentus fallback (escala Fundamentus para alerts_stock)
            fd = _recon.get_multiplos_fund_fmt(_base_td(pos["ticker"]))
            if not fd:
                fd = _fund._scrape_stock(_base_td(pos["ticker"]))
            for a in _fund.alerts_stock(pos, fd):
                all_alerts.append(a)

        fiis_td = [p for p in posicoes if "fii" in p["classe"].lower()]
        for pos in fiis_td:
            fd = _fund._scrape_fii(pos["ticker"])
            for a in _fund.alerts_fii(pos, fd):
                all_alerts.append(a)

        if not all_alerts:
            st.success("Nenhum alerta identificado — portfólio dentro dos parâmetros.", icon="✅")
        else:
            red    = [a for a in all_alerts if a[0] == "red"]
            yellow = [a for a in all_alerts if a[0] == "yellow"]
            green  = [a for a in all_alerts if a[0] == "green"]
            blue   = [a for a in all_alerts if a[0] == "blue"]

            for group in (red, yellow, green, blue):
                for cls, lbl_cls, label, msg in group:
                    st.markdown(_f_alert_html(cls, lbl_cls, label, msg),
                                unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# RENDER PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

def render() -> None:
    carteira  = get_carteira()
    cashflow  = get_cashflow_mensal()
    proventos = get_proventos()
    evolucao  = get_evolucao_patrimonial()

    # ── Header ────────────────────────────────────────────────────────────────
    _fonte = carteira.get("data_source", "mock")
    _fonte_label = (
        "Dados reais" if _fonte == "real"
        else "Fallback (mock)" if _fonte == "mock_fallback"
        else "Modo mock"
    )

    col_title, col_date = st.columns([3, 1])
    with col_title:
        st.markdown(
            '<div style="display:flex;align-items:center;gap:12px;">'
            '<span style="font-size:2rem">📈</span>'
            '<h1 style="font-size:2rem;font-weight:800;color:#E2E8F0;margin:0;">'
            'Investimentos</h1>'
            '</div>',
            unsafe_allow_html=True,
        )
    with col_date:
        st.markdown(
            f'<div style="text-align:right;padding-top:6px;">'
            f'<div style="font-size:0.60rem;text-transform:uppercase;'
            f'letter-spacing:0.1em;color:#4A5568;">Última Atualização</div>'
            f'<div style="font-size:1.00rem;font-weight:700;color:{_COR_POSITIVO};">'
            f'{_date.today().strftime("%d/%m/%Y")}</div>'
            f'<div style="font-size:0.70rem;color:#4A5568;">{_fonte_label}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # Badges
    col_b1, col_b2, col_b3, *_ = st.columns([1, 1, 1, 4])
    with col_b1:
        badge_status(
            "Dados reais"     if _fonte == "real" else
            "Fallback (mock)" if _fonte == "mock_fallback" else "Modo mock",
            "sucesso" if _fonte == "real" else
            "erro"    if _fonte == "mock_fallback" else "alerta",
        )
    with col_b2:
        badge_status(
            "Cotações OK" if carteira["cotacoes_disponiveis"] else "Sem cotações",
            "sucesso" if carteira["cotacoes_disponiveis"] else "alerta",
        )
    with col_b3:
        badge_status(f"{proventos['num_eventos']} proventos", "neutro")

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Sub-navegação via tabs ────────────────────────────────────────────────
    tab1, tab2, tab3, tab4 = st.tabs([
        "📊  Dashboard",
        "📈  Histórico",
        "💼  Carteira",
        "🔍  Análise",
    ])

    with tab1:
        _tab_dashboard(carteira, proventos, cashflow, evolucao)

    with tab2:
        _tab_historico(cashflow, proventos, evolucao)

    with tab3:
        _tab_carteira(carteira, proventos)

    with tab4:
        _tab_analise(carteira, proventos)
