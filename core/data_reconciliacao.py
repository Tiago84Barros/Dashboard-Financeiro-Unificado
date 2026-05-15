"""
core/data_reconciliacao.py
Camada de reconciliação de dados fundamentalistas.

Estratégia:
  • Fonte primária:   banco de dados  (core/b3_db.py)
  • Fontes web:       Fundamentus     (core/fundamentus.py)
                     Status Invest   (core/status_invest.py)

Para cada indicador:
  1. Se BD tem valor e fontes web têm valor:
     - Calcula discrepância relativa (ratios) ou absoluta (% em decimal).
     - Se discrepância > threshold → usa média das fontes web como verdade.
  2. Se BD não tem valor → usa a fonte web disponível.
  3. Se nenhuma fonte tem valor → None.

Thresholds:
  • Campos ratio  (P/L, P/VP, EV_EBIT, …):   30 % relativo
  • Campos %      (DY, ROE, Margens, …):       5 p.p. (= 0.05 em escala decimal)

Escala de retorno:
  • Campos %     (DY, ROE, ROIC, …):  decimal   (0.15 = 15%)   ← igual ao BD
  • Campos ratio (P/L, P/VP, …):      valor direto (15.0 = 15×)

Funções exportadas:
  get_multiplos_reconciliados(ticker)  → dict  (escala BD + _fontes + _alertas)
  get_multiplos_fund_fmt(ticker)       → dict  (escala Fundamentus: raw % — compatível com alerts_stock)
  batch_fund_fmt(tickers)             → dict[ticker, fund_dict]  (batch, sem Status Invest)
  reconciliacao_to_series(recon)       → pd.Series  (escala BD, sem metadados)
  fmt_fonte_badge(field, fontes)       → str  (emoji indicador de fonte)
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pandas as pd
import streamlit as st

# ── Mapeamento Fundamentus key → coluna canônica BD ───────────────────────────
# Fundamentus retorna campos % como raw % (roe=15 para 15%)
# BD armazena como decimal (ROE=0.15 para 15%)
_FUND_TO_DB: dict[str, str] = {
    "pl":              "P/L",
    "pvp":             "P/VP",
    "dy":              "DY",
    "roe":             "ROE",
    "roic":            "ROIC",
    "marg_liq":        "Margem_Liquida",
    "marg_ebit":       "Margem_Operacional",
    "ev_ebit":         "EV_EBIT",
    "liq_corr":        "Liquidez_Corrente",
    "div_brut_patrim": "Endividamento_Total",
}

# Mapeamento inverso BD → Fundamentus key (para fund_fmt)
_DB_TO_FUND: dict[str, str] = {v: k for k, v in _FUND_TO_DB.items()}
_DB_TO_FUND["P_FCO"]  = "p_fco"
_DB_TO_FUND["Payout"] = "pout"
_DB_TO_FUND["ROA"]    = "roa"

# Campos armazenados como decimal no BD (0.15 = 15%), raw % no Fundamentus (15 = 15%)
_PCT_FIELDS: frozenset[str] = frozenset({
    "DY", "ROE", "ROIC", "ROA",
    "Margem_Liquida", "Margem_Operacional",
    "Payout",
})

# Campos ratio (mesma escala no BD e Fundamentus)
_RATIO_FIELDS: frozenset[str] = frozenset({
    "P/L", "P/VP", "EV_EBIT", "Liquidez_Corrente",
    "Endividamento_Total", "P_FCO",
})

# Thresholds de discrepância
_THRESH_RATIO = 0.30   # 30 % relativo
_THRESH_PCT   = 0.05   # 5 p.p. em escala decimal


# ── Helpers internos ──────────────────────────────────────────────────────────

def _norm_fund(db_field: str, fund_value: float | None) -> float | None:
    """Converte valor Fundamentus para escala BD (÷100 para campos %)."""
    if fund_value is None:
        return None
    return fund_value / 100.0 if db_field in _PCT_FIELDS else fund_value


def _to_float(v: Any) -> float | None:
    """Tenta converter para float; retorna None em falha ou NaN."""
    try:
        x = float(v)
        return x if (x == x) and (x not in (float("inf"), float("-inf"))) else None
    except (TypeError, ValueError):
        return None


def _is_discrepant(db_field: str, db_val: float, web_val: float) -> bool:
    """True se a diferença entre BD e web excede o threshold do campo."""
    if db_field in _PCT_FIELDS:
        return abs(db_val - web_val) > _THRESH_PCT
    # ratio: discrepância relativa
    if abs(db_val) < 1e-9:
        return abs(web_val) > 0.01
    return abs(db_val - web_val) / abs(db_val) > _THRESH_RATIO


def _web_avg(*values: float | None) -> float | None:
    """Média simples dos valores não-nulos."""
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def _diff_str(db_field: str, db_val: float, web_val: float) -> str:
    if db_field in _PCT_FIELDS:
        return f"{abs(db_val - web_val) * 100:.1f}pp"
    if abs(db_val) > 1e-9:
        return f"{abs(db_val - web_val) / abs(db_val) * 100:.0f}%"
    return f"{abs(db_val - web_val):.4g}"


# ── Função principal: single-ticker com Status Invest ─────────────────────────

@st.cache_data(ttl=1800, show_spinner=False)
def get_multiplos_reconciliados(ticker: str) -> dict[str, Any]:
    """
    Retorna múltiplos reconciliados para um único ticker.

    Formato de valores:
    • Campos %  (DY, ROE, ROIC, Margens, Payout): decimal   (0.15 = 15%)
    • Campos ratio (P/L, P/VP, EV_EBIT, …):       direto     (15.0 = 15×)

    Metadados incluídos no retorno:
    • "_fontes":  dict campo → "db" | "web" | "db_sobrescrito" | "sem_dados"
    • "_alertas": list[str]  — discrepâncias detectadas

    Retorna {} se todas as fontes falharem.
    """
    from core import b3_db        as _db
    from core import fundamentus  as _fund
    from core import status_invest as _si

    tk = ticker.strip().upper().replace(".SA", "")
    fontes:  dict[str, str] = {}
    alertas: list[str]      = []

    # ── 1. BD (primário) ──────────────────────────────────────────────────────
    db_series = _db.load_multiplos(tk)
    db_row: dict[str, Any] = db_series.to_dict() if not db_series.empty else {}

    # ── 2. Fontes web (paralelo) ───────────────────────────────────────────────
    def _fetch_fund():  return _fund._scrape_stock(tk)
    def _fetch_si():    return _si.fetch_stock_si(tk)

    with ThreadPoolExecutor(max_workers=2) as ex:
        f_fund = ex.submit(_fetch_fund)
        f_si   = ex.submit(_fetch_si)
        fund_raw = f_fund.result()
        si_raw   = f_si.result()

    # Normaliza Fundamentus → escala BD
    fund: dict[str, float | None] = {
        db_key: _norm_fund(db_key, _to_float(fund_raw.get(fk)))
        for fk, db_key in _FUND_TO_DB.items()
    }
    # Status Invest já vem na escala BD
    si: dict[str, float | None] = {
        k: _to_float(v)
        for k, v in si_raw.items()
        if k not in ("_fontes", "_alertas")
    }

    # ── 3. Universo de campos a reconciliar ───────────────────────────────────
    all_fields: set[str] = (
        set(_FUND_TO_DB.values()) |
        set(si.keys()) |
        {k for k in db_row if k not in ("Ticker", "data", "id", "_fontes", "_alertas")}
    )

    result: dict[str, Any] = {}

    for field in all_fields:
        db_val   = _to_float(db_row.get(field))
        fund_val = fund.get(field)
        si_val   = si.get(field)

        # Caso 1: BD tem valor
        if db_val is not None:
            web_avg = _web_avg(fund_val, si_val)
            if web_avg is not None and _is_discrepant(field, db_val, web_avg):
                result[field]  = web_avg
                fontes[field]  = "db_sobrescrito"
                alertas.append(
                    f"**{field}**: BD={db_val:.4g} | Web≈{web_avg:.4g} "
                    f"(Δ {_diff_str(field, db_val, web_avg)}) → fonte web"
                )
            else:
                result[field] = db_val
                fontes[field] = "db"

        # Caso 2: BD sem valor, web tem
        else:
            web_avg = _web_avg(fund_val, si_val)
            if web_avg is not None:
                result[field] = web_avg
                fontes[field] = "web"
            else:
                result[field] = None
                fontes[field] = "sem_dados"

    result["_fontes"]  = fontes
    result["_alertas"] = alertas

    # Preserva identificadores do BD
    for extra in ("Ticker", "data"):
        if extra in db_row:
            result.setdefault(extra, db_row[extra])

    return result


# ── Batch: sem Status Invest (mais rápido para carteiras) ─────────────────────

def batch_fund_fmt(tickers: tuple[str, ...]) -> dict[str, dict]:
    """
    Reconcilia múltiplos para múltiplos tickers em batch.

    Performance: usa load_multiplos_todos() (uma query) + batch_stocks() (paralelo).
    Status Invest é omitido no batch para não sobrecarregar.

    Retorno: dict ticker → dict no formato Fundamentus (raw % para campos percentuais).
    Compatible com _stock_card_html() e alerts_stock().
    Inclui todos os campos Fundamentus originais (cotacao, setor, psr, etc.),
    com campos do BD sobrescrevendo Fundamentus quando há dado válido sem discrepância.
    """
    from core import b3_db       as _db
    from core import fundamentus as _fund

    if not tickers:
        return {}

    # DB em batch
    df_todos = _db.load_multiplos_todos()
    db_by_ticker: dict[str, dict] = {}
    if not df_todos.empty and "Ticker" in df_todos.columns:
        for _, row in df_todos.iterrows():
            t = str(row.get("Ticker", "")).upper().replace(".SA", "")
            if t:
                db_by_ticker[t] = row.to_dict()

    # Fundamentus em batch (paralelo interno)
    fund_all = _fund.batch_stocks(tickers)

    result: dict[str, dict] = {}
    for tk in tickers:
        tk_clean = tk.upper().replace(".SA", "")

        # Base: dicionário completo do Fundamentus (tem cotacao, setor, psr, etc.)
        fd_base: dict = dict(fund_all.get(tk_clean, {}))
        db_row:  dict = db_by_ticker.get(tk_clean, {})

        if not db_row:
            # Sem dado no BD → usa Fundamentus puro
            result[tk_clean] = fd_base
            continue

        # Merge campo a campo: para cada campo mapeado BD→Fund, decide qual usar
        fd_merged = dict(fd_base)

        for fk, db_key in _FUND_TO_DB.items():
            # db_val_db: valor em escala BD (decimal para campos %)
            db_val_db    = _to_float(db_row.get(db_key))
            # fund_val_raw: valor em escala Fundamentus (raw % para campos %)
            fund_val_raw = _to_float(fd_base.get(fk))
            # Normaliza Fundamentus para escala BD (para poder comparar)
            fund_val_db  = _norm_fund(db_key, fund_val_raw)

            if db_val_db is not None and fund_val_db is not None:
                if not _is_discrepant(db_key, db_val_db, fund_val_db):
                    # Sem discrepância: BD validado → converte BD → Fundamentus fmt
                    fd_merged[fk] = (
                        db_val_db * 100.0 if db_key in _PCT_FIELDS else db_val_db
                    )
                # Com discrepância: mantém Fundamentus (já está em fd_merged)
            elif db_val_db is not None and fund_val_raw is None:
                # Apenas BD tem dado → converte para Fundamentus fmt
                fd_merged[fk] = (
                    db_val_db * 100.0 if db_key in _PCT_FIELDS else db_val_db
                )
            # Apenas Fundamentus tem dado → fd_merged já tem o valor correto

        result[tk_clean] = fd_merged

    return result


# ── Helpers de conversão e display ───────────────────────────────────────────

def get_multiplos_fund_fmt(ticker: str) -> dict[str, Any]:
    """
    Retorna múltiplos reconciliados (single ticker, com Status Invest)
    no formato Fundamentus — raw % para campos percentuais.

    Compatible com fundamentus.alerts_stock() e _stock_card_html().
    """
    recon = get_multiplos_reconciliados(ticker)
    out: dict[str, Any] = {}
    for db_key, fund_key in _DB_TO_FUND.items():
        v = recon.get(db_key)
        if v is not None:
            out[fund_key] = v * 100.0 if db_key in _PCT_FIELDS else v
    return out


def reconciliacao_to_series(recon: dict[str, Any]) -> pd.Series:
    """
    Converte o resultado de get_multiplos_reconciliados() em pd.Series (escala BD).
    Remove metadados (_fontes, _alertas, Ticker, data).
    Retorna pd.Series vazia se recon for vazio.
    """
    _excluir = {"_fontes", "_alertas", "Ticker", "data", "id"}
    data = {k: v for k, v in recon.items() if k not in _excluir and v is not None}
    return pd.Series(data) if data else pd.Series(dtype=object)


def fmt_fonte_badge(field: str, fontes: dict) -> str:
    """Retorna emoji badge indicando a fonte do valor para exibição inline."""
    src = fontes.get(field, "")
    return {
        "db":             "🗄️",
        "web":            "🌐",
        "db_sobrescrito": "⚠️",
    }.get(src, "")
