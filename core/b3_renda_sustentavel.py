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

As constantes da banda e ``sustentabilidade_do_ano`` moraram aqui até a rodada
de correção 1 da task 1, quando se descobriu que a mesma faixa estava
duplicada, verbatim, em ``core/us_renda_sustentavel.py`` — e as duas cópias já
haviam divergido na política de ausência (C-1). Ambas agora importam de
``core/renda_sustentavel_banda.py``. ``leitura_da_serie`` só recebe payouts já
filtrados como finitos por ``_anos_observados``, então este módulo não muda de
comportamento observável: o ramo de ausência da banda compartilhada nunca era
alcançável a partir daqui.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.renda_sustentavel_banda import (
    JANELA_ANOS,
    MIN_ANOS,
    OTIMO_HI,
    OTIMO_LO,
    PISO,
    TETO,
    sustentabilidade_do_ano,
)

# Contradição interna da fonte: a empresa não pode distribuir (DY acima de
# 0,5%) e não distribuir (payout de até 1%) no mesmo exercício. O ano sai do
# cálculo como AUSÊNCIA — punir por dado incoerente é medir a fonte errada.
DY_MIN_COERENCIA = 0.005
PAYOUT_MAX_COERENCIA = 0.01

__all__ = [
    "JANELA_ANOS", "MIN_ANOS", "PISO", "OTIMO_LO", "OTIMO_HI", "TETO",
    "sustentabilidade_do_ano", "leitura_da_serie", "enrich_com_renda_sustentavel",
    "fracao_pl_em_queda_com_lucro", "enrich_com_historico_patrimonial",
    "enrich_decision_universe",
]


def _anos_observados(df_hist: pd.DataFrame) -> list[float]:
    """Payouts coerentes dos últimos JANELA_ANOS exercícios, do mais antigo
    para o mais recente.

    A janela é sobre EXERCÍCIOS, não sobre observações válidas: primeiro
    recorta os últimos ``JANELA_ANOS`` exercícios da série ordenada, e só
    então descarta incoerentes e lacunas dentro dessa janela (I-1). Cortar
    depois de filtrar faz uma série com muitos anos incoerentes recentes
    "empurrar" a janela para trás e pontuar com dados antigos, sem nenhum
    sinal recente.
    """
    if df_hist is None or df_hist.empty or "Payout" not in df_hist.columns:
        return []
    df = df_hist.copy()
    if "Data" in df.columns:
        df = df.sort_values("Data")
    df = df.tail(JANELA_ANOS)
    payout = pd.to_numeric(df["Payout"], errors="coerce")
    dy = (pd.to_numeric(df["DY"], errors="coerce") if "DY" in df.columns
          else pd.Series(np.nan, index=df.index))
    # Infinitos não são observações financeiras válidas; como qualquer lacuna,
    # não contam para a janela nem participam da mediana.
    payout = payout.where(np.isfinite(payout))
    dy = dy.where(np.isfinite(dy))
    incoerente = (dy > DY_MIN_COERENCIA) & (payout <= PAYOUT_MAX_COERENCIA)
    payout = payout[~incoerente].dropna()
    return [float(v) for v in payout.tolist()]


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


_COLUNAS_CONTRATO = (
    "payout_sustentabilidade", "payout_mediano_hist", "n_anos_payout",
    "dy_sustentavel",
)


def _com_colunas_de_contrato(df_mult: pd.DataFrame) -> pd.DataFrame:
    """Garante as quatro colunas do contrato de saída, mesmo sem histórico.

    ``enrich_com_renda_sustentavel`` tinha saídas antecipadas que devolviam o
    quadro cru, sem estas colunas (I-3). Isso não quebrava porque o scorer
    caía no neutro na ausência, mas é justamente o caminho de "nenhum
    histórico" — alcançável em produção quando o loader devolve vazio (ex.:
    `core/b3_data.py::_financeiro` engole exceção e devolve `{}`). O contrato
    do quadro de saída não pode depender de ter havido histórico.
    """
    if df_mult is None:
        return df_mult
    out = df_mult.copy()
    for coluna in _COLUNAS_CONTRATO:
        if coluna not in out.columns:
            out[coluna] = np.nan
    return out


def enrich_com_renda_sustentavel(
    df_mult: pd.DataFrame,
    hist_batch: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """Acrescenta a sustentabilidade histórica ao cross-section de múltiplos."""
    if df_mult is None or df_mult.empty or not hist_batch:
        return _com_colunas_de_contrato(df_mult)
    dados: dict[str, dict] = {}
    for tk, df_h in hist_batch.items():
        leitura = leitura_da_serie(df_h)
        dados[str(tk)] = {
            "payout_sustentabilidade": leitura["payout_sustentabilidade"],
            "payout_mediano_hist": leitura["payout_mediano_hist"],
            "n_anos_payout": leitura["n_anos_payout"],
        }
    if not dados:
        return _com_colunas_de_contrato(df_mult)
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


def enrich_decision_universe(
    df_mult_todos: pd.DataFrame,
    hist_batch: dict[str, pd.DataFrame],
    all_tickers: tuple[str, ...],
) -> pd.DataFrame:
    """Inclui evidência histórica no quadro lido pelas decisões de carteira.

    Promovida de ``views/portfolio_b3.py`` (task 6) para existir num único
    lugar: o piso de qualidade, a Saúde da Carteira e a Rota de Valor
    consomem ``df_mult_todos``. Portanto, a sustentabilidade não pode ficar
    apenas no quadro reconciliado de entrada: lacunas continuam ``NaN`` e são
    tratadas como ausência pelos consumidores, nunca como uma nota ou risco
    zero.
    """
    from core.dossie_b3 import load_pl_lucro_anual_batch

    enriched = enrich_com_renda_sustentavel(df_mult_todos, hist_batch)
    return enrich_com_historico_patrimonial(
        enriched, load_pl_lucro_anual_batch(all_tickers)
    )
