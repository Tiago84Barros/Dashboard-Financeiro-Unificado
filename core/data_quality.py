"""
core/data_quality.py
Fonte ÚNICA da verdade para qualidade de dados fundamentalistas B3.

Centraliza:
  • Faixas canônicas (financeiramente coerentes) de cada indicador.
  • Distinção entre DADO AUSENTE (None/NaN) e ZERO (0.0) — nunca tratar um
    como o outro. Zero é inválido apenas onde é economicamente implausível
    (ex.: DY=0 quase sempre é dado faltante).
  • Validação de múltiplos, DRE e macro.
  • Detecção de campos críticos ausentes e de outliers.
  • Completude por empresa (para impedir que incompletos sejam ranqueados
    como completos).
  • Relatório consolidado de qualidade.

Mantido PURO (apenas pandas/numpy) para ser testável sem Streamlit/banco/rede.
`core/data_reconciliacao.py` e `views/empresas_b3.py` importam as faixas daqui
para eliminar a duplicação que antes deixava margens de ±200% passarem.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

# ── Faixas canônicas (apertadas e coerentes) ──────────────────────────────────
# Margens líquida/operacional em [-100%, +100%]: net income > receita só ocorre
# por itens não-recorrentes — para fins de ranking é artefato de dado. Isso é o
# que captura o caso UGPA3=190%.
CANONICAL_RANGES: dict[str, tuple[float | None, float | None]] = {
    "DY": (0.000001, 0.50),
    "ROE": (-3.0, 5.0),
    "ROA": (-1.0, 1.5),
    "ROIC": (-2.0, 3.0),
    "Margem_Liquida": (-1.0, 1.0),
    "Margem_Operacional": (-1.0, 1.0),
    "Payout": (-2.0, 5.0),
    "P/L": (0.01, 200.0),
    "P/VP": (0.01, 50.0),
    "EV_EBIT": (0.01, 200.0),
    "P_FCO": (0.01, 200.0),
    "Endividamento_Total": (0.0, 20.0),
    "Liquidez_Corrente": (0.0, 20.0),
}

CANONICAL_MULTIPLOS_FIELDS: tuple[str, ...] = tuple(CANONICAL_RANGES.keys())

# SINAIS (0/1), não indicadores ranqueáveis. Existem porque a faixa coerente,
# ao rejeitar um valor absurdo, apagava informação em vez de registrá-la:
# dívida/PL com patrimônio NEGATIVO dá razão negativa, era descartada, e o
# resultado era NULL — a mesma coisa que "sem dado". Medido em 30/07/2026: dos
# 41 tickers que tinham dívida e patrimônio no balanço e nenhuma métrica de
# endividamento, 37 tinham PATRIMÔNIO NEGATIVO e 4 razão fora de faixa. O piso
# de qualidade tratava empresa tecnicamente insolvente igual a empresa sem
# balanço. Ausência é indecidível; patrimônio negativo é um veredito.
#
# Deliberadamente FORA de CANONICAL_RANGES: aquele dict define o universo de
# indicadores para reconciliação (core/data_reconciliacao.py) e healing
# (core/data_healing.py). Sinal não se reconcilia com fonte externa nem se
# imputa — ou o balanço diz, ou não diz.
SIGNAL_RANGES: dict[str, tuple[float | None, float | None]] = {
    "Patrimonio_Negativo": (0.0, 1.0),
    "Endividamento_Fora_De_Faixa": (0.0, 1.0),
    "FCO_Negativo": (0.0, 1.0),
}

SIGNAL_FIELDS: tuple[str, ...] = tuple(SIGNAL_RANGES.keys())

# Campos % (armazenados em decimal no BD: 0.15 = 15%)
PCT_FIELDS: frozenset[str] = frozenset({
    "DY", "ROE", "ROIC", "ROA", "Margem_Liquida", "Margem_Operacional", "Payout",
})

# Campos onde ZERO é quase sempre dado faltante (não um valor real).
ZERO_INVALID_FIELDS: frozenset[str] = frozenset({"DY"})

# Campos críticos: sem eles, a empresa NÃO deve ser ranqueada como completa.
CRITICAL_FIELDS: tuple[str, ...] = ("ROE", "Margem_Liquida", "P/L", "P/VP", "DY")

# Colunas obrigatórias de DRE para considerar a demonstração utilizável.
DRE_REQUIRED_COLS: tuple[str, ...] = ("Receita_Liquida", "Lucro_Liquido")


# ── Helpers escalares ─────────────────────────────────────────────────────────

def to_float(v: Any) -> float | None:
    """Converte para float; retorna None em falha, NaN ou infinito."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x or x in (float("inf"), float("-inf")):
        return None
    return x


def is_missing(v: Any) -> bool:
    """True se o valor é AUSENTE (None/NaN). Zero NÃO é ausente."""
    if v is None:
        return True
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def is_valid_value(
    field: str,
    value: Any,
    zero_invalid_fields: set[str] | frozenset[str] | None = None,
) -> bool:
    """
    True quando o valor pode entrar em score/ranking sem distorcer.
    Separa explicitamente ausente de zero; aplica a faixa canônica.
    """
    x = to_float(value)
    if x is None:
        return False
    zero_invalid = set(ZERO_INVALID_FIELDS)
    if zero_invalid_fields:
        zero_invalid.update(zero_invalid_fields)
    if field in zero_invalid and abs(x) <= 1e-12:
        return False
    lo, hi = CANONICAL_RANGES.get(field, SIGNAL_RANGES.get(field, (None, None)))
    if lo is not None and x < lo:
        return False
    if hi is not None and x > hi:
        return False
    return True


def clean_value(field: str, value: Any,
                zero_invalid_fields: set[str] | frozenset[str] | None = None) -> float | None:
    """Retorna o float se válido, senão None (faixa fora → AUSENTE, nunca 0)."""
    return to_float(value) if is_valid_value(field, value, zero_invalid_fields) else None


# ── Limpeza de DataFrame ──────────────────────────────────────────────────────

def clean_multiples_frame(
    df: pd.DataFrame,
    zero_invalid_fields: set[str] | frozenset[str] | None = None,
    fields: tuple[str, ...] | list[str] | None = None,
) -> pd.DataFrame:
    """Substitui nulos mascarados/outliers por NaN, sem alterar demais colunas."""
    if df is None or df.empty:
        return df.copy() if df is not None else pd.DataFrame()
    out = df.copy()
    for field in (fields or CANONICAL_MULTIPLOS_FIELDS):
        if field not in out.columns:
            continue
        vals = pd.to_numeric(out[field], errors="coerce")
        out[field] = vals.where(
            vals.map(lambda v, f=field: is_valid_value(f, v, zero_invalid_fields))
        )
    return out


# ── Validações por domínio ────────────────────────────────────────────────────

def _ticker_col(df: pd.DataFrame) -> str | None:
    for c in ("Ticker", "ticker"):
        if c in df.columns:
            return c
    return None


def validate_multiples_data(
    df: pd.DataFrame,
    zero_invalid_fields: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    """Audita um DataFrame de múltiplos (uma linha por ticker recomendado)."""
    if df is None or df.empty:
        return {"ok": False, "motivo": "vazio", "celulas_invalidas": 0,
                "invalidos_por_campo": {}, "outliers": [], "linhas": 0}
    fields = [c for c in CANONICAL_MULTIPLOS_FIELDS if c in df.columns]
    tcol = _ticker_col(df)
    invalid_by_field: dict[str, int] = {}
    outliers: list[dict] = []
    total_invalid = 0
    for field in fields:
        raw = pd.to_numeric(df[field], errors="coerce")
        present = raw.notna()
        bad = present & ~raw.map(lambda v, f=field: is_valid_value(f, v, zero_invalid_fields))
        n_bad = int(bad.sum())
        if n_bad:
            invalid_by_field[field] = n_bad
            total_invalid += n_bad
            for idx in df.index[bad]:
                outliers.append({
                    "Ticker": (str(df.at[idx, tcol]) if tcol else str(idx)),
                    "Indicador": field,
                    "Valor": to_float(df.at[idx, field]),
                })
    return {
        "ok": total_invalid == 0,
        "linhas": int(len(df)),
        "campos_monitorados": len(fields),
        "celulas_invalidas": total_invalid,
        "invalidos_por_campo": invalid_by_field,
        "outliers": outliers,
    }


def validate_dre_data(df: pd.DataFrame) -> dict[str, Any]:
    """Verifica completude mínima de uma DRE (histórico por ano)."""
    if df is None or df.empty:
        return {"ok": False, "motivo": "vazio", "anos": 0, "faltando": list(DRE_REQUIRED_COLS)}
    faltando = [c for c in DRE_REQUIRED_COLS if c not in df.columns]
    anos = 0
    if "Data" in df.columns:
        anos = int(pd.to_datetime(df["Data"], errors="coerce").dt.year.dropna().nunique())
    vazias = {
        c: int(pd.to_numeric(df[c], errors="coerce").isna().sum())
        for c in DRE_REQUIRED_COLS if c in df.columns
    }
    return {
        "ok": not faltando and anos >= 1,
        "anos": anos,
        "faltando": faltando,
        "celulas_vazias": vazias,
    }


def validate_macro_data(macro_hist: dict | None) -> dict[str, Any]:
    """Confere se o contexto macro tem os campos essenciais por ano."""
    if not macro_hist:
        return {"ok": False, "motivo": "indisponível", "anos": 0, "faltando": ["selic", "ipca", "cambio"]}
    anos = sorted(macro_hist.keys())
    ultimo = macro_hist[anos[-1]] if anos else {}
    faltando = [k for k in ("selic", "ipca", "cambio") if k not in ultimo]
    return {"ok": not faltando, "anos": len(anos), "ultimo_ano": anos[-1] if anos else None,
            "faltando": faltando}


def validate_company_fundamentals(
    df: pd.DataFrame,
    zero_invalid_fields: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    """Validação geral de fundamentos (múltiplos) — alias de alto nível."""
    return validate_multiples_data(df, zero_invalid_fields)


# ── Campos críticos ausentes / completude ─────────────────────────────────────

def detect_missing_critical_fields(
    df: pd.DataFrame,
    critical: tuple[str, ...] | list[str] | None = None,
    zero_invalid_fields: set[str] | frozenset[str] | None = None,
) -> dict[str, list[str]]:
    """
    Retorna {ticker: [campos críticos ausentes/invalidos]}.
    ZERO em campo zero-inválido (ex.: DY) conta como AUSENTE — separa 0 de nulo.
    """
    if df is None or df.empty:
        return {}
    crit = [c for c in (critical or CRITICAL_FIELDS) if c in df.columns]
    tcol = _ticker_col(df)
    out: dict[str, list[str]] = {}
    for idx, row in df.iterrows():
        tk = str(row[tcol]) if tcol else str(idx)
        faltando = [
            f for f in crit
            if not is_valid_value(f, row.get(f), zero_invalid_fields)
        ]
        if faltando:
            out[tk] = faltando
    return out


def critical_completeness(
    df: pd.DataFrame,
    critical: tuple[str, ...] | list[str] | None = None,
    zero_invalid_fields: set[str] | frozenset[str] | None = None,
) -> dict[str, float]:
    """{ticker: fração de campos críticos presentes e válidos} (0.0–1.0)."""
    if df is None or df.empty:
        return {}
    crit = [c for c in (critical or CRITICAL_FIELDS) if c in df.columns]
    if not crit:
        return {}
    tcol = _ticker_col(df)
    out: dict[str, float] = {}
    for idx, row in df.iterrows():
        tk = str(row[tcol]) if tcol else str(idx)
        ok = sum(1 for f in crit if is_valid_value(f, row.get(f), zero_invalid_fields))
        out[tk] = ok / len(crit)
    return out


def detect_outliers(
    df: pd.DataFrame,
    zero_invalid_fields: set[str] | frozenset[str] | None = None,
) -> list[dict]:
    """Lista de valores presentes que caem fora da faixa canônica."""
    return validate_multiples_data(df, zero_invalid_fields).get("outliers", [])


def detect_duplicate_tickers(df: pd.DataFrame) -> list[str]:
    """Tickers repetidos no DataFrame (uma linha por ticker é o esperado)."""
    tcol = _ticker_col(df)
    if df is None or df.empty or tcol is None:
        return []
    s = df[tcol].astype(str).str.upper().str.replace(".SA", "", regex=False)
    dup = s[s.duplicated(keep=False)]
    return sorted(dup.unique().tolist())


def detect_missing_sector(df: pd.DataFrame) -> list[str]:
    """Tickers sem setor/segmento preenchido."""
    tcol = _ticker_col(df)
    if df is None or df.empty or tcol is None:
        return []
    sec_cols = [c for c in ("SETOR", "SEGMENTO", "setor", "segmento") if c in df.columns]
    if not sec_cols:
        return sorted(df[tcol].astype(str).unique().tolist())
    def _blank(row) -> bool:
        return all(is_missing(row.get(c)) or str(row.get(c)).strip() == "" for c in sec_cols)
    mask = df.apply(_blank, axis=1)
    return sorted(df.loc[mask, tcol].astype(str).unique().tolist())


# ── Relatório consolidado ─────────────────────────────────────────────────────

def generate_data_quality_report(
    df: pd.DataFrame,
    critical: tuple[str, ...] | list[str] | None = None,
    completeness_threshold: float = 0.6,
    zero_invalid_fields: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    """
    Relatório de qualidade para UI/auditoria. Indica empresas incompletas,
    campos críticos ausentes, indicadores suspeitos, duplicidades e o impacto
    provável (quem deveria ficar de fora do ranking).
    """
    if df is None or df.empty:
        return {"ok": False, "motivo": "vazio"}
    mult = validate_multiples_data(df, zero_invalid_fields)
    missing = detect_missing_critical_fields(df, critical, zero_invalid_fields)
    completeness = critical_completeness(df, critical, zero_invalid_fields)
    insuficientes = sorted(tk for tk, c in completeness.items() if c < completeness_threshold)
    return {
        "ok": mult["ok"] and not insuficientes,
        "linhas": mult["linhas"],
        "celulas_invalidas": mult["celulas_invalidas"],
        "invalidos_por_campo": mult["invalidos_por_campo"],
        "outliers": mult["outliers"],
        "duplicados": detect_duplicate_tickers(df),
        "sem_setor": detect_missing_sector(df),
        "campos_criticos_ausentes": missing,
        "completude": completeness,
        "empresas_insuficientes": insuficientes,
        "limiar_completude": completeness_threshold,
        "impacto": (
            f"{len(insuficientes)} empresa(s) abaixo de {completeness_threshold:.0%} de "
            f"completude crítica — não devem ser ranqueadas como completas."
            if insuficientes else "Sem empresas críticas incompletas."
        ),
    }
