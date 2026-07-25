"""Correlação de carteira com frequência, moeda e cobertura consistentes."""

from __future__ import annotations

import math

import pandas as pd


MIN_CORR_MONTHS = 24
DEFAULT_CORR_PERIOD = "5y"
MAX_FX_GAP_DAYS = 7


def converter_precos_para_brl(
    precos: pd.DataFrame,
    moedas_por_ativo: dict[str, str],
    cambio_para_brl: dict[str, pd.Series],
    max_gap_days: int = MAX_FX_GAP_DAYS,
) -> dict[str, pd.DataFrame | list[str] | str]:
    """Converte preços para BRL usando câmbio histórico anterior de até 7 dias.

    ``cambio_para_brl`` contém BRL por unidade da moeda de origem. Ausência de
    câmbio não vira taxa 1: a série do ativo permanece indisponível.
    """
    if precos is None or precos.empty:
        return {
            "prices": pd.DataFrame(),
            "converted": [],
            "missing_fx": [],
            "base_currency": "BRL",
        }

    base = precos.copy()
    base.index = pd.to_datetime(base.index, errors="coerce")
    base = base.loc[~base.index.isna()].sort_index()
    base = base.groupby(level=0).last()
    base = base.apply(pd.to_numeric, errors="coerce")

    converted: list[str] = []
    missing_fx: list[str] = []
    for ativo in base.columns:
        moeda = str(moedas_por_ativo.get(str(ativo), "BRL") or "BRL").upper()
        if moeda == "BRL":
            continue
        fx = cambio_para_brl.get(moeda)
        if fx is None or fx.empty:
            base[ativo] = pd.NA
            missing_fx.append(str(ativo))
            continue

        fx_num = pd.to_numeric(fx, errors="coerce")
        fx_num.index = pd.to_datetime(fx_num.index, errors="coerce")
        fx_num = fx_num.loc[~fx_num.index.isna()].dropna().sort_index()
        fx_num = fx_num.groupby(level=0).last()
        if fx_num.empty:
            base[ativo] = pd.NA
            missing_fx.append(str(ativo))
            continue

        left = pd.DataFrame(index=base.index)
        right = fx_num.rename("fx_brl").to_frame()
        aligned = pd.merge_asof(
            left,
            right,
            left_index=True,
            right_index=True,
            direction="backward",
            tolerance=pd.Timedelta(days=max_gap_days),
        )["fx_brl"]
        base[ativo] = base[ativo] * aligned
        converted.append(str(ativo))

    return {
        "prices": base,
        "converted": converted,
        "missing_fx": missing_fx,
        "base_currency": "BRL",
    }


def retornos_mensais(precos: pd.DataFrame, min_obs: int = MIN_CORR_MONTHS) -> pd.DataFrame:
    """Alinha preços por mês e calcula retornos simples sem preencher ausências."""
    if precos is None or precos.empty:
        return pd.DataFrame()
    base = precos.copy()
    base.index = pd.to_datetime(base.index, errors="coerce")
    base = base.loc[~base.index.isna()].sort_index()
    base = base.apply(pd.to_numeric, errors="coerce")
    if base.empty:
        return pd.DataFrame()
    mensal = base.resample("ME").last().dropna(how="all")
    retornos = mensal.pct_change(fill_method=None).replace(
        [float("inf"), float("-inf")], pd.NA
    ).dropna(how="all")
    return retornos.dropna(axis=1, thresh=min_obs)


def matriz_sobreposicao(retornos: pd.DataFrame) -> pd.DataFrame:
    if retornos is None or retornos.empty:
        return pd.DataFrame()
    validos = retornos.notna().astype(int)
    return validos.T.dot(validos).astype(int)


def calcular_correlacao_mensal(
    precos: pd.DataFrame,
    min_obs: int = MIN_CORR_MONTHS,
) -> dict[str, pd.DataFrame | int | str]:
    """Retorna matriz Pearson pairwise e contagem de observações por par."""
    retornos = retornos_mensais(precos, min_obs=min_obs)
    if retornos.shape[1] < 2:
        return {
            "corr": pd.DataFrame(),
            "returns": retornos,
            "overlap": matriz_sobreposicao(retornos),
            "frequency": "mensal",
            "min_obs": min_obs,
        }
    corr = retornos.corr(min_periods=min_obs)
    corr = corr.dropna(how="all").dropna(axis=1, how="all")
    retornos = retornos.loc[:, [c for c in corr.columns if c in retornos.columns]]
    overlap = matriz_sobreposicao(retornos)
    return {
        "corr": corr.round(3),
        "returns": retornos,
        "overlap": overlap,
        "frequency": "mensal",
        "min_obs": min_obs,
    }


def classificar_correlacao(valor: float) -> str:
    """Classifica intensidade e direção sem confundir inversão com independência."""
    valor = float(valor)
    abs_val = abs(valor)
    if abs_val < 0.40:
        return "Baixa dependência"
    intensidade = "Alta" if abs_val >= 0.70 else "Moderada"
    direcao = "positiva" if valor > 0 else "inversa"
    return f"{intensidade} {direcao}"


def intervalo_confianca_correlacao(
    valor: float,
    observacoes: int,
    nivel: float = 0.95,
) -> tuple[float, float] | None:
    """Intervalo aproximado de Pearson pela transformação de Fisher."""
    valor = float(valor)
    observacoes = int(observacoes)
    if observacoes <= 3 or not -1.0 <= valor <= 1.0:
        return None
    if valor in (-1.0, 1.0):
        return valor, valor
    if nivel != 0.95:
        raise ValueError("Somente o intervalo de 95% é suportado.")
    erro = 1.96 / math.sqrt(observacoes - 3)
    centro = math.atanh(valor)
    return math.tanh(centro - erro), math.tanh(centro + erro)


def correlacao_media_ponderada(
    corr: pd.DataFrame,
    pesos: dict[str, float],
) -> float | None:
    """Média de |correlação| ponderada pelo produto dos pesos dos pares."""
    if corr is None or corr.empty:
        return None
    numerador = 0.0
    denominador = 0.0
    colunas = list(corr.columns)
    for i, ativo_a in enumerate(colunas):
        peso_a = float(pesos.get(str(ativo_a), 0.0) or 0.0)
        if peso_a <= 0:
            continue
        for ativo_b in colunas[i + 1:]:
            peso_b = float(pesos.get(str(ativo_b), 0.0) or 0.0)
            valor = corr.loc[ativo_a, ativo_b]
            if peso_b <= 0 or pd.isna(valor):
                continue
            peso_par = peso_a * peso_b
            numerador += peso_par * abs(float(valor))
            denominador += peso_par
    return numerador / denominador if denominador > 0 else None
