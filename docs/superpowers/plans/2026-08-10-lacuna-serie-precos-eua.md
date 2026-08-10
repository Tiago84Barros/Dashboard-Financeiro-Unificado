# Lacuna: série mensal de preços dos EUA — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Levar a cobertura da estatística do Portfólio Global de 62% para 100% do patrimônio, ingerindo a série mensal de preços dos ativos americanos que a carteira realmente possui.

**Architecture:** Uma tabela que o schema 040 já define e nunca foi aplicada, um script de ingestão que busca apenas os símbolos das carteiras salvas, e uma linha em `returns._CLASSES_COM_PRECO`. Correlação, fatores, risco e os papéis de volatilidade e diversificação passam a valer para o patrimônio inteiro sem que nenhum desses módulos mude.

**Tech Stack:** Python 3.12, yfinance 0.2.66, pandas, SQLAlchemy, pytest.

## Global Constraints

- **Espaço é a restrição dominante.** O Supabase está em **467 MB de 500 MB** — 33 MB de folga. A série mensal dos 12 ativos da carteira custa **~1,1 MB** (3.600 linhas a ~298 bytes). Ingerir a série **diária** custaria ~22,5 MB e está fora de escopo: consumiria dois terços da folga restante.
- **Só os símbolos das carteiras salvas.** Nunca as 3.052 empresas da vitrine. O script lê os símbolos de `us_portfolio_model_items` e busca esses. Um universo maior é o que torna a conta inviável.
- **Aditividade:** as únicas alterações em arquivo pré-existente são uma entrada em `returns._CLASSES_COM_PRECO` (Task 3) e uma função nova ao final de `core/us_read.py` (Task 3). Nenhuma lógica existente é removida.
- **Simulação por padrão:** o script de ingestão só grava com `--apply`, como todo script deste projeto.
- **Rede é falível.** yfinance pode devolver vazio, faltar símbolo ou falhar. O script reporta por símbolo o que conseguiu e o que não, e nunca grava silenciosamente menos do que pediu.
- **Determinismo:** nenhuma saída depende de ordem de iteração de `dict`/`set`.
- **Idioma:** comentários, docstrings e saída do script em português.
- **Interpretador:** `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest ...`
- **Baseline da suíte:** `1782 passed, 3 skipped, 0 failed`.

---

## O que existe e será consumido

Verificado contra o banco e o código, não presumido:

- `supabase_unificado/schema/040_market_us_schema.sql:331` **já define** `market_us.prices_monthly` — colunas `symbol`, `month_end`, `close`, `adjusted_close`, `volume`, `total_return`, `source`, `ingested_at`, chave primária `(symbol, month_end)`. A tabela nunca foi criada em produção: `market_us` tem hoje só `company_snapshots`.
- `us_portfolio_model_items` existe e tem 12 itens (criada quando o usuário salvou a carteira americana).
- `core/market_read.py::load_precos_mensais(tickers) -> pd.DataFrame` é o análogo da B3: devolve um DataFrame com `DatetimeIndex` mensal × colunas = tickers, via `pivot_table(index="date", columns="ticker", values="c", aggfunc="last")`. A função nova para os EUA deve devolver **exatamente esse formato**, porque `returns.retornos_mensais` consome os dois pelo mesmo caminho.
- `core/global_portfolio/returns.py` tem `_CLASSES_COM_PRECO = ("b3", "fii")` e `_default_loader` chamando `load_precos_mensais`.
- yfinance 0.2.66 está instalado.

---

## Estrutura de arquivos

| Arquivo | Responsabilidade |
|---|---|
| `scripts/ingest_us_prices_monthly.py` | Busca no yfinance e grava em `market_us.prices_monthly` |
| `core/us_read.py` (modificar) | +`load_precos_mensais_us` no formato do análogo da B3 |
| `core/global_portfolio/returns.py` (modificar) | `us` entra em `_CLASSES_COM_PRECO`; o loader escolhe a fonte por classe |

---

### Task 1: Script de ingestão

**Files:**
- Create: `scripts/ingest_us_prices_monthly.py`
- Test: `tests/test_ingest_us_prices_monthly.py`

**Interfaces:**
- Consumes: `core.database.get_engine`; `yfinance`.
- Produces:
  - `simbolos_das_carteiras(*, engine) -> list[str]` — símbolos distintos de `us_portfolio_model_items`, ordenados. Lista vazia se a tabela não existir.
  - `serie_mensal(symbol, *, fetcher=None) -> pd.DataFrame` — colunas `month_end`, `close`, `adjusted_close`, `volume`; vazio quando não há dado. `fetcher` é injetável para testar sem rede.
  - `ingerir(*, engine, apply: bool, simbolos=None, fetcher=None) -> dict[str, int]` — símbolo → linhas gravadas (ou que seriam).
  - `main(argv=None) -> int` — CLI com `--apply` e `--simbolo` repetível.

O `fetcher` padrão usa `yfinance.download(symbol, interval="1mo", auto_adjust=False)` e devolve o frame bruto; a normalização para as quatro colunas fica em `serie_mensal`, que é o que os testes exercitam.

Gravação idempotente: `ON CONFLICT (symbol, month_end) DO UPDATE`, como o repositório de snapshots já faz.

- [ ] **Step 1: Escrever o teste que falha**

```python
"""Ingestao da serie mensal de precos dos ativos americanos."""
import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from scripts import ingest_us_prices_monthly as ing


@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:")
    with eng.begin() as conn:
        conn.execute(text("""
            CREATE TABLE us_portfolio_model_items (
                model_id TEXT, symbol TEXT, weight REAL
            )
        """))
        conn.execute(text("""
            CREATE TABLE prices_monthly (
                symbol TEXT NOT NULL, month_end TEXT NOT NULL,
                close REAL, adjusted_close REAL, volume INTEGER,
                total_return REAL, source TEXT DEFAULT 'yfinance',
                ingested_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (symbol, month_end)
            )
        """))
        for s in ("AAPL", "MSFT", "AAPL"):
            conn.execute(text("INSERT INTO us_portfolio_model_items (model_id, symbol, weight) "
                              "VALUES ('m1', :s, 0.5)"), {"s": s})
    return eng


def _frame_bruto():
    idx = pd.to_datetime(["2024-01-31", "2024-02-29", "2024-03-31"])
    return pd.DataFrame({
        "Close": [100.0, 110.0, 120.0],
        "Adj Close": [99.0, 109.0, 119.0],
        "Volume": [1000, 1100, 1200],
    }, index=idx)


def test_simbolos_vem_das_carteiras_sem_repetir(engine):
    assert ing.simbolos_das_carteiras(engine=engine) == ["AAPL", "MSFT"]


def test_simbolos_com_tabela_ausente_devolve_vazio():
    eng = create_engine("sqlite:///:memory:")
    assert ing.simbolos_das_carteiras(engine=eng) == []


def test_serie_mensal_normaliza_as_colunas():
    df = ing.serie_mensal("AAPL", fetcher=lambda s: _frame_bruto())
    assert list(df.columns) == ["month_end", "close", "adjusted_close", "volume"]
    assert len(df) == 3
    assert df["adjusted_close"].iloc[-1] == 119.0


def test_serie_mensal_sem_dado_devolve_frame_vazio():
    df = ing.serie_mensal("ZZZZ", fetcher=lambda s: pd.DataFrame())
    assert df.empty
    assert list(df.columns) == ["month_end", "close", "adjusted_close", "volume"]


def test_simulacao_nao_grava_nada(engine):
    resumo = ing.ingerir(engine=engine, apply=False, fetcher=lambda s: _frame_bruto())
    assert resumo == {"AAPL": 3, "MSFT": 3}
    with engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM prices_monthly")).scalar() == 0


def test_apply_grava_e_e_idempotente(engine):
    ing.ingerir(engine=engine, apply=True, fetcher=lambda s: _frame_bruto())
    ing.ingerir(engine=engine, apply=True, fetcher=lambda s: _frame_bruto())
    with engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM prices_monthly")).scalar() == 6


def test_simbolo_sem_dado_aparece_com_zero_e_nao_some(engine):
    def fetcher(s):
        return _frame_bruto() if s == "AAPL" else pd.DataFrame()
    resumo = ing.ingerir(engine=engine, apply=False, fetcher=fetcher)
    assert resumo["MSFT"] == 0, "simbolo sem dado precisa aparecer, nao sumir"


def test_falha_de_rede_num_simbolo_nao_derruba_os_demais(engine):
    def fetcher(s):
        if s == "MSFT":
            raise RuntimeError("rede fora")
        return _frame_bruto()
    resumo = ing.ingerir(engine=engine, apply=False, fetcher=fetcher)
    assert resumo["AAPL"] == 3
    assert resumo["MSFT"] == 0
```

**Nota para o implementador:** os testes usam SQLite com a tabela chamada `prices_monthly` sem o schema `market_us`, porque SQLite não tem schemas. O código deve montar o nome da tabela a partir de uma constante escolhida pelo dialeto — `"market_us.prices_monthly"` em PostgreSQL, `"prices_monthly"` em SQLite — exatamente como `core/portfolio/repository.py` já faz para o cast de JSONB.

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_ingest_us_prices_monthly.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'scripts.ingest_us_prices_monthly'`

- [ ] **Step 3: Escrever o script**

Seguindo o padrão de `scripts/backfill_portfolio_snapshots.py`: simulação por padrão, `--apply` para gravar, resumo por símbolo impresso ao final, e a nota de que a fonte é o yfinance.

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_ingest_us_prices_monthly.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/ingest_us_prices_monthly.py tests/test_ingest_us_prices_monthly.py
git commit -m "feat(us): script de ingestao da serie mensal de precos"
```

---

### Task 2: Leitor no formato do análogo da B3

**Files:**
- Modify: `core/us_read.py` (acrescentar ao final)
- Test: `tests/test_us_read_precos_mensais.py`

**Interfaces:**
- Produces: `load_precos_mensais_us(symbols: tuple[str, ...]) -> pd.DataFrame` — `DatetimeIndex` mensal × colunas = símbolos, valores = `adjusted_close`. Frame vazio quando não há símbolos ou a tabela não existe.

O formato **tem de ser idêntico** ao de `core/market_read.py::load_precos_mensais`, porque `returns.retornos_mensais` vai concatenar os dois pelo mesmo caminho. Um formato divergente aqui produziria colunas duplicadas ou índice desalinhado, e o defeito apareceria como cobertura estranha em vez de erro.

- [ ] **Step 1: Escrever o teste que falha**

```python
def test_load_precos_mensais_us_devolve_indice_mensal_por_simbolo():
    import pandas as pd
    from sqlalchemy import create_engine, text
    import core.us_read as ur

    eng = create_engine("sqlite:///:memory:")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE prices_monthly (symbol TEXT, month_end TEXT, "
                       "adjusted_close REAL, close REAL)"))
        for s, d, p in [("AAPL", "2024-01-31", 100.0), ("AAPL", "2024-02-29", 110.0),
                        ("MSFT", "2024-01-31", 200.0), ("MSFT", "2024-02-29", 210.0)]:
            c.execute(text("INSERT INTO prices_monthly VALUES (:s,:d,:p,:p)"),
                      {"s": s, "d": d, "p": p})

    df = ur.load_precos_mensais_us(("AAPL", "MSFT"), engine=eng)
    assert list(df.columns) == ["AAPL", "MSFT"]
    assert len(df) == 2
    assert df["AAPL"].iloc[-1] == 110.0


def test_load_precos_mensais_us_sem_simbolos_devolve_vazio():
    import core.us_read as ur
    assert ur.load_precos_mensais_us(()).empty


def test_load_precos_mensais_us_com_tabela_ausente_devolve_vazio():
    from sqlalchemy import create_engine
    import core.us_read as ur
    assert ur.load_precos_mensais_us(("AAPL",), engine=create_engine("sqlite:///:memory:")).empty
```

- [ ] **Step 2: Rodar e confirmar que falha**

- [ ] **Step 3: Implementar**

Parâmetro `engine=None` com o padrão resolvendo por `_engine()`, para os testes poderem injetar SQLite. Mesmo truque de nome de tabela por dialeto da Task 1.

- [ ] **Step 4: Rodar e confirmar que passa** — 3 passed

- [ ] **Step 5: Commit**

```bash
git add core/us_read.py tests/test_us_read_precos_mensais.py
git commit -m "feat(us): leitor da serie mensal no formato do analogo da B3"
```

---

### Task 3: `us` entra na cobertura de retornos

**Files:**
- Modify: `core/global_portfolio/returns.py`
- Test: `tests/test_global_returns.py`

**Interfaces:**
- Consumes: `load_precos_mensais_us` (Task 2).
- Produces: nenhuma assinatura nova. `_CLASSES_COM_PRECO` passa a incluir `"us"`, e o loader padrão escolhe a fonte por classe.

O ponto delicado: hoje `_default_loader` chama `load_precos_mensais(tickers)` para todos os candidatos. Com duas fontes, o loader precisa separar os símbolos por classe, buscar em cada fonte e **juntar pelo índice de data**, alinhando por mês. Um `concat` no eixo errado ou sem alinhar o índice produziria NaN em massa e derrubaria a cobertura em vez de aumentá-la.

- [ ] **Step 1: Escrever o teste que falha**

```python
def test_us_entra_nas_classes_com_preco():
    from core.global_portfolio.returns import _CLASSES_COM_PRECO
    assert "us" in _CLASSES_COM_PRECO


def test_retornos_juntam_series_das_duas_fontes_alinhadas_por_mes():
    import pandas as pd
    from core.global_portfolio.returns import retornos_mensais

    idx = pd.date_range("2022-01-31", periods=30, freq="ME")
    br = pd.DataFrame({"PETR4": range(100, 130)}, index=idx).astype(float)
    us = pd.DataFrame({"AAPL": range(200, 230)}, index=idx).astype(float)

    df = pd.DataFrame([
        {"asset_class": "b3", "symbol": "PETR4", "weight_global": 0.5, "payload": {}},
        {"asset_class": "us", "symbol": "AAPL", "weight_global": 0.5, "payload": {}},
    ])

    ret, cob = retornos_mensais(df, loader=lambda tks: (
        pd.concat([br[[c for c in br.columns if c in tks]],
                   us[[c for c in us.columns if c in tks]]], axis=1)))
    assert set(ret.columns) == {"PETR4", "AAPL"}
    assert cob.peso_coberto == 1.0
    assert not ret.isna().all().any(), "coluna toda NaN indica desalinhamento de indice"
```

- [ ] **Step 2: Rodar e confirmar que falha**

- [ ] **Step 3: Implementar**

`_default_loader` separa os símbolos por classe. Como a assinatura atual recebe só `tickers`, o loader precisa saber a classe de cada um — a forma mais simples e menos invasiva é `retornos_mensais` passar o mapa símbolo→classe ao loader padrão, mantendo a assinatura injetável para os testes. O implementador escolhe a forma; o requisito é não quebrar a injeção que os testes existentes usam.

- [ ] **Step 4: Rodar e confirmar que passa**

- [ ] **Step 5: Suíte completa**

Run: `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/ -q --tb=short`

- [ ] **Step 6: Commit**

```bash
git add core/global_portfolio/returns.py tests/test_global_returns.py
git commit -m "feat(global): serie dos EUA entra na cobertura de retornos"
```

---

## Passos operacionais após o merge

Na ordem, e cada um verificado antes do seguinte:

1. Aplicar `supabase_unificado/schema/040_market_us_schema.sql` no Supabase — cria `market_us.prices_monthly` e as demais tabelas do módulo EUA que faltam. É `CREATE TABLE IF NOT EXISTS`, aditivo.
2. Simular a ingestão: `python -m scripts.ingest_us_prices_monthly`
3. Conferir o resumo por símbolo — 12 símbolos, cada um com sua contagem de meses.
4. Aplicar: `python -m scripts.ingest_us_prices_monthly --apply`
5. Medir o espaço consumido de fato e comparar com a estimativa de 1,1 MB.
6. Regravar os snapshots: `python -m scripts.backfill_portfolio_snapshots --apply`

Sem o passo 6 nada muda na tela: os módulos leem a série pelo símbolo, mas a cobertura é calculada sobre o quadro de posições montado a partir dos snapshots.

## Auto-revisão deste plano

**Cobertura:** a lacuna era "os 12 ativos americanos não têm série mensal, então correlação, fatores, risco e dois papéis cobrem 62% do patrimônio". A Task 1 traz o dado, a Task 2 o lê no formato certo, a Task 3 o inclui na cobertura. Nenhum módulo de análise muda — eles já operam sobre o que o loader entrega.

**Consistência de nomes:** `load_precos_mensais_us` é produzida na Task 2 e consumida na Task 3. `simbolos_das_carteiras`, `serie_mensal` e `ingerir` são da Task 1 e só o script as usa. O formato de saída da Task 2 é explicitamente amarrado ao de `market_read.load_precos_mensais`, e o teste da Task 3 falha se o alinhamento por índice quebrar.

**Restrição que permanece:** 1,1 MB numa folga de 33 MB. O plano não toca em espaço; a investigação dos 170 MB de `docs_corporativos_chunks` é trabalho separado e vem depois.
