"""Correlação de carteira com frequência, moeda e cobertura consistentes."""

from __future__ import annotations

import math

import pandas as pd

MIN_CORR_MONTHS = 24
DEFAULT_CORR_PERIOD = "5y"

# A-135: janela COMUM a todos os pares, em meses. `DataFrame.corr()` e pairwise:
# sem truncar antes, cada par usa toda a sobreposicao que tiver, e a mesma
# matriz mistura pares de 32 meses com pares de 556. Duas correlacoes medidas em
# janelas diferentes nao sao comparaveis -- e a matriz existe justamente para
# comparar linhas entre si. Pior: a legenda ja declarava "janela solicitada: 5y"
# enquanto os caminhos que leem do banco usavam a historia inteira; a execucao
# estava em desacordo com o que o app dizia ao usuario.
#
# 60 meses = os mesmos 5 anos que DEFAULT_CORR_PERIOD ja pedia ao yfinance, de
# modo que as tres fontes (yfinance, asset_quotes, snapshots) passem a medir a
# MESMA coisa. Nao e escolha de gosto: correlacao de 46 anos descreve um regime
# de mercado que nao existe mais, e ao lado de uma de 2,7 anos ela ainda parece
# "mais confiavel" por ter mais observacoes.
JANELA_CORR_MESES = 60
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


def _truncar_janela(retornos: pd.DataFrame, janela_meses: int | None) -> pd.DataFrame:
    """Mantem apenas os ultimos ``janela_meses`` meses do quadro de retornos.

    Corta por POSICAO no indice mensal ja resampleado, nao por data de corte:
    o indice tem um ponto por mes, entao as ultimas N linhas sao os ultimos N
    meses -- inclusive quando o ativo mais novo comecou depois.
    """
    if not janela_meses or janela_meses <= 0 or retornos.empty:
        return retornos
    return retornos.tail(int(janela_meses))


def retornos_mensais(precos: pd.DataFrame, min_obs: int = MIN_CORR_MONTHS,
                     janela_meses: int | None = None) -> pd.DataFrame:
    """Alinha preços por mês e calcula retornos simples sem preencher ausências."""
    if precos is None or precos.empty:
        return pd.DataFrame()
    base = precos.copy()
    base.index = pd.to_datetime(base.index, errors="coerce")
    base = base.loc[~base.index.isna()].sort_index()
    base = base.apply(pd.to_numeric, errors="coerce")
    # A-122: preço <= 0 não é preço. Sem isto, a cotação zerada de MMAQ4
    # produz retorno infinito e as cotações NEGATIVAS de NEMO3/PPAR3/RSUL3/
    # FIGE4 produzem correlações calculadas sobre números impossíveis.
    base = base.where(base > 0)
    if base.empty:
        return pd.DataFrame()
    mensal = base.resample("ME").last().dropna(how="all")
    # A-121: `pd.NA` num quadro float o torna `object`, e `DataFrame.corr()`
    # levanta TypeError -- a seção inteira de Correlação caía com UMA cotação
    # zerada na carteira. `np.nan` preserva o dtype e o tratamento pairwise.
    retornos = mensal.pct_change(fill_method=None).replace(
        [float("inf"), float("-inf")], float("nan")
    ).dropna(how="all")
    # A janela e aplicada ANTES do corte por cobertura: um ativo so entra se
    # tiver observacoes suficientes DENTRO da janela. Cortar depois deixaria
    # passar quem cumpre o minimo so com dado antigo.
    retornos = _truncar_janela(retornos, janela_meses)
    return retornos.dropna(axis=1, thresh=min_obs)


def _periodo(retornos: pd.DataFrame) -> tuple | None:
    """(primeiro, ultimo) mes efetivamente usado, ou None se nao houve dado."""
    if retornos is None or retornos.empty:
        return None
    return (retornos.index.min(), retornos.index.max())


def matriz_sobreposicao(retornos: pd.DataFrame) -> pd.DataFrame:
    if retornos is None or retornos.empty:
        return pd.DataFrame()
    validos = retornos.notna().astype(int)
    return validos.T.dot(validos).astype(int)


def calcular_correlacao_mensal(
    precos: pd.DataFrame,
    min_obs: int = MIN_CORR_MONTHS,
    janela_meses: int | None = JANELA_CORR_MESES,
) -> dict[str, pd.DataFrame | int | str]:
    """Matriz Pearson pairwise, contagem de observacoes e a janela medida.

    ``janela_meses=None`` desliga o corte e volta ao comportamento de historia
    inteira -- util para analise de regime, onde comparar janelas longas e
    curtas e o objetivo, e errado para a tela de carteira, onde a matriz e lida
    linha contra linha.
    """
    retornos = retornos_mensais(precos, min_obs=min_obs, janela_meses=janela_meses)
    if retornos.shape[1] < 2:
        return {
            "corr": pd.DataFrame(),
            "returns": retornos,
            "overlap": matriz_sobreposicao(retornos),
            "frequency": "mensal",
            "min_obs": min_obs,
            "janela_meses": janela_meses,
            "periodo_medido": _periodo(retornos),
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
        "janela_meses": janela_meses,
        # O que foi MEDIDO, para a legenda parar de prometer o que foi PEDIDO.
        "periodo_medido": _periodo(retornos),
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
