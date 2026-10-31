"""Retorno total diário das ações da Carteira 20 a partir do pregão bruto.

Puro: sem banco e sem rede. Estende a série da pesquisa
(``dados/b3_retornos_diarios.parquet``), que só avança quando o pipeline dela
roda de novo, com as regras de ``codigo/b3/retornos.py`` e ``eventos.py`` da
pesquisa:

    r_t = ( k_t × (P_t + dist_t) + D_t ) / P_{t-1} − 1

- P = fechamento por ação (``close`` / ``fator_cotacao``), dos BDIs 02 (lote
  padrão) e 05-09 (regimes especiais: recuperação judicial e afins). No mesmo
  dia em dois BDIs fica o lote padrão. Sem os especiais, ação em recuperação
  "some" da série no pior período dela;
- D = proventos em dinheiro com dia ex no pregão, de todos os códigos da cadeia
  do ativo (a brapi às vezes publica sob o código antigo: ALSO3 -> ALOS3). O dia
  ex é o primeiro pregão do ativo estritamente depois da data com (o
  ``ex_date`` da brapi é a data com). A limpeza das duplicatas é a da pesquisa;
- k = fator de desdobramento, grupamento ou bonificação no dia com marca nova
  B/G da B3: o candidato redondo mais próximo do salto corrigido pelo mercado
  mediano. dist = restituição ou cisão sem valor na tabela, medida pela queda;
- dia com marca D/J/X sem provento casado e fora da cobertura da tabela (ou
  com queda de mais de 10% além do mercado) recebe o provento imputado pela
  queda, isto é, o retorno do mercado;
- salto de 5× sem evento: retorno vazio e ``suspeito``.

O que a pesquisa usa e isto não: a confirmação do fator pela DFP, as tabelas de
eventos e cisões conhecidos (todos antigos) e o fator da classe irmã. Os dias em
que isso faria diferença saem ``suspeito``: fator estimado sem candidato
redondo, marca X com salto sem confirmação e marca D/J ainda sem provento na
tabela (a brapi publica dias depois, e a rodada seguinte recalcula desde a
última data da pesquisa).
"""
from __future__ import annotations

import itertools
import math
import re
import unicodedata
from datetime import date

import numpy as np
import pandas as pd

BDIS_ACOES = ("02", "05", "06", "07", "08", "09")
TIPOS_DINHEIRO = {"DIVIDENDO", "JCP", "RENDIMENTO", "REST CAP DIN", "AMORTIZACAO"}
TIPOS_RESTITUICAO = {"REST CAP DIN", "AMORTIZACAO"}
FATORES_REDONDOS = (2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 15, 20, 25, 30, 40, 50,
                    60, 75, 100, 120, 150, 200, 250, 300, 400, 500, 1000)
BONIFICACOES = tuple(sorted({round(1 + p / 100, 6) for p in
                             (2, 2.5, 3, 10 / 3, 4, 5, 6, 6.25, 7, 7.5, 8, 10, 12, 12.5,
                              15, 50 / 3, 20, 25, 30, 100 / 3, 40, 50, 60, 200 / 3, 75, 80)}))
TOL_FATOR_GRANDE = 0.12
TOL_BONIFICACAO = 0.02
SEM_EFEITO_B = 0.015
QUEDA_MINIMA_DIST = 0.03
YIELD_MAXIMO_PROVENTO = 0.60
MINIMO_MERCADO = 20
PERTO_DO_SALTO = 0.10
PENDENTE_DIAS = 15
INCERTOS = {"estimado", "x_sem_confirmacao", "marca_b3_ambigua"}

_MARCA = re.compile(r"^\*?E([A-Z]{1,3})$")
_COLUNAS = ["id", "data", "ret", "suspeito"]


def letras_marca(spec: str | None) -> str:
    """Letras da marcação "ex" da B3 ("ON  EDJ NM" -> "DJ"; "ON *ED" -> "D")."""
    for tok in str(spec or "").split():
        m = _MARCA.match(tok)
        if m:
            return m.group(1)
    return ""


def _norm(s) -> str:
    if s is None or (isinstance(s, float) and np.isnan(s)):
        return ""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().upper()
    return " ".join(s.split())


def _sem_linhas_soma(g: pd.DataFrame) -> pd.DataFrame:
    """Remove a linha cujo valor é a soma (±0,2%) de duas a quatro outras do grupo."""
    if len(g) < 3:
        return g
    vals = g["amount"].to_dict()
    removidos = set()
    for idx in sorted(vals, key=lambda i: -vals[i]):
        outros = [i for i in vals if i != idx and i not in removidos]
        achou = any(abs(sum(vals[i] for i in comb) - vals[idx]) <= 0.002 * vals[idx]
                    for k in range(2, min(4, len(outros)) + 1)
                    for comb in itertools.combinations(outros, k))
        if achou:
            removidos.add(idx)
    return g.drop(index=list(removidos))


def limpar_proventos(dv: pd.DataFrame, ids_por_ticker: dict[str, str]) -> pd.DataFrame:
    """Proventos em dinheiro por ativo, sem as duplicatas da brapi.

    ``dv``: colunas ``ticker``, ``ex_date`` (data com), ``payment_date``,
    ``amount`` e ``type`` de ``market.dividends``. As chaves são por ativo: o
    mesmo provento sob o código antigo e o novo conta uma vez.
    """
    x = dv.copy()
    x["id"] = x["ticker"].map(ids_por_ticker)
    x["tipo"] = x["type"].map(_norm)
    x["amount"] = pd.to_numeric(x["amount"], errors="coerce").astype(float)
    x = x[x["id"].notna() & x["tipo"].isin(TIPOS_DINHEIRO) & (x["amount"] > 0)].copy()
    x["data_com"] = pd.to_datetime(x["ex_date"])
    x["pag"] = pd.to_datetime(x["payment_date"])
    x["a_r"] = x["amount"].round(8)
    # 1) duplicata exata
    x = x.drop_duplicates(["id", "data_com", "tipo", "a_r", "pag"])
    # 2) quase-duplicata: mesmo tipo e pagamento, valor a 0,01%
    x = x.sort_values(["id", "data_com", "tipo", "pag", "amount"])
    ant = x.groupby(["id", "data_com", "tipo", "pag"], dropna=False)["amount"].shift(1)
    x = x[~(ant.notna() & ((x["amount"] - ant).abs() <= 1e-4 * x["amount"]))]
    # 2b) mesmo valor, tipo e data com, pagamento deslocado até 7 dias
    x = x.sort_values(["id", "data_com", "tipo", "amount", "pag"])
    g = x.groupby(["id", "data_com", "tipo"])
    a_ant, p_ant = g["amount"].shift(1), g["pag"].shift(1)
    x = x[~(a_ant.notna() & ((x["amount"] - a_ant).abs() <= 1e-4 * x["amount"])
            & ((x["pag"] - p_ant).dt.days.abs() <= 7))].copy()
    # 3) gêmeo com pagamento anterior à data com
    x["a_4"] = x["amount"].round(6)
    gemeo = x.duplicated(["id", "data_com", "tipo", "a_4"], keep=False)
    x = x[~(gemeo & (x["pag"] < x["data_com"]))]
    # 4) restituição de capital repetida
    rc = x["tipo"].isin(TIPOS_RESTITUICAO)
    x = x[~(rc & x.duplicated(["id", "data_com", "tipo", "a_4"], keep="first"))]
    # 5) linhas-soma dentro de (ativo, data com, pagamento)
    partes = [_sem_linhas_soma(p) for _, p in x.groupby(["id", "data_com", "pag"], dropna=False)]
    x = pd.concat(partes) if partes else x
    return x[["id", "ticker", "data_com", "tipo", "amount"]].reset_index(drop=True)


def pregoes_de_acoes(linhas: list[dict], tickers: set[str]) -> pd.DataFrame:
    """Uma linha por (código, pregão) dos BDIs de ação, com o fechamento por ação."""
    d = pd.DataFrame([x for x in linhas
                      if x["ticker"] in tickers and x["bdi"] in BDIS_ACOES and x.get("close")],
                     columns=["ticker", "trade_date", "bdi", "specification", "close",
                              "fator_cotacao"])
    d["trade_date"] = pd.to_datetime(d["trade_date"])
    d["fator_cotacao"] = pd.to_numeric(d["fator_cotacao"]).fillna(1).clip(lower=1)
    d["close_unitario"] = d["close"].astype(float) / d["fator_cotacao"]
    d["_ord"] = (d["bdi"] != "02").astype(int)
    return (d.sort_values(["ticker", "trade_date", "_ord"])
             .drop_duplicates(["ticker", "trade_date"], keep="first")
             .drop(columns="_ord").reset_index(drop=True))


def mercado_mediano(d: pd.DataFrame) -> pd.Series:
    """Mediana do retorno bruto entre ativos com dois pregões seguidos e sem marca
    nova no dia (com ao menos 20 deles; senão zero). Só corrige a estimativa de
    fatores e distribuições."""
    cal = pd.DatetimeIndex(sorted(d["trade_date"].unique()))
    pos = pd.Series(np.arange(len(cal)), index=cal)
    g = d.groupby("id")
    pa, ia = g["close_unitario"].shift(1), g["trade_date"].shift(1)
    ok = ((d["trade_date"].map(pos) - ia.map(pos)) == 1) & (d["nova"] == "") & pa.notna()
    r = (d["close_unitario"] / pa - 1)[ok]
    med = r.groupby(d.loc[ok, "trade_date"]).agg(["median", "size"])
    return med.loc[med["size"] >= MINIMO_MERCADO, "median"].reindex(cal).fillna(0.0)


def _candidatos(letras: str) -> list[float]:
    c = []
    if "G" in letras:
        c += [1.0 / f for f in FATORES_REDONDOS]
    if "B" in letras:
        c += [float(f) for f in FATORES_REDONDOS] + list(BONIFICACOES)
    return c


def estimar_fator(letras: str, k_obs: float, k_pers: float) -> tuple[float, str]:
    """Fator (ações novas por antiga) de um dia com marca B/G: a
    ``eventos.estimar_fator`` da pesquisa sem a confirmação pela DFP.

    Com mais de um candidato a até 0,10 (log) do salto -- bonificações de 10%,
    12%, 12,5%... --, é a DFP que decide na pesquisa: aqui o escolhido sai
    ``marca_b3_ambigua``.
    """
    if not (k_obs > 0 and math.isfinite(k_obs)):
        return 1.0, "sem_preco"
    lk = math.log(k_obs)
    so_b = "B" in letras and "G" not in letras
    so_g = "G" in letras and "B" not in letras
    if (so_b and lk < SEM_EFEITO_B) or (so_g and lk > -SEM_EFEITO_B) or abs(lk) < SEM_EFEITO_B:
        return 1.0, "marca_sem_efeito"
    cands = _candidatos(letras)
    for alvo in (k_obs, k_pers):
        if not (alvo > 0 and math.isfinite(alvo)):
            continue
        ok = [c for c in cands
              if abs(math.log(alvo / c)) <= (TOL_BONIFICACAO if abs(math.log(c)) < 0.30
                                             else TOL_FATOR_GRANDE)]
        if ok:
            perto = [c for c in cands if abs(math.log(k_obs / c)) <= PERTO_DO_SALTO]
            return (float(min(ok, key=lambda c: abs(math.log(alvo / c)))),
                    "marca_b3_ambigua" if len(perto) > 1 else "marca_b3")
    return float(k_obs), "estimado"


def _por_ativo(pregoes: pd.DataFrame, ids_por_ticker: dict[str, str]) -> pd.DataFrame:
    d = pregoes[pregoes["ticker"].isin(ids_por_ticker)].copy()
    d["id"] = d["ticker"].map(ids_por_ticker)
    d = d.sort_values(["ticker", "trade_date"])
    d["marca"] = d["specification"].map(letras_marca)
    ant = d.groupby("ticker")["marca"].shift(1).fillna("")
    d["nova"] = ["".join(ch for ch in a if ch not in b) for a, b in zip(d["marca"], ant)]
    # dois códigos da mesma cadeia no mesmo dia (troca de código): fica um
    return (d.sort_values(["id", "trade_date", "ticker"])
             .drop_duplicates(["id", "trade_date"], keep="last").reset_index(drop=True))


def _alinhar(prov: pd.DataFrame, d: pd.DataFrame) -> pd.DataFrame:
    """Dia ex = primeiro pregão do ativo estritamente depois da data com."""
    datas = {k: g["trade_date"].values for k, g in d.groupby("id")}
    out = []
    for a, g in prov.groupby("id"):
        dts = datas.get(a)
        if dts is None:
            continue
        i = np.searchsorted(dts, g["data_com"].values.astype("datetime64[ns]"), side="right")
        ok = (i > 0) & (i < len(dts))
        g = g[ok].copy()
        g["dia_ex"] = pd.to_datetime(dts[i[ok]])
        out.append(g)
    cols = list(prov.columns) + ["dia_ex"]
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=cols)


def calcular(pregoes: pd.DataFrame, proventos: pd.DataFrame, ids_por_ticker: dict[str, str],
             depois_de: date, *, componentes: bool = False) -> pd.DataFrame:
    """Retorno total diário com data em ``(depois_de, ...)``: colunas ``id``, ``data``,
    ``ret`` e ``suspeito``, no formato de ``core.carteira20_desempenho.calcular``.

    ``ids_por_ticker`` liga cada código da cadeia de um ativo ao id dele.
    ``pregoes`` precisa trazer ao menos 5 pregões de cada papel antes de
    ``depois_de`` (P_{t-1}, marcas persistentes e a mediana pré-evento) e o
    universo inteiro (o mercado mediano pede 20 ativos no dia); ``proventos``,
    as datas com desde 400 dias antes (a cobertura que decide a imputação).

    ``componentes`` acrescenta ``preco`` (fechamento por ação), ``k`` (ações
    novas por antiga) e ``caixa`` (dinheiro por ação antiga: ``k × dist + D +
    D_imp``), com ``1 + ret = (k × preco + caixa) / P_{t-1}``. É o que a medição
    mensal (``core.carteira20_medicao``) precisa para contar ações inteiras e
    creditar os proventos em caixa. No dia de retorno vazio (salto sem evento)
    ``k`` faz o valor da posição ficar parado, como o retorno zero da curva
    diária.
    """
    d = _por_ativo(pregoes, ids_por_ticker)
    if d.empty:
        return pd.DataFrame(columns=_COLUNAS)
    cum = (1 + mercado_mediano(d)).cumprod()

    # ---------------- proventos em dinheiro ----------------
    prov = (limpar_proventos(proventos, ids_por_ticker) if len(proventos)
            else pd.DataFrame(columns=["id", "ticker", "data_com", "tipo", "amount"]))
    prov = _alinhar(prov, d)
    if len(prov):
        agg = (prov.assign(rest=prov["tipo"].isin(TIPOS_RESTITUICAO))
                   .groupby(["id", "dia_ex"]).agg(D=("amount", "sum"), tem_rest=("rest", "any")))
        d = d.join(agg, on=["id", "trade_date"])
    else:
        d["D"], d["tem_rest"] = np.nan, np.nan
    d["D"] = d["D"].fillna(0.0).astype(float)
    d["tem_rest"] = d["tem_rest"].astype("boolean").fillna(False).astype(bool)

    g = d.groupby("id")
    d["p_ant"] = g["close_unitario"].shift(1)
    d["dt_ant"] = g["trade_date"].shift(1)
    lote = (d["fator_cotacao"] >= 1000) & (d["D"] > 0.2 * d["p_ant"]) & d["p_ant"].notna()
    d.loc[lote, "D"] = d.loc[lote, "D"] / 1000.0
    alto = (d["D"] > YIELD_MAXIMO_PROVENTO * d["p_ant"]) & d["p_ant"].notna()
    d.loc[alto, "D"] = 0.0

    # ---------------- eventos B/G e R/C ----------------
    d["k"], d["dist"], d["evento"] = 1.0, 0.0, ""
    rel = np.abs(np.log(d["close_unitario"] / d["p_ant"]))
    fat_ant = g["fator_cotacao"].shift(1)
    troca_fator = fat_ant.notna() & (fat_ant != d["fator_cotacao"]) & (rel >= np.log(3))
    idx_ev = d.index[(d["nova"].str.contains("[BGRC]", regex=True)
                      | (d["nova"].str.contains("X") & (rel >= np.log(1.8))) | troca_fator)
                     & d["p_ant"].notna()]
    precos = d["close_unitario"].to_numpy()
    ativo = d["id"].to_numpy()
    for i in idx_ev:
        letras = d.at[i, "nova"]
        p0, p1, D = d.at[i, "p_ant"], d.at[i, "close_unitario"], d.at[i, "D"]
        m_int = float(cum.loc[d.at[i, "trade_date"]] / cum.loc[d.at[i, "dt_ant"]])
        lo = i - 1
        while lo - 1 >= 0 and ativo[lo - 1] == ativo[i] and (i - lo) < 5:
            lo -= 1
        hi = i
        while hi + 1 < len(d) and ativo[hi + 1] == ativo[i] and (hi - i) < 4:
            hi += 1
        antes, depois = float(np.median(precos[lo:i])), float(np.median(precos[i:hi + 1]))
        k_obs = (p0 - D) / p1 * m_int
        k, status = 1.0, ""
        if troca_fator.at[i] and not ("R" in letras or "C" in letras):
            k, status = k_obs, "troca_fator_cotacao"
        elif not any(ch in letras for ch in "BGRC"):
            status = "x_sem_confirmacao"
        elif "B" in letras or "G" in letras:
            k, status = estimar_fator(letras, k_obs, antes / depois)
            if status == "marca_sem_efeito" and abs(np.log(k_obs)) >= np.log(5):
                k, status = float(k_obs), "estimado_salto"
        dist = 0.0
        if ("R" in letras or "C" in letras) and not ("R" in letras and d.at[i, "tem_rest"]
                                                     and "C" not in letras):
            ref = (p0 - D) / k * m_int
            queda = ref - p1
            if ref > 0 and queda / ref >= QUEDA_MINIMA_DIST and (ref - depois) / ref >= QUEDA_MINIMA_DIST:
                dist = queda
                status = (status + "+" if status else "") + ("cisao" if "C" in letras else "restituicao")
        d.at[i, "k"], d.at[i, "dist"], d.at[i, "evento"] = k, dist, status

    # ---------------- provento sem valor na tabela (imputado) ----------------
    cob = prov.groupby("id")["data_com"].agg(["min", "max"]) if len(prov) else None
    if cob is not None and len(cob):
        lim = d[["id"]].join(cob, on="id")
        dentro = (lim["min"].notna() & (d["trade_date"] >= lim["min"] - pd.Timedelta(days=400))
                  & (d["trade_date"] <= lim["max"] + pd.Timedelta(days=400)))
    else:
        dentro = pd.Series(False, index=d.index)
    tem_d = (d["D"] > 0).astype(int)
    casado = tem_d.groupby(d["id"]).transform(
        lambda s: s.rolling(7, center=True, min_periods=1).max()) > 0
    m_int_all = (cum.reindex(d["trade_date"]).to_numpy()
                 / cum.reindex(d["dt_ant"].fillna(d["trade_date"])).to_numpy())
    esperado = d["p_ant"].to_numpy() * m_int_all
    d_imp = esperado - d["k"].to_numpy() * d["close_unitario"].to_numpy()
    cand = (d["nova"].str.contains("[DJX]", regex=True) & d["p_ant"].notna() & ~casado
            & (d["evento"] == ""))
    imputa = cand & (~dentro | (d_imp / esperado > 0.10))
    d["D_imp"] = np.where(imputa, d_imp, 0.0)

    # ---------------- retorno ----------------
    d["ret"] = (d["k"] * (d["close_unitario"] + d["dist"]) + d["D"] + d["D_imp"]) / d["p_ant"] - 1.0
    sem_ev = (d["evento"] == "") & (d["D"] == 0) & ~imputa
    salto = sem_ev & (np.abs(np.log1p(d["ret"])) > np.log(5))
    d.loc[salto, "ret"] = np.nan
    # marca D/J sem provento: recente, a brapi ainda não publicou; antiga, a
    # tabela não tem e a pesquisa faz igual (fica o retorno do preço)
    recente = d["trade_date"] >= d["trade_date"].max() - pd.Timedelta(days=PENDENTE_DIAS)
    pendente = d["nova"].str.contains("[DJ]", regex=True) & cand & ~imputa & recente
    d["suspeito"] = salto | d["evento"].isin(INCERTOS) | pendente

    d = d[(d["trade_date"].dt.date > depois_de) & d["p_ant"].notna()]
    saida = pd.DataFrame({"id": d["id"].values,
                          "data": d["trade_date"].dt.date.values,
                          "ret": d["ret"].values,
                          "suspeito": d["suspeito"].astype(bool).values})
    if componentes:
        vazio = d["ret"].isna().to_numpy()
        saida["preco"] = d["close_unitario"].to_numpy()
        saida["k"] = np.where(vazio, d["p_ant"] / d["close_unitario"], d["k"])
        saida["caixa"] = np.where(vazio, 0.0, d["k"] * d["dist"] + d["D"] + d["D_imp"])
    return saida
