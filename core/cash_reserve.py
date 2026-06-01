"""
core/cash_reserve.py — Reserva de Fluxo de Caixa (colchão de liquidez).

Camada LÓGICA de transição entre o fluxo mensal e os investimentos
patrimoniais de longo prazo. NÃO é renda, despesa nem aporte patrimonial —
é o excedente de meses bons separado para cobrir meses ruins.

Modelo (escolha do usuário): DERIVADA AUTOMÁTICA + camada lógica.
  • Mês superavitário (receitas − despesas − investido > 0): o excedente entra
    na Reserva (por padrão, todo ele).
  • Mês deficitário (resultado < 0): a Reserva cobre o déficit até o limite do
    seu saldo. O que a Reserva não cobrir vira "déficit real".

Assim um mês pode ser OPERACIONALMENTE negativo, mas FINANCEIRAMENTE saudável
se o déficit foi coberto por uma reserva criada exatamente para isso.

O cálculo é cronológico (mês a mês). Não há persistência em banco: a reserva é
derivada do próprio histórico de fluxo de caixa (camada lógica). O único ajuste
opcional é o "saldo inicial" (colchão anterior ao histórico exibido), mantido
em sessão.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


_COLS = [
    "mes", "resultado_operacional", "aporte_reserva", "resgate_reserva",
    "saldo_reserva", "deficit_coberto", "deficit_real", "sobra_livre", "status",
]


# ──────────────────────────────────────────────────────────────────────────
# Adaptador: histórico de fluxo de caixa → série mensal normalizada
# ──────────────────────────────────────────────────────────────────────────

def serie_from_cashflow(historico: list[dict] | None) -> pd.DataFrame:
    """
    Converte a saída de core.investimentos.get_cashflow_mensal() na série usada
    pelo cálculo da reserva.

    Cada item de `historico` tem: ano, mes, receitas, despesas, investimentos.
    Retorna DataFrame com colunas: mes (YYYY-MM), entradas, despesas, investido.
    """
    rows: list[dict] = []
    for h in historico or []:
        try:
            ano = int(h.get("ano"))
            mes = int(h.get("mes"))
        except (TypeError, ValueError):
            continue
        rows.append({
            "mes": f"{ano:04d}-{mes:02d}",
            "entradas": float(h.get("receitas") or 0.0),
            "despesas": float(h.get("despesas") or 0.0),
            "investido": float(h.get("investimentos") or 0.0),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("mes").reset_index(drop=True)
    return df


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
      serie: DataFrame com 'mes' (YYYY-MM), 'entradas', 'despesas', 'investido'.
      ajustes: {mes: contribuicao_manual} — quanto do excedente daquele mês vai
               para a reserva (apenas meses superavitários). Ausente → todo o
               excedente. Limitado a [0, excedente].
      saldo_inicial: saldo da reserva antes do primeiro mês da série.

    Returns:
      DataFrame com: mes, resultado_operacional, aporte_reserva, resgate_reserva,
      saldo_reserva, deficit_coberto, deficit_real, sobra_livre, status.
    """
    ajustes = ajustes or {}
    if serie is None or serie.empty:
        return pd.DataFrame(columns=_COLS)

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
            aporte = float(ajustes.get(mes, resultado))
            aporte = max(0.0, min(aporte, resultado))
            sobra_livre = resultado - aporte
            status = "Superávit"
        else:
            necessidade = -resultado
            resgate = min(necessidade, max(saldo, 0.0))
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
    meses_cobertos:     int
    meses_deficit_real: int


def reserve_summary(df_flow: pd.DataFrame, mes_atual: str | None = None) -> ReserveSummary:
    """Resumo: saldo atual + situação do mês corrente (ou do último mês)."""
    if df_flow is None or df_flow.empty:
        return ReserveSummary(0, 0, 0, 0, "Sem dados", 0, 0, 0)

    saldo_atual = float(df_flow["saldo_reserva"].iloc[-1])
    if mes_atual and (df_flow["mes"] == mes_atual).any():
        row = df_flow[df_flow["mes"] == mes_atual].iloc[0]
    else:
        row = df_flow.iloc[-1]

    return ReserveSummary(
        saldo_atual=saldo_atual,
        aporte_mes=float(row["aporte_reserva"]),
        resgate_mes=float(row["resgate_reserva"]),
        resultado_mes=float(row["resultado_operacional"]),
        status_mes=str(row["status"]),
        deficit_real_mes=float(row["deficit_real"]),
        meses_cobertos=int((df_flow["status"] == "Coberto pela reserva").sum()),
        meses_deficit_real=int((df_flow["status"] == "Déficit real").sum()),
    )
