"""
core/b3_nota_fatores.py — carteira ampla "nota + fatores" da B3.

Alternativa medida à carteira concentrada da aba (líder + maior participação
por segmento aprovado). A pergunta que ela responde é outra: em vez de
escolher poucos vencedores, **tirar os piores** do universo e ficar com o
resto em pesos iguais. A medição fora da amostra (régua de
`scripts/medir_oos_carteira_b3.py`, gravada em `data/oos_nota_fatores_b3.json`
por `scripts/medir_nota_fatores_b3.py`) foi a melhor variante ampla que o
projeto mediu na B3 (19/11/2026), com efeito pequeno e só nove safras.

A regra foi fixada antes de medir e não muda aqui:

- **Sinal** = média simples de duas partes, cada uma em percentil (0–1)
  dentro do universo da safra:
  - a nota do motor (``Score_Ajustado``/``score_proximo``, já com a
    penalidade de decaimento), em percentil sobre o universo inteiro;
  - o composto de fatores: média dos percentis de momento 12-1, baixa
    volatilidade e ROE; nome sem nenhum fator recebe 0,5.

  Nome sem nota usa só o composto; nome sem nada fica no neutro 0,5.
- **Carteira** = tirar os ``floor(30% · n)`` de pior sinal e dar peso igual ao
  resto. Empate desempata pelo ticker, para a lista não depender da ordem de
  chegada dos dados.
- **Formação** uma vez por ano, no início da vigência (abril, ver
  `core.b3_vigencia`). O momento e a volatilidade usam o mês anterior ao
  início; o ROE é o último publicado (``AvailableAt``) antes do início.

Funções puras: nada de banco nem de Streamlit. A tela e o script de medição
chamam as mesmas funções, para que a carteira mostrada seja a medida.
"""

from __future__ import annotations

import math
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

from core.b3_vigencia import janela_de_vigencia

VERSAO = "nota-fatores-1.0.0"

#: Fração do universo que sai da carteira (os de pior sinal).
CORTE = 0.30

#: Fatores do composto, na ordem em que a tela os mostra.
FATORES = ("mom", "lowvol", "roe")

#: Janela da volatilidade (meses) e mínimo de retornos válidos nela.
JANELA_VOL = 36
MIN_VOL = 24

#: Momento 12-1: do mês t-12 ao t-1 (o mês t, o último antes da formação,
#: fica de fora -- reversão de curto prazo).
MOM_INICIO = 12
MOM_FIM = 1

#: Sinal de quem não tem nota nem fator.
NEUTRO = 0.5


def precos_mensais(precos: pd.DataFrame) -> pd.DataFrame:
    """Último preço positivo de cada mês, em grade mensal contínua.

    Preço zero ou negativo vira faltante (não é cotação). Mês sem preço fica
    faltante -- nada é preenchido, para a volatilidade não ver um retorno zero
    inventado.
    """
    if precos is None or precos.empty:
        return pd.DataFrame()
    pm = precos.apply(pd.to_numeric, errors="coerce")
    pm = pm.where(pm > 0)
    idx = pd.to_datetime(pm.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    pm.index = idx.to_period("M")
    pm = pm.groupby(level=0).last().sort_index()
    if pm.empty:
        return pm
    return pm.reindex(pd.period_range(pm.index.min(), pm.index.max(), freq="M"))


def _em_utc(ts) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def roe_disponivel(hist: pd.DataFrame | None, inicio) -> float:
    """Último ROE publicado antes de ``inicio`` (``AvailableAt`` < início).

    Empate de publicação desempata pela data do balanço. Sem ROE publicado a
    tempo, ``nan``.
    """
    if hist is None or len(hist) == 0 or "ROE" not in hist or "AvailableAt" not in hist:
        return float("nan")
    av = pd.to_datetime(hist["AvailableAt"], utc=True, errors="coerce")
    d = hist.assign(_av=av, _roe=pd.to_numeric(hist["ROE"], errors="coerce"))
    d = d[(d["_av"] < _em_utc(inicio)) & d["_roe"].notna()]
    if d.empty:
        return float("nan")
    ordem = ["_av", "Data"] if "Data" in d.columns else ["_av"]
    return float(d.sort_values(ordem, kind="mergesort")["_roe"].iloc[-1])


def fatores_na_formacao(
    universo: Iterable[str],
    precos_mes: pd.DataFrame,
    hist_batch: Mapping[str, pd.DataFrame] | None,
    inicio,
) -> pd.DataFrame:
    """Momento 12-1, baixa volatilidade e ROE de cada nome no início ``inicio``.

    ``precos_mes`` vem de `precos_mensais`. O mês de referência ``t`` é o
    anterior ao início: tudo o que entra foi observável antes de a carteira
    ser montada.
    """
    nomes = list(dict.fromkeys(str(t) for t in universo))
    out = pd.DataFrame(index=pd.Index(nomes, name="ticker"),
                       columns=list(FATORES), dtype=float)
    if not nomes:
        return out
    t = pd.Period(pd.Timestamp(inicio), freq="M") - 1
    cols = [c for c in nomes if precos_mes is not None and c in precos_mes.columns]
    if cols:
        pm = precos_mes[cols]
        p_fim = pm.reindex([t - MOM_FIM]).iloc[0]
        p_ini = pm.reindex([t - MOM_INICIO]).iloc[0]
        out.loc[cols, "mom"] = (p_fim / p_ini - 1).astype(float).values
        rm = pm.pct_change(fill_method=None)
        jan = rm.reindex(pd.period_range(t - (JANELA_VOL - 1), t, freq="M"))
        vol = jan.std().where(jan.count() >= MIN_VOL)
        out.loc[cols, "lowvol"] = (-vol).astype(float).values
    hb = hist_batch or {}
    out["roe"] = [roe_disponivel(hb.get(n), inicio) for n in nomes]
    return out.replace([np.inf, -np.inf], np.nan)


def composto(fat: pd.DataFrame) -> pd.Series:
    """Média dos percentis dos fatores presentes; nenhum presente → 0,5."""
    if fat is None or fat.empty:
        return pd.Series(dtype=float)
    return fat[list(FATORES)].rank(pct=True).mean(axis=1).fillna(NEUTRO)


def sinal_nota_fatores(
    universo: Iterable[str],
    scores: Mapping[str, float],
    fat: pd.DataFrame,
) -> pd.DataFrame:
    """Partes e sinal de cada nome do universo.

    Colunas: ``nota_pct`` (percentil da nota no universo, faltante sem nota),
    ``fatores`` (composto) e ``sinal`` (média das partes presentes).
    """
    nomes = list(dict.fromkeys(str(t) for t in universo))
    nota = pd.Series({t: float(scores[t]) for t in nomes
                      if t in scores and scores[t] is not None
                      and np.isfinite(float(scores[t]))}, dtype=float)
    partes = pd.DataFrame(index=pd.Index(nomes, name="ticker"))
    partes["nota_pct"] = nota.rank(pct=True).reindex(partes.index)
    partes["fatores"] = composto(fat.reindex(partes.index)) if len(nomes) else []
    partes["sinal"] = partes[["nota_pct", "fatores"]].mean(axis=1).fillna(NEUTRO)
    return partes


def tirar_piores(sinal: pd.Series, corte: float = CORTE) -> tuple[list[str], list[str]]:
    """(ficam, saem): saem os ``floor(corte · n)`` de pior sinal.

    Ordem: sinal decrescente, depois ticker crescente -- determinística.
    """
    s = pd.Series(sinal, dtype=float).fillna(NEUTRO)
    ordem = sorted(s.index, key=lambda t: (-float(s[t]), str(t)))
    n_fora = math.floor(float(corte) * len(ordem))
    corte_em = len(ordem) - n_fora
    return list(ordem[:corte_em]), list(ordem[corte_em:])


def inicio_da_formacao(ano_safra: int, hoje=None) -> pd.Timestamp:
    """Início da vigência da safra; se ainda não chegou, o início do mês atual.

    Entre janeiro e março a safra do ano ainda não começou: a lista é uma
    prévia com os dados de hoje, e a tela diz isso.
    """
    inicio = pd.Timestamp(janela_de_vigencia(int(ano_safra))[0]).normalize()
    agora = pd.Timestamp.now() if hoje is None else pd.Timestamp(hoje)
    mes_atual = agora.normalize().replace(day=1)
    return inicio if inicio <= agora else mes_atual


def carteira_nota_fatores(
    universo: Iterable[str],
    scores: Mapping[str, float],
    precos: pd.DataFrame,
    hist_batch: Mapping[str, pd.DataFrame] | None,
    inicio,
    *,
    corte: float = CORTE,
    precos_mes: pd.DataFrame | None = None,
) -> dict:
    """Carteira "nota + fatores" formada em ``inicio``.

    Devolve ``pesos`` (peso igual para quem fica), ``tabela`` (fatores, partes,
    sinal e se fica), ``cobertura`` (quantos nomes têm cada parte), ``inicio``
    e ``mes_fatores``. ``precos_mes`` já preparado poupa refazer a grade
    quando o chamador forma várias safras com os mesmos preços.
    """
    nomes = list(dict.fromkeys(str(t) for t in universo))
    pm = precos_mensais(precos) if precos_mes is None else precos_mes
    fat = fatores_na_formacao(nomes, pm, hist_batch, inicio)
    partes = sinal_nota_fatores(nomes, scores, fat)
    ficam, _saem = tirar_piores(partes["sinal"], corte)
    tabela = fat.join(partes)
    tabela["fica"] = tabela.index.isin(ficam)
    pesos = {t: 1.0 / len(ficam) for t in ficam} if ficam else {}
    cobertura = {"n": len(nomes), "nota": int(partes["nota_pct"].notna().sum())}
    cobertura.update({f: int(fat[f].notna().sum()) for f in FATORES})
    return {
        "versao": VERSAO,
        "corte": float(corte),
        "inicio": pd.Timestamp(inicio),
        "mes_fatores": str(pd.Period(pd.Timestamp(inicio), freq="M") - 1),
        "pesos": pesos,
        "tabela": tabela,
        "cobertura": cobertura,
    }
