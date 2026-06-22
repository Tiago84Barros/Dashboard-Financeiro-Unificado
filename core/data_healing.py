"""
core/data_healing.py
Saneamento PERSISTENTE de múltiplos B3 com validação cruzada (≥2 fontes).

Política (definida pelo usuário):
  • Nunca confiar em uma única fonte: um valor só é aceito/corrigido quando ≥2
    fontes válidas o corroboram.
  • Em divergência entre banco e web, acreditar em Fundamentus/Status Invest e
    sobrescrever o banco.
  • Nunca gravar 0 no lugar de dado faltante; valor fora de faixa coerente vira
    AUSENTE e dispara busca web.
  • Dry-run por padrão: `preview_healing` só simula; `apply_healing` grava (com
    backup + auditoria) apenas sob confirmação.

Reaproveita:
  • core/data_quality.py        — faixas/validação (fonte única)
  • core/data_reconciliacao.py  — fetch de Fundamentus/Status Invest em escala BD
  • scripts/backfill_b3_fundamentals.py — máquinas de escrita (Change/apply_changes)
  • core/database.py            — engine do banco unificado / b3_db engine
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd

import core.data_quality as _dq

logger = logging.getLogger(__name__)

# Campos saneáveis (mesmos da fonte única)
HEAL_FIELDS: tuple[str, ...] = _dq.CANONICAL_MULTIPLOS_FIELDS

# Concordância entre fontes: valores precisam ser apenas RELATIVAMENTE PRÓXIMOS,
# não idênticos. Usa tolerância RELATIVA (X% de diferença entre os dois) com um
# piso ABSOLUTO para não acusar desacordo em valores muito pequenos (perto de 0),
# onde uma diferença relativa fica artificialmente grande.
_PCT_FIELDS = _dq.PCT_FIELDS
_REL_TOL = 0.15            # 15% de diferença relativa é considerado "concordante"
_ABS_FLOOR_PCT = 0.005     # piso de 0,5 p.p. para campos % (escala decimal)
_ABS_FLOOR_RATIO = 0.10    # piso para múltiplos pequenos (P/L, P/VP, …)

_BACKUP_TABLE = "multiplos_healing_backup"
_AUDIT_TABLE = "data_healing_audit"


# ─────────────────────────────────────────────────────────────────────────────
# Núcleo PURO de resolução (testável sem banco/rede)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FieldResolution:
    field: str
    bd: float | None
    fundamentus: float | None
    status_invest: float | None
    novo: float | None          # valor proposto (None = sem proposta)
    fonte: str                  # 'banco' | 'Fundamentus+StatusInvest' | ...
    acao: str                   # 'mantido'|'corrigido'|'preenchido'|'sem_corroboracao'|'divergencia_nao_resolvida'|'sem_dado'
    n_fontes: int
    motivo: str
    brapi: float | None = None  # 3ª fonte web (opcional)


def _agree(field: str, a: float, b: float) -> bool:
    """
    True se dois valores do mesmo indicador são RELATIVAMENTE PRÓXIMOS — não
    precisam ser iguais. Concordam quando a diferença entre eles fica dentro de
    _REL_TOL (15%) do maior valor, OU dentro de um piso absoluto (para valores
    perto de zero). Ex.: ROE 18% vs 19,5% concordam; DY 3% vs 7% não.
    """
    diff = abs(a - b)
    scale = max(abs(a), abs(b))
    floor = _ABS_FLOOR_PCT if field in _PCT_FIELDS else _ABS_FLOOR_RATIO
    return diff <= max(floor, _REL_TOL * scale)


def _valid(field: str, v: Any) -> float | None:
    return _dq.clean_value(field, v)


def _web_consensus(field: str, pairs: list[tuple[str, float]]) -> tuple[float, list[str]] | None:
    """
    Maior cluster de fontes web RELATIVAMENTE PRÓXIMAS entre si (≥2).
    Retorna (média do cluster, [nomes das fontes]) ou None.
    `pairs`: lista de (nome_fonte, valor) já validados.
    """
    vals = [(n, v) for n, v in pairs if v is not None]
    if len(vals) < 2:
        return None
    best: list[tuple[str, float]] | None = None
    for _, anchor in vals:
        cluster = [(n, v) for n, v in vals if _agree(field, anchor, v)]
        if len(cluster) >= 2 and (best is None or len(cluster) > len(best)):
            best = cluster
    if not best:
        return None
    media = float(sum(v for _, v in best) / len(best))
    return media, [n for n, _ in best]


def resolve_field(
    field: str,
    bd: Any,
    fundamentus: Any,
    status_invest: Any,
    brapi: Any = None,
) -> FieldResolution:
    """
    Decide o valor saneado de um campo cruzando banco + até 3 fontes web
    (Fundamentus, Status Invest, brapi.dev).

    Regra:
      • Coleta apenas valores VÁLIDOS (faixa coerente; 0 em DY = inválido).
      • Exige ≥2 fontes válidas para QUALQUER gravação (corroboração).
      • Banco válido e concordando com ≥1 web → mantém banco.
      • Banco inválido/ausente OU divergente → só sobrescreve se ≥2 fontes web
        concordarem entre si (relativamente próximas); usa a média do cluster.
        Caso contrário, não grava e marca para revisão.
    """
    bd_v = _valid(field, bd)
    fu_v = _valid(field, fundamentus)
    si_v = _valid(field, status_invest)
    br_v = _valid(field, brapi)
    web_pairs = [("Fundamentus", fu_v), ("StatusInvest", si_v), ("brapi", br_v)]
    web = [v for _, v in web_pairs if v is not None]
    n_valid = sum(v is not None for v in (bd_v, fu_v, si_v, br_v))

    def _res(novo, fonte, acao, motivo):
        return FieldResolution(field, bd_v, fu_v, si_v, novo, fonte, acao, n_valid, motivo, brapi=br_v)

    # Sem corroboração possível (<2 fontes válidas) → nunca grava
    if n_valid < 2:
        if bd_v is not None:
            return _res(None, "banco", "sem_corroboracao",
                        "Apenas 1 fonte válida — mantém banco, sem gravar (precisa ≥2).")
        return _res(None, "nenhuma", "sem_dado",
                    "Nenhuma/uma fonte válida — sem proposta; revisar manualmente.")

    # Banco válido: concorda com alguma web?
    if bd_v is not None:
        if any(_agree(field, bd_v, w) for w in web):
            return _res(None, "banco", "mantido",
                        "Banco corroborado por fonte web (sem divergência).")
        cons = _web_consensus(field, web_pairs)
        if cons is not None:
            novo, fontes = cons
            return _res(novo, "+".join(fontes), "corrigido",
                        f"Banco divergente; {', '.join(fontes)} concordam → sobrescreve.")
        return _res(None, "banco", "divergencia_nao_resolvida",
                    "Banco diverge de web, mas web não corrobora (≥2 concordantes). Revisar.")

    # Banco ausente/inválido: preenche se ≥2 web concordam
    cons = _web_consensus(field, web_pairs)
    if cons is not None:
        novo, fontes = cons
        return _res(novo, "+".join(fontes), "preenchido",
                    f"Banco ausente/inválido; {', '.join(fontes)} concordam → preenche.")
    return _res(None, "web", "divergencia_nao_resolvida",
                "Banco ausente e web sem corroboração suficiente. Revisar.")


def resolve_ticker(
    sources: dict[str, dict[str, Any]],
    fields: tuple[str, ...] = HEAL_FIELDS,
) -> list[FieldResolution]:
    """
    sources = {'banco':{...}, 'fundamentus':{...}, 'status_invest':{...}, 'brapi':{...}}
    (todos em escala BD: % em decimal). Retorna uma resolução por campo.
    """
    bd = sources.get("banco", {}) or {}
    fu = sources.get("fundamentus", {}) or {}
    si = sources.get("status_invest", {}) or {}
    br = sources.get("brapi", {}) or {}
    return [resolve_field(f, bd.get(f), fu.get(f), si.get(f), br.get(f)) for f in fields]


def proposals_only(resolutions: list[FieldResolution]) -> list[FieldResolution]:
    """Apenas as resoluções que de fato propõem gravar (novo is not None)."""
    return [r for r in resolutions if r.novo is not None]


# ─────────────────────────────────────────────────────────────────────────────
# IO: coleta de fontes (banco + web em escala BD)
# ─────────────────────────────────────────────────────────────────────────────

def _collect_sources(tickers: tuple[str, ...]) -> dict[str, dict[str, dict[str, Any]]]:
    """Retorna {ticker: {'banco':..., 'fundamentus':..., 'status_invest':...}} em escala BD."""
    from core import b3_db as _db
    from core import data_reconciliacao as _recon
    from core import fundamentus as _fund
    from core import status_invest as _si
    try:
        from core import brapi as _brapi
    except Exception:
        _brapi = None

    out: dict[str, dict[str, dict[str, Any]]] = {}
    # Banco em lote
    df_all = _db.load_multiplos_todos()
    bd_by: dict[str, dict] = {}
    if not df_all.empty and "Ticker" in df_all.columns:
        for _, row in df_all.iterrows():
            bd_by[str(row["Ticker"]).upper().replace(".SA", "")] = row.to_dict()
    # Fundamentus em lote (escala Fundamentus → converte p/ BD)
    try:
        fund_raw = _fund.batch_stocks(tickers)
    except Exception:
        fund_raw = {}
    for tk in tickers:
        tkc = tk.upper().replace(".SA", "")
        fu_db = _recon._fund_to_db_values(fund_raw.get(tkc, {}) or {})
        try:
            si_db = {k: _recon._to_float(v) for k, v in (_si.fetch_stock_si(tkc) or {}).items()
                     if k not in ("_fontes", "_alertas")}
        except Exception:
            si_db = {}
        # brapi.dev: 3ª fonte web (P/L + DY trailing). Falha/sem token → {}.
        br_db: dict[str, Any] = {}
        if _brapi is not None:
            try:
                q = _brapi.fetch_quote(tkc, range_="1y", interval="1mo")
                if q:
                    br_db = _brapi.current_fundamentals(q)
            except Exception:
                br_db = {}
        out[tkc] = {"banco": bd_by.get(tkc, {}), "fundamentus": fu_db,
                    "status_invest": si_db, "brapi": br_db}
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Preview (dry-run) e Apply (gravação)
# ─────────────────────────────────────────────────────────────────────────────

def collect_and_resolve(
    tickers: list[str] | tuple[str, ...],
) -> dict[str, list[FieldResolution]]:
    """
    Coleta as fontes (banco + Fundamentus + Status Invest) UMA vez e resolve
    TODOS os campos por ticker. Base comum para preview, gravação e score —
    evita raspar a web mais de uma vez por execução.
    """
    tks = tuple(dict.fromkeys(str(t).upper().replace(".SA", "") for t in (tickers or []) if t))
    if not tks:
        return {}
    sources = _collect_sources(tks)
    return {tk: resolve_ticker(sources.get(tk, {})) for tk in tks}


def resolutions_to_preview_df(
    resolutions_by_ticker: dict[str, list[FieldResolution]],
    include_kept: bool = False,
) -> pd.DataFrame:
    """Converte resoluções em DataFrame de preview. Por padrão omite 'mantido'."""
    rows: list[dict] = []
    for tk, resolutions in (resolutions_by_ticker or {}).items():
        for r in resolutions:
            if not include_kept and r.acao == "mantido":
                continue
            rows.append({
                "Ticker": tk, "Indicador": r.field,
                "Banco": r.bd, "Fundamentus": r.fundamentus, "StatusInvest": r.status_invest,
                "Brapi": r.brapi,
                "Novo": r.novo, "Fonte": r.fonte, "Acao": r.acao,
                "NFontes": r.n_fontes, "Motivo": r.motivo,
            })
    return pd.DataFrame(rows)


def preview_healing(tickers: list[str] | tuple[str, ...]) -> pd.DataFrame:
    """
    Dry-run: para cada ticker/campo, mostra fontes e a ação proposta.
    NÃO grava nada. Colunas: Ticker, Indicador, Banco, Fundamentus, StatusInvest,
    Novo, Fonte, Acao, NFontes, Motivo.
    """
    return resolutions_to_preview_df(collect_and_resolve(tickers))


def _ensure_aux_tables(conn) -> None:
    from sqlalchemy import text
    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {_BACKUP_TABLE} (
            id BIGSERIAL PRIMARY KEY,
            run_ts TIMESTAMPTZ NOT NULL,
            ticker TEXT NOT NULL, data DATE,
            indicador TEXT NOT NULL, valor_antigo DOUBLE PRECISION
        )
    """))
    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {_AUDIT_TABLE} (
            id BIGSERIAL PRIMARY KEY,
            run_ts TIMESTAMPTZ NOT NULL,
            ticker TEXT NOT NULL, data DATE,
            indicador TEXT NOT NULL,
            valor_antigo DOUBLE PRECISION, valor_novo DOUBLE PRECISION,
            fonte TEXT, acao TEXT, n_fontes INTEGER, motivo TEXT
        )
    """))


def apply_healing(
    preview_df: pd.DataFrame,
    run_ts: str | None = None,
) -> dict[str, Any]:
    """
    Grava no banco SOMENTE as linhas com ação de gravação (corrigido/preenchido)
    e Novo != None. Registra backup (valor antigo) e auditoria antes de escrever.
    Retorna {gravados, backupados, erro}.
    """
    from sqlalchemy import text
    from core.database import get_engine

    if preview_df is None or preview_df.empty:
        return {"gravados": 0, "backupados": 0, "erro": None, "motivo": "nada a aplicar"}

    grav = preview_df[
        preview_df["Acao"].isin(["corrigido", "preenchido"]) & preview_df["Novo"].notna()
    ].copy()
    if grav.empty:
        return {"gravados": 0, "backupados": 0, "erro": None, "motivo": "nenhuma proposta gravável"}

    engine = get_engine()
    if engine is None:
        return {"gravados": 0, "backupados": 0, "erro": "Banco não conectado"}

    ts = run_ts or datetime.now(timezone.utc).isoformat(timespec="seconds")
    gravados = 0
    backupados = 0
    try:
        with engine.begin() as conn:
            _ensure_aux_tables(conn)
            for _, r in grav.iterrows():
                tk = str(r["Ticker"])
                ind = str(r["Indicador"])
                novo = float(r["Novo"])
                # data da linha mais recente do ticker
                dt = conn.execute(
                    text('SELECT MAX(data) FROM public.multiplos WHERE "Ticker" = :tk OR "Ticker" = :tks'),
                    {"tk": tk, "tks": f"{tk}.SA"},
                ).scalar()
                if dt is None:
                    continue
                old = conn.execute(
                    text(f'SELECT "{ind}" FROM public.multiplos WHERE ("Ticker" = :tk OR "Ticker" = :tks) AND data = :dt'),
                    {"tk": tk, "tks": f"{tk}.SA", "dt": dt},
                ).scalar()
                # backup + auditoria
                conn.execute(text(f"""
                    INSERT INTO {_BACKUP_TABLE} (run_ts, ticker, data, indicador, valor_antigo)
                    VALUES (:ts, :tk, :dt, :ind, :old)
                """), {"ts": ts, "tk": tk, "dt": dt, "ind": ind,
                       "old": float(old) if old is not None else None})
                backupados += 1
                conn.execute(text(f"""
                    INSERT INTO {_AUDIT_TABLE}
                      (run_ts, ticker, data, indicador, valor_antigo, valor_novo, fonte, acao, n_fontes, motivo)
                    VALUES (:ts, :tk, :dt, :ind, :old, :new, :fonte, :acao, :nf, :motivo)
                """), {"ts": ts, "tk": tk, "dt": dt, "ind": ind,
                       "old": float(old) if old is not None else None, "new": novo,
                       "fonte": str(r.get("Fonte", "")), "acao": str(r.get("Acao", "")),
                       "nf": int(r.get("NFontes", 0) or 0), "motivo": str(r.get("Motivo", ""))})
                # escrita
                conn.execute(
                    text(f'UPDATE public.multiplos SET "{ind}" = :v WHERE ("Ticker" = :tk OR "Ticker" = :tks) AND data = :dt'),
                    {"v": novo, "tk": tk, "tks": f"{tk}.SA", "dt": dt},
                )
                gravados += 1
        # invalida caches de leitura
        try:
            from core import b3_db as _db
            _db.load_multiplos_todos.clear()
        except Exception:
            pass
        return {"gravados": gravados, "backupados": backupados, "erro": None, "run_ts": ts}
    except Exception as exc:
        logger.warning("apply_healing falhou: %s", exc)
        return {"gravados": gravados, "backupados": backupados, "erro": str(exc)}
