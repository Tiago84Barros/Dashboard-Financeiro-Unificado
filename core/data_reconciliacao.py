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

# Ranges canonicos usados antes de qualquer score/backfill. Eles sao
# intencionalmente conservadores: valores fora daqui viram "sem dado" ate uma
# fonte externa valida confirmar outra coisa.
VALID_RANGES: dict[str, tuple[float | None, float | None]] = {
    "DY": (0.000001, 0.50),
    "ROE": (-3.0, 5.0),
    "ROA": (-1.0, 1.5),
    "ROIC": (-2.0, 3.0),
    "Margem_Liquida": (-2.0, 2.0),
    "Margem_Operacional": (-2.0, 2.0),
    "Payout": (-2.0, 5.0),
    "P/L": (0.01, 200.0),
    "P/VP": (0.01, 50.0),
    "EV_EBIT": (0.01, 200.0),
    "P_FCO": (0.01, 200.0),
    "Endividamento_Total": (0.0, 20.0),
    "Liquidez_Corrente": (0.0, 20.0),
}

CANONICAL_MULTIPLOS_FIELDS: tuple[str, ...] = tuple(VALID_RANGES.keys())
_ZERO_ALWAYS_INVALID: frozenset[str] = frozenset({"DY"})
_ZERO_SUSPECT_MIN_ROWS = 20
_ZERO_SUSPECT_RATE = 0.80
_ZERO_SUSPECT_POS_RATE = 0.05


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


def is_usable_value(
    field: str,
    value: Any,
    zero_invalid_fields: set[str] | frozenset[str] | None = None,
) -> bool:
    """True quando o valor pode entrar em score/backfill sem distorcer ranking."""
    x = _to_float(value)
    if x is None:
        return False
    zero_invalid = set(_ZERO_ALWAYS_INVALID)
    if zero_invalid_fields:
        zero_invalid.update(zero_invalid_fields)
    if field in zero_invalid and abs(x) <= 1e-12:
        return False
    lo, hi = VALID_RANGES.get(field, (None, None))
    if lo is not None and x < lo:
        return False
    if hi is not None and x > hi:
        return False
    return True


def infer_zero_as_missing_fields(
    df: pd.DataFrame,
    fields: tuple[str, ...] | list[str] | None = None,
) -> set[str]:
    """
    Detecta colunas provavelmente importadas como zero no lugar de missing.

    Zero pode ser real em um ticker, mas quase nunca e real para o universo
    inteiro em campos como DY/Payout.
    """
    if df.empty:
        return set()
    out: set[str] = set()
    for field in fields or CANONICAL_MULTIPLOS_FIELDS:
        if field not in df.columns:
            continue
        s = pd.to_numeric(df[field], errors="coerce").dropna()
        if len(s) < _ZERO_SUSPECT_MIN_ROWS:
            continue
        zero_rate = (s.abs() <= 1e-12).mean()
        pos_rate = (s > 1e-12).mean()
        if zero_rate >= _ZERO_SUSPECT_RATE and pos_rate <= _ZERO_SUSPECT_POS_RATE:
            out.add(field)
    return out


def clean_multiplos_frame(
    df: pd.DataFrame,
    zero_invalid_fields: set[str] | frozenset[str] | None = None,
) -> pd.DataFrame:
    """Remove nulos mascarados e outliers de multiplos sem alterar demais colunas."""
    if df.empty:
        return df.copy()
    out = df.copy()
    for field in CANONICAL_MULTIPLOS_FIELDS:
        if field not in out.columns:
            continue
        vals = pd.to_numeric(out[field], errors="coerce")
        out[field] = vals.where(
            vals.map(lambda v, f=field: is_usable_value(f, v, zero_invalid_fields))
        )
    return out


def data_quality_summary(
    df_raw: pd.DataFrame,
    df_clean: pd.DataFrame,
    zero_invalid_fields: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    """Resumo pequeno para UI/auditoria da carteira."""
    fields = [c for c in CANONICAL_MULTIPLOS_FIELDS if c in df_raw.columns]
    invalid_cells = 0
    total_cells = 0
    invalid_by_field: dict[str, int] = {}
    for field in fields:
        raw = pd.to_numeric(df_raw[field], errors="coerce")
        total_cells += int(raw.notna().sum())
        invalid = raw.notna() & ~raw.map(lambda v, f=field: is_usable_value(f, v, zero_invalid_fields))
        count = int(invalid.sum())
        invalid_cells += count
        if count:
            invalid_by_field[field] = count
    return {
        "tickers": int(df_raw["Ticker"].nunique()) if "Ticker" in df_raw.columns else int(len(df_raw)),
        "campos_monitorados": len(fields),
        "celulas_invalidas": invalid_cells,
        "celulas_monitoradas": total_cells,
        "campos_zero_suspeito": sorted(zero_invalid_fields or []),
        "invalidos_por_campo": invalid_by_field,
        "celulas_validas_apos_limpeza": int(df_clean[fields].notna().sum().sum()) if fields else 0,
    }


def _fund_to_db_values(raw: dict[str, Any]) -> dict[str, float | None]:
    return {
        db_key: _norm_fund(db_key, _to_float(raw.get(fk)))
        for fk, db_key in _FUND_TO_DB.items()
    }


def _valid_web_values(
    field: str,
    *values: float | None,
    zero_invalid_fields: set[str] | frozenset[str] | None = None,
) -> list[float]:
    return [
        float(v) for v in values
        if is_usable_value(field, v, zero_invalid_fields)
    ]


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
        db_ok = is_usable_value(field, db_val)
        web_vals = _valid_web_values(field, fund_val, si_val)
        web_avg = _web_avg(*web_vals)

        # Caso 1: BD tem valor
        if db_ok:
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
            if web_avg is not None:
                result[field] = web_avg
                fontes[field] = "web"
                if db_val is not None:
                    alertas.append(
                        f"**{field}**: BD invalido={db_val:.4g} | Webâ‰ˆ{web_avg:.4g} "
                        "-> fonte web"
                    )
            else:
                result[field] = None
                fontes[field] = "sem_dados"
                if db_val is not None:
                    alertas.append(f"**{field}**: BD invalido={db_val:.4g} -> sem dado")

    result["_fontes"]  = fontes
    result["_alertas"] = alertas

    # Preserva identificadores do BD
    for extra in ("Ticker", "data"):
        if extra in db_row:
            result.setdefault(extra, db_row[extra])

    return result


# ── Batch: sem Status Invest (mais rápido para carteiras) ─────────────────────

def _clean_ticker(ticker: str) -> str:
    return str(ticker).strip().upper().replace(".SA", "")


def _web_source_label(fund_ok: bool, si_ok: bool) -> str:
    if fund_ok and si_ok:
        return "Fundamentus+StatusInvest"
    if si_ok:
        return "StatusInvest"
    if fund_ok:
        return "Fundamentus"
    return "web"


def _status_batch(tickers: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    from core import status_invest as _si

    def _fetch(tk: str) -> tuple[str, dict[str, Any]]:
        try:
            return tk, _si.fetch_stock_si(tk)
        except Exception:
            return tk, {}

    with ThreadPoolExecutor(max_workers=4) as ex:
        return dict(ex.map(_fetch, tickers))


def batch_multiplos_reconciliados(
    tickers: tuple[str, ...] | list[str],
    df_base: pd.DataFrame | None = None,
    include_status: bool = False,
    fund_data: dict[str, dict[str, Any]] | None = None,
    status_data: dict[str, dict[str, Any]] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """
    Reconciliacao em lote para carteiras/rankings.

    Usa BD como base, corrige nulos/outliers com Fundamentus e, quando pedido,
    Status Invest. Retorna (dataframe_final, auditoria, resumo).
    """
    from core import b3_db as _db
    from core import fundamentus as _fund

    tks = tuple(dict.fromkeys(_clean_ticker(t) for t in tickers if _clean_ticker(t)))
    if not tks:
        return pd.DataFrame(), pd.DataFrame(), {}

    base = df_base.copy() if df_base is not None else _db.load_multiplos_todos()
    zero_suspect_source = base.copy()
    if base.empty or "Ticker" not in base.columns:
        base = pd.DataFrame({"Ticker": list(tks)})
    else:
        base["Ticker"] = base["Ticker"].astype(str).map(_clean_ticker)
        base = base[base["Ticker"].isin(tks)].drop_duplicates("Ticker", keep="first")
        faltantes = [tk for tk in tks if tk not in set(base["Ticker"])]
        if faltantes:
            base = pd.concat([base, pd.DataFrame({"Ticker": faltantes})], ignore_index=True)

    zero_suspect = infer_zero_as_missing_fields(zero_suspect_source)

    fund_all = fund_data if fund_data is not None else _fund.batch_stocks(tks)
    si_all: dict[str, dict[str, Any]] = status_data or {}
    if include_status and status_data is None:
        si_all = _status_batch(tks)

    fields = list(CANONICAL_MULTIPLOS_FIELDS)
    rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []

    for _, row in base.iterrows():
        tk = _clean_ticker(row.get("Ticker", ""))
        if not tk:
            continue
        out = row.to_dict()
        out["Ticker"] = tk
        fund = _fund_to_db_values(fund_all.get(tk, {}) or {})
        si = {
            k: _to_float(v)
            for k, v in (si_all.get(tk, {}) or {}).items()
            if k not in ("_fontes", "_alertas")
        }

        for field in fields:
            db_val = _to_float(row.get(field))
            db_ok = is_usable_value(field, db_val, zero_suspect)
            fund_ok = is_usable_value(field, fund.get(field), zero_suspect)
            si_ok = is_usable_value(field, si.get(field), zero_suspect)
            web_vals = _valid_web_values(
                field, fund.get(field), si.get(field), zero_invalid_fields=zero_suspect
            )
            web_avg = _web_avg(*web_vals)
            source = "db"
            action = "mantido"

            if db_ok:
                final = float(db_val)
                if web_avg is not None and _is_discrepant(field, float(db_val), web_avg):
                    final = float(web_avg)
                    source = _web_source_label(fund_ok, si_ok)
                    action = "db_sobrescrito"
            elif web_avg is not None:
                final = float(web_avg)
                source = _web_source_label(fund_ok, si_ok)
                action = "web_preencheu" if db_val is None else "web_corrigiu"
            else:
                final = float("nan")
                source = "sem_dados"
                action = "sem_dados" if db_val is None else "invalidado"

            out[field] = final
            if action != "mantido":
                audit_rows.append({
                    "Ticker": tk,
                    "Indicador": field,
                    "Antes": db_val,
                    "Depois": final,
                    "Fonte": source,
                    "Acao": action,
                })

        rows.append(out)

    reconciled = pd.DataFrame(rows)
    cleaned = clean_multiplos_frame(reconciled, zero_suspect)
    audit = pd.DataFrame(audit_rows)
    summary = data_quality_summary(base, cleaned, zero_suspect)
    summary["correcoes_web"] = int(len(audit[audit["Acao"].isin(["web_preencheu", "web_corrigiu", "db_sobrescrito"])]) if not audit.empty else 0)
    summary["celulas_invalidadas"] = int(len(audit[audit["Acao"].eq("invalidado")]) if not audit.empty else 0)
    summary["usa_status_invest"] = bool(include_status or status_data is not None)
    return cleaned, audit, summary


def clean_multiplos_history_batch(
    hist_batch: dict[str, pd.DataFrame],
    zero_invalid_fields: set[str] | frozenset[str] | None = None,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """Aplica os mesmos ranges aos historicos usados no backtest."""
    cleaned: dict[str, pd.DataFrame] = {}
    audit_rows: list[dict[str, Any]] = []
    for tk, df in (hist_batch or {}).items():
        if df is None or df.empty:
            continue
        before = df.copy()
        after = clean_multiplos_frame(before, zero_invalid_fields)
        for field in CANONICAL_MULTIPLOS_FIELDS:
            if field not in before.columns:
                continue
            raw = pd.to_numeric(before[field], errors="coerce")
            new = pd.to_numeric(after[field], errors="coerce")
            invalid = raw.notna() & new.isna()
            if invalid.any():
                audit_rows.append({
                    "Ticker": _clean_ticker(tk),
                    "Indicador": field,
                    "Ocorrencias": int(invalid.sum()),
                    "Acao": "historico_invalidado",
                })
        cleaned[_clean_ticker(tk)] = after
    return cleaned, pd.DataFrame(audit_rows)


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
            db_ok = is_usable_value(db_key, db_val_db)
            fund_ok = is_usable_value(db_key, fund_val_db)

            if db_ok and fund_ok:
                if not _is_discrepant(db_key, db_val_db, fund_val_db):
                    # Sem discrepância: BD validado → converte BD → Fundamentus fmt
                    fd_merged[fk] = (
                        db_val_db * 100.0 if db_key in _PCT_FIELDS else db_val_db
                    )
                # Com discrepância: mantém Fundamentus (já está em fd_merged)
            elif db_ok and not fund_ok:
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
