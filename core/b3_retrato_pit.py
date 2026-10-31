"""
core/b3_retrato_pit.py — o quadro de múltiplos que a decisão de uma safra
passada teria lido.

O piso de qualidade e o Score de Entrada da carteira de HOJE leem o retrato
corrente (``load_multiplos_todos`` enriquecido pela sustentabilidade histórica
e pela série de patrimônio). Aplicá-los a uma safra passada com esse retrato
seria reprovar (ou aprovar) com balanços que ainda não existiam. Este módulo
recorta tudo em N-1 -- a mesma convenção do score (``lag=1``: a safra N, que
vigora de abril/N a março/N+1, decide com exercícios até N-1):

- ``historico_ate``: a série de múltiplos de cada ticker só até o ano N-1 e,
  quando o chamador passa ``aceita``, só com as linhas que a regra de vintage
  do motor de score aceitaria na data de decisão (``AvailableAt`` medido ou
  prazo CVM modelado) -- o retrato da safra não pode ver balanço que o score
  daquela safra não viu;
- ``retrato_ate``: a última linha dessa série, nas MESMAS colunas que
  ``core.market_read`` entrega (``COLUNAS_RETRATO``, sinais 0/1 incluídos) --
  coluna a mais aqui seria um critério que a tela não aplica;
- ``series_pl_ate``: a série anual de PL e lucro só até N-1;
- ``anos_hist_ate``: a contagem de exercícios que o Score de Entrada usa,
  descontados os exercícios posteriores a N-1;
- ``quadro_decisao_pit``: o retrato enriquecido como a tela enriquece o dela
  (``enrich_com_renda_sustentavel`` + ``enrich_com_historico_patrimonial``).

Diferenças declaradas: o retrato de hoje é o TTM cru do banco; o da safra é
o último exercício ANUAL aceito, já saneado por faixas
(``clean_multiples_frame``), que é o que o motor de score recebe. Saneamento só
anula valor fora de faixa (nulo fica nulo), e ausência nunca reprova no piso.

Puro: sem streamlit, sem banco. Coberto por tests/test_b3_retrato_pit.py.
"""
from __future__ import annotations

from typing import Callable

import pandas as pd

from core.b3_renda_sustentavel import (
    enrich_com_historico_patrimonial,
    enrich_com_renda_sustentavel,
)

#: As colunas de ``core.market_read._MULT_COLS`` (o retrato de hoje), na mesma
#: ordem. Cópia literal porque ``market_read`` importa streamlit e este módulo
#: é puro; tests/test_b3_retrato_pit.py falha se as duas divergirem. Os três
#: sinais finais são o que faz o piso REPROVAR balanço rompido.
COLUNAS_RETRATO = (
    "P/L", "P/VP", "DY", "ROE", "ROA", "ROIC",
    "Margem_Liquida", "Margem_Operacional",
    "Endividamento_Total", "Liquidez_Corrente",
    "EV_EBIT", "P_FCO", "Payout",
    "Patrimonio_Negativo", "Endividamento_Fora_De_Faixa", "FCO_Negativo",
)

#: Mesmo teto de ``core.dossie_b3.load_pl_lucro_anual_batch``.
MAX_ANOS_PL = 12


def _anos(df: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(df["Data"], errors="coerce").dt.year


#: Regra de vintage: recebe o recorte de UM ticker (com a coluna ``_ano``) e
#: devolve a máscara das linhas disponíveis na data de decisão. É a do motor
#: de score (``views.empresas_b3._classificar_disponibilidade_pit``), injetada
#: pelo chamador para não haver uma segunda cópia que divirja.
FiltroVintage = Callable[[pd.DataFrame], pd.Series]


def historico_ate(hist_batch: dict[str, pd.DataFrame] | None,
                  ano_max: int,
                  aceita: FiltroVintage | None = None) -> dict[str, pd.DataFrame]:
    """Série de cada ticker só com linhas de exercício <= ``ano_max``.

    Linha sem data não tem como provar que já existia: sai. Ticker sem
    nenhuma linha até ``ano_max`` não aparece (não havia ano-base). Com
    ``aceita``, sai também a linha que a regra de vintage barraria -- a
    vintage publicada depois da decisão, ou a baseline antes do prazo CVM.
    """
    saida: dict[str, pd.DataFrame] = {}
    for tk, df in (hist_batch or {}).items():
        if df is None or df.empty or "Data" not in df.columns:
            continue
        anos = _anos(df)
        recorte = df[anos.notna() & (anos <= int(ano_max))].copy()
        if recorte.empty:
            continue
        if aceita is not None:
            recorte["_ano"] = _anos(recorte).astype(int)
            recorte = recorte.reset_index(drop=True)
            mascara = pd.Series(aceita(recorte), index=recorte.index).astype(bool)
            recorte = recorte[mascara].drop(columns="_ano")
            if recorte.empty:
                continue
        saida[str(tk)] = recorte.sort_values("Data").reset_index(drop=True)
    return saida


def retrato_ate(hist_batch: dict[str, pd.DataFrame] | None,
                ano_max: int,
                aceita: FiltroVintage | None = None) -> pd.DataFrame:
    """Cross-section: a última linha disponível de cada ticker até ano_max.

    ``Ticker``, ``data`` e ``COLUNAS_RETRATO``, numéricas -- o formato do
    ``df_mult_todos`` que a tela entrega ao piso e ao Score de Entrada.
    """
    linhas = []
    for tk, df in historico_ate(hist_batch, ano_max, aceita).items():
        ultima = df.iloc[-1]
        linha = {"Ticker": str(tk).upper().replace(".SA", ""), "data": ultima["Data"]}
        for col in COLUNAS_RETRATO:
            linha[col] = ultima[col] if col in df.columns else None
        linhas.append(linha)
    colunas = ["Ticker", "data", *COLUNAS_RETRATO]
    if not linhas:
        return pd.DataFrame(columns=colunas)
    out = pd.DataFrame(linhas, columns=colunas)
    for col in COLUNAS_RETRATO:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def series_pl_ate(series_batch: dict[str, list[dict]] | None,
                  ano_max: int, max_anos: int = MAX_ANOS_PL) -> dict[str, list[dict]]:
    """Série anual de PL e lucro só até ``ano_max``, com o mesmo teto do loader.

    O teto vem DEPOIS do recorte: cortar antes deixaria a safra antiga com
    menos anos do que o loader daria a quem decidisse naquela época.
    """
    saida: dict[str, list[dict]] = {}
    for tk, serie in (series_batch or {}).items():
        recorte = [linha for linha in (serie or [])
                   if linha.get("ano") is not None and int(linha["ano"]) <= int(ano_max)]
        recorte.sort(key=lambda linha: int(linha["ano"]))
        if recorte:
            saida[str(tk)] = recorte[-int(max_anos):]
    return saida


def anos_hist_ate(anos_hist: dict[str, int] | None,
                  hist_batch: dict[str, pd.DataFrame] | None,
                  ano_max: int) -> dict[str, int]:
    """Exercícios de DRE que existiam até ``ano_max``.

    ``anos_hist`` conta os exercícios de hoje; tira deles os exercícios
    posteriores a ``ano_max`` que a série de múltiplos mostra (os múltiplos
    saem das mesmas demonstrações). Nunca negativo.
    """
    saida: dict[str, int] = {}
    for tk, total in (anos_hist or {}).items():
        df = (hist_batch or {}).get(tk)
        futuros = 0
        if df is not None and not df.empty and "Data" in df.columns:
            anos = _anos(df).dropna()
            futuros = int(anos[anos > int(ano_max)].nunique())
        saida[str(tk)] = max(0, int(total) - futuros)
    return saida


def quadro_decisao_pit(hist_batch: dict[str, pd.DataFrame] | None,
                       series_pl: dict[str, list[dict]] | None,
                       ano_max: int,
                       aceita: FiltroVintage | None = None) -> pd.DataFrame:
    """O ``df_mult_todos`` enriquecido que a decisão de N teria lido.

    Mesma composição de ``enrich_decision_universe``, com cada insumo
    recortado em ``ano_max`` (e pela vintage, quando ``aceita`` vem).
    """
    hist = historico_ate(hist_batch, ano_max, aceita)
    retrato = retrato_ate(hist_batch, ano_max, aceita)
    enriquecido = enrich_com_renda_sustentavel(retrato, hist)
    return enrich_com_historico_patrimonial(
        enriquecido, series_pl_ate(series_pl, ano_max))
