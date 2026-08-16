# Lacuna: série mensal de preços dos EUA — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Levar a cobertura da estatística do Portfólio Global de 62% para 100% do patrimônio, publicando no Supabase a série mensal que **já existe no warehouse local**.

**Architecture:** Um publicador que copia do warehouse local para o Supabase apenas os símbolos das carteiras salvas, seguindo o padrão de `publish_fii_selection_from_local.py`. Mais um leitor no formato do análogo da B3, e `us` entrando em `_CLASSES_COM_PRECO`. Correlação, fatores, risco e os papéis de volatilidade e diversificação passam a valer para o patrimônio inteiro sem que nenhum desses módulos mude.

**Tech Stack:** Python 3.12, pandas, SQLAlchemy, pytest.

> **Correção de rumo (10/08/2026).** A primeira versão deste plano propunha buscar
> os preços no yfinance. Estava errado: o warehouse local (`dfu_warehouse`, porta
> 5433) já tem `market_us.prices_monthly` com 609.347 linhas, incluindo os 12
> ativos da carteira com **4.720 linhas mensais** e histórico desde 1984. Buscar
> de fora seria refazer trabalho já feito e introduzir dependência de rede sem
> necessidade. A lição virou a skill `inventario-de-dados`.

## Global Constraints

- **Espaço é a restrição dominante.** O Supabase está em **491 MB de 500** — 9 MB de folga. As 4.720 linhas custam **~1,4 MB** (a ~298 bytes/linha, medido em `market.historical_prices`). Cabe, mas sem margem para desperdício.
- **Só os símbolos das carteiras salvas.** Nunca os 3.052 da vitrine, nunca a série diária (2 GB no local). O publicador lê os símbolos de `us_portfolio_model_items`.
- **Mensal, não diária.** A série diária local tem 12 milhões de linhas; publicá-la é impossível na cota atual e desnecessário para retornos mensais.
- **Aditividade:** as únicas alterações em arquivo pré-existente são uma função nova ao final de `core/us_read.py` (Task 2) e a entrada de `us` em `_CLASSES_COM_PRECO` (Task 3).
- **Simulação por padrão:** o publicador só grava com `--apply`, como todo script deste projeto.
- **Determinismo:** nenhuma saída depende de ordem de iteração de `dict`/`set`.
- **Idioma:** comentários, docstrings e saída do script em português.
- **Interpretador:** `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest ...`
- **Baseline da suíte:** `1782 passed, 3 skipped, 0 failed`.

---

## O que existe e será consumido

Verificado contra os dois bancos em 10/08/2026:

- **Warehouse local** — `market_us.prices_monthly` com colunas `symbol`, `month_end`, `close`, `adjusted_close`, `volume`, `total_return`, `source`, `ingested_at`. Os 12 símbolos da carteira somam 4.720 linhas; ADBE tem 480 meses desde 1986, AME 505 desde 1984.
- **Supabase** — `market_us` tem só `company_snapshots`. A tabela `prices_monthly` está definida em `supabase_unificado/schema/040_market_us_schema.sql:331` com **exatamente as mesmas colunas**, e nunca foi criada.
- `scripts/publish_fii_selection_from_local.py` — o padrão a seguir, incluindo `_warehouse_url()`, que resolve a conexão local lendo a senha do container via `docker inspect`.
- `core/market_read.py::load_precos_mensais(tickers)` — o análogo da B3: `DatetimeIndex` mensal × colunas = tickers, via `pivot_table(index="date", columns="ticker", values="c", aggfunc="last")`.
- `core/global_portfolio/returns.py` — `_CLASSES_COM_PRECO = ("b3", "fii")` e `_default_loader` chamando `load_precos_mensais`.

---

## Estrutura de arquivos

| Arquivo | Responsabilidade |
|---|---|
| `scripts/publish_us_prices_monthly.py` | Copia do warehouse local para o Supabase |
| `core/us_read.py` (modificar) | +`load_precos_mensais_us` no formato do análogo da B3 |
| `core/global_portfolio/returns.py` (modificar) | `us` entra em `_CLASSES_COM_PRECO` |

---

### Task 1: Publicador do local para o Supabase

**Files:**
- Create: `scripts/publish_us_prices_monthly.py`
- Test: `tests/test_publish_us_prices_monthly.py`

**Interfaces:**
- Consumes: `core.database.get_engine`; `scripts.publish_fii_selection_from_local._warehouse_url`.
- Produces:
  - `simbolos_das_carteiras(*, engine) -> list[str]` — símbolos distintos de `us_portfolio_model_items`, ordenados. Vazio se a tabela não existir.
  - `ler_do_local(simbolos, *, engine) -> pd.DataFrame` — colunas `symbol`, `month_end`, `close`, `adjusted_close`, `volume`, `total_return`. Vazio se não houver.
  - `publicar(*, local, remoto, apply: bool, simbolos=None) -> dict[str, int]` — símbolo → linhas publicadas (ou que seriam).
  - `main(argv=None) -> int` — CLI com `--apply` e `--simbolo` repetível.

Gravação idempotente: `ON CONFLICT (symbol, month_end) DO UPDATE`. Ambos os engines são parâmetros, para os testes usarem dois SQLite em memória sem tocar em Docker nem em rede.

**Nota de dialeto:** SQLite não tem schemas. Montar o nome da tabela por `engine.dialect.name` — `"market_us.prices_monthly"` em PostgreSQL, `"prices_monthly"` em SQLite — como `core/portfolio/repository.py` já faz para o cast de JSONB.

- [ ] **Step 1: Escrever o teste que falha**

```python
"""Publicacao da serie mensal dos EUA do warehouse local para o Supabase."""
import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from scripts import publish_us_prices_monthly as pub

LINHAS = [
    ("AAPL", "2024-01-31", 100.0, 99.0, 1000, 0.01),
    ("AAPL", "2024-02-29", 110.0, 109.0, 1100, 0.10),
    ("MSFT", "2024-01-31", 200.0, 199.0, 2000, 0.02),
]


def _cria_prices(conn):
    conn.execute(text("""
        CREATE TABLE prices_monthly (
            symbol TEXT NOT NULL, month_end TEXT NOT NULL,
            close REAL, adjusted_close REAL, volume INTEGER, total_return REAL,
            source TEXT DEFAULT 'local', ingested_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (symbol, month_end)
        )
    """))


@pytest.fixture()
def local():
    eng = create_engine("sqlite:///:memory:")
    with eng.begin() as c:
        _cria_prices(c)
        for r in LINHAS:
            c.execute(text("INSERT INTO prices_monthly (symbol, month_end, close, "
                           "adjusted_close, volume, total_return) "
                           "VALUES (:a,:b,:c,:d,:e,:f)"), dict(zip("abcdef", r)))
    return eng


@pytest.fixture()
def remoto():
    eng = create_engine("sqlite:///:memory:")
    with eng.begin() as c:
        _cria_prices(c)
        c.execute(text("CREATE TABLE us_portfolio_model_items "
                       "(model_id TEXT, symbol TEXT, weight REAL)"))
        for s in ("AAPL", "MSFT", "AAPL"):
            c.execute(text("INSERT INTO us_portfolio_model_items VALUES ('m1', :s, 0.5)"),
                      {"s": s})
    return eng


def test_simbolos_vem_das_carteiras_sem_repetir(remoto):
    assert pub.simbolos_das_carteiras(engine=remoto) == ["AAPL", "MSFT"]


def test_simbolos_com_tabela_ausente_devolve_vazio():
    assert pub.simbolos_das_carteiras(engine=create_engine("sqlite:///:memory:")) == []


def test_ler_do_local_traz_as_colunas_esperadas(local):
    df = pub.ler_do_local(["AAPL"], engine=local)
    assert list(df.columns) == ["symbol", "month_end", "close",
                                "adjusted_close", "volume", "total_return"]
    assert len(df) == 2


def test_ler_do_local_sem_simbolos_devolve_vazio(local):
    assert pub.ler_do_local([], engine=local).empty


def test_simulacao_nao_grava_nada(local, remoto):
    resumo = pub.publicar(local=local, remoto=remoto, apply=False)
    assert resumo == {"AAPL": 2, "MSFT": 1}
    with remoto.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM prices_monthly")).scalar() == 0


def test_apply_grava_e_e_idempotente(local, remoto):
    pub.publicar(local=local, remoto=remoto, apply=True)
    pub.publicar(local=local, remoto=remoto, apply=True)
    with remoto.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM prices_monthly")).scalar() == 3


def test_simbolo_sem_serie_no_local_aparece_com_zero(local, remoto):
    with remoto.begin() as c:
        c.execute(text("INSERT INTO us_portfolio_model_items VALUES ('m1','ZZZZ',0.1)"))
    resumo = pub.publicar(local=local, remoto=remoto, apply=False)
    assert resumo["ZZZZ"] == 0, "simbolo sem serie precisa aparecer, nao sumir"


def test_publicar_respeita_lista_explicita_de_simbolos(local, remoto):
    resumo = pub.publicar(local=local, remoto=remoto, apply=False, simbolos=["MSFT"])
    assert set(resumo) == {"MSFT"}
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_publish_us_prices_monthly.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'scripts.publish_us_prices_monthly'`

- [ ] **Step 3: Escrever o publicador**

Padrão de `scripts/backfill_portfolio_snapshots.py`: simulação por omissão, `--apply` para gravar, resumo por símbolo ao final. O `main` resolve o engine local por `_warehouse_url()` e o remoto por `get_engine()`; ambos injetáveis nas funções para os testes.

Um símbolo pedido que não tenha série no local aparece no resumo com zero — sumir do resumo é como uma lacuna de dado vira lacuna de cobertura sem ninguém perceber.

- [ ] **Step 4: Rodar e confirmar que passa** — 8 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/publish_us_prices_monthly.py tests/test_publish_us_prices_monthly.py
git commit -m "feat(us): publica serie mensal do warehouse local para o Supabase"
```

---

### Task 2: Leitor no formato do análogo da B3

**Files:**
- Modify: `core/us_read.py` (acrescentar ao final)
- Test: `tests/test_us_read_precos_mensais.py`

**Interfaces:**
- Produces: `load_precos_mensais_us(symbols: tuple[str, ...], *, engine=None) -> pd.DataFrame` — `DatetimeIndex` mensal × colunas = símbolos, valores = `adjusted_close`. Vazio quando não há símbolos ou a tabela não existe.

O formato **tem de ser idêntico** ao de `core/market_read.py::load_precos_mensais`, porque `returns.retornos_mensais` consome os dois pelo mesmo caminho. Formato divergente produziria colunas duplicadas ou índice desalinhado, e o defeito apareceria como cobertura estranha em vez de erro.

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
    assert isinstance(df.index, pd.DatetimeIndex)


def test_load_precos_mensais_us_sem_simbolos_devolve_vazio():
    import core.us_read as ur
    assert ur.load_precos_mensais_us(()).empty


def test_load_precos_mensais_us_com_tabela_ausente_devolve_vazio():
    from sqlalchemy import create_engine
    import core.us_read as ur
    assert ur.load_precos_mensais_us(("AAPL",),
                                     engine=create_engine("sqlite:///:memory:")).empty
```

- [ ] **Step 2: Rodar e confirmar que falha**

- [ ] **Step 3: Implementar** — mesmo truque de nome de tabela por dialeto da Task 1.

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
- Produces: nenhuma assinatura nova. `_CLASSES_COM_PRECO` passa a incluir `"us"`.

O ponto delicado: hoje `_default_loader` chama `load_precos_mensais(tickers)` para todos os candidatos. Com duas fontes, ele precisa separar por classe, buscar em cada uma e **juntar alinhando pelo índice de data**. Um `concat` no eixo errado ou sem alinhar produziria NaN em massa e derrubaria a cobertura em vez de aumentá-la.

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

    ret, cob = retornos_mensais(df, loader=lambda tks: pd.concat(
        [br[[c for c in br.columns if c in tks]],
         us[[c for c in us.columns if c in tks]]], axis=1))
    assert set(ret.columns) == {"PETR4", "AAPL"}
    assert cob.peso_coberto == 1.0
    assert not ret.isna().all().any(), "coluna toda NaN indica desalinhamento de indice"
```

- [ ] **Step 2: Rodar e confirmar que falha**

- [ ] **Step 3: Implementar**

`_default_loader` precisa saber a classe de cada símbolo. A forma menos invasiva é `retornos_mensais` passar o mapa símbolo→classe ao loader padrão, mantendo a assinatura injetável que os testes existentes usam. O implementador escolhe a forma; o requisito é não quebrar essa injeção.

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

Na ordem, cada um verificado antes do seguinte:

1. Aplicar `supabase_unificado/schema/040_market_us_schema.sql` no Supabase — cria `market_us.prices_monthly`. É `CREATE TABLE IF NOT EXISTS`, aditivo.
2. Conferir que o container local está de pé: `docker ps --filter name=dfu_warehouse`
3. Simular: `python -m scripts.publish_us_prices_monthly`
4. Conferir o resumo — 12 símbolos, ~4.720 linhas no total.
5. Aplicar: `python -m scripts.publish_us_prices_monthly --apply`
6. **Medir o espaço consumido de fato** e comparar com a estimativa de 1,4 MB. Com 9 MB de folga, uma surpresa aqui importa.
7. Regravar os snapshots: `python -m scripts.backfill_portfolio_snapshots --apply`

Sem o passo 7 nada muda na tela: a cobertura é calculada sobre o quadro montado a partir dos snapshots.

## Auto-revisão deste plano

**Cobertura:** a lacuna era "os 12 ativos americanos não têm série mensal, então correlação, fatores, risco e dois papéis cobrem 62% do patrimônio". A Task 1 traz o dado do local, a Task 2 o lê no formato certo, a Task 3 o inclui na cobertura. Nenhum módulo de análise muda.

**Consistência de nomes:** `load_precos_mensais_us` é produzida na Task 2 e consumida na Task 3. `simbolos_das_carteiras`, `ler_do_local` e `publicar` são da Task 1 e só o script as usa. O formato de saída da Task 2 é amarrado ao de `market_read.load_precos_mensais`, e o teste da Task 3 falha se o alinhamento por índice quebrar.

**Restrição que permanece:** 1,4 MB numa folga de 9 MB. Este plano não resolve o espaço — a limpeza dos 63 MB de `brapi_raw_payloads` é trabalho separado, e o inventário está em `05_Banco_de_Dados/warehouse_local_dfu_inventario.md` no vault.
