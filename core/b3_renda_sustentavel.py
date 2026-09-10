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
    "fracao_pl_em_queda_com_lucro", "enrich_com_historico_patrimonial",
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
    # Infinitos não são observações financeiras válidas; como qualquer lacuna,
    # não contam para a janela nem participam da mediana.
    payout = payout.where(np.isfinite(payout))
    dy = dy.where(np.isfinite(dy))
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


def _num_ou_none(valor) -> float | None:
    """Número finito, preservando ausência como ``None``."""
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    return numero if np.isfinite(numero) else None


def fracao_pl_em_queda_com_lucro(serie: list[dict]) -> tuple[float | None, int]:
    """Fração dos pares consecutivos com PL caindo e lucro positivo.

    PL e lucro ausentes em qualquer ponta necessária tornam o par ausente, e
    não zero. Assim não se fabrica uma queda patrimonial a partir de lacuna da
    fonte. O lucro avaliado é o do exercício mais recente do par.
    """
    linhas = sorted(serie or [], key=lambda linha: linha.get("ano") or 0)
    pares = 0
    quedas_com_lucro = 0
    for anterior, atual in zip(linhas, linhas[1:]):
        pl_anterior = _num_ou_none(anterior.get("pl_mi"))
        pl_atual = _num_ou_none(atual.get("pl_mi"))
        lucro_atual = _num_ou_none(atual.get("lucro_mi"))
        if pl_anterior is None or pl_atual is None or lucro_atual is None:
            continue
        pares += 1
        if pl_atual < pl_anterior and lucro_atual > 0:
            quedas_com_lucro += 1
    if pares == 0:
        return None, 0
    return quedas_com_lucro / pares, pares


def enrich_com_historico_patrimonial(
    df_mult: pd.DataFrame,
    series_batch: dict[str, list[dict]],
) -> pd.DataFrame:
    """Acrescenta a fração histórica de PL em queda ao cross-section."""
    if df_mult is None or df_mult.empty or not series_batch:
        return df_mult
    dados: dict[str, dict] = {}
    for ticker, serie in series_batch.items():
        fracao, n_pares = fracao_pl_em_queda_com_lucro(serie)
        dados[str(ticker)] = {
            "pl_queda_com_lucro_frac": fracao,
            "n_pares_pl": n_pares,
        }
    if not dados:
        return df_mult
    df_pl = pd.DataFrame.from_dict(dados, orient="index")
    df_pl.index.name = "Ticker"
    return df_mult.merge(df_pl.reset_index(), on="Ticker", how="left")
