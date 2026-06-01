"""
core/cash_reserve.py — Reserva de Fluxo de Caixa (colchão de liquidez).

Camada LÓGICA de transição entre o fluxo mensal e os investimentos
patrimoniais de longo prazo. NÃO é renda, despesa nem aporte patrimonial.

Ideia:
  • Mês superavitário (entradas − despesas − investido > 0): o excedente
    (total ou parte, conforme ajuste manual) entra na Reserva.
  • Mês deficitário (resultado < 0): a Reserva cobre o déficit até o limite
    do seu saldo, evitando resgatar investimentos de longo prazo. O que a
    Reserva não cobrir vira "déficit real".

Assim um mês pode ser OPERACIONALMENTE negativo, mas FINANCEIRAMENTE saudável
se o déficit foi coberto por uma reserva criada exatamente para isso.

O cálculo é cronológico (mês a mês), mantendo o saldo da reserva acumulado.

Modelo HÍBRIDO: por padrão a reserva é alimentada automaticamente com todo o
excedente, mas o usuário pode ajustar manualmente quanto de cada mês vai para
a reserva (o restante fica como "sobra livre"). Ajustes e saldo inicial são
persistidos (tabela aditiva) com fallback para a sessão.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

try:
    from sqlalchemy import text
    from core.config import settings
    from core.database import get_engine
except Exception:  # pragma: no cover
    text = None
    settings = None
    get_engine = lambda: None  # noqa: E731


_TABELA = "cash_reserve_config"
_KEY_INICIAL = "__saldo_inicial__"


# ──────────────────────────────────────────────────────────────────────────
# Núcleo: cálculo cronológico da reserva (função pura, testável)
# ──────────────────────────────────────────────────────────────────────────

def compute_reserve_flow(
    serie: pd.DataFrame,
    ajustes: dict[str, float] | None = None,
    saldo_inicial: float = 0.0,
) -> pd.DataFrame:
    """
    Computa a mecânica da Reserva de Fluxo de Caixa mês a mês.

    Args:
      serie: DataFrame com colunas 'mes' (YYYY-MM), 'entradas', 'despesas',
             'investido' (saída de core.controle.serie_mensal).
      ajustes: {mes: contribuicao_manual} — quanto do excedente daquele mês vai
               para a reserva (apenas meses superavitários). Ausente → todo o
               excedente. Limitado a [0, excedente].
      saldo_inicial: saldo da reserva antes do primeiro mês da série.

    Returns:
      DataFrame com colunas:
        mes, resultado_operacional, aporte_reserva, resgate_reserva,
        saldo_reserva, deficit_coberto, deficit_real, sobra_livre, status
    """
    ajustes = ajustes or {}
    if serie is None or serie.empty:
        return pd.DataFrame(columns=[
            "mes", "resultado_operacional", "aporte_reserva", "resgate_reserva",
            "saldo_reserva", "deficit_coberto", "deficit_real", "sobra_livre", "status",
        ])

    df = serie.copy()
    for c in ("entradas", "despesas", "investido"):
        if c not in df.columns:
            df[c] = 0.0
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    df = df.sort_values("mes")

    saldo = float(saldo_inicial)
    linhas: list[dict] = []
    for _, r in df.iterrows():
        mes = str(r["mes"])
        resultado = float(r["entradas"]) - float(r["despesas"]) - float(r["investido"])

        aporte = resgate = deficit_coberto = deficit_real = sobra_livre = 0.0

        if resultado >= 0:
            aporte_default = resultado
            aporte = float(ajustes.get(mes, aporte_default))
            aporte = max(0.0, min(aporte, resultado))   # não reserva mais que o excedente
            sobra_livre = resultado - aporte
            status = "Superávit"
        else:
            necessidade = -resultado
            resgate = min(necessidade, max(saldo, 0.0))   # reserva cobre até o saldo
            deficit_coberto = resgate
            deficit_real = necessidade - resgate
            status = "Coberto pela reserva" if deficit_real <= 1e-9 else "Déficit real"

        saldo += aporte - resgate
        linhas.append({
            "mes": mes,
            "resultado_operacional": round(resultado, 2),
            "aporte_reserva": round(aporte, 2),
            "resgate_reserva": round(resgate, 2),
            "saldo_reserva": round(saldo, 2),
            "deficit_coberto": round(deficit_coberto, 2),
            "deficit_real": round(deficit_real, 2),
            "sobra_livre": round(sobra_livre, 2),
            "status": status,
        })

    return pd.DataFrame(linhas)


@dataclass
class ReserveSummary:
    saldo_atual:        float
    aporte_mes:         float
    resgate_mes:        float
    resultado_mes:      float
    status_mes:         str
    deficit_real_mes:   float
    meses_cobertos:     int   # nº de meses deficitários cobertos pela reserva
    meses_deficit_real: int


def reserve_summary(df_flow: pd.DataFrame, mes_atual: str | None = None) -> ReserveSummary:
    """Resumo da reserva: saldo atual + situação do mês corrente."""
    if df_flow is None or df_flow.empty:
        return ReserveSummary(0, 0, 0, 0, "Sem dados", 0, 0, 0)

    saldo_atual = float(df_flow["saldo_reserva"].iloc[-1])
    if mes_atual and (df_flow["mes"] == mes_atual).any():
        row = df_flow[df_flow["mes"] == mes_atual].iloc[0]
    else:
        row = df_flow.iloc[-1]

    cobertos = int((df_flow["status"] == "Coberto pela reserva").sum())
    deficit_real = int((df_flow["status"] == "Déficit real").sum())

    return ReserveSummary(
        saldo_atual=saldo_atual,
        aporte_mes=float(row["aporte_reserva"]),
        resgate_mes=float(row["resgate_reserva"]),
        resultado_mes=float(row["resultado_operacional"]),
        status_mes=str(row["status"]),
        deficit_real_mes=float(row["deficit_real"]),
        meses_cobertos=cobertos,
        meses_deficit_real=deficit_real,
    )


# ──────────────────────────────────────────────────────────────────────────
# Persistência (tabela aditiva + fallback para sessão)
# ──────────────────────────────────────────────────────────────────────────

def _uid() -> str | None:
    try:
        return str(settings.OWNER_USER_ID) if settings and settings.OWNER_USER_ID else None
    except Exception:
        return None


def _ensure_table(conn) -> None:
    """Cria a tabela de configuração da reserva se não existir (aditivo)."""
    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS public.{_TABELA} (
            user_id     TEXT NOT NULL,
            chave       TEXT NOT NULL,
            valor       NUMERIC(18,2),
            observacao  TEXT,
            updated_at  TIMESTAMPTZ DEFAULT now(),
            PRIMARY KEY (user_id, chave)
        )
    """))


def load_reserve_config() -> tuple[float, dict[str, float]]:
    """Carrega (saldo_inicial, {mes: contribuicao_manual}) do banco ou da sessão."""
    engine = get_engine()
    uid = _uid()
    if engine is None or uid is None or text is None:
        return _session_config()
    try:
        # Somente leitura — não cria tabela no render (DDL só ocorre no save).
        with engine.connect() as conn:
            rows = conn.execute(
                text(f"SELECT chave, valor FROM public.{_TABELA} WHERE user_id = :uid"),
                {"uid": uid},
            ).mappings().all()
        saldo_inicial = 0.0
        ajustes: dict[str, float] = {}
        for r in rows:
            if r["chave"] == _KEY_INICIAL:
                saldo_inicial = float(r["valor"] or 0)
            elif r["valor"] is not None:
                ajustes[str(r["chave"])] = float(r["valor"])
        return saldo_inicial, ajustes
    except Exception:
        return _session_config()


def save_reserve_value(chave: str, valor: float, observacao: str = "") -> bool:
    """Upsert de um ajuste (chave = mes 'YYYY-MM' ou _KEY_INICIAL). Retorna sucesso."""
    engine = get_engine()
    uid = _uid()
    if engine is None or uid is None or text is None:
        _session_set(chave, valor)
        return False
    try:
        with engine.begin() as conn:
            _ensure_table(conn)
            conn.execute(text(f"""
                INSERT INTO public.{_TABELA} (user_id, chave, valor, observacao, updated_at)
                VALUES (:uid, :chave, :valor, :obs, now())
                ON CONFLICT (user_id, chave)
                DO UPDATE SET valor = EXCLUDED.valor,
                              observacao = EXCLUDED.observacao,
                              updated_at = now()
            """), {"uid": uid, "chave": chave, "valor": float(valor), "obs": observacao})
        _session_set(chave, valor)
        return True
    except Exception:
        _session_set(chave, valor)
        return False


def save_initial_balance(valor: float) -> bool:
    return save_reserve_value(_KEY_INICIAL, valor, "saldo inicial da reserva")


# ── Fallback de sessão (quando não há banco) ──────────────────────────────

def _session_store() -> dict:
    try:
        import streamlit as st
        return st.session_state.setdefault("_cash_reserve_cfg", {})
    except Exception:
        return {}


def _session_set(chave: str, valor: float) -> None:
    _session_store()[chave] = float(valor)


def _session_config() -> tuple[float, dict[str, float]]:
    store = dict(_session_store())
    saldo = float(store.pop(_KEY_INICIAL, 0.0) or 0.0)
    ajustes = {k: float(v) for k, v in store.items()}
    return saldo, ajustes
