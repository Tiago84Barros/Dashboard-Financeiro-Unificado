# Fase 2b — Correlação, Fatores e Risco: Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Completar a seção Portfólio Global com correlação entre ativos, redundância, exposição a fatores de risco e métricas de risco do patrimônio.

**Architecture:** Quatro módulos puros novos em `core/global_portfolio/`, alimentados por uma única leitura de séries mensais, mais três painéis na view existente. Nenhum módulo de análise toca SQL ou Streamlit.

**Tech Stack:** Python 3.12, pandas, numpy, statsmodels (já em uso no projeto via `ff_risk_model`), pytest.

## Global Constraints

- **Aditividade:** nada existente é removido ou reescrito. As únicas alterações em arquivo pré-existente são acréscimos ao final de `views/portfolio_global.py`.
- **Camadas:** `core/global_portfolio/*` não executa SQL, não importa Streamlit e não faz I/O. Séries chegam por parâmetro ou por um `loader` injetável.
- **Cobertura explícita e nomeada.** Os preços americanos **não existem no Supabase** (`market_us.prices_daily` e `prices_monthly` ausentes; só a vitrine de scores foi publicada). Correlação, fatores e risco cobrem B3 e FIIs — hoje 61,5% do patrimônio. Toda saída publica a fração coberta **e nomeia os ativos de fora**. Nunca apresentar um número calculado sobre parte da carteira como se fosse do todo.
- **Base mensal, não diária.** `market.historical_prices` é mensal (`load_precos_mensais`). O §6.4 da spec diz "diários" — está errado e este plano corrige.
- **Piso de observações:** `MIN_OBS_CORRELACAO = 18` (de `core/b3_correlation_diversification.py`). Par com menos sobreposição não gera correlação; conta como não coberto.
- **Determinismo:** nenhuma saída depende de ordem de iteração de `dict`/`set`.
- **Idioma:** comentários, docstrings e textos de interface em português.
- **UI:** métricas em cards via `design.componentes.card_metrica`. Nunca `_kpi_html` local.
- **Interpretador:** `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest`
- **Baseline da suíte:** `1696 passed, 3 skipped, 0 failed`.
- **Costura real, não fake:** onde dois módulos se encontram, pelo menos um teste exercita os dois lados de verdade. Foi o que faltou nos defeitos mais caros das fases anteriores.

---

## Fatores disponíveis — verificado em produção

Todos em `market.historical_prices`, mensais, em BRL:

| Proxy | Meses | Fator canônico |
|---|---|---|
| `BOVA11` | 236 | `mercado_br` |
| `SMAL11` | 238 | `small_cap` (spread SMAL−BOVA) |
| `IVVB11` | 125 | `global_dolar` (S&P 500 em BRL) |
| `IRFM11` | 108 | `juros_nominais` |
| `GOLD11` | 95 | `ouro` |
| `IMAB11` | 62 | `inflacao` (IPCA+) |

`B5P211` (45) e `FIXA11` (42) existem mas ficam de fora: janela curta demais e redundantes com `IMAB11`/`IRFM11`.

---

## Estrutura de arquivos

| Arquivo | Responsabilidade |
|---|---|
| `core/global_portfolio/returns.py` | Retornos mensais dos ativos da carteira + relatório de cobertura |
| `core/global_portfolio/correlation.py` | Matriz, pares redundantes, clusters, razão de diversificação, apostas efetivas |
| `core/global_portfolio/factors.py` | Betas contra os seis proxies + camada qualitativa de fallback |
| `core/global_portfolio/risk.py` | Volatilidade do patrimônio, VaR/CVaR, drawdown sintético |
| `views/portfolio_global.py` (modificar) | Três painéis novos ao final |

---

### Task 1: Retornos mensais e cobertura

**Files:**
- Create: `core/global_portfolio/returns.py`
- Test: `tests/test_global_returns.py`

**Interfaces:**
- Consumes: `core.market_read.load_precos_mensais(tickers: tuple[str, ...]) -> pd.DataFrame` (índice mensal × colunas = tickers).
- Produces:
  - `MIN_OBS: int = 18`
  - `Cobertura` — dataclass congelada: `simbolos_com_serie: tuple[str, ...]`, `simbolos_sem_serie: tuple[str, ...]`, `peso_coberto: float`, `meses: int`.
  - `retornos_mensais(df_posicoes, *, loader=None) -> tuple[pd.DataFrame, Cobertura]`

Regras:
- Busca série apenas para posições cujo `asset_class` seja `b3` ou `fii`; `us` entra direto em `simbolos_sem_serie` — não há preço americano no banco.
- Retorno mensal simples: `precos.pct_change().dropna(how="all")`.
- Símbolo com menos de `MIN_OBS` observações válidas sai do quadro e conta como sem série.
- `peso_coberto` = soma de `weight_global` dos símbolos com série, dividida pelo peso total do quadro.
- `meses` = número de linhas do quadro de retornos.
- Ordenação determinística: colunas em ordem alfabética; as duas tuplas de símbolos ordenadas.
- Quadro vazio devolve `(DataFrame vazio, Cobertura zerada)` sem levantar.

- [ ] **Step 1: Escrever o teste que falha**

```python
"""Retornos mensais dos ativos da carteira e relatorio de cobertura."""
import numpy as np
import pandas as pd
import pytest

from core.global_portfolio.returns import MIN_OBS, retornos_mensais


def _posicoes():
    return pd.DataFrame([
        {"asset_class": "b3", "symbol": "PETR4", "weight_global": 0.3},
        {"asset_class": "fii", "symbol": "HGLG11", "weight_global": 0.3},
        {"asset_class": "us", "symbol": "AAPL", "weight_global": 0.4},
    ])


def _precos(tickers, meses=36):
    idx = pd.date_range("2023-01-31", periods=meses, freq="ME")
    rng = np.random.default_rng(7)
    return pd.DataFrame(
        {t: 100 * np.cumprod(1 + rng.normal(0.01, 0.05, meses)) for t in tickers},
        index=idx,
    )


def _loader(disponiveis=("PETR4", "HGLG11"), meses=36):
    def carregar(tickers):
        alvo = [t for t in tickers if t in disponiveis]
        return _precos(alvo, meses) if alvo else pd.DataFrame()
    return carregar


def test_us_nunca_entra_na_busca_de_precos():
    pedidos = []

    def espiao(tickers):
        pedidos.append(tuple(sorted(tickers)))
        return _precos([t for t in tickers if t != "AAPL"])

    retornos_mensais(_posicoes(), loader=espiao)
    assert pedidos == [("HGLG11", "PETR4")]


def test_us_aparece_como_sem_serie():
    _, cob = retornos_mensais(_posicoes(), loader=_loader())
    assert cob.simbolos_sem_serie == ("AAPL",)
    assert cob.simbolos_com_serie == ("HGLG11", "PETR4")


def test_peso_coberto_e_a_fracao_com_serie():
    _, cob = retornos_mensais(_posicoes(), loader=_loader())
    assert cob.peso_coberto == pytest.approx(0.6)


def test_retornos_sao_variacao_percentual_mensal():
    ret, _ = retornos_mensais(_posicoes(), loader=_loader(meses=25))
    assert list(ret.columns) == ["HGLG11", "PETR4"]
    assert len(ret) == 24            # 25 precos -> 24 retornos
    assert ret.abs().max().max() < 1.0


def test_simbolo_com_serie_curta_e_descartado():
    ret, cob = retornos_mensais(_posicoes(), loader=_loader(meses=MIN_OBS))
    # MIN_OBS precos -> MIN_OBS-1 retornos, abaixo do piso
    assert ret.empty
    assert cob.simbolos_com_serie == ()
    assert set(cob.simbolos_sem_serie) == {"AAPL", "HGLG11", "PETR4"}


def test_meses_reflete_o_tamanho_do_quadro():
    ret, cob = retornos_mensais(_posicoes(), loader=_loader(meses=30))
    assert cob.meses == len(ret) == 29


def test_quadro_vazio_nao_levanta():
    vazio = pd.DataFrame(columns=["asset_class", "symbol", "weight_global"])
    ret, cob = retornos_mensais(vazio, loader=_loader())
    assert ret.empty
    assert cob.peso_coberto == 0.0
    assert cob.simbolos_com_serie == ()


def test_colunas_em_ordem_deterministica():
    ret, _ = retornos_mensais(_posicoes(), loader=_loader(("HGLG11", "PETR4")))
    assert list(ret.columns) == sorted(ret.columns)
```

- [ ] **Step 2: Rodar e confirmar que falha**

Expected: `ModuleNotFoundError: No module named 'core.global_portfolio.returns'`

- [ ] **Step 3: Implementar**

```python
"""Retornos mensais dos ativos da carteira, com cobertura explicita.

Os precos americanos NAO existem no Supabase: so a vitrine de scores
(market_us.company_snapshots) foi publicada; market_us.prices_daily e
prices_monthly ficaram no armazem local. Por isso todo ativo da classe `us`
entra direto em simbolos_sem_serie — nao e falha, e ausencia de dado, e a
interface precisa dizer isso em vez de exibir um numero parcial como se fosse
do patrimonio inteiro.

Coberto por tests/test_global_returns.py.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

MIN_OBS = 18                      # mesmo piso de core/b3_correlation_diversification
_CLASSES_COM_PRECO = ("b3", "fii")


@dataclass(frozen=True)
class Cobertura:
    """Quanto do patrimonio a serie de precos alcanca."""

    simbolos_com_serie: tuple[str, ...]
    simbolos_sem_serie: tuple[str, ...]
    peso_coberto: float
    meses: int


_VAZIA = Cobertura((), (), 0.0, 0)


def _default_loader(tickers: tuple[str, ...]) -> pd.DataFrame:
    from core.market_read import load_precos_mensais
    return load_precos_mensais(tickers)


def retornos_mensais(df_posicoes: pd.DataFrame,
                     *, loader=None) -> tuple[pd.DataFrame, Cobertura]:
    """Retornos mensais dos ativos com serie, mais o relatorio de cobertura."""
    if df_posicoes is None or df_posicoes.empty:
        return pd.DataFrame(), _VAZIA

    loader = loader or _default_loader
    linhas = df_posicoes.to_dict(orient="records")
    peso_total = sum(float(l.get("weight_global") or 0.0) for l in linhas)

    candidatos = sorted({
        str(l["symbol"]) for l in linhas
        if str(l.get("asset_class") or "").lower() in _CLASSES_COM_PRECO
    })
    sem_preco = sorted({
        str(l["symbol"]) for l in linhas
        if str(l.get("asset_class") or "").lower() not in _CLASSES_COM_PRECO
    })

    precos = loader(tuple(candidatos)) if candidatos else pd.DataFrame()
    if not isinstance(precos, pd.DataFrame) or precos.empty:
        return pd.DataFrame(), Cobertura((), tuple(sorted(sem_preco + candidatos)), 0.0, 0)

    retornos = precos.sort_index().pct_change().dropna(how="all")
    # Serie curta nao sustenta correlacao: sai e conta como nao coberta.
    validos = sorted(c for c in retornos.columns if retornos[c].count() >= MIN_OBS)
    descartados = [c for c in retornos.columns if c not in validos]
    retornos = retornos[validos] if validos else pd.DataFrame()

    faltantes = sorted(set(sem_preco) | set(descartados)
                       | (set(candidatos) - set(retornos.columns)))
    peso_ok = sum(float(l.get("weight_global") or 0.0) for l in linhas
                  if str(l["symbol"]) in set(retornos.columns))

    return retornos, Cobertura(
        simbolos_com_serie=tuple(retornos.columns),
        simbolos_sem_serie=tuple(faltantes),
        peso_coberto=(peso_ok / peso_total) if peso_total > 0 else 0.0,
        meses=len(retornos),
    )
```

- [ ] **Step 4: Rodar e confirmar 8 passed**
- [ ] **Step 5: Commit** — `feat(global): retornos mensais dos ativos com cobertura explicita`

---

### Task 2: Correlação e redundância

**Files:**
- Create: `core/global_portfolio/correlation.py`
- Test: `tests/test_global_correlation.py`

**Interfaces:**
- Consumes: `core.b3_correlation_diversification.correlation_matrix`, `high_correlation_pairs`, `MIN_OBS_CORRELACAO`; o quadro de `retornos_mensais`.
- Produces:
  - `LIMIAR_REDUNDANCIA: float = 0.80`
  - `matriz(retornos) -> pd.DataFrame`
  - `pares_redundantes(retornos, limiar=LIMIAR_REDUNDANCIA) -> list[tuple[str, str, float]]` — ordenado por correlação decrescente, depois pelos símbolos.
  - `correlacao_media(retornos) -> float | None`
  - `razao_diversificacao(retornos, pesos: dict[str, float]) -> float | None` — `(Σ wᵢσᵢ) / σₚ`.
  - `apostas_efetivas(retornos, pesos: dict[str, float]) -> float | None` — número efetivo de apostas por PCA da matriz de covariância ponderada.

Regras:
- Menos de dois ativos com série: tudo devolve `None` ou lista vazia, sem levantar.
- Pesos renormalizados sobre os símbolos presentes no quadro de retornos.
- `razao_diversificacao` é 1,0 quando todos os ativos são perfeitamente correlacionados e cresce conforme a diversificação real aumenta — é a medida que distingue "muitos ativos" de "muitas apostas".

- [ ] **Step 1: Escrever o teste que falha**

```python
"""Correlacao, redundancia e diversificacao real."""
import numpy as np
import pandas as pd
import pytest

from core.global_portfolio.correlation import (
    LIMIAR_REDUNDANCIA,
    apostas_efetivas,
    correlacao_media,
    matriz,
    pares_redundantes,
    razao_diversificacao,
)


def _retornos_com_correlacao(rho: float, n: int = 60, cols=("A", "B")):
    rng = np.random.default_rng(11)
    base = rng.normal(0, 0.05, n)
    ruido = rng.normal(0, 0.05, n)
    b = rho * base + np.sqrt(max(0.0, 1 - rho ** 2)) * ruido
    idx = pd.date_range("2021-01-31", periods=n, freq="ME")
    return pd.DataFrame({cols[0]: base, cols[1]: b}, index=idx)


def test_matriz_recupera_a_correlacao_plantada():
    ret = _retornos_com_correlacao(0.9)
    assert matriz(ret).loc["A", "B"] == pytest.approx(0.9, abs=0.08)


def test_pares_redundantes_encontra_o_par_alto():
    ret = _retornos_com_correlacao(0.95)
    pares = pares_redundantes(ret)
    assert len(pares) == 1
    a, b, c = pares[0]
    assert {a, b} == {"A", "B"}
    assert c > LIMIAR_REDUNDANCIA


def test_pares_redundantes_ignora_correlacao_baixa():
    assert pares_redundantes(_retornos_com_correlacao(0.1)) == []


def test_correlacao_media_de_ativos_independentes_e_proxima_de_zero():
    assert correlacao_media(_retornos_com_correlacao(0.0)) == pytest.approx(0.0, abs=0.15)


def test_razao_de_diversificacao_e_um_quando_tudo_e_identico():
    ret = _retornos_com_correlacao(1.0)
    r = razao_diversificacao(ret, {"A": 0.5, "B": 0.5})
    assert r == pytest.approx(1.0, abs=0.02)


def test_razao_de_diversificacao_cresce_com_independencia():
    identico = razao_diversificacao(_retornos_com_correlacao(0.99), {"A": .5, "B": .5})
    independente = razao_diversificacao(_retornos_com_correlacao(0.0), {"A": .5, "B": .5})
    assert independente > identico


def test_apostas_efetivas_de_dois_independentes_se_aproxima_de_dois():
    ret = _retornos_com_correlacao(0.0)
    assert apostas_efetivas(ret, {"A": 0.5, "B": 0.5}) == pytest.approx(2.0, abs=0.4)


def test_apostas_efetivas_de_dois_identicos_se_aproxima_de_um():
    ret = _retornos_com_correlacao(1.0)
    assert apostas_efetivas(ret, {"A": 0.5, "B": 0.5}) == pytest.approx(1.0, abs=0.3)


def test_um_ativo_so_nao_levanta():
    ret = _retornos_com_correlacao(0.5)[["A"]]
    assert pares_redundantes(ret) == []
    assert correlacao_media(ret) is None
    assert razao_diversificacao(ret, {"A": 1.0}) is None


def test_quadro_vazio_nao_levanta():
    vazio = pd.DataFrame()
    assert matriz(vazio).empty
    assert pares_redundantes(vazio) == []
    assert correlacao_media(vazio) is None
    assert apostas_efetivas(vazio, {}) is None
```

- [ ] **Step 2: Rodar e confirmar que falha**
- [ ] **Step 3: Implementar**

```python
"""Correlacao entre os ativos do patrimonio e diversificacao real.

Contar ativos nao mede diversificacao: onze FIIs de logistica sao uma aposta,
nao onze. A razao de diversificacao e o numero efetivo de apostas respondem a
essa pergunta; a contagem simples nao.

Reaproveita core/b3_correlation_diversification, que ja implementa a matriz com
piso de observacoes e a busca de pares altos.

Coberto por tests/test_global_correlation.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.b3_correlation_diversification import (
    correlation_matrix,
    high_correlation_pairs,
)

LIMIAR_REDUNDANCIA = 0.80


def matriz(retornos: pd.DataFrame) -> pd.DataFrame:
    """Matriz de correlacao dos retornos mensais."""
    if not isinstance(retornos, pd.DataFrame) or retornos.shape[1] < 2:
        return pd.DataFrame()
    return correlation_matrix(retornos)


def pares_redundantes(retornos: pd.DataFrame,
                      limiar: float = LIMIAR_REDUNDANCIA) -> list[tuple[str, str, float]]:
    """Pares acima do limiar, do mais correlacionado ao menos."""
    corr = matriz(retornos)
    if corr.empty:
        return []
    brutos = high_correlation_pairs(corr, threshold=limiar)
    saida = [(str(a), str(b), float(c)) for a, b, c in brutos]
    return sorted(saida, key=lambda t: (-t[2], t[0], t[1]))


def correlacao_media(retornos: pd.DataFrame) -> float | None:
    """Correlacao media entre pares distintos, ou None se nao houver par."""
    corr = matriz(retornos)
    if corr.empty:
        return None
    valores = corr.to_numpy(dtype=float)
    triangulo = valores[np.triu_indices_from(valores, k=1)]
    triangulo = triangulo[~np.isnan(triangulo)]
    return float(triangulo.mean()) if triangulo.size else None


def _pesos_alinhados(retornos: pd.DataFrame, pesos: dict) -> np.ndarray | None:
    if not isinstance(retornos, pd.DataFrame) or retornos.shape[1] < 2:
        return None
    w = np.array([float(pesos.get(c, 0.0)) for c in retornos.columns], dtype=float)
    total = w.sum()
    return (w / total) if total > 0 else None


def razao_diversificacao(retornos: pd.DataFrame, pesos: dict) -> float | None:
    """(soma de wi*sigma_i) / sigma_p. 1,0 = nenhuma diversificacao real."""
    w = _pesos_alinhados(retornos, pesos)
    if w is None:
        return None
    sigmas = retornos.std(ddof=1).to_numpy(dtype=float)
    cov = retornos.cov(ddof=1).to_numpy(dtype=float)
    sigma_p = float(np.sqrt(max(w @ cov @ w, 0.0)))
    if sigma_p <= 0:
        return None
    return float((w * sigmas).sum() / sigma_p)


def apostas_efetivas(retornos: pd.DataFrame, pesos: dict) -> float | None:
    """Numero efetivo de apostas independentes, por decomposicao espectral.

    Projeta os pesos nos componentes principais da covariancia e devolve o
    inverso do HHI dessas contribuicoes: dois ativos identicos dao ~1, dois
    independentes de peso igual dao ~2.
    """
    w = _pesos_alinhados(retornos, pesos)
    if w is None:
        return None
    cov = retornos.cov(ddof=1).to_numpy(dtype=float)
    autovalores, autovetores = np.linalg.eigh(cov)
    contrib = (autovetores.T @ w) ** 2 * np.clip(autovalores, 0.0, None)
    total = contrib.sum()
    if total <= 0:
        return None
    p = contrib / total
    return float(1.0 / np.square(p).sum())
```

- [ ] **Step 4: Rodar e confirmar 10 passed**
- [ ] **Step 5: Commit** — `feat(global): correlacao, redundancia e diversificacao real`

---

### Task 3: Exposição a fatores

**Files:**
- Create: `core/global_portfolio/factors.py`
- Test: `tests/test_global_factors.py`

**Interfaces:**
- Consumes: o `loader` de preços mensais (mesmo de Task 1); o quadro de retornos.
- Produces:
  - `PROXIES: dict[str, str]` — fator canônico → ticker do proxy.
  - `ROTULOS_FATOR: dict[str, str]` — fator → rótulo em português.
  - `MIN_OBS_REGRESSAO: int = 24`
  - `Exposicao` — dataclass congelada: `fator`, `beta`, `erro_padrao`, `r2`, `n_obs`, `significativo` (property: `abs(beta) > 2 * erro_padrao`).
  - `series_de_fatores(*, loader=None) -> pd.DataFrame` — retornos mensais dos proxies, colunas = fatores canônicos, com `small_cap` como spread `SMAL11 − BOVA11`.
  - `betas_do_ativo(retornos_ativo: pd.Series, fatores: pd.DataFrame) -> list[Exposicao]`
  - `exposicao_do_portfolio(retornos, pesos, fatores) -> list[Exposicao]` — regride o retorno do portfólio sintético.

Regras:
- Regressão por mínimos quadrados com intercepto, sobre a interseção de datas.
- Menos de `MIN_OBS_REGRESSAO` observações comuns: devolve lista vazia. Não estimar beta com 12 pontos.
- `significativo` usa dois erros-padrão — aproximação honesta de 95%, e a saída publica `erro_padrao` e `n_obs` para quem quiser julgar.
- Ordenação: exposições por `abs(beta)` decrescente, desempate pelo nome do fator.

- [ ] **Step 1: Escrever o teste que falha**

```python
"""Exposicao a fatores de risco por regressao."""
import numpy as np
import pandas as pd
import pytest

from core.global_portfolio.factors import (
    MIN_OBS_REGRESSAO,
    PROXIES,
    ROTULOS_FATOR,
    betas_do_ativo,
    exposicao_do_portfolio,
    series_de_fatores,
)


def _fatores(n=60):
    idx = pd.date_range("2021-01-31", periods=n, freq="ME")
    rng = np.random.default_rng(3)
    return pd.DataFrame(
        {f: rng.normal(0.008, 0.04, n) for f in ("mercado_br", "juros_nominais")},
        index=idx,
    )


def test_todo_fator_tem_rotulo():
    assert set(ROTULOS_FATOR) == set(PROXIES)


def test_proxies_sao_deterministicos():
    assert list(PROXIES) == sorted(PROXIES)


def test_beta_plantado_e_recuperado():
    f = _fatores()
    rng = np.random.default_rng(5)
    ativo = 1.5 * f["mercado_br"] + 0.3 * f["juros_nominais"] + rng.normal(0, 0.005, len(f))
    exp = {e.fator: e for e in betas_do_ativo(ativo, f)}
    assert exp["mercado_br"].beta == pytest.approx(1.5, abs=0.12)
    assert exp["juros_nominais"].beta == pytest.approx(0.3, abs=0.12)


def test_r2_alto_quando_os_fatores_explicam():
    f = _fatores()
    ativo = 1.2 * f["mercado_br"] + np.random.default_rng(1).normal(0, 0.002, len(f))
    assert betas_do_ativo(ativo, f)[0].r2 > 0.9


def test_beta_forte_e_marcado_significativo():
    f = _fatores()
    ativo = 1.5 * f["mercado_br"] + np.random.default_rng(2).normal(0, 0.005, len(f))
    exp = {e.fator: e for e in betas_do_ativo(ativo, f)}
    assert exp["mercado_br"].significativo is True


def test_ruido_puro_nao_e_significativo():
    f = _fatores()
    ativo = pd.Series(np.random.default_rng(9).normal(0, 0.05, len(f)), index=f.index)
    assert all(not e.significativo for e in betas_do_ativo(ativo, f))


def test_serie_curta_nao_estima_beta():
    f = _fatores(n=MIN_OBS_REGRESSAO - 1)
    ativo = 1.0 * f["mercado_br"]
    assert betas_do_ativo(ativo, f) == []


def test_exposicoes_ordenadas_por_beta_absoluto():
    f = _fatores()
    ativo = 0.2 * f["mercado_br"] + 1.4 * f["juros_nominais"]
    assert [e.fator for e in betas_do_ativo(ativo, f)][0] == "juros_nominais"


def test_exposicao_do_portfolio_pondera_os_ativos():
    f = _fatores()
    rng = np.random.default_rng(4)
    a = 2.0 * f["mercado_br"] + rng.normal(0, 0.004, len(f))
    b = 0.0 * f["mercado_br"] + rng.normal(0, 0.004, len(f))
    ret = pd.DataFrame({"A": a, "B": b}, index=f.index)
    exp = {e.fator: e for e in exposicao_do_portfolio(ret, {"A": 0.5, "B": 0.5}, f)}
    assert exp["mercado_br"].beta == pytest.approx(1.0, abs=0.15)


def test_series_de_fatores_monta_small_cap_como_spread():
    idx = pd.date_range("2021-01-31", periods=30, freq="ME")
    precos = pd.DataFrame(
        {t: np.linspace(100, 200, 30) for t in PROXIES.values()}, index=idx)
    precos["SMAL11"] = np.linspace(100, 300, 30)     # sobe mais que BOVA11
    f = series_de_fatores(loader=lambda tks: precos)
    assert "small_cap" in f.columns
    assert f["small_cap"].mean() > 0                  # spread positivo
    assert "mercado_br" in f.columns


def test_series_de_fatores_sem_dados_devolve_vazio():
    assert series_de_fatores(loader=lambda tks: pd.DataFrame()).empty
```

- [ ] **Step 2: Rodar e confirmar que falha**
- [ ] **Step 3: Implementar**

```python
"""Exposicao do patrimonio a fatores de risco, por regressao.

Os proxies sao ETFs negociados na B3, todos em reais e com serie mensal na
mesma tabela dos ativos — nao ha dependencia externa nem mistura de moeda.

Duas honestidades que a saida carrega sempre: o erro-padrao e o numero de
observacoes. Beta estimado com 24 pontos mensais nao e a mesma coisa que beta
estimado com 120, e quem le precisa poder distinguir.

Coberto por tests/test_global_factors.py.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Fator canonico -> ticker do proxy na B3.
PROXIES: dict[str, str] = {
    "global_dolar": "IVVB11",     # S&P 500 em BRL: mercado americano + cambio
    "inflacao": "IMAB11",         # IPCA+
    "juros_nominais": "IRFM11",   # prefixado
    "mercado_br": "BOVA11",
    "ouro": "GOLD11",
    "small_cap": "SMAL11",        # entra como spread SMAL11 - BOVA11
}

ROTULOS_FATOR: dict[str, str] = {
    "global_dolar": "Global / Dólar",
    "inflacao": "Inflação (IPCA+)",
    "juros_nominais": "Juros nominais",
    "mercado_br": "Mercado brasileiro",
    "ouro": "Ouro",
    "small_cap": "Small caps",
}

MIN_OBS_REGRESSAO = 24


@dataclass(frozen=True)
class Exposicao:
    """Beta a um fator, com o que permite julgar a estimativa."""

    fator: str
    beta: float
    erro_padrao: float
    r2: float
    n_obs: int

    @property
    def significativo(self) -> bool:
        """Dois erros-padrao — aproximacao de 95%."""
        return bool(self.erro_padrao > 0 and abs(self.beta) > 2 * self.erro_padrao)


def _default_loader(tickers: tuple[str, ...]) -> pd.DataFrame:
    from core.market_read import load_precos_mensais
    return load_precos_mensais(tickers)


def series_de_fatores(*, loader=None) -> pd.DataFrame:
    """Retornos mensais dos proxies, ja como fatores canonicos."""
    loader = loader or _default_loader
    precos = loader(tuple(sorted(set(PROXIES.values()))))
    if not isinstance(precos, pd.DataFrame) or precos.empty:
        return pd.DataFrame()

    retornos = precos.sort_index().pct_change().dropna(how="all")
    saida = pd.DataFrame(index=retornos.index)
    for fator in sorted(PROXIES):
        ticker = PROXIES[fator]
        if ticker in retornos.columns:
            saida[fator] = retornos[ticker]

    # Small cap e premio sobre o mercado, nao o retorno do indice inteiro:
    # sem o spread, small_cap e mercado_br seriam quase colineares.
    if "small_cap" in saida.columns and "mercado_br" in saida.columns:
        saida["small_cap"] = saida["small_cap"] - saida["mercado_br"]

    return saida.dropna(how="all")


def _regredir(y: pd.Series, X: pd.DataFrame) -> list[Exposicao]:
    comum = y.dropna().index.intersection(X.dropna().index)
    if len(comum) < MIN_OBS_REGRESSAO:
        return []

    yv = y.loc[comum].to_numpy(dtype=float)
    Xv = X.loc[comum].to_numpy(dtype=float)
    A = np.column_stack([np.ones(len(comum)), Xv])

    coef, *_ = np.linalg.lstsq(A, yv, rcond=None)
    residuo = yv - A @ coef
    gl = len(comum) - A.shape[1]
    if gl <= 0:
        return []

    var_res = float(residuo @ residuo) / gl
    try:
        cov = var_res * np.linalg.inv(A.T @ A)
    except np.linalg.LinAlgError:
        return []
    erros = np.sqrt(np.clip(np.diag(cov), 0.0, None))

    sq_tot = float(((yv - yv.mean()) ** 2).sum())
    r2 = float(1.0 - (residuo @ residuo) / sq_tot) if sq_tot > 0 else 0.0

    saida = [
        Exposicao(fator=str(nome), beta=float(coef[i + 1]),
                  erro_padrao=float(erros[i + 1]), r2=r2, n_obs=len(comum))
        for i, nome in enumerate(X.columns)
    ]
    return sorted(saida, key=lambda e: (-abs(e.beta), e.fator))


def betas_do_ativo(retornos_ativo: pd.Series, fatores: pd.DataFrame) -> list[Exposicao]:
    """Betas de um ativo contra os fatores."""
    if not isinstance(fatores, pd.DataFrame) or fatores.empty:
        return []
    return _regredir(pd.Series(retornos_ativo).dropna(), fatores)


def exposicao_do_portfolio(retornos: pd.DataFrame, pesos: dict,
                           fatores: pd.DataFrame) -> list[Exposicao]:
    """Betas do portfolio sintetico, ponderado pelos pesos informados."""
    if (not isinstance(retornos, pd.DataFrame) or retornos.empty
            or not isinstance(fatores, pd.DataFrame) or fatores.empty):
        return []
    w = np.array([float(pesos.get(c, 0.0)) for c in retornos.columns], dtype=float)
    total = w.sum()
    if total <= 0:
        return []
    carteira = (retornos.to_numpy(dtype=float) @ (w / total))
    return _regredir(pd.Series(carteira, index=retornos.index), fatores)
```

- [ ] **Step 4: Rodar e confirmar 11 passed**
- [ ] **Step 5: Commit** — `feat(global): exposicao a fatores de risco por regressao`

---

### Task 4: Métricas de risco do patrimônio

**Files:**
- Create: `core/global_portfolio/risk.py`
- Test: `tests/test_global_risk.py`

**Interfaces:**
- Consumes: o quadro de retornos e os pesos.
- Produces:
  - `Risco` — dataclass congelada: `vol_mensal`, `vol_anual`, `var_95`, `cvar_95`, `drawdown_max`, `n_obs`.
  - `retorno_do_portfolio(retornos, pesos) -> pd.Series | None`
  - `metricas_de_risco(retornos, pesos) -> Risco | None`

Regras:
- `vol_anual = vol_mensal × √12`.
- VaR e CVaR **históricos** no percentil 5 dos retornos mensais da carteira, reportados como perdas positivas.
- `drawdown_max` sobre a série acumulada do portfólio sintético, valor positivo.
- Menos de `MIN_OBS` observações ou nenhum peso: devolve `None`.

- [ ] **Step 1: Escrever o teste que falha**

```python
"""Volatilidade, VaR, CVaR e drawdown do patrimonio."""
import numpy as np
import pandas as pd
import pytest

from core.global_portfolio.risk import metricas_de_risco, retorno_do_portfolio


def _retornos(n=60, vol=0.05, seed=13):
    idx = pd.date_range("2021-01-31", periods=n, freq="ME")
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {"A": rng.normal(0.01, vol, n), "B": rng.normal(0.01, vol, n)}, index=idx)


def test_retorno_do_portfolio_e_media_ponderada_linha_a_linha():
    ret = _retornos()
    serie = retorno_do_portfolio(ret, {"A": 0.5, "B": 0.5})
    esperado = ret.mean(axis=1)
    assert np.allclose(serie.to_numpy(), esperado.to_numpy())


def test_volatilidade_anual_e_mensal_vezes_raiz_de_doze():
    r = metricas_de_risco(_retornos(), {"A": 0.5, "B": 0.5})
    assert r.vol_anual == pytest.approx(r.vol_mensal * np.sqrt(12))


def test_volatilidade_cresce_com_a_dispersao():
    calmo = metricas_de_risco(_retornos(vol=0.02), {"A": .5, "B": .5})
    agitado = metricas_de_risco(_retornos(vol=0.10), {"A": .5, "B": .5})
    assert agitado.vol_mensal > calmo.vol_mensal


def test_var_e_cvar_sao_perdas_positivas_e_cvar_e_pior():
    r = metricas_de_risco(_retornos(), {"A": 0.5, "B": 0.5})
    assert r.var_95 > 0
    assert r.cvar_95 >= r.var_95


def test_drawdown_de_serie_sempre_positiva_e_zero():
    idx = pd.date_range("2021-01-31", periods=30, freq="ME")
    ret = pd.DataFrame({"A": [0.01] * 30}, index=idx)
    r = metricas_de_risco(ret, {"A": 1.0})
    assert r.drawdown_max == pytest.approx(0.0, abs=1e-9)


def test_drawdown_captura_a_queda_conhecida():
    idx = pd.date_range("2021-01-31", periods=4, freq="ME")
    # +0%, -20%, +0%, +0%  -> drawdown maximo 20%
    ret = pd.DataFrame({"A": [0.0, -0.20, 0.0, 0.0]}, index=idx)
    from core.global_portfolio.risk import _drawdown_maximo
    assert _drawdown_maximo(ret["A"]) == pytest.approx(0.20)


def test_serie_curta_devolve_none():
    assert metricas_de_risco(_retornos(n=10), {"A": .5, "B": .5}) is None


def test_sem_peso_devolve_none():
    assert metricas_de_risco(_retornos(), {}) is None


def test_quadro_vazio_devolve_none():
    assert metricas_de_risco(pd.DataFrame(), {}) is None
    assert retorno_do_portfolio(pd.DataFrame(), {}) is None
```

- [ ] **Step 2: Rodar e confirmar que falha**
- [ ] **Step 3: Implementar**

```python
"""Risco do patrimonio consolidado.

VaR e CVaR sao HISTORICOS, nao parametricos: com 60 observacoes mensais, supor
normalidade subestima a cauda justamente onde ela importa. O percentil empirico
nao supoe forma nenhuma.

Coberto por tests/test_global_risk.py.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from core.global_portfolio.returns import MIN_OBS

PERCENTIL_VAR = 5


@dataclass(frozen=True)
class Risco:
    """Risco do portfolio sintetico, em base mensal e anualizada."""

    vol_mensal: float
    vol_anual: float
    var_95: float
    cvar_95: float
    drawdown_max: float
    n_obs: int


def retorno_do_portfolio(retornos: pd.DataFrame, pesos: dict) -> pd.Series | None:
    """Serie de retornos do portfolio sintetico, ponderada e renormalizada."""
    if not isinstance(retornos, pd.DataFrame) or retornos.empty:
        return None
    w = np.array([float(pesos.get(c, 0.0)) for c in retornos.columns], dtype=float)
    total = w.sum()
    if total <= 0:
        return None
    limpo = retornos.fillna(0.0).to_numpy(dtype=float)
    return pd.Series(limpo @ (w / total), index=retornos.index)


def _drawdown_maximo(serie: pd.Series) -> float:
    """Maior queda percentual do pico ate o vale, como numero positivo."""
    acumulado = (1.0 + serie.fillna(0.0)).cumprod()
    pico = acumulado.cummax()
    return float((1.0 - acumulado / pico).max())


def metricas_de_risco(retornos: pd.DataFrame, pesos: dict) -> Risco | None:
    """Volatilidade, VaR/CVaR historicos e drawdown do patrimonio."""
    serie = retorno_do_portfolio(retornos, pesos)
    if serie is None or len(serie) < MIN_OBS:
        return None

    vol_mensal = float(serie.std(ddof=1))
    valores = serie.to_numpy(dtype=float)
    corte = float(np.percentile(valores, PERCENTIL_VAR))
    cauda = valores[valores <= corte]

    return Risco(
        vol_mensal=vol_mensal,
        vol_anual=vol_mensal * float(np.sqrt(12)),
        var_95=float(-corte),
        cvar_95=float(-cauda.mean()) if cauda.size else float(-corte),
        drawdown_max=_drawdown_maximo(serie),
        n_obs=len(serie),
    )
```

- [ ] **Step 4: Rodar e confirmar 9 passed**
- [ ] **Step 5: Commit** — `feat(global): volatilidade, VaR/CVaR historicos e drawdown`

---

### Task 5: Painéis de correlação, fatores e risco na seção

**Files:**
- Modify: `views/portfolio_global.py` (acréscimos ao final de `render()` e novas funções privadas)
- Test: `tests/test_portfolio_global_view.py` (acrescentar casos)

**Interfaces:**
- Consumes: tudo das Tasks 1 a 4.
- Produces: `_painel_correlacao(df)`, `_painel_fatores(df)`, `_painel_risco(df)` e a função pura `aviso_de_cobertura(cob) -> str | None`.

Regras:
- Uma única chamada a `retornos_mensais` por render; o quadro é passado aos três painéis. Não buscar preço três vezes.
- `aviso_de_cobertura` devolve a mensagem quando `peso_coberto < 1`, nomeando os símbolos de fora e o percentual descoberto; `None` quando tudo está coberto. É função pura e testada.
- Cobertura zero: os três painéis mostram a orientação e nada mais — sem matriz vazia nem beta de nada.
- Cards: volatilidade anual, VaR 95%, CVaR 95%, drawdown máximo, razão de diversificação, apostas efetivas.
- Fatores: tabela com rótulo, beta, erro-padrão, R² e uma marca visível de significância. Fator não significativo aparece, mas marcado — omitir seria esconder que a estimativa é fraca.
- Pares redundantes: lista com os dois símbolos e a correlação, ordenada.

- [ ] **Step 1: Escrever os testes que falham**

```python
def test_aviso_de_cobertura_nomeia_os_ativos_de_fora():
    from core.global_portfolio.returns import Cobertura
    from views.portfolio_global import aviso_de_cobertura
    cob = Cobertura(("PETR4",), ("AAPL", "MSFT"), 0.615, 60)
    msg = aviso_de_cobertura(cob)
    assert "AAPL" in msg and "MSFT" in msg
    assert "38" in msg or "38,5" in msg


def test_aviso_de_cobertura_silencia_quando_tudo_coberto():
    from core.global_portfolio.returns import Cobertura
    from views.portfolio_global import aviso_de_cobertura
    assert aviso_de_cobertura(Cobertura(("PETR4",), (), 1.0, 60)) is None


def test_aviso_de_cobertura_com_cobertura_zero_orienta():
    from core.global_portfolio.returns import Cobertura
    from views.portfolio_global import aviso_de_cobertura
    msg = aviso_de_cobertura(Cobertura((), ("AAPL",), 0.0, 0))
    assert msg is not None and "AAPL" in msg
```

- [ ] **Step 2: Rodar e confirmar que falham**
- [ ] **Step 3: Implementar os três painéis e a função pura**
- [ ] **Step 4: Rodar a suíte da view e a suíte completa**

Esperado: baseline `1696 passed` mais os testes desta fase, zero falhas.

- [ ] **Step 5: Verificar contra produção**

```
"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -c "
import sys, io; sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from core.portfolio.repository import load_active_snapshots, load_allocation_targets
from core.portfolio.registry import asset_classes
from core.global_portfolio.aggregate import montar_posicoes
from core.global_portfolio import returns, correlation, factors, risk
snaps = {c: load_active_snapshots(c) for c in asset_classes()}
a = load_allocation_targets()
df = montar_posicoes(snaps, a['targets'], total_brl=a['total_brl'])
ret, cob = returns.retornos_mensais(df)
print('cobertura: %.1f%% | %d meses | fora: %s' % (cob.peso_coberto*100, cob.meses, ', '.join(cob.simbolos_sem_serie[:6])))
pesos = dict(zip(df['symbol'], df['weight_global']))
print('correlacao media:', correlation.correlacao_media(ret))
print('razao diversificacao:', correlation.razao_diversificacao(ret, pesos))
print('apostas efetivas:', correlation.apostas_efetivas(ret, pesos))
print('pares redundantes:', correlation.pares_redundantes(ret)[:5])
f = factors.series_de_fatores()
for e in factors.exposicao_do_portfolio(ret, pesos, f)[:6]:
    print('  %-16s beta %+.2f (ep %.2f) R2 %.2f n=%d %s' % (e.fator, e.beta, e.erro_padrao, e.r2, e.n_obs, 'sig' if e.significativo else ''))
r = risk.metricas_de_risco(ret, pesos)
print('vol anual %.1f%% | VaR95 %.1f%% | CVaR95 %.1f%% | DD max %.1f%%' % (r.vol_anual*100, r.var_95*100, r.cvar_95*100, r.drawdown_max*100))
"
```

Incluir a saída real no relatório. Números implausíveis são achado, não ruído.

- [ ] **Step 6: Commit** — `feat(global): paineis de correlacao, fatores e risco`

---

## Auto-revisão deste plano

**Cobertura do §6 da spec:** §6.4 correlação → Task 2; §6.5 fatores → Task 3; §6.6 risco e volatilidade → Task 4; exibição → Task 5. O look-through de FII fica fora: `fii_lookthrough` opera sobre imóveis individuais e não alimenta nenhum dos quatro módulos desta fase.

**Correções à spec registradas aqui:** a base é mensal, não diária; e os preços americanos não existem no Supabase, então a cobertura é parcial por ausência de dado, o que a interface nomeia.

**Consistência:** `MIN_OBS` da Task 1 é reusado na Task 4. `Cobertura` (Task 1) é consumida na Task 5. `Exposicao.significativo` (Task 3) é lido na Task 5. Os pesos são sempre `dict[str, float]` de símbolo para `weight_global`.
