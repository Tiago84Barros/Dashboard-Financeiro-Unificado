"""
core/b3_previsao_lucros.py -- previsao da VARIACAO do lucro das empresas B3.

Pergunta que este modulo responde, e so ela: prever a variacao do lucro do
proximo exercicio vence o modelo ingenuo "lucro do ano que vem = lucro deste
ano"? E essa previsao melhora o Rank-IC do score B3?

O estudo pessoal de 2010-2021 (MATLAB, uma rede por empresa, ~15 pontos,
entradas escolhidas olhando a validacao, sem baseline ingenuo) "funcionou"
para 2 de 12 empresas -- o que e exatamente o que o acaso entrega. Este modulo
existe para nao repetir aquilo:

  - Alvo sem escala: y = (LL_s - LL_b) / AtivoTotal_b, com b = s-1, e o sinal
    da variacao. Nunca o nivel: nivel de lucro e quase todo persistencia e
    qualquer modelo "acerta" 95% dele.
  - Um modelo de PAINEL so, com todas as empresas, entradas em razoes
    (margens, ROE, ROA, crescimento, accruals, alavancagem). Nada por empresa.
  - Walk-forward por safra respeitando AvailableAt: a safra s decide em
    01/04/s (core.b3_vigencia) com o exercicio s-1 publicado ate la; o
    treino so ve alvos ja PUBLICADOS na data de decisao. Normalizacao,
    winsorizacao e imputacao saem so do treino da dobra.
  - Comparacao obrigatoria contra o ingenuo (variacao zero), ridge e gradient
    boosting. Um ensemble pequeno de MLP so entra se superar os tres no
    desenvolvimento. Hiperparametros fixos aqui, antes de qualquer medicao.
  - Holdout final (safras >= HOLDOUT_INICIO) usado uma vez: o desenvolvimento
    escolhe o modelo, o holdout so confirma ou reprova.
  - Erro e acerto de sinal para TODAS as empresas avaliaveis, com teste
    binomial e intervalo. Nenhum recorte por empresa.

Modulo puro: sem streamlit, sem banco, sem rede. A leitura da DFP e do
armazem fica em scripts/treinar_previsao_lucros_b3.py; o resultado vai para
data/public/previsao_lucros_b3.json.gz. Nenhuma tela o le desde 29/10/2026, quando
a aba Previsao de Lucros saiu de Empresas B3; o modelo e o treino ficam aqui.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from core.b3_vigencia import REBAL_MONTH

VERSAO_METODOLOGIA = "1.0.0"

# Primeira safra testada: precisa de tres safras de alvo publicadas atras dela
# (alvos 2011-2013) para o primeiro treino ter massa.
SAFRA_MIN_TESTE = 2014
# Holdout: as quatro ultimas safras com alvo conhecido. Fixado ANTES da
# primeira medicao; mudar isto depois de ver o holdout e ajustar a regua.
HOLDOUT_INICIO = 2022
# Prazo CVM da DFP: 31/03 do ano seguinte (mesma regra de views/empresas_b3).
PRAZO_CVM_MES, PRAZO_CVM_DIA = 3, 31

FEATURES = [
    "roa",              # LL_b / Ativo_b
    "roe",              # LL_b / PL_b (PL > 0)
    "margem_liquida",   # LL_b / Receita_b (Receita > 0)
    "margem_ebit",      # EBIT_b / Receita_b
    "cresc_receita",    # Receita_b / Receita_b-1 - 1
    "cresc_ativo",      # Ativo_b / Ativo_b-1 - 1
    "dlucro_passado",   # (LL_b - LL_b-1) / Ativo_b-1
    "accruals",         # (LL_b - FCO_b) / Ativo_b
    "alavancagem",      # Divida bruta_b / Ativo_b
    "passivo_ativo",    # (Ativo_b - PL_b) / Ativo_b
    "liquidez",         # Ativo circulante / Passivo circulante
    "fco_ativo",        # FCO_b / Ativo_b
    "dividendos_ativo",  # Dividendos pagos_b / Ativo_b
    "log_ativo",        # log(Ativo_b)
    "prejuizo",         # 1 se LL_b < 0
    "pl_negativo",      # 1 se PL_b <= 0
]

# Hiperparametros declarados antes de medir. Nenhum deles foi escolhido
# olhando o holdout; o alpha do ridge sai de validacao INTERNA ao treino.
HIPERPARAMETROS = {
    "winsor_q": 0.01,
    "ridge_alphas": [0.1, 1.0, 10.0, 100.0, 1000.0],
    "gbm": {"n_arvores": 300, "taxa": 0.05, "profundidade": 3,
            "min_folha": 20, "subamostra": 0.7, "n_bins": 32, "seed": 7},
    "mlp": {"membros": 5, "ocultos": 16, "epocas": 400, "taxa": 0.01,
            "decaimento": 1e-3, "paciencia": 40, "seed": 11},
    "nivel_intervalo": 0.80,
    "bootstrap_amostras": 2000,
}

MODELOS_BASE = ("ingenuo", "ridge", "gbm")


def data_decisao(safra: int) -> pd.Timestamp:
    """01/04 da safra -- a mesma data em que o score B3 decide."""
    return pd.Timestamp(int(safra), REBAL_MONTH, 1)


def disponivel_em(available_at, ano_fiscal: int) -> pd.Timestamp:
    """Quando o exercicio ficou publico.

    Sem DT_RECEB, vale o prazo da CVM -- a mesma regra fail-closed do score:
    passa depois do prazo, nunca antes.
    """
    ts = pd.to_datetime(available_at, errors="coerce")
    if pd.isna(ts):
        return pd.Timestamp(int(ano_fiscal) + 1, PRAZO_CVM_MES, PRAZO_CVM_DIA)
    return pd.Timestamp(ts).tz_localize(None) if getattr(ts, "tzinfo", None) else pd.Timestamp(ts)


def config_hash() -> str:
    """Impressao digital da configuracao: holdout so vale para este hash."""
    blob = json.dumps({"v": VERSAO_METODOLOGIA, "f": FEATURES,
                       "h": HIPERPARAMETROS, "min": SAFRA_MIN_TESTE,
                       "hold": HOLDOUT_INICIO}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# --------------------------------------------------------------------------
# Painel
# --------------------------------------------------------------------------

def _razao(num, den, *, den_positivo: bool = True):
    """num/den com o denominador do sinal certo (A-101: denominador que troca
    de sinal inverte a leitura). Fora disso, NaN -- nunca zero."""
    num = pd.to_numeric(num, errors="coerce")
    den = pd.to_numeric(den, errors="coerce")
    ok = den > 0 if den_positivo else den.abs() > 0
    return (num / den).where(ok)


def montar_painel(fund: pd.DataFrame) -> pd.DataFrame:
    """Uma linha por (empresa, safra s): entradas do exercicio b = s-1 (e b-1),
    alvo do exercicio s.

    ``fund`` tem uma linha por (cd_cvm, ano) com ``available_at`` e os campos
    de ``data_pipeline.market.b3_saidas.fundamentos``. Linhas sem ativo
    positivo no ano-base nao entram: o alvo nao tem escala.
    """
    cols = ["net_income", "revenue", "ebit", "total_assets", "equity",
            "gross_debt", "fco", "current_assets", "current_liabilities",
            "dividendos_pagos"]
    f = fund.copy()
    for c in cols:
        if c not in f.columns:
            f[c] = np.nan
        f[c] = pd.to_numeric(f[c], errors="coerce")
    f["ano"] = f["ano"].astype(int)
    if "available_at" not in f.columns:
        f["available_at"] = pd.NaT
    f["disp"] = [disponivel_em(a, y) for a, y in zip(f["available_at"], f["ano"])]
    f = f.sort_values(["cd_cvm", "ano"]).drop_duplicates(["cd_cvm", "ano"], keep="last")

    base = f.set_index(["cd_cvm", "ano"])
    ant = base.copy()
    ant.index = pd.MultiIndex.from_arrays(
        [ant.index.get_level_values(0), ant.index.get_level_values(1) + 1],
        names=base.index.names)
    alvo = base.copy()
    alvo.index = pd.MultiIndex.from_arrays(
        [alvo.index.get_level_values(0), alvo.index.get_level_values(1) - 1],
        names=base.index.names)

    b = base
    a = ant.reindex(b.index)    # exercicio b-1
    t = alvo.reindex(b.index)   # exercicio s = b+1

    out = pd.DataFrame(index=b.index)
    ta = b["total_assets"]
    out["roa"] = _razao(b["net_income"], ta)
    out["roe"] = _razao(b["net_income"], b["equity"])
    out["margem_liquida"] = _razao(b["net_income"], b["revenue"])
    out["margem_ebit"] = _razao(b["ebit"], b["revenue"])
    out["cresc_receita"] = _razao(b["revenue"], a["revenue"]) - 1.0
    out["cresc_ativo"] = _razao(ta, a["total_assets"]) - 1.0
    out["dlucro_passado"] = _razao(b["net_income"] - a["net_income"], a["total_assets"])
    out["accruals"] = _razao(b["net_income"] - b["fco"], ta)
    out["alavancagem"] = _razao(b["gross_debt"], ta)
    out["passivo_ativo"] = _razao(ta - b["equity"], ta)
    out["liquidez"] = _razao(b["current_assets"], b["current_liabilities"])
    out["fco_ativo"] = _razao(b["fco"], ta)
    out["dividendos_ativo"] = _razao(b["dividendos_pagos"], ta)
    out["log_ativo"] = np.log(ta.where(ta > 0))
    out["prejuizo"] = (b["net_income"] < 0).astype(float).where(b["net_income"].notna())
    out["pl_negativo"] = (b["equity"] <= 0).astype(float).where(b["equity"].notna())

    out["lucro_base"] = b["net_income"]
    out["ativo_base"] = ta
    out["lucro_alvo"] = t["net_income"]
    out["y"] = _razao(t["net_income"] - b["net_income"], ta)
    out["disp_base"] = b["disp"]
    out["disp_alvo"] = t["disp"]
    out = out.reset_index().rename(columns={"ano": "ano_base"})
    out["safra"] = out["ano_base"] + 1
    out = out[(out["ativo_base"] > 0) & out["lucro_base"].notna()]
    return out.reset_index(drop=True)


def conjunto_teste(painel: pd.DataFrame, safra: int) -> pd.DataFrame:
    """Empresas que a safra pode prever: ano-base publicado ate a decisao."""
    d = data_decisao(safra)
    return painel[(painel["safra"] == int(safra)) & (painel["disp_base"] <= d)]


def conjunto_treino(painel: pd.DataFrame, safra: int) -> pd.DataFrame:
    """Pares cujo ALVO ja era publico na data de decisao da safra.

    E aqui que mora o PIT do treino: o exercicio s-1 publicado em maio/s nao
    entra na safra s, mesmo sendo de um ano anterior.
    """
    d = data_decisao(safra)
    m = (painel["safra"] < int(safra)) & painel["y"].notna() & (painel["disp_alvo"] <= d)
    return painel[m]


# --------------------------------------------------------------------------
# Pre-processamento (so estatistica do treino)
# --------------------------------------------------------------------------

@dataclass
class Preparador:
    """Winsoriza, imputa pela mediana e padroniza -- tudo aprendido no treino.

    Indicador de ausencia para cada entrada que falta em algum ponto do treino:
    ausencia de receita (holding, pre-operacional) e informacao, nao ruido.
    """
    q: float = HIPERPARAMETROS["winsor_q"]
    lo: dict = field(default_factory=dict)
    hi: dict = field(default_factory=dict)
    med: dict = field(default_factory=dict)
    mu: np.ndarray | None = None
    sd: np.ndarray | None = None
    ind: list = field(default_factory=list)

    def fit(self, X: pd.DataFrame) -> "Preparador":
        for c in FEATURES:
            s = pd.to_numeric(X[c], errors="coerce")
            s = s[np.isfinite(s)]
            if s.empty:
                self.lo[c] = self.hi[c] = self.med[c] = 0.0
                continue
            self.lo[c] = float(s.quantile(self.q))
            self.hi[c] = float(s.quantile(1 - self.q))
            self.med[c] = float(s.clip(self.lo[c], self.hi[c]).median())
        self.ind = [c for c in FEATURES if pd.to_numeric(X[c], errors="coerce").isna().any()]
        Z = self._bruto(X)
        self.mu = Z.mean(axis=0)
        self.sd = Z.std(axis=0)
        self.sd[self.sd <= 1e-12] = 1.0
        return self

    def _bruto(self, X: pd.DataFrame) -> np.ndarray:
        partes = []
        for c in FEATURES:
            s = pd.to_numeric(X[c], errors="coerce").replace([np.inf, -np.inf], np.nan)
            partes.append(s.clip(self.lo[c], self.hi[c]).fillna(self.med[c]).to_numpy(float))
        for c in self.ind:
            partes.append(pd.to_numeric(X[c], errors="coerce").isna().to_numpy(float))
        return np.column_stack(partes) if partes else np.zeros((len(X), 0))

    def transform(self, X: pd.DataFrame) -> np.ndarray:
        return (self._bruto(X) - self.mu) / self.sd


def winsor_alvo(y: np.ndarray, q: float = HIPERPARAMETROS["winsor_q"]):
    """Limites do alvo no treino. O teste NUNCA e winsorizado: o erro medido
    e o do mundo, inclusive a empresa que dobrou o ativo em lucro."""
    y = np.asarray(y, float)
    return float(np.quantile(y, q)), float(np.quantile(y, 1 - q))


# --------------------------------------------------------------------------
# Modelos (numpy puro: sem scikit-learn no app nem no CI)
# --------------------------------------------------------------------------

class Ridge:
    def __init__(self, alpha: float = 1.0):
        self.alpha = float(alpha)

    def fit(self, X, y):
        X = np.asarray(X, float)
        y = np.asarray(y, float)
        # Centra aqui: no sub-fold da validacao interna o X nao tem media zero.
        mx, my = X.mean(axis=0), float(y.mean())
        Xc = X - mx
        A = Xc.T @ Xc + self.alpha * np.eye(X.shape[1])
        self.w = np.linalg.solve(A, Xc.T @ (y - my))
        self.b0 = my - float(mx @ self.w)
        return self

    def predict(self, X):
        return np.asarray(X, float) @ self.w + self.b0


def _ultima_safra_como_validacao(safras: np.ndarray):
    """Validacao interna: a ultima safra do treino. Respeita o tempo."""
    ult = int(np.max(safras))
    return safras < ult, safras == ult


def ridge_com_alpha_interno(X, y, safras) -> Ridge:
    tr, va = _ultima_safra_como_validacao(np.asarray(safras))
    melhor, alpha = None, HIPERPARAMETROS["ridge_alphas"][0]
    if tr.sum() >= 30 and va.sum() >= 10:
        for a in HIPERPARAMETROS["ridge_alphas"]:
            m = Ridge(a).fit(X[tr], y[tr])
            e = float(np.mean(np.abs(m.predict(X[va]) - y[va])))
            if melhor is None or e < melhor:
                melhor, alpha = e, a
    return Ridge(alpha).fit(X, y)


class GBMAbsoluto:
    """Gradient boosting com perda absoluta (LAD-TreeBoost, Friedman 2001).

    Perda absoluta porque a regua e o erro absoluto medio: otimizar quadrado e
    medir absoluto premiaria o ingenuo. Arvores rasas sobre entradas
    discretizadas pelos quantis do TREINO.
    """

    def __init__(self, **kw):
        p = dict(HIPERPARAMETROS["gbm"])
        p.update(kw)
        self.p = p

    def _binarizar(self, X):
        return np.column_stack([np.searchsorted(self.cortes[j], X[:, j], side="right")
                                for j in range(X.shape[1])]).astype(np.int16)

    def fit(self, X, y):
        X = np.asarray(X, float)
        y = np.asarray(y, float)
        nb = self.p["n_bins"]
        qs = np.linspace(0, 1, nb + 1)[1:-1]
        self.cortes = [np.unique(np.quantile(X[:, j], qs)) for j in range(X.shape[1])]
        B = self._binarizar(X)
        rng = np.random.default_rng(self.p["seed"])
        self.f0 = float(np.median(y))
        F = np.full(len(y), self.f0)
        self.arvores = []
        n = len(y)
        for _ in range(self.p["n_arvores"]):
            idx = rng.choice(n, size=max(2, int(self.p["subamostra"] * n)), replace=False)
            r = y - F
            g = np.sign(r)
            arv = self._crescer(B[idx], g[idx], r[idx], 0)
            self.arvores.append(arv)
            F += self.p["taxa"] * self._prever_arvore(arv, B)
        return self

    def _crescer(self, B, g, r, prof):
        if prof >= self.p["profundidade"] or len(g) < 2 * self.p["min_folha"]:
            return ("folha", float(np.median(r)) if len(r) else 0.0)
        melhor = None
        tot, n = g.sum(), len(g)
        mf = self.p["min_folha"]
        for j in range(B.shape[1]):
            nbj = len(self.cortes[j]) + 1
            if nbj < 2:
                continue
            cnt = np.bincount(B[:, j], minlength=nbj)
            sm = np.bincount(B[:, j], weights=g, minlength=nbj)
            cc, cs = np.cumsum(cnt)[:-1], np.cumsum(sm)[:-1]
            ok = (cc >= mf) & (n - cc >= mf)
            if not ok.any():
                continue
            ganho = cs ** 2 / np.maximum(cc, 1) + (tot - cs) ** 2 / np.maximum(n - cc, 1)
            ganho[~ok] = -np.inf
            k = int(np.argmax(ganho))
            if melhor is None or ganho[k] > melhor[0]:
                melhor = (ganho[k], j, k)
        if melhor is None:
            return ("folha", float(np.median(r)))
        _, j, k = melhor
        esq = B[:, j] <= k
        return ("no", j, k, self._crescer(B[esq], g[esq], r[esq], prof + 1),
                self._crescer(B[~esq], g[~esq], r[~esq], prof + 1))

    def _prever_arvore(self, arv, B):
        out = np.empty(len(B))
        pilha = [(arv, np.arange(len(B)))]
        while pilha:
            no, ix = pilha.pop()
            if no[0] == "folha":
                out[ix] = no[1]
                continue
            _, j, k, e, d = no
            m = B[ix, j] <= k
            pilha.append((e, ix[m]))
            pilha.append((d, ix[~m]))
        return out

    def predict(self, X):
        B = self._binarizar(np.asarray(X, float))
        F = np.full(len(B), self.f0)
        for arv in self.arvores:
            F += self.p["taxa"] * self._prever_arvore(arv, B)
        return F


class MLPEnsemble:
    """Ensemble pequeno de perceptrons (1 camada oculta, tanh), perda de Huber.

    Parada antecipada na ultima safra do treino. So vira candidato se vencer
    ingenuo, ridge e GBM no desenvolvimento -- e a regra, nao uma preferencia.
    """

    def __init__(self, **kw):
        p = dict(HIPERPARAMETROS["mlp"])
        p.update(kw)
        self.p = p

    @staticmethod
    def _huber_grad(e, delta):
        return np.clip(e, -delta, delta)

    def _treinar_um(self, X, y, Xv, yv, seed):
        rng = np.random.default_rng(seed)
        d, h = X.shape[1], self.p["ocultos"]
        W1 = rng.normal(0, 1 / math.sqrt(d), (d, h))
        b1 = np.zeros(h)
        W2 = rng.normal(0, 1 / math.sqrt(h), h)
        b2 = float(np.median(y))
        params = [W1, b1, W2, np.array([b2])]
        m = [np.zeros_like(q) for q in params]
        v = [np.zeros_like(q) for q in params]
        delta = max(float(np.median(np.abs(y - np.median(y)))), 1e-4)
        lr, wd = self.p["taxa"], self.p["decaimento"]
        melhor, melhor_p, sem_melhora = np.inf, [q.copy() for q in params], 0
        for ep in range(1, self.p["epocas"] + 1):
            W1, b1, W2, b2 = params
            H = np.tanh(X @ W1 + b1)
            pred = H @ W2 + b2[0]
            gpred = self._huber_grad(pred - y, delta) / len(y)
            gW2 = H.T @ gpred + wd * W2
            gb2 = np.array([gpred.sum()])
            gH = np.outer(gpred, W2) * (1 - H ** 2)
            gW1 = X.T @ gH + wd * W1
            gb1 = gH.sum(axis=0)
            for i, gq in enumerate((gW1, gb1, gW2, gb2)):
                m[i] = 0.9 * m[i] + 0.1 * gq
                v[i] = 0.999 * v[i] + 0.001 * gq ** 2
                mh = m[i] / (1 - 0.9 ** ep)
                vh = v[i] / (1 - 0.999 ** ep)
                params[i] = params[i] - lr * mh / (np.sqrt(vh) + 1e-8)
            if Xv is not None and len(yv):
                W1, b1, W2, b2 = params
                ev = float(np.mean(np.abs(np.tanh(Xv @ W1 + b1) @ W2 + b2[0] - yv)))
                if ev < melhor - 1e-9:
                    melhor, melhor_p, sem_melhora = ev, [q.copy() for q in params], 0
                else:
                    sem_melhora += 1
                    if sem_melhora >= self.p["paciencia"]:
                        break
        return melhor_p if Xv is not None and len(yv) else params

    def fit(self, X, y, safras=None):
        X = np.asarray(X, float)
        y = np.asarray(y, float)
        if safras is not None:
            tr, va = _ultima_safra_como_validacao(np.asarray(safras))
            if tr.sum() < 30 or va.sum() < 10:
                tr, va = np.ones(len(y), bool), np.zeros(len(y), bool)
        else:
            tr, va = np.ones(len(y), bool), np.zeros(len(y), bool)
        self.membros = [self._treinar_um(X[tr], y[tr], X[va] if va.any() else None,
                                         y[va], self.p["seed"] + k)
                        for k in range(self.p["membros"])]
        return self

    def predict(self, X):
        X = np.asarray(X, float)
        return np.mean([np.tanh(X @ W1 + b1) @ W2 + b2[0]
                        for W1, b1, W2, b2 in self.membros], axis=0)


# --------------------------------------------------------------------------
# Walk-forward
# --------------------------------------------------------------------------

def treinar_e_prever(treino: pd.DataFrame, alvo: pd.DataFrame, *,
                     com_mlp: bool = True) -> dict[str, np.ndarray]:
    """Treina todos os modelos na dobra e preve ``alvo``.

    ``alvo`` pode nao ter y (a safra viva): nada aqui olha para ele alem das
    entradas.
    """
    prep = Preparador().fit(treino)
    Xtr, Xte = prep.transform(treino), prep.transform(alvo)
    lo, hi = winsor_alvo(treino["y"].to_numpy(float))
    ytr = np.clip(treino["y"].to_numpy(float), lo, hi)
    saf = treino["safra"].to_numpy(int)
    out = {"ingenuo": np.zeros(len(alvo))}
    out["ridge"] = ridge_com_alpha_interno(Xtr, ytr, saf).predict(Xte)
    out["gbm"] = GBMAbsoluto().fit(Xtr, ytr).predict(Xte)
    if com_mlp:
        out["mlp"] = MLPEnsemble().fit(Xtr, ytr, saf).predict(Xte)
    # Baseline de SINAL: o sinal majoritario do treino para todo mundo. O
    # ingenuo de nivel preve variacao zero e nao tem sinal; acertar 60% num
    # ano de inflacao em que 60% sobem nao e habilidade.
    out["sinal_majoritario"] = np.full(len(alvo), 1.0 if np.mean(ytr > 0) >= 0.5 else -1.0)
    return out


def walk_forward(painel: pd.DataFrame, safras: list[int], *,
                 com_mlp: bool = True, log=None) -> pd.DataFrame:
    """Previsoes fora da amostra, uma safra por vez, so com o que era publico."""
    linhas = []
    for s in safras:
        tr = conjunto_treino(painel, s)
        te = conjunto_teste(painel, s)
        if tr.empty or te.empty:
            continue
        prev = treinar_e_prever(tr, te, com_mlp=com_mlp)
        df = te[["cd_cvm", "safra", "ano_base", "y", "lucro_base", "ativo_base",
                 "lucro_alvo"]].copy()
        for k, v in prev.items():
            df[f"pred_{k}"] = v
        df["n_treino"] = len(tr)
        linhas.append(df)
        if log:
            log(f"safra {s}: treino={len(tr)} teste={len(te)} com_alvo={int(te['y'].notna().sum())}")
    if not linhas:
        return pd.DataFrame()
    return pd.concat(linhas, ignore_index=True)


# --------------------------------------------------------------------------
# Metricas e testes
# --------------------------------------------------------------------------

def wilson(k: int, n: int, conf: float = 0.95) -> tuple[float | None, float | None]:
    if n <= 0:
        return None, None
    from scipy.stats import norm
    z = float(norm.ppf(1 - (1 - conf) / 2))
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, c - h), min(1.0, c + h)


def p_binomial_maior(k: int, n: int, p0: float = 0.5) -> float | None:
    """P(X >= k | p0): o acerto de sinal e maior que o acaso?"""
    if n <= 0:
        return None
    from scipy.stats import binomtest
    return float(binomtest(int(k), int(n), p0, alternative="greater").pvalue)


def p_mcnemar(acerto_a: np.ndarray, acerto_b: np.ndarray) -> dict:
    """McNemar exato unilateral: A acerta mais que B nas MESMAS empresas?"""
    a = np.asarray(acerto_a, bool)
    b = np.asarray(acerto_b, bool)
    so_a, so_b = int((a & ~b).sum()), int((~a & b).sum())
    n = so_a + so_b
    return {"so_modelo": so_a, "so_base": so_b,
            "p": p_binomial_maior(so_a, n, 0.5) if n else None}


def _acerto_sinal(y, p):
    y = np.asarray(y, float)
    p = np.asarray(p, float)
    m = (y != 0) & (p != 0) & np.isfinite(y) & np.isfinite(p)
    return (np.sign(y[m]) == np.sign(p[m])), m


def metricas(df: pd.DataFrame, modelos) -> dict:
    """Erro e sinal de cada modelo sobre as linhas COM alvo."""
    d = df[df["y"].notna()]
    y = d["y"].to_numpy(float)
    out = {"n": int(len(d))}
    for k in modelos:
        p = d[f"pred_{k}"].to_numpy(float)
        e = np.abs(p - y)
        r = {"mae": float(e.mean()) if len(e) else None,
             "medae": float(np.median(e)) if len(e) else None,
             "rmse": float(np.sqrt(np.mean((p - y) ** 2))) if len(e) else None}
        if k != "ingenuo":
            ac, _ = _acerto_sinal(y, p)
            kk, nn = int(ac.sum()), int(len(ac))
            lo, hi = wilson(kk, nn)
            r.update({"acerto_sinal": kk / nn if nn else None, "n_sinal": nn,
                      "sinal_ic_low": lo, "sinal_ic_high": hi,
                      "p_sinal_vs_50": p_binomial_maior(kk, nn, 0.5)})
        out[k] = r
    return out


def metricas_por_safra(preds: pd.DataFrame, modelos) -> list[dict]:
    linhas = []
    for s, g in preds.groupby("safra"):
        m = metricas(g, modelos)
        m["safra"] = int(s)
        m["n_previstas"] = int(len(g))
        m["n_sem_alvo"] = int(g["y"].isna().sum())
        linhas.append(m)
    return linhas


def bootstrap_diferenca_por_empresa(dif: np.ndarray, clusters: np.ndarray, *,
                                    amostras: int = HIPERPARAMETROS["bootstrap_amostras"],
                                    conf: float = 0.95, seed: int = 42) -> dict:
    """IC da media de uma diferenca pareada, reamostrando EMPRESAS.

    A mesma empresa aparece em varias safras e seus erros andam juntos;
    reamostrar linhas soltas finge independencia e estreita o intervalo.
    """
    dif = np.asarray(dif, float)
    cl = np.asarray(clusters)
    if len(dif) < 2:
        return {"media": None, "ic_low": None, "ic_high": None, "n": int(len(dif))}
    uni, inv = np.unique(cl, return_inverse=True)
    soma = np.bincount(inv, weights=dif)
    cont = np.bincount(inv)
    rng = np.random.default_rng(seed)
    medias = np.empty(amostras)
    for i in range(amostras):
        pick = rng.integers(0, len(uni), len(uni))
        medias[i] = soma[pick].sum() / cont[pick].sum()
    a = (1 - conf) / 2
    return {"media": float(dif.mean()), "ic_low": float(np.quantile(medias, a)),
            "ic_high": float(np.quantile(medias, 1 - a)), "n": int(len(dif)),
            "n_empresas": int(len(uni))}


def comparar_com_ingenuo(df: pd.DataFrame, modelo: str) -> dict:
    """Veredito de um modelo contra o ingenuo e contra o sinal majoritario.

    APROVADO so se o IC da diferenca de erro absoluto (modelo - ingenuo),
    reamostrado por empresa, ficar inteiro abaixo de zero.
    """
    d = df[df["y"].notna()]
    y = d["y"].to_numpy(float)
    p = d[f"pred_{modelo}"].to_numpy(float)
    e_mod, e_ing = np.abs(p - y), np.abs(y)
    dif = bootstrap_diferenca_por_empresa(e_mod - e_ing, d["cd_cvm"].to_numpy())
    mae_m, mae_i = float(e_mod.mean()), float(e_ing.mean())

    ac_m, msk_m = _acerto_sinal(y, p)
    base = d["pred_sinal_majoritario"].to_numpy(float)
    ac_b, msk_b = _acerto_sinal(y, base)
    comum = msk_m & msk_b
    acm = np.sign(y[comum]) == np.sign(p[comum])
    acb = np.sign(y[comum]) == np.sign(base[comum])
    k, n = int(acm.sum()), int(len(acm))
    lo, hi = wilson(k, n)
    mc = p_mcnemar(acm, acb)
    erro_aprovado = dif["ic_high"] is not None and dif["ic_high"] < 0
    sinal_aprovado = (n > 0 and (p_binomial_maior(k, n, 0.5) or 1) < 0.05
                      and (mc["p"] if mc["p"] is not None else 1) < 0.05)
    return {
        "modelo": modelo, "n": int(len(d)), "n_empresas": dif.get("n_empresas"),
        "mae_modelo": mae_m, "mae_ingenuo": mae_i,
        "razao_mae": mae_m / mae_i if mae_i > 0 else None,
        "dif_erro_abs": dif,
        "erro": "APROVADO" if erro_aprovado else "REPROVADO",
        "acerto_sinal": k / n if n else None, "n_sinal": n,
        "sinal_ic_low": lo, "sinal_ic_high": hi,
        "p_sinal_vs_50": p_binomial_maior(k, n, 0.5),
        "acerto_sinal_majoritario": float(acb.mean()) if n else None,
        "mcnemar_vs_majoritario": mc,
        "sinal": "APROVADO" if sinal_aprovado else "REPROVADO",
        "veredito": "APROVADO" if erro_aprovado else "REPROVADO",
    }


def escolher_modelo(dev: pd.DataFrame, com_mlp: bool = True) -> dict:
    """Regra declarada: o candidato de menor MAE no desenvolvimento.

    A MLP so e candidata se tiver MAE menor que ingenuo, ridge E gbm.
    """
    m = metricas(dev, [k for k in ("ingenuo", "ridge", "gbm", "mlp")
                       if f"pred_{k}" in dev.columns and (k != "mlp" or com_mlp)])
    maes = {k: m[k]["mae"] for k in ("ingenuo", "ridge", "gbm", "mlp") if k in m}
    mlp_ok = "mlp" in maes and all(maes["mlp"] < maes[k] for k in MODELOS_BASE)
    cand = {k: maes[k] for k in ("ridge", "gbm")}
    if mlp_ok:
        cand["mlp"] = maes["mlp"]
    escolhido = min(cand, key=cand.get)
    return {"modelo": escolhido, "mae_dev": maes, "mlp_elegivel": bool(mlp_ok),
            "regra": "menor MAE no desenvolvimento entre ridge e gbm; a MLP so "
                     "concorre se vencer ingenuo, ridge e gbm"}


# --------------------------------------------------------------------------
# Intervalo da previsao
# --------------------------------------------------------------------------

def intervalo_conformal(residuos: np.ndarray, nivel: float = HIPERPARAMETROS["nivel_intervalo"]):
    """Quantis empiricos de (y - previsao) fora da amostra. Sem hipotese de
    normalidade: a cauda do lucro e gorda e o intervalo tem de mostrar isso."""
    r = np.asarray(residuos, float)
    r = r[np.isfinite(r)]
    if len(r) < 20:
        return None, None
    a = (1 - nivel) / 2
    return float(np.quantile(r, a)), float(np.quantile(r, 1 - a))


def intervalos_pit(preds: pd.DataFrame, modelo: str,
                   nivel: float = HIPERPARAMETROS["nivel_intervalo"]) -> pd.DataFrame:
    """Intervalo de cada safra com os residuos das safras ANTERIORES (cujo
    alvo ja era publico). A cobertura no holdout e medida, nao suposta."""
    out = preds.copy()
    out["ic_low"] = np.nan
    out["ic_high"] = np.nan
    for s in sorted(out["safra"].unique()):
        ant = out[(out["safra"] < s) & out["y"].notna()]
        lo, hi = intervalo_conformal(ant["y"] - ant[f"pred_{modelo}"], nivel)
        if lo is None:
            continue
        m = out["safra"] == s
        out.loc[m, "ic_low"] = out.loc[m, f"pred_{modelo}"] + lo
        out.loc[m, "ic_high"] = out.loc[m, f"pred_{modelo}"] + hi
    return out


def cobertura(df: pd.DataFrame) -> dict:
    d = df[df["y"].notna() & df["ic_low"].notna()]
    if d.empty:
        return {"n": 0, "cobertura": None}
    dentro = (d["y"] >= d["ic_low"]) & (d["y"] <= d["ic_high"])
    return {"n": int(len(d)), "cobertura": float(dentro.mean())}


# --------------------------------------------------------------------------
# Efeito no Rank-IC do score B3
# --------------------------------------------------------------------------

def _pct_rank(x: pd.Series) -> pd.Series:
    return x.rank(pct=True, method="average")


def efeito_rank_ic(pares_tk: list[tuple], previsao: dict[tuple[int, str], float], *,
                   peso_previsao: float = 0.5) -> dict:
    """Rank-IC do score, da previsao e da combinacao, NO MESMO subconjunto.

    ``pares_tk``: (ano, ticker, score, retorno) que a aba de portfolio B3
    montou (score de abril/N contra retorno abril/N -> marco/N+1).
    ``previsao``: (safra, ticker) -> y previsto (variacao do lucro / ativo).

    Comparar o IC do score no universo inteiro com o da combinacao num
    subconjunto mediria a mudanca de populacao, nao a previsao. Por isso os
    tres ICs saem das mesmas empresas; o do universo inteiro vai junto so
    como referencia. Peso 50/50 declarado antes -- nao otimizado.
    """
    from core.b3_pooled_evidence import pooled_yearly_ics
    from core.us_backtest import bootstrap_mean_ci

    df = pd.DataFrame(pares_tk, columns=["ano", "ticker", "score", "ret"])
    if df.empty:
        return {"status": "sem_pares"}
    df["pred"] = [previsao.get((int(a), str(t))) for a, t in zip(df["ano"], df["ticker"])]
    df["pred"] = pd.to_numeric(df["pred"], errors="coerce")
    ic_universo = pooled_yearly_ics(list(zip(df["ano"], df["score"], df["ret"])))
    sub = df.dropna(subset=["score", "ret", "pred"]).copy()
    sub["comb"] = sub.groupby("ano")["score"].transform(_pct_rank) * (1 - peso_previsao) \
        + sub.groupby("ano")["pred"].transform(_pct_rank) * peso_previsao
    ic_s = pooled_yearly_ics(list(zip(sub["ano"], sub["score"], sub["ret"])))
    ic_p = pooled_yearly_ics(list(zip(sub["ano"], sub["pred"], sub["ret"])))
    ic_c = pooled_yearly_ics(list(zip(sub["ano"], sub["comb"], sub["ret"])))
    anos = sorted(set(ic_s) & set(ic_c))
    delta = [ic_c[a] - ic_s[a] for a in anos]
    amostras = HIPERPARAMETROS["bootstrap_amostras"]
    ci = lambda v: bootstrap_mean_ci(v, samples=amostras)  # noqa: E731
    ci_d = ci(delta)
    melhora = ci_d["ci_low"] is not None and ci_d["ci_low"] > 0
    return {
        "status": "medido",
        "peso_previsao": peso_previsao,
        "n_pares_score": int(len(df)),
        "n_pares_com_previsao": int(len(sub)),
        "cobertura_previsao": float(len(sub) / len(df)) if len(df) else None,
        "ic_por_ano": {str(a): {"score": ic_s.get(a), "previsao": ic_p.get(a),
                                "combinado": ic_c.get(a), "delta": ic_c[a] - ic_s[a],
                                "score_universo": ic_universo.get(a)}
                       for a in anos},
        "score_universo": ci(list(ic_universo.values())),
        "score": ci([ic_s[a] for a in anos]),
        "previsao": ci([ic_p[a] for a in anos if a in ic_p]),
        "combinado": ci([ic_c[a] for a in anos]),
        "delta": ci_d,
        "veredito": "MELHORA" if melhora else "REPROVADO",
    }


# --------------------------------------------------------------------------
# Leitura do artefato (para a aba)
# --------------------------------------------------------------------------

ARTEFATO = "data/public/previsao_lucros_b3.json.gz"


def carregar_artefato(caminho: str | None = None) -> dict | None:
    import gzip
    from pathlib import Path
    p = Path(caminho) if caminho else Path(__file__).resolve().parents[1] / ARTEFATO
    if not p.exists():
        return None
    try:
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None
