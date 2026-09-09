"""Sustentabilidade histórica da distribuição — a qualidade da política de
dividendos da empresa, medida na janela de exercícios anuais.

Definição única, no molde de `core/b3_slopes.py`: a tela de Empresas B3, a
Criação de Portfólio e a Análise do Portfólio precisam da MESMA leitura de
sustentabilidade, senão a mesma empresa recebe duas notas e nenhuma das duas é
auditável. O módulo é puro (pandas/numpy), sem Streamlit e sem banco.

O princípio que ele implementa: vale a qualidade histórica, não o período
isolado. Um payout de 318% no TTM com mediana de 63,5% em oito anos é um
exercício fora da curva, não uma política insustentável — 79% das empresas que
o diagnóstico antigo condenava pelo TTM têm payout mediano abaixo de 100%.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

JANELA_ANOS = 8
MIN_ANOS = 3

# Faixa de dois lados. Distribuir quase nada e distribuir muito acima do lucro
# são as duas formas de o dividendo não ser um dividendo sustentável — por isso
# a nota cai nas DUAS pontas, e não apenas acima do teto.
PISO, OTIMO_LO, OTIMO_HI, TETO = 0.05, 0.25, 0.80, 1.30

# Contradição interna da fonte: a empresa não pode distribuir (DY acima de
# 0,5%) e não distribuir (payout de até 1%) no mesmo exercício. O ano sai do
# cálculo como AUSÊNCIA — punir por dado incoerente é medir a fonte errada.
DY_MIN_COERENCIA = 0.005
PAYOUT_MAX_COERENCIA = 0.01

__all__ = [
    "JANELA_ANOS", "MIN_ANOS", "PISO", "OTIMO_LO", "OTIMO_HI", "TETO",
    "sustentabilidade_do_ano", "leitura_da_serie", "enrich_com_renda_sustentavel",
]


def sustentabilidade_do_ano(payout) -> float:
    """Nota [0,1] de sustentabilidade da distribuição de UM exercício."""
    try:
        p = float(payout)
    except (TypeError, ValueError):
        return 0.0
    if not np.isfinite(p):
        return 0.0
    if p <= PISO or p >= TETO:
        return 0.0
    if OTIMO_LO <= p <= OTIMO_HI:
        return 1.0
    if p < OTIMO_LO:
        return (p - PISO) / (OTIMO_LO - PISO)
    return (TETO - p) / (TETO - OTIMO_HI)


def _anos_observados(df_hist: pd.DataFrame) -> list[float]:
    """Payouts anuais coerentes da janela, do mais antigo para o mais recente."""
    if df_hist is None or df_hist.empty or "Payout" not in df_hist.columns:
        return []
    df = df_hist.copy()
    if "Data" in df.columns:
        df = df.sort_values("Data")
    payout = pd.to_numeric(df["Payout"], errors="coerce")
    dy = (pd.to_numeric(df["DY"], errors="coerce") if "DY" in df.columns
          else pd.Series(np.nan, index=df.index))
    incoerente = (dy > DY_MIN_COERENCIA) & (payout <= PAYOUT_MAX_COERENCIA)
    payout = payout[~incoerente].dropna()
    return [float(v) for v in payout.tolist()[-JANELA_ANOS:]]


def leitura_da_serie(df_hist: pd.DataFrame) -> dict:
    """Sustentabilidade média, payout mediano e nº de anos observados."""
    anos = _anos_observados(df_hist)
    n = len(anos)
    if n < MIN_ANOS:
        return {"payout_sustentabilidade": None, "payout_mediano_hist": None,
                "n_anos_payout": n}
    notas = [sustentabilidade_do_ano(p) for p in anos]
    return {
        "payout_sustentabilidade": float(np.mean(notas)),
        "payout_mediano_hist": float(np.median(anos)),
        "n_anos_payout": n,
    }


def enrich_com_renda_sustentavel(
    df_mult: pd.DataFrame,
    hist_batch: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """Acrescenta a sustentabilidade histórica ao cross-section de múltiplos."""
    if df_mult is None or df_mult.empty or not hist_batch:
        return df_mult
    dados: dict[str, dict] = {}
    for tk, df_h in hist_batch.items():
        leitura = leitura_da_serie(df_h)
        dados[str(tk)] = {
            "payout_sustentabilidade": leitura["payout_sustentabilidade"],
            "payout_mediano_hist": leitura["payout_mediano_hist"],
            "n_anos_payout": leitura["n_anos_payout"],
        }
    if not dados:
        return df_mult
    df_rs = pd.DataFrame.from_dict(dados, orient="index")
    df_rs.index.name = "Ticker"
    out = df_mult.merge(df_rs.reset_index(), on="Ticker", how="left")
    # dy_sustentavel é NaN quando a sustentabilidade é NaN: nunca cai no DY
    # bruto. Um fallback que só preenche lacuna nunca contradiz — e contradizer
    # o DY divulgado é justamente o objetivo desta métrica.
    dy = (pd.to_numeric(out["DY"], errors="coerce") if "DY" in out.columns
          else pd.Series(np.nan, index=out.index))
    out["dy_sustentavel"] = dy * pd.to_numeric(
        out["payout_sustentabilidade"], errors="coerce")
    return out
