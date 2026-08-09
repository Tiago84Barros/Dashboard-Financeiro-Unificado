# Fase 2a — Portfólio Global (composição): Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Criar a seção Portfólio Global que reúne as três carteiras-modelo num único patrimônio e mostra composição, concentração e métricas agregadas, lendo exclusivamente os snapshots persistidos na Fase 1.

**Architecture:** Uma camada de análise pura em `core/global_portfolio/` que recebe os snapshots do repositório e devolve estruturas de dados; uma view Streamlit que só formata. Nenhum módulo de análise toca SQL ou Streamlit. A Fase 2a **não depende de série de preço** — tudo sai do payload dos snapshots e dos pesos.

**Tech Stack:** Python 3.12, pandas, SQLAlchemy (apenas no repositório), Streamlit, pytest.

## Global Constraints

- **Aditividade:** nenhuma funcionalidade existente pode ser removida ou reescrita. As únicas alterações em arquivo pré-existente são: novas funções ao final de `core/portfolio/repository.py` (Task 1) e uma entrada nova no dicionário `_ROTAS` de `app.py` (Task 7).
- **Camadas:** `core/global_portfolio/*` não executa SQL, não importa Streamlit e não faz I/O. Recebe dados prontos por parâmetro. `views/portfolio_global.py` não calcula.
- **Determinismo:** nenhuma saída pode depender de ordem de iteração de `dict`/`set`. Ordenar explicitamente antes de qualquer agregação ou desempate.
- **Cobertura explícita:** toda métrica agregada publica a fração do patrimônio que possuía o dado. Abaixo de 60% a métrica é exibida com aviso, nunca omitida em silêncio.
- **Sem FX nesta fase:** pesos são adimensionais (`alvo_da_classe × peso_no_modelo`), então não há conversão cambial. Valores em R$ só aparecem se o usuário informar `total_brl`, e são `total_brl × peso_global`. Não inventar taxa de câmbio.
- **Idioma:** comentários, docstrings e textos de interface em português.
- **UI:** toda métrica em card CSS. O padrão do projeto é o helper `_kpi_html(label, value, detail, icon, color) -> str` em `views/dashboard_geral.py:377`. Nenhuma informação solta.
- **Interpretador:** o `python` do PATH cai numa venv sem pytest. Usar sempre:
  `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest ...`
- **Baseline da suíte antes desta fase:** `1539 passed, 3 skipped, 0 failed`. Nenhum teste que passava pode quebrar.
- **Costura real, não fake:** lição registrada da Fase 1 — três defeitos sobreviveram a dez revisões porque todo teste da costura usava mock. Onde dois módulos se encontram, pelo menos um teste exercita os dois lados de verdade.

---

## Correções à spec aplicadas neste plano

A spec (§6.6) pede "qualidade média" como número único do patrimônio, normalizando cada score ao percentil dentro da classe. **Isso não é defensável e não será implementado assim.** Percentil dentro das próprias posições da classe mede ordenação interna, não qualidade: a média dos percentis de 20 ativos tende a 0,5 por construção, independentemente de a carteira ser boa ou ruim. O plano entrega `qualidade_por_classe()`, com a escala de cada classe explícita, e a interface diz por que não há número único.

A spec (§6.4) fala em "retornos diários". O dado real é **mensal** (`market.historical_prices` via `load_precos_mensais`; `market_us.prices_monthly`). Isso afeta a Fase 2b, não esta.

---

## Estrutura de arquivos

| Arquivo | Responsabilidade |
|---|---|
| `core/portfolio/repository.py` (modificar) | +`active_model_id`, +`load_active_snapshots`, +`save_allocation_targets`, +`load_allocation_targets` |
| `core/global_portfolio/__init__.py` | Reexporta a API pública |
| `core/global_portfolio/taxonomy.py` | Setor canônico a partir do vocabulário de cada classe |
| `core/global_portfolio/fields.py` | Extrai um campo canônico (`pe`, `dy`, …) do payload, seja qual for a classe |
| `core/global_portfolio/aggregate.py` | Monta o quadro unificado de posições com peso global |
| `core/global_portfolio/concentration.py` | HHI, número efetivo, top-N, Gini |
| `core/global_portfolio/metrics.py` | Valuation agregado, DY, crescimento, qualidade por classe, cobertura |
| `views/portfolio_global.py` | Interface: cards, tabelas, estado vazio |
| `app.py` (modificar) | +1 entrada em `_ROTAS` |

---

### Task 1: Repositório — modelo ativo, snapshots ativos e alocação-alvo

**Files:**
- Modify: `core/portfolio/repository.py` (acrescentar funções ao final; não alterar as existentes)
- Test: `tests/test_portfolio_repository_global.py`

**Interfaces:**
- Consumes: `core.portfolio.registry.get_spec` (com `.models_table`), `_resolve_engine`, `_resolve_owner`, `_json_placeholder`, `_decode` — todos já existentes em `repository.py`.
- Produces:
  - `active_model_id(asset_class: str, *, engine=None, owner_id=None) -> str | None`
  - `load_active_snapshots(asset_class: str, *, engine=None, owner_id=None) -> dict[str, dict]` — símbolo → payload; `{}` se não houver modelo ativo.
  - `save_allocation_targets(targets: dict[str, float], *, total_brl: float | None = None, notes: str = "", engine=None, owner_id=None) -> str`
  - `load_allocation_targets(*, engine=None, owner_id=None) -> dict` — `{"targets": {classe: peso}, "total_brl": float|None, "notes": str}`; pesos normalizados para somar 1. Devolve `{"targets": {}, "total_brl": None, "notes": ""}` se não houver registro ativo.

**Nota:** `save_allocation_targets` arquiva o registro ativo anterior antes de inserir, espelhando o que `save_*_portfolio_model` já faz. O índice único parcial `uq_portfolio_allocation_targets_active_per_user` (schema 049) garante um só ativo por dono.

- [ ] **Step 1: Escrever o teste que falha**

```python
"""Leitura do modelo ativo e persistencia da alocacao-alvo."""
import datetime as dt

import pytest
from sqlalchemy import create_engine, text

from core.portfolio.models import AssetSnapshot
from core.portfolio.repository import (
    active_model_id,
    load_active_snapshots,
    load_allocation_targets,
    save_allocation_targets,
    save_snapshots,
)

OWNER = "22222222-2222-2222-2222-222222222222"


@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:")
    with eng.begin() as conn:
        conn.execute(text("""
            CREATE TABLE portfolio_asset_snapshots (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, asset_class TEXT NOT NULL,
                model_id TEXT NOT NULL, symbol TEXT NOT NULL, schema_version INTEGER NOT NULL,
                as_of_date TEXT NOT NULL, payload TEXT NOT NULL, payload_digest TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (asset_class, model_id, symbol)
            )
        """))
        conn.execute(text("""
            CREATE TABLE portfolio_allocation_targets (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
                total_brl REAL, targets_json TEXT NOT NULL DEFAULT '{}', notes TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        for tabela in ("b3_portfolio_models", "us_portfolio_models", "fii_portfolio_models"):
            conn.execute(text(f"""
                CREATE TABLE {tabela} (
                    id TEXT PRIMARY KEY, user_id TEXT, status TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """))
    return eng


def _modelo(engine, tabela, model_id, status, created="2026-08-01"):
    with engine.begin() as conn:
        conn.execute(
            text(f"INSERT INTO {tabela} (id, user_id, status, created_at) "
                 f"VALUES (:i, :u, :s, :c)"),
            {"i": model_id, "u": OWNER, "s": status, "c": created},
        )


def _snap(model_id, symbol):
    return AssetSnapshot.from_blocks(
        asset_class="b3", model_id=model_id, symbol=symbol,
        as_of_date=dt.date(2026, 8, 9),
        blocks={"identity": {"symbol": symbol}, "metrics": {"weight": 0.5}},
    )


def test_active_model_id_devolve_o_ativo(engine):
    _modelo(engine, "b3_portfolio_models", "m_old", "archived", "2026-07-01")
    _modelo(engine, "b3_portfolio_models", "m_new", "active", "2026-08-01")
    assert active_model_id("b3", engine=engine, owner_id=OWNER) == "m_new"


def test_active_model_id_sem_modelo_devolve_none(engine):
    assert active_model_id("us", engine=engine, owner_id=OWNER) is None


def test_load_active_snapshots_traz_apenas_o_modelo_ativo(engine):
    _modelo(engine, "b3_portfolio_models", "m_old", "archived", "2026-07-01")
    _modelo(engine, "b3_portfolio_models", "m_new", "active", "2026-08-01")
    save_snapshots([_snap("m_old", "ANTIGA3")], engine=engine, owner_id=OWNER)
    save_snapshots([_snap("m_new", "PETR4")], engine=engine, owner_id=OWNER)

    ativos = load_active_snapshots("b3", engine=engine, owner_id=OWNER)
    assert set(ativos) == {"PETR4"}


def test_load_active_snapshots_sem_modelo_devolve_vazio(engine):
    assert load_active_snapshots("fii", engine=engine, owner_id=OWNER) == {}


def test_alocacao_alvo_round_trip_normaliza_pesos(engine):
    save_allocation_targets({"b3": 50, "us": 30, "fii": 20}, total_brl=100000.0,
                            engine=engine, owner_id=OWNER)
    alvo = load_allocation_targets(engine=engine, owner_id=OWNER)
    assert alvo["targets"] == pytest.approx({"b3": 0.5, "us": 0.3, "fii": 0.2})
    assert sum(alvo["targets"].values()) == pytest.approx(1.0)
    assert alvo["total_brl"] == 100000.0


def test_alocacao_alvo_sem_registro_devolve_estrutura_vazia(engine):
    alvo = load_allocation_targets(engine=engine, owner_id=OWNER)
    assert alvo == {"targets": {}, "total_brl": None, "notes": ""}


def test_salvar_alocacao_arquiva_a_anterior(engine):
    save_allocation_targets({"b3": 100}, engine=engine, owner_id=OWNER)
    save_allocation_targets({"b3": 60, "fii": 40}, engine=engine, owner_id=OWNER)

    alvo = load_allocation_targets(engine=engine, owner_id=OWNER)
    assert alvo["targets"] == pytest.approx({"b3": 0.6, "fii": 0.4})

    with engine.connect() as conn:
        ativos = conn.execute(
            text("SELECT COUNT(*) FROM portfolio_allocation_targets WHERE status='active'")
        ).scalar()
    assert ativos == 1


def test_alocacao_com_soma_zero_e_rejeitada(engine):
    with pytest.raises(ValueError, match="soma"):
        save_allocation_targets({"b3": 0, "us": 0}, engine=engine, owner_id=OWNER)


def test_alocacao_com_classe_desconhecida_e_rejeitada(engine):
    with pytest.raises(KeyError, match="cripto"):
        save_allocation_targets({"cripto": 100}, engine=engine, owner_id=OWNER)
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_portfolio_repository_global.py -v`
Expected: FAIL com `ImportError: cannot import name 'active_model_id'`

- [ ] **Step 3: Acrescentar as funções ao final de `core/portfolio/repository.py`**

Não alterar nada acima. Acrescentar:

```python
_TABELA_ALVO = "portfolio_allocation_targets"


def active_model_id(asset_class: str, *, engine=None, owner_id=None) -> str | None:
    """Id do modelo ativo do dono para a classe, ou None se nao houver."""
    spec = get_spec(asset_class)
    eng = _resolve_engine(engine)
    owner = _resolve_owner(owner_id)

    with eng.connect() as conn:
        linha = conn.execute(
            text(f"""
                SELECT id FROM {spec.models_table}
                WHERE user_id = :uid AND status = 'active'
                ORDER BY created_at DESC, id DESC
                LIMIT 1
            """),
            {"uid": owner},
        ).mappings().first()
    return str(linha["id"]) if linha else None


def load_active_snapshots(asset_class: str, *, engine=None, owner_id=None) -> dict[str, dict]:
    """{simbolo: payload} do modelo ATIVO da classe. Vazio se nao houver modelo."""
    model_id = active_model_id(asset_class, engine=engine, owner_id=owner_id)
    if not model_id:
        return {}
    return load_snapshots(asset_class, model_id, engine=engine)


def _normalizar_alvos(targets: dict) -> dict[str, float]:
    """Valida as classes e normaliza os pesos para somar 1."""
    limpos: dict[str, float] = {}
    for chave, valor in targets.items():
        spec = get_spec(chave)              # levanta KeyError em classe desconhecida
        peso = float(valor or 0.0)
        if peso < 0:
            raise ValueError(f"peso negativo para a classe {spec.key!r}")
        limpos[spec.key] = peso

    total = sum(limpos.values())
    if total <= 0:
        raise ValueError("a soma dos pesos da alocacao-alvo precisa ser maior que zero")
    return {k: limpos[k] / total for k in sorted(limpos)}


def save_allocation_targets(targets: dict[str, float], *, total_brl: float | None = None,
                            notes: str = "", engine=None, owner_id=None) -> str:
    """Salva a alocacao-alvo ativa, arquivando a anterior. Devolve o id."""
    normalizados = _normalizar_alvos(targets)
    eng = _resolve_engine(engine)
    owner = _resolve_owner(owner_id)
    placeholder = ("CAST(:targets_json AS jsonb)"
                   if eng.dialect.name == "postgresql" else ":targets_json")
    novo_id = str(uuid.uuid4())

    with eng.begin() as conn:
        conn.execute(
            text(f"UPDATE {_TABELA_ALVO} SET status = 'archived' "
                 f"WHERE user_id = :uid AND status = 'active'"),
            {"uid": owner},
        )
        conn.execute(
            text(f"""
                INSERT INTO {_TABELA_ALVO}
                    (id, user_id, status, total_brl, targets_json, notes)
                VALUES
                    (:id, :uid, 'active', :total_brl, {placeholder}, :notes)
            """),
            {
                "id": novo_id, "uid": owner, "total_brl": total_brl,
                "targets_json": canonical_json(normalizados), "notes": notes or "",
            },
        )
    return novo_id


def load_allocation_targets(*, engine=None, owner_id=None) -> dict:
    """Alocacao-alvo ativa do dono. Estrutura vazia se nao houver."""
    eng = _resolve_engine(engine)
    owner = _resolve_owner(owner_id)

    with eng.connect() as conn:
        linha = conn.execute(
            text(f"""
                SELECT total_brl, targets_json, notes FROM {_TABELA_ALVO}
                WHERE user_id = :uid AND status = 'active'
                ORDER BY created_at DESC, id DESC
                LIMIT 1
            """),
            {"uid": owner},
        ).mappings().first()

    if not linha:
        return {"targets": {}, "total_brl": None, "notes": ""}

    alvos = _decode(linha["targets_json"]) or {}
    total = linha["total_brl"]
    return {
        "targets": {str(k): float(v) for k, v in sorted(alvos.items())},
        "total_brl": float(total) if total is not None else None,
        "notes": linha["notes"] or "",
    }
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_portfolio_repository_global.py -v`
Expected: 9 passed

- [ ] **Step 5: Confirmar que a Fase 1 não regrediu**

Run: `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_portfolio_repository.py tests/test_portfolio_capture.py -v`
Expected: todos passam.

- [ ] **Step 6: Commit**

```bash
git add core/portfolio/repository.py tests/test_portfolio_repository_global.py
git commit -m "feat(global): leitura do modelo ativo e persistencia da alocacao-alvo"
```

---

### Task 2: Taxonomia de setor canônica

**Files:**
- Create: `core/global_portfolio/__init__.py`, `core/global_portfolio/taxonomy.py`
- Test: `tests/test_global_taxonomy.py`

**Interfaces:**
- Consumes: nada.
- Produces:
  - `SETORES_CANONICOS: tuple[str, ...]` — chaves canônicas ordenadas.
  - `ROTULOS: dict[str, str]` — chave canônica → rótulo em português para a interface.
  - `setor_canonico(asset_class: str, setor: str | None, segmento: str | None = None) -> str`
  - `nao_mapeados(linhas: list[dict]) -> list[tuple[str, str]]` — diagnóstico: pares `(asset_class, setor)` que caíram em `outros`, ordenados e sem repetição.

O vocabulário canônico **reaproveita** as chaves já usadas em `core/empresas.py::_SETOR_LABEL` (`real_estate`, `financials`, `utilities`, `energy`, `materials`, `industrials`, `consumer`, `consumer_staples`, `health_care`, `technology`, `telecom`, `other`). Não inventar um vocabulário novo: o projeto já tem um.

- [ ] **Step 1: Escrever o teste que falha**

```python
"""Mapa canonico de setor entre B3, EUA e FII."""
import pytest

from core.global_portfolio.taxonomy import (
    ROTULOS,
    SETORES_CANONICOS,
    nao_mapeados,
    setor_canonico,
)


def test_todo_setor_canonico_tem_rotulo():
    assert set(ROTULOS) == set(SETORES_CANONICOS)


def test_setores_canonicos_sao_deterministicos():
    assert SETORES_CANONICOS == tuple(sorted(SETORES_CANONICOS))


@pytest.mark.parametrize("setor,esperado", [
    ("Petróleo, Gás e Biocombustíveis", "energy"),
    ("Materiais Básicos", "materials"),
    ("Bens Industriais", "industrials"),
    ("Consumo não Cíclico", "consumer_staples"),
    ("Consumo Cíclico", "consumer"),
    ("Saúde", "health_care"),
    ("Tecnologia da Informação", "technology"),
    ("Comunicações", "telecom"),
    ("Utilidade Pública", "utilities"),
    ("Financeiro", "financials"),
])
def test_setores_da_b3(setor, esperado):
    assert setor_canonico("b3", setor) == esperado


@pytest.mark.parametrize("setor,esperado", [
    ("Energy", "energy"),
    ("Basic Materials", "materials"),
    ("Industrials", "industrials"),
    ("Consumer Defensive", "consumer_staples"),
    ("Consumer Cyclical", "consumer"),
    ("Healthcare", "health_care"),
    ("Technology", "technology"),
    ("Communication Services", "telecom"),
    ("Utilities", "utilities"),
    ("Financial Services", "financials"),
    ("Real Estate", "real_estate"),
])
def test_setores_do_mercado_americano(setor, esperado):
    assert setor_canonico("us", setor) == esperado


def test_fii_sempre_cai_em_real_estate_independente_do_segmento():
    assert setor_canonico("fii", "Logística", "Tijolo") == "real_estate"
    assert setor_canonico("fii", "Papel", "Papel") == "real_estate"
    assert setor_canonico("fii", None, None) == "real_estate"


def test_comparacao_ignora_acento_caixa_e_espaco():
    assert setor_canonico("b3", "  consumo NAO ciclico  ") == "consumer_staples"
    assert setor_canonico("us", "  HEALTHCARE ") == "health_care"


def test_setor_desconhecido_cai_em_other_sem_levantar():
    assert setor_canonico("b3", "Setor Inventado") == "other"
    assert setor_canonico("us", None) == "other"


def test_nao_mapeados_lista_os_pares_que_cairam_em_other():
    linhas = [
        {"asset_class": "b3", "sector": "Financeiro"},
        {"asset_class": "b3", "sector": "Setor Inventado"},
        {"asset_class": "us", "sector": "Outro Inventado"},
        {"asset_class": "b3", "sector": "Setor Inventado"},   # repetido
    ]
    assert nao_mapeados(linhas) == [("b3", "Setor Inventado"), ("us", "Outro Inventado")]


def test_nao_mapeados_ignora_setor_vazio():
    assert nao_mapeados([{"asset_class": "b3", "sector": None}]) == []
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_global_taxonomy.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'core.global_portfolio'`

- [ ] **Step 3: Escrever a implementação**

Criar `core/global_portfolio/__init__.py`:

```python
"""Analise do patrimonio consolidado a partir dos snapshots das carteiras.

Camada pura: nenhum modulo aqui executa SQL, importa Streamlit ou faz I/O.
"""
```

Criar `core/global_portfolio/taxonomy.py`:

```python
"""Setor canonico comum a B3, mercado americano e FIIs.

Sem um vocabulario unico, "concentracao por setor" mistura escalas
incompativeis e produz um numero enganoso: a B3 usa os setores economicos
proprios, o mercado americano usa GICS e o FII usa segmento de imovel.

As chaves canonicas sao as mesmas ja usadas em core/empresas.py::_SETOR_LABEL —
o projeto ja tinha um vocabulario, nao criamos outro.

Coberto por tests/test_global_taxonomy.py.
"""
from __future__ import annotations

import unicodedata

SETORES_CANONICOS: tuple[str, ...] = (
    "consumer", "consumer_staples", "energy", "financials", "health_care",
    "industrials", "materials", "other", "real_estate", "technology",
    "telecom", "utilities",
)

ROTULOS: dict[str, str] = {
    "consumer": "Consumo Cíclico",
    "consumer_staples": "Consumo Básico",
    "energy": "Energia",
    "financials": "Financeiro",
    "health_care": "Saúde",
    "industrials": "Industrial",
    "materials": "Materiais",
    "other": "Outros",
    "real_estate": "Imóveis",
    "technology": "Tecnologia",
    "telecom": "Telecom",
    "utilities": "Utilidades",
}


def _chave(texto) -> str:
    """Minusculo, sem acento e sem espaco nas pontas, para comparar rotulos."""
    bruto = str(texto or "").strip().lower()
    sem_acento = unicodedata.normalize("NFKD", bruto)
    return "".join(c for c in sem_acento if not unicodedata.combining(c))


# Setores economicos da B3 (11 oficiais) -> canonico.
_B3: dict[str, str] = {
    _chave("Petróleo, Gás e Biocombustíveis"): "energy",
    _chave("Materiais Básicos"): "materials",
    _chave("Bens Industriais"): "industrials",
    _chave("Consumo não Cíclico"): "consumer_staples",
    _chave("Consumo Cíclico"): "consumer",
    _chave("Saúde"): "health_care",
    _chave("Tecnologia da Informação"): "technology",
    _chave("Comunicações"): "telecom",
    _chave("Utilidade Pública"): "utilities",
    _chave("Financeiro"): "financials",
    _chave("Outros"): "other",
}

# Setores GICS como o yfinance os devolve -> canonico.
_US: dict[str, str] = {
    _chave("Energy"): "energy",
    _chave("Basic Materials"): "materials",
    _chave("Materials"): "materials",
    _chave("Industrials"): "industrials",
    _chave("Consumer Defensive"): "consumer_staples",
    _chave("Consumer Staples"): "consumer_staples",
    _chave("Consumer Cyclical"): "consumer",
    _chave("Consumer Discretionary"): "consumer",
    _chave("Healthcare"): "health_care",
    _chave("Health Care"): "health_care",
    _chave("Technology"): "technology",
    _chave("Information Technology"): "technology",
    _chave("Communication Services"): "telecom",
    _chave("Utilities"): "utilities",
    _chave("Financial Services"): "financials",
    _chave("Financials"): "financials",
    _chave("Real Estate"): "real_estate",
}

_POR_CLASSE: dict[str, dict[str, str]] = {"b3": _B3, "us": _US}


def setor_canonico(asset_class: str, setor: str | None,
                   segmento: str | None = None) -> str:
    """Traduz o setor da classe para a chave canonica. Desconhecido vira 'other'.

    FII nao usa `setor`/`segmento` para esta decisao: todo FII e exposicao
    imobiliaria. O segmento (tijolo, papel, hibrido) e detalhe de subsetor e
    e preservado a parte, no proprio snapshot.
    """
    classe = str(asset_class or "").strip().lower()
    if classe == "fii":
        return "real_estate"
    return _POR_CLASSE.get(classe, {}).get(_chave(setor), "other")


def nao_mapeados(linhas: list[dict]) -> list[tuple[str, str]]:
    """Pares (classe, setor) que cairam em 'other' tendo valor preenchido.

    Diagnostico de cobertura: se um setor real da carteira aparece aqui, o mapa
    precisa crescer — 'other' silencioso e o modo de falha a evitar.
    """
    achados: set[tuple[str, str]] = set()
    for linha in linhas:
        classe = str(linha.get("asset_class") or "").strip().lower()
        setor = linha.get("sector")
        if not str(setor or "").strip():
            continue
        if setor_canonico(classe, setor) == "other":
            achados.add((classe, str(setor)))
    return sorted(achados)
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_global_taxonomy.py -v`
Expected: 28 passed (7 simples + 10 + 11 parametrizados)

- [ ] **Step 5: Commit**

```bash
git add core/global_portfolio/__init__.py core/global_portfolio/taxonomy.py tests/test_global_taxonomy.py
git commit -m "feat(global): taxonomia de setor canonica entre B3, EUA e FII"
```

---

### Task 3: Extração de campo canônico do payload

**Files:**
- Create: `core/global_portfolio/fields.py`
- Test: `tests/test_global_fields.py`

**Interfaces:**
- Consumes: nada.
- Produces:
  - `CAMPOS: tuple[str, ...]` — nomes canônicos suportados, ordenados.
  - `valor(payload: dict, asset_class: str, campo: str) -> float | None`
  - `disponivel(payload: dict, asset_class: str, campo: str) -> bool`

Cada classe grava o mesmo conceito com nome diferente: o P/L é `P/L` no B3 e `pe_ratio` nos EUA; o DY é `DY` no B3, `dividend_yield` nos EUA e `dy_12m` no FII. Sem esta camada, cada consumidor reimplementaria o de-para.

**Campos canônicos e origem por classe:**

| Canônico | B3 (`fundamentals`) | EUA (`fundamentals`) | FII (`fundamentals`) |
|---|---|---|---|
| `pe` | `P/L` | `pe_ratio` | — (não aplicável) |
| `pvp` | `P/VP` | `price_to_book` | `pvp` |
| `dy` | `DY` | `dividend_yield` | `dy_12m` |
| `roe` | `ROE` | `return_on_equity` | — |
| `market_cap` | `Valor de mercado` | `market_cap` | `patrimonio_liquido` |

Campo ausente ou não aplicável devolve `None` — nunca `0`, que seria confundido com valor real.

- [ ] **Step 1: Escrever o teste que falha**

```python
"""Extracao de campo canonico do payload, seja qual for a classe."""
import pytest

from core.global_portfolio.fields import CAMPOS, disponivel, valor

B3 = {"fundamentals": {"P/L": 8.5, "P/VP": 1.2, "DY": 6.4, "ROE": 15.0,
                       "Valor de mercado": 1.2e11}}
US = {"fundamentals": {"pe_ratio": 28.4, "price_to_book": 45.0,
                       "dividend_yield": 0.5, "return_on_equity": 120.0,
                       "market_cap": 3.4e12}}
FII = {"fundamentals": {"pvp": 0.95, "dy_12m": 8.4, "patrimonio_liquido": 3.2e9}}


def test_campos_sao_deterministicos():
    assert CAMPOS == tuple(sorted(CAMPOS))


@pytest.mark.parametrize("payload,classe,campo,esperado", [
    (B3, "b3", "pe", 8.5),
    (B3, "b3", "dy", 6.4),
    (B3, "b3", "market_cap", 1.2e11),
    (US, "us", "pe", 28.4),
    (US, "us", "dy", 0.5),
    (US, "us", "roe", 120.0),
    (FII, "fii", "pvp", 0.95),
    (FII, "fii", "dy", 8.4),
    (FII, "fii", "market_cap", 3.2e9),
])
def test_extrai_o_campo_certo_por_classe(payload, classe, campo, esperado):
    assert valor(payload, classe, campo) == esperado


def test_campo_nao_aplicavel_a_classe_devolve_none():
    # FII nao tem P/L: nao existe lucro contabil comparavel.
    assert valor(FII, "fii", "pe") is None


def test_campo_ausente_devolve_none_e_nao_zero():
    assert valor({"fundamentals": {}}, "b3", "pe") is None


def test_payload_sem_bloco_fundamentals_devolve_none():
    assert valor({}, "b3", "pe") is None


def test_valor_nao_numerico_devolve_none():
    assert valor({"fundamentals": {"P/L": "n/d"}}, "b3", "pe") is None


def test_campo_desconhecido_levanta_erro_claro():
    with pytest.raises(KeyError, match="ebitda"):
        valor(B3, "b3", "ebitda")


def test_disponivel_reflete_a_presenca_do_valor():
    assert disponivel(B3, "b3", "pe") is True
    assert disponivel(FII, "fii", "pe") is False
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_global_fields.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'core.global_portfolio.fields'`

- [ ] **Step 3: Escrever a implementação**

```python
"""De-para entre o nome canonico de um indicador e a chave real de cada classe.

Cada classe gravou o mesmo conceito com nome proprio: o P/L e "P/L" no B3 e
"pe_ratio" nos EUA. Sem esta camada, todo consumidor reimplementaria o de-para
e eles divergiriam.

Campo ausente ou nao aplicavel devolve None, nunca 0 — zero seria confundido
com valor real e contaminaria qualquer media.

Coberto por tests/test_global_fields.py.
"""
from __future__ import annotations

CAMPOS: tuple[str, ...] = ("dy", "market_cap", "pe", "pvp", "roe")

# campo canonico -> {classe: chave dentro de payload["fundamentals"]}
# Ausencia da classe no dicionario interno significa "nao aplicavel".
_ORIGEM: dict[str, dict[str, str]] = {
    "pe": {"b3": "P/L", "us": "pe_ratio"},
    "pvp": {"b3": "P/VP", "us": "price_to_book", "fii": "pvp"},
    "dy": {"b3": "DY", "us": "dividend_yield", "fii": "dy_12m"},
    "roe": {"b3": "ROE", "us": "return_on_equity"},
    "market_cap": {"b3": "Valor de mercado", "us": "market_cap",
                   "fii": "patrimonio_liquido"},
}


def valor(payload: dict, asset_class: str, campo: str) -> float | None:
    """Valor numerico do campo canonico, ou None se ausente/nao aplicavel."""
    if campo not in _ORIGEM:
        raise KeyError(f"campo canonico desconhecido: {campo!r}")

    chave = _ORIGEM[campo].get(str(asset_class or "").strip().lower())
    if not chave:
        return None

    bruto = (payload or {}).get("fundamentals", {}).get(chave)
    if bruto is None or isinstance(bruto, bool):
        return None
    try:
        return float(bruto)
    except (TypeError, ValueError):
        return None


def disponivel(payload: dict, asset_class: str, campo: str) -> bool:
    """True quando o campo tem valor numerico utilizavel."""
    return valor(payload, asset_class, campo) is not None
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_global_fields.py -v`
Expected: 16 passed

- [ ] **Step 5: Commit**

```bash
git add core/global_portfolio/fields.py tests/test_global_fields.py
git commit -m "feat(global): extracao de campo canonico independente da classe"
```

---

### Task 4: Agregação — o quadro unificado de posições

**Files:**
- Create: `core/global_portfolio/aggregate.py`
- Test: `tests/test_global_aggregate.py`

**Interfaces:**
- Consumes: `core.global_portfolio.taxonomy.setor_canonico`, `core.portfolio.registry.get_spec` (para `.currency` e `.country`).
- Produces:
  - `montar_posicoes(snapshots_por_classe: dict[str, dict[str, dict]], alvos: dict[str, float], *, total_brl: float | None = None) -> pd.DataFrame`

Colunas do DataFrame, nesta ordem: `asset_class`, `symbol`, `name`, `sector_raw`, `sector`, `segment`, `currency`, `country`, `weight_class`, `weight_global`, `valor_brl`, `payload`.

Regras:
- `weight_global = alvos[classe] × peso_do_ativo_no_modelo`, com os pesos do modelo renormalizados dentro da classe (se somarem 0,98 por arredondamento, viram 1).
- Classe presente nos alvos mas sem snapshot é ignorada sem erro.
- Classe com snapshot mas ausente dos alvos recebe peso 0 e permanece na tabela, marcada — desaparecer em silêncio é o que não pode acontecer.
- `valor_brl` = `total_brl × weight_global`, ou `None` quando `total_brl` é `None`. **Sem conversão cambial**: o peso é adimensional e o total já está em BRL.
- Ordenação determinística: `weight_global` decrescente, desempate por `asset_class` e depois `symbol`.

- [ ] **Step 1: Escrever o teste que falha**

```python
"""Quadro unificado de posicoes das tres carteiras."""
import pandas as pd
import pytest

from core.global_portfolio.aggregate import montar_posicoes

SNAPS = {
    "b3": {
        "PETR4": {"identity": {"symbol": "PETR4", "name": "Petrobras",
                               "sector": "Petróleo, Gás e Biocombustíveis"},
                  "metrics": {"weight": 0.6}},
        "ITUB4": {"identity": {"symbol": "ITUB4", "name": "Itaú",
                               "sector": "Financeiro"},
                  "metrics": {"weight": 0.4}},
    },
    "us": {
        "AAPL": {"identity": {"symbol": "AAPL", "name": "Apple",
                              "sector": "Technology"},
                 "metrics": {"weight": 1.0}},
    },
    "fii": {
        "HGLG11": {"identity": {"symbol": "HGLG11", "name": "CSHG Log",
                                "sector": "Logística", "segment": "Tijolo"},
                   "metrics": {"weight": 1.0}},
    },
}

ALVOS = {"b3": 0.5, "us": 0.3, "fii": 0.2}


def test_peso_global_e_alvo_da_classe_vezes_peso_no_modelo():
    df = montar_posicoes(SNAPS, ALVOS)
    petr = df[df["symbol"] == "PETR4"].iloc[0]
    assert petr["weight_global"] == pytest.approx(0.5 * 0.6)


def test_pesos_globais_somam_um():
    df = montar_posicoes(SNAPS, ALVOS)
    assert df["weight_global"].sum() == pytest.approx(1.0)


def test_pesos_do_modelo_sao_renormalizados_dentro_da_classe():
    snaps = {"b3": {
        "A3": {"identity": {"symbol": "A3"}, "metrics": {"weight": 0.3}},
        "B3X": {"identity": {"symbol": "B3X"}, "metrics": {"weight": 0.3}},
    }}
    df = montar_posicoes(snaps, {"b3": 1.0})
    assert df["weight_global"].sum() == pytest.approx(1.0)
    assert df["weight_class"].tolist() == pytest.approx([0.5, 0.5])


def test_setor_canonico_e_preenchido_e_o_bruto_preservado():
    df = montar_posicoes(SNAPS, ALVOS).set_index("symbol")
    assert df.loc["PETR4", "sector"] == "energy"
    assert df.loc["PETR4", "sector_raw"] == "Petróleo, Gás e Biocombustíveis"
    assert df.loc["AAPL", "sector"] == "technology"
    assert df.loc["HGLG11", "sector"] == "real_estate"


def test_moeda_e_pais_vem_do_registro_da_classe():
    df = montar_posicoes(SNAPS, ALVOS).set_index("symbol")
    assert df.loc["PETR4", "currency"] == "BRL"
    assert df.loc["PETR4", "country"] == "BR"
    assert df.loc["AAPL", "currency"] == "USD"
    assert df.loc["AAPL", "country"] == "US"


def test_valor_brl_sai_do_total_informado_sem_conversao_cambial():
    df = montar_posicoes(SNAPS, ALVOS, total_brl=100000.0).set_index("symbol")
    assert df.loc["AAPL", "valor_brl"] == pytest.approx(30000.0)


def test_sem_total_informado_valor_brl_fica_nulo():
    df = montar_posicoes(SNAPS, ALVOS)
    assert df["valor_brl"].isna().all()


def test_classe_no_alvo_sem_snapshot_e_ignorada():
    df = montar_posicoes({"b3": SNAPS["b3"]}, {"b3": 0.5, "us": 0.5})
    assert set(df["asset_class"]) == {"b3"}
    assert df["weight_global"].sum() == pytest.approx(0.5)


def test_classe_com_snapshot_fora_do_alvo_aparece_com_peso_zero():
    df = montar_posicoes(SNAPS, {"b3": 1.0})
    fora = df[df["asset_class"] != "b3"]
    assert not fora.empty
    assert (fora["weight_global"] == 0.0).all()


def test_ordenacao_e_deterministica():
    df = montar_posicoes(SNAPS, ALVOS)
    pesos = df["weight_global"].tolist()
    assert pesos == sorted(pesos, reverse=True)


def test_snapshots_vazios_devolvem_dataframe_vazio_com_as_colunas():
    df = montar_posicoes({}, {})
    assert df.empty
    for coluna in ("asset_class", "symbol", "sector", "weight_global"):
        assert coluna in df.columns
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_global_aggregate.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'core.global_portfolio.aggregate'`

- [ ] **Step 3: Escrever a implementação**

```python
"""Quadro unificado das posicoes das tres carteiras.

Peso global = alvo da classe x peso do ativo dentro do modelo. O peso e
adimensional, entao NAO ha conversao cambial aqui: o valor em reais, quando o
usuario informa o total, e total_brl x peso_global.

Coberto por tests/test_global_aggregate.py.
"""
from __future__ import annotations

import pandas as pd

from core.global_portfolio.taxonomy import setor_canonico
from core.portfolio.registry import get_spec

COLUNAS: tuple[str, ...] = (
    "asset_class", "symbol", "name", "sector_raw", "sector", "segment",
    "currency", "country", "weight_class", "weight_global", "valor_brl",
    "payload",
)


def _peso_do_modelo(payload: dict) -> float:
    bruto = (payload or {}).get("metrics", {}).get("weight")
    try:
        peso = float(bruto)
    except (TypeError, ValueError):
        return 0.0
    return peso if peso > 0 else 0.0


def montar_posicoes(snapshots_por_classe: dict[str, dict[str, dict]],
                    alvos: dict[str, float],
                    *, total_brl: float | None = None) -> pd.DataFrame:
    """Une as tres carteiras num unico quadro de posicoes com peso global."""
    linhas: list[dict] = []

    for classe in sorted(snapshots_por_classe):
        snaps = snapshots_por_classe[classe] or {}
        if not snaps:
            continue

        spec = get_spec(classe)
        alvo = float(alvos.get(classe, 0.0) or 0.0)

        pesos = {sym: _peso_do_modelo(p) for sym, p in snaps.items()}
        total_classe = sum(pesos.values())

        for symbol in sorted(snaps):
            payload = snaps[symbol]
            identidade = (payload or {}).get("identity", {})
            # Renormaliza dentro da classe: arredondamento no salvamento pode
            # deixar a soma em 0,98 e distorceria o peso global.
            peso_classe = (pesos[symbol] / total_classe) if total_classe > 0 else 0.0
            peso_global = alvo * peso_classe

            linhas.append({
                "asset_class": classe,
                "symbol": symbol,
                "name": identidade.get("name") or symbol,
                "sector_raw": identidade.get("sector"),
                "sector": setor_canonico(classe, identidade.get("sector"),
                                         identidade.get("segment")),
                "segment": identidade.get("segment"),
                "currency": spec.currency,
                "country": spec.country,
                "weight_class": peso_classe,
                "weight_global": peso_global,
                "valor_brl": (total_brl * peso_global) if total_brl is not None else None,
                "payload": payload,
            })

    if not linhas:
        return pd.DataFrame(columns=list(COLUNAS))

    df = pd.DataFrame(linhas, columns=list(COLUNAS))
    return (df.sort_values(["weight_global", "asset_class", "symbol"],
                           ascending=[False, True, True])
              .reset_index(drop=True))
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_global_aggregate.py -v`
Expected: 11 passed

- [ ] **Step 5: Verificar determinismo**

Run com dois seeds:
`PYTHONHASHSEED=1 "/c/.../Python312/python.exe" -m pytest tests/test_global_aggregate.py -q`
`PYTHONHASHSEED=99 "/c/.../Python312/python.exe" -m pytest tests/test_global_aggregate.py -q`
Expected: 11 passed nas duas.

- [ ] **Step 6: Commit**

```bash
git add core/global_portfolio/aggregate.py tests/test_global_aggregate.py
git commit -m "feat(global): quadro unificado de posicoes com peso global"
```

---

### Task 5: Concentração

**Files:**
- Create: `core/global_portfolio/concentration.py`
- Test: `tests/test_global_concentration.py`

**Interfaces:**
- Consumes: o DataFrame de `montar_posicoes` (Task 4).
- Produces:
  - `hhi(pesos: pd.Series) -> float`
  - `numero_efetivo(pesos: pd.Series) -> float` — `1/HHI`, `0.0` quando não há peso.
  - `top_n(df: pd.DataFrame, n: int) -> float` — soma dos `n` maiores `weight_global`.
  - `gini(pesos: pd.Series) -> float`
  - `por_dimensao(df: pd.DataFrame, dimensao: str) -> pd.DataFrame` — colunas `dimensao`, `peso`, `n_ativos`, ordenado por peso desc.
  - `resumo(df: pd.DataFrame) -> dict` — por dimensão (`symbol`, `sector`, `country`, `currency`, `asset_class`): `hhi`, `numero_efetivo`, `maior_peso`, `maior_nome`.

`DIMENSOES: tuple[str, ...] = ("asset_class", "country", "currency", "sector", "symbol")`

- [ ] **Step 1: Escrever o teste que falha**

```python
"""Concentracao por ativo, setor, pais, moeda e classe."""
import pandas as pd
import pytest

from core.global_portfolio.concentration import (
    DIMENSOES,
    gini,
    hhi,
    numero_efetivo,
    por_dimensao,
    resumo,
    top_n,
)


def _df():
    return pd.DataFrame([
        {"asset_class": "b3", "symbol": "PETR4", "sector": "energy",
         "country": "BR", "currency": "BRL", "weight_global": 0.4},
        {"asset_class": "b3", "symbol": "ITUB4", "sector": "financials",
         "country": "BR", "currency": "BRL", "weight_global": 0.3},
        {"asset_class": "us", "symbol": "AAPL", "sector": "technology",
         "country": "US", "currency": "USD", "weight_global": 0.2},
        {"asset_class": "fii", "symbol": "HGLG11", "sector": "real_estate",
         "country": "BR", "currency": "BRL", "weight_global": 0.1},
    ])


def test_hhi_de_carteira_igualmente_dividida():
    # 4 posicoes de 25% -> HHI = 4 * 0.0625 = 0.25
    assert hhi(pd.Series([0.25] * 4)) == pytest.approx(0.25)


def test_hhi_de_posicao_unica_e_um():
    assert hhi(pd.Series([1.0])) == pytest.approx(1.0)


def test_numero_efetivo_e_o_inverso_do_hhi():
    # 4 posicoes iguais -> numero efetivo 4
    assert numero_efetivo(pd.Series([0.25] * 4)) == pytest.approx(4.0)


def test_numero_efetivo_sem_peso_e_zero():
    assert numero_efetivo(pd.Series([0.0, 0.0])) == 0.0


def test_hhi_conhecido_da_carteira_do_teste():
    # 0.4^2 + 0.3^2 + 0.2^2 + 0.1^2 = 0.16+0.09+0.04+0.01 = 0.30
    assert hhi(_df()["weight_global"]) == pytest.approx(0.30)


def test_top_n_soma_as_maiores():
    assert top_n(_df(), 2) == pytest.approx(0.7)
    assert top_n(_df(), 10) == pytest.approx(1.0)


def test_gini_de_carteira_igual_e_zero():
    assert gini(pd.Series([0.25] * 4)) == pytest.approx(0.0, abs=1e-9)


def test_gini_cresce_com_a_desigualdade():
    assert gini(pd.Series([0.97, 0.01, 0.01, 0.01])) > gini(pd.Series([0.4, 0.3, 0.2, 0.1]))


def test_por_dimensao_agrupa_e_conta():
    saida = por_dimensao(_df(), "country")
    br = saida[saida["country"] == "BR"].iloc[0]
    assert br["peso"] == pytest.approx(0.8)
    assert br["n_ativos"] == 3


def test_por_dimensao_ordena_por_peso_decrescente():
    saida = por_dimensao(_df(), "sector")
    assert saida["peso"].tolist() == sorted(saida["peso"].tolist(), reverse=True)


def test_resumo_cobre_todas_as_dimensoes():
    saida = resumo(_df())
    assert set(saida) == set(DIMENSOES)


def test_resumo_aponta_o_maior_de_cada_dimensao():
    saida = resumo(_df())
    assert saida["country"]["maior_nome"] == "BR"
    assert saida["country"]["maior_peso"] == pytest.approx(0.8)
    assert saida["symbol"]["maior_nome"] == "PETR4"


def test_dataframe_vazio_nao_quebra():
    vazio = pd.DataFrame(columns=["asset_class", "symbol", "sector",
                                  "country", "currency", "weight_global"])
    saida = resumo(vazio)
    assert saida["symbol"]["numero_efetivo"] == 0.0
    assert saida["symbol"]["maior_nome"] is None
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_global_concentration.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'core.global_portfolio.concentration'`

- [ ] **Step 3: Escrever a implementação**

```python
"""Concentracao do patrimonio por ativo, setor, pais, moeda e classe.

O HHI e publicado tambem como NUMERO EFETIVO DE POSICOES (1/HHI) porque o
indice cru nao e legivel: "HHI 0,30" nao diz nada, "equivale a 3,3 posicoes
iguais" diz.

Coberto por tests/test_global_concentration.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DIMENSOES: tuple[str, ...] = ("asset_class", "country", "currency", "sector", "symbol")


def hhi(pesos: pd.Series) -> float:
    """Indice Herfindahl-Hirschman dos pesos (0 a 1)."""
    limpos = pd.to_numeric(pesos, errors="coerce").fillna(0.0)
    return float((limpos ** 2).sum())


def numero_efetivo(pesos: pd.Series) -> float:
    """Numero de posicoes iguais que teria a mesma concentracao. 0 se sem peso."""
    indice = hhi(pesos)
    return float(1.0 / indice) if indice > 0 else 0.0


def top_n(df: pd.DataFrame, n: int) -> float:
    """Participacao somada das n maiores posicoes."""
    if df.empty or n <= 0:
        return 0.0
    pesos = pd.to_numeric(df["weight_global"], errors="coerce").fillna(0.0)
    return float(pesos.nlargest(n).sum())


def gini(pesos: pd.Series) -> float:
    """Coeficiente de Gini dos pesos: 0 = perfeitamente igual."""
    limpos = pd.to_numeric(pesos, errors="coerce").fillna(0.0).to_numpy(dtype=float)
    limpos = np.sort(limpos[limpos >= 0])
    total = limpos.sum()
    if total <= 0 or limpos.size == 0:
        return 0.0
    n = limpos.size
    indices = np.arange(1, n + 1)
    return float((2.0 * (indices * limpos).sum()) / (n * total) - (n + 1.0) / n)


def por_dimensao(df: pd.DataFrame, dimensao: str) -> pd.DataFrame:
    """Peso somado e contagem de ativos por valor da dimensao."""
    if df.empty:
        return pd.DataFrame(columns=[dimensao, "peso", "n_ativos"])

    agrupado = (df.groupby(dimensao, dropna=False)
                  .agg(peso=("weight_global", "sum"), n_ativos=("symbol", "count"))
                  .reset_index())
    return (agrupado.sort_values(["peso", dimensao], ascending=[False, True])
                    .reset_index(drop=True))


def resumo(df: pd.DataFrame) -> dict:
    """Concentracao consolidada por dimensao."""
    saida: dict[str, dict] = {}
    for dimensao in DIMENSOES:
        agrupado = por_dimensao(df, dimensao)
        pesos = agrupado["peso"] if not agrupado.empty else pd.Series(dtype=float)
        maior = agrupado.iloc[0] if not agrupado.empty else None
        saida[dimensao] = {
            "hhi": hhi(pesos),
            "numero_efetivo": numero_efetivo(pesos),
            "maior_peso": float(maior["peso"]) if maior is not None else 0.0,
            "maior_nome": (str(maior[dimensao]) if maior is not None else None),
        }
    return saida
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_global_concentration.py -v`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add core/global_portfolio/concentration.py tests/test_global_concentration.py
git commit -m "feat(global): concentracao por ativo, setor, pais, moeda e classe"
```

---

### Task 6: Métricas agregadas

**Files:**
- Create: `core/global_portfolio/metrics.py`
- Test: `tests/test_global_metrics.py`

**Interfaces:**
- Consumes: `core.global_portfolio.fields.valor`, o DataFrame de `montar_posicoes`.
- Produces:
  - `MetricaAgregada` — dataclass congelada com `valor: float | None`, `cobertura: float`, `n_ativos: int`, `confiavel: bool`.
  - `COBERTURA_MINIMA: float = 0.60`
  - `valuation_agregado(df: pd.DataFrame, campo: str = "pe") -> MetricaAgregada`
  - `dy_consolidado(df: pd.DataFrame) -> MetricaAgregada`
  - `qualidade_por_classe(df: pd.DataFrame) -> dict[str, MetricaAgregada]`
  - `cobertura(df: pd.DataFrame, campo: str) -> float`

**Duas decisões metodológicas que o implementador não deve "simplificar":**

1. **Valuation por earnings yield ponderado, invertido no fim.** A média aritmética ponderada de P/L é matematicamente incorreta: uma empresa com lucro quase zero tem P/L enorme e domina a média, distorcendo para cima. O correto é ponderar `E/P = 1/(P/L)` e inverter o resultado. Ativos com P/L ausente ou não positivo saem do cálculo e reduzem a cobertura.

2. **Qualidade NÃO é agregada num número único.** Score B3, `entry_score` americano e score FII vêm de metodologias diferentes e escalas diferentes; somá-los ou normalizá-los por percentil dentro das próprias posições produz um número sem significado (a média dos percentis de N posições tende a 0,5 por construção). A função devolve uma métrica **por classe**, e a interface explica por quê.

- [ ] **Step 1: Escrever o teste que falha**

```python
"""Metricas agregadas do patrimonio, com cobertura explicita."""
import pandas as pd
import pytest

from core.global_portfolio.metrics import (
    COBERTURA_MINIMA,
    cobertura,
    dy_consolidado,
    qualidade_por_classe,
    valuation_agregado,
)


def _linha(classe, symbol, peso, fundamentals, metrics=None):
    return {"asset_class": classe, "symbol": symbol, "weight_global": peso,
            "payload": {"fundamentals": fundamentals, "metrics": metrics or {}}}


def test_valuation_usa_earnings_yield_e_nao_media_aritmetica():
    # Pesos iguais, P/L 10 e P/L 100.
    # Media aritmetica daria 55. Correto: E/P medio = (0.1+0.01)/2 = 0.055 -> 18,18.
    df = pd.DataFrame([
        _linha("b3", "A3", 0.5, {"P/L": 10.0}),
        _linha("b3", "B3X", 0.5, {"P/L": 100.0}),
    ])
    resultado = valuation_agregado(df, "pe")
    assert resultado.valor == pytest.approx(18.1818, rel=1e-3)
    assert resultado.valor != pytest.approx(55.0)


def test_valuation_ignora_pl_nao_positivo_e_reduz_cobertura():
    df = pd.DataFrame([
        _linha("b3", "A3", 0.5, {"P/L": 10.0}),
        _linha("b3", "B3X", 0.5, {"P/L": -5.0}),   # prejuizo: fora do calculo
    ])
    resultado = valuation_agregado(df, "pe")
    assert resultado.valor == pytest.approx(10.0)
    assert resultado.cobertura == pytest.approx(0.5)
    assert resultado.n_ativos == 1


def test_valuation_sem_nenhum_dado_devolve_none():
    df = pd.DataFrame([_linha("fii", "HGLG11", 1.0, {"pvp": 0.9})])
    resultado = valuation_agregado(df, "pe")
    assert resultado.valor is None
    assert resultado.cobertura == 0.0


def test_dy_consolidado_e_media_ponderada_simples():
    # DY e razao sobre preco e os pesos sao sobre preco: aritmetica esta correta.
    df = pd.DataFrame([
        _linha("b3", "A3", 0.6, {"DY": 10.0}),
        _linha("fii", "H11", 0.4, {"dy_12m": 5.0}),
    ])
    resultado = dy_consolidado(df)
    assert resultado.valor == pytest.approx(0.6 * 10.0 + 0.4 * 5.0)
    assert resultado.cobertura == pytest.approx(1.0)


def test_cobertura_e_a_fracao_de_peso_com_o_dado():
    df = pd.DataFrame([
        _linha("b3", "A3", 0.7, {"P/L": 8.0}),
        _linha("b3", "B3X", 0.3, {}),
    ])
    assert cobertura(df, "pe") == pytest.approx(0.7)


def test_metrica_abaixo_do_minimo_e_marcada_como_nao_confiavel():
    df = pd.DataFrame([
        _linha("b3", "A3", 0.4, {"P/L": 8.0}),
        _linha("b3", "B3X", 0.6, {}),
    ])
    resultado = valuation_agregado(df, "pe")
    assert resultado.cobertura == pytest.approx(0.4)
    assert resultado.cobertura < COBERTURA_MINIMA
    assert resultado.confiavel is False


def test_metrica_acima_do_minimo_e_confiavel():
    df = pd.DataFrame([_linha("b3", "A3", 1.0, {"P/L": 8.0})])
    assert valuation_agregado(df, "pe").confiavel is True


def test_qualidade_e_reportada_por_classe_e_nunca_agregada():
    df = pd.DataFrame([
        _linha("b3", "A3", 0.5, {}, {"score": 80.0}),
        _linha("b3", "B3X", 0.2, {}, {"score": 60.0}),
        _linha("us", "AAPL", 0.3, {}, {"entry_score": 70.0}),
    ])
    saida = qualidade_por_classe(df)
    assert set(saida) == {"b3", "us"}
    # b3: media ponderada pelos pesos DENTRO da classe -> (0.5*80 + 0.2*60)/0.7
    assert saida["b3"].valor == pytest.approx((0.5 * 80.0 + 0.2 * 60.0) / 0.7)
    assert saida["us"].valor == pytest.approx(70.0)


def test_qualidade_de_classe_sem_score_tem_cobertura_zero():
    df = pd.DataFrame([_linha("fii", "H11", 1.0, {}, {})])
    saida = qualidade_por_classe(df)
    assert saida["fii"].valor is None
    assert saida["fii"].cobertura == 0.0


def test_dataframe_vazio_nao_quebra():
    vazio = pd.DataFrame(columns=["asset_class", "symbol", "weight_global", "payload"])
    assert valuation_agregado(vazio, "pe").valor is None
    assert dy_consolidado(vazio).valor is None
    assert qualidade_por_classe(vazio) == {}
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_global_metrics.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'core.global_portfolio.metrics'`

- [ ] **Step 3: Escrever a implementação**

```python
"""Metricas agregadas do patrimonio, sempre acompanhadas da cobertura.

Duas decisoes metodologicas deliberadas:

1. Valuation agregado por EARNINGS YIELD ponderado, invertido no fim. A media
   aritmetica ponderada de P/L e matematicamente incorreta: uma empresa com
   lucro quase zero tem P/L enorme e domina a media.

2. Qualidade NAO e agregada num numero unico. Score B3, entry_score americano e
   score FII vem de metodologias e escalas diferentes. Normalizar por percentil
   dentro das proprias posicoes daria uma media proxima de 0,5 por construcao,
   sem significado. A qualidade e reportada por classe.

Coberto por tests/test_global_metrics.py.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from core.global_portfolio.fields import valor as campo_valor

COBERTURA_MINIMA = 0.60

# Nome do score de qualidade dentro de payload["metrics"], por classe.
_SCORE_POR_CLASSE: dict[str, str] = {
    "b3": "score",
    "us": "entry_score",
    "fii": "score",
}


@dataclass(frozen=True)
class MetricaAgregada:
    """Valor agregado mais a fracao do patrimonio que sustentou o calculo."""

    valor: float | None
    cobertura: float
    n_ativos: int

    @property
    def confiavel(self) -> bool:
        return self.valor is not None and self.cobertura >= COBERTURA_MINIMA


_VAZIA = MetricaAgregada(valor=None, cobertura=0.0, n_ativos=0)


def _pares(df: pd.DataFrame, campo: str) -> list[tuple[float, float]]:
    """(peso, valor) das linhas que possuem o campo canonico."""
    saida: list[tuple[float, float]] = []
    for linha in df.to_dict(orient="records"):
        v = campo_valor(linha.get("payload") or {}, linha.get("asset_class"), campo)
        if v is None:
            continue
        peso = float(linha.get("weight_global") or 0.0)
        if peso <= 0:
            continue
        saida.append((peso, v))
    return saida


def cobertura(df: pd.DataFrame, campo: str) -> float:
    """Fracao do peso total que possui o campo."""
    if df.empty:
        return 0.0
    total = float(pd.to_numeric(df["weight_global"], errors="coerce").fillna(0.0).sum())
    if total <= 0:
        return 0.0
    return sum(peso for peso, _ in _pares(df, campo)) / total


def valuation_agregado(df: pd.DataFrame, campo: str = "pe") -> MetricaAgregada:
    """Multiplo agregado via earnings yield ponderado, invertido no fim."""
    if df.empty:
        return _VAZIA

    total = float(pd.to_numeric(df["weight_global"], errors="coerce").fillna(0.0).sum())
    # Multiplo nao positivo (prejuizo) nao tem inverso interpretavel: sai do calculo.
    usaveis = [(peso, v) for peso, v in _pares(df, campo) if v > 0]
    if not usaveis or total <= 0:
        return _VAZIA

    peso_usado = sum(peso for peso, _ in usaveis)
    yield_ponderado = sum(peso * (1.0 / v) for peso, v in usaveis) / peso_usado
    if yield_ponderado <= 0:
        return MetricaAgregada(None, peso_usado / total, len(usaveis))

    return MetricaAgregada(
        valor=1.0 / yield_ponderado,
        cobertura=peso_usado / total,
        n_ativos=len(usaveis),
    )


def dy_consolidado(df: pd.DataFrame) -> MetricaAgregada:
    """DY do patrimonio: media ponderada aritmetica.

    Aqui a aritmetica esta correta — o DY e razao sobre preco e os pesos
    tambem sao sobre preco, entao a soma ponderada e o rendimento real.
    """
    if df.empty:
        return _VAZIA

    total = float(pd.to_numeric(df["weight_global"], errors="coerce").fillna(0.0).sum())
    usaveis = _pares(df, "dy")
    if not usaveis or total <= 0:
        return _VAZIA

    peso_usado = sum(peso for peso, _ in usaveis)
    return MetricaAgregada(
        valor=sum(peso * v for peso, v in usaveis) / peso_usado,
        cobertura=peso_usado / total,
        n_ativos=len(usaveis),
    )


def qualidade_por_classe(df: pd.DataFrame) -> dict[str, MetricaAgregada]:
    """Score medio ponderado DENTRO de cada classe. Nunca agregado entre classes."""
    if df.empty:
        return {}

    saida: dict[str, MetricaAgregada] = {}
    for classe in sorted(df["asset_class"].dropna().unique()):
        recorte = df[df["asset_class"] == classe]
        chave = _SCORE_POR_CLASSE.get(str(classe))
        total = float(pd.to_numeric(recorte["weight_global"],
                                    errors="coerce").fillna(0.0).sum())

        usaveis: list[tuple[float, float]] = []
        if chave:
            for linha in recorte.to_dict(orient="records"):
                bruto = (linha.get("payload") or {}).get("metrics", {}).get(chave)
                peso = float(linha.get("weight_global") or 0.0)
                if bruto is None or isinstance(bruto, bool) or peso <= 0:
                    continue
                try:
                    usaveis.append((peso, float(bruto)))
                except (TypeError, ValueError):
                    continue

        if not usaveis or total <= 0:
            saida[str(classe)] = _VAZIA
            continue

        peso_usado = sum(peso for peso, _ in usaveis)
        saida[str(classe)] = MetricaAgregada(
            valor=sum(peso * v for peso, v in usaveis) / peso_usado,
            cobertura=peso_usado / total,
            n_ativos=len(usaveis),
        )
    return saida
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_global_metrics.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add core/global_portfolio/metrics.py tests/test_global_metrics.py
git commit -m "feat(global): metricas agregadas com cobertura e qualidade por classe"
```

---

### Task 7: A seção Portfólio Global na interface

**Files:**
- Create: `views/portfolio_global.py`
- Modify: `app.py` (uma entrada nova no dicionário `_ROTAS` e na lista `opcoes_invest`)
- Test: `tests/test_portfolio_global_view.py`

**Interfaces:**
- Consumes: `core.portfolio.repository.load_active_snapshots`, `load_allocation_targets`, `save_allocation_targets`; `core.portfolio.registry.asset_classes`, `get_spec`; `core.global_portfolio.aggregate.montar_posicoes`; `concentration.resumo`, `por_dimensao`, `top_n`; `metrics.valuation_agregado`, `dy_consolidado`, `qualidade_por_classe`; `taxonomy.ROTULOS`, `nao_mapeados`.
- Produces: `render() -> None` (assinatura exigida pelo roteador de `app.py`), e as funções puras testáveis abaixo.

Para que a view seja testável sem Streamlit, a lógica de decisão fica em funções puras e só a formatação chama `st.*`:
- `carregar_snapshots(*, engine=None, owner_id=None) -> dict[str, dict[str, dict]]`
- `estado_vazio(snapshots: dict, alvos: dict) -> str | None` — devolve a mensagem apropriada, ou `None` quando há o que exibir.
- `_kpi_html(label, value, detail, icon, color) -> str` — **copiar a implementação de `views/dashboard_geral.py:377`**, porque ela é privada àquele módulo; não importar de lá.

**Mensagens de estado vazio, exatamente estas:**
- Sem snapshot em nenhuma classe → `"Nenhum snapshot encontrado. Rode o schema 049 no Supabase e depois `python -m scripts.backfill_portfolio_snapshots --apply`."`
- Há snapshots mas nenhuma alocação-alvo → `"Defina a alocação-alvo por classe para consolidar o patrimônio."`

- [ ] **Step 1: Escrever o teste que falha**

```python
"""Secao Portfolio Global: roteamento, estado vazio e montagem."""
import pandas as pd
import pytest

from views import portfolio_global


def test_a_rota_esta_registrada_no_app():
    from pathlib import Path
    fonte = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    assert '"portfolio_global"' in fonte, "modulo nao registrado em _ROTAS"
    assert "Portfólio Global" in fonte, "rotulo ausente na sidebar"


def test_o_modulo_expoe_render_sem_argumentos_obrigatorios():
    import inspect
    assinatura = inspect.signature(portfolio_global.render)
    obrigatorios = [p for p in assinatura.parameters.values()
                    if p.default is inspect.Parameter.empty]
    assert obrigatorios == []


def test_estado_vazio_sem_snapshot_orienta_o_backfill():
    msg = portfolio_global.estado_vazio({}, {})
    assert "049" in msg and "backfill_portfolio_snapshots" in msg


def test_estado_vazio_sem_alocacao_pede_o_alvo():
    snaps = {"b3": {"PETR4": {"identity": {"symbol": "PETR4"}}}}
    msg = portfolio_global.estado_vazio(snaps, {})
    assert "alocação-alvo" in msg


def test_sem_estado_vazio_quando_ha_snapshot_e_alvo():
    snaps = {"b3": {"PETR4": {"identity": {"symbol": "PETR4"}}}}
    assert portfolio_global.estado_vazio(snaps, {"b3": 1.0}) is None


def test_classe_com_dicionario_vazio_conta_como_sem_snapshot():
    assert portfolio_global.estado_vazio({"b3": {}, "us": {}}, {"b3": 1.0}) is not None


def test_kpi_html_escapa_o_rotulo():
    html = portfolio_global._kpi_html("<script>", "1", "d", "i", "#fff")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_carregar_snapshots_usa_o_modelo_ativo_de_cada_classe(monkeypatch):
    chamadas = []

    def fake(classe, *, engine=None, owner_id=None):
        chamadas.append(classe)
        return {"X": {"identity": {"symbol": "X"}}} if classe == "b3" else {}

    monkeypatch.setattr(portfolio_global, "load_active_snapshots", fake)
    saida = portfolio_global.carregar_snapshots()
    assert sorted(chamadas) == ["b3", "fii", "us"]
    assert set(saida) == {"b3", "fii", "us"}
    assert set(saida["b3"]) == {"X"}
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_portfolio_global_view.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'views.portfolio_global'`

- [ ] **Step 3: Criar `views/portfolio_global.py`**

```python
"""
views/portfolio_global.py — Portfolio Global

Reune as carteiras-modelo das tres classes num unico patrimonio e mostra
composicao, concentracao e metricas agregadas. Le exclusivamente os snapshots
persistidos; nao recalcula nada contra market.*.

A logica de decisao fica em funcoes puras (estado_vazio, carregar_snapshots)
para poder ser testada sem Streamlit. Coberto por
tests/test_portfolio_global_view.py.
"""
from __future__ import annotations

from html import escape

import pandas as pd
import streamlit as st

from core.global_portfolio import concentration, metrics
from core.global_portfolio.aggregate import montar_posicoes
from core.global_portfolio.taxonomy import ROTULOS, nao_mapeados
from core.portfolio.registry import asset_classes, get_spec
from core.portfolio.repository import (
    load_active_snapshots,
    load_allocation_targets,
    save_allocation_targets,
)

MSG_SEM_SNAPSHOT = (
    "Nenhum snapshot encontrado. Rode o schema 049 no Supabase e depois "
    "`python -m scripts.backfill_portfolio_snapshots --apply`."
)
MSG_SEM_ALVO = "Defina a alocação-alvo por classe para consolidar o patrimônio."


def _kpi_html(label: str, value: str, detail: str, icon: str, color: str) -> str:
    """Card de metrica. Copia do helper de views/dashboard_geral.py, que e privado."""
    return (
        f'<article class="dg-kpi" style="--kpi-color:{escape(color)}">'
        '<div class="dg-kpi-top">'
        f'<div class="dg-kpi-label">{escape(label)}</div>'
        f'<div class="dg-kpi-icon" aria-hidden="true">{escape(icon)}</div>'
        '</div>'
        f'<div class="dg-kpi-value">{escape(value)}</div>'
        f'<div class="dg-kpi-detail">{detail}</div>'
        '</article>'
    )


def carregar_snapshots(*, engine=None, owner_id=None) -> dict[str, dict[str, dict]]:
    """Snapshots do modelo ativo de cada classe registrada."""
    return {
        classe: load_active_snapshots(classe, engine=engine, owner_id=owner_id)
        for classe in asset_classes()
    }


def estado_vazio(snapshots: dict, alvos: dict) -> str | None:
    """Mensagem a exibir quando nao ha o que consolidar, ou None."""
    if not any(snapshots.get(c) for c in snapshots):
        return MSG_SEM_SNAPSHOT
    if not alvos:
        return MSG_SEM_ALVO
    return None


def _fmt(valor: float | None, sufixo: str = "", casas: int = 2) -> str:
    if valor is None:
        return "—"
    return f"{valor:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".") + sufixo


def _detalhe_cobertura(metrica) -> str:
    pct = f"{metrica.cobertura * 100:.0f}%"
    if metrica.valor is None:
        return "sem dado disponível"
    if not metrica.confiavel:
        return f"⚠️ cobertura {pct} — abaixo do mínimo confiável"
    return f"cobertura {pct} · {metrica.n_ativos} ativos"


def _editor_de_alocacao(alvos: dict) -> None:
    """Formulario da alocacao-alvo por classe."""
    with st.expander("⚖️ Alocação-alvo por classe", expanded=not alvos):
        with st.form("form_alocacao_global"):
            entradas: dict[str, float] = {}
            colunas = st.columns(len(asset_classes()))
            for coluna, classe in zip(colunas, asset_classes()):
                with coluna:
                    entradas[classe] = st.number_input(
                        get_spec(classe).label,
                        min_value=0.0, max_value=100.0, step=1.0,
                        value=float(alvos.get(classe, 0.0) * 100.0),
                        key=f"alvo_{classe}",
                    )
            total = st.number_input(
                "Patrimônio total em R$ (opcional)",
                min_value=0.0, step=1000.0, value=0.0,
                help="Se informado, a tabela mostra o valor por ativo. Não é usado nos percentuais.",
            )
            if st.form_submit_button("Salvar alocação"):
                try:
                    save_allocation_targets(entradas, total_brl=total or None)
                    st.success("Alocação-alvo salva.")
                    st.rerun()
                except (ValueError, KeyError) as exc:
                    st.error(f"Não foi possível salvar: {exc}")


def _cards_de_concentracao(resumo: dict) -> None:
    st.markdown("#### Concentração")
    colunas = st.columns(4)
    cartoes = [
        ("Posições efetivas", resumo["symbol"], "🎯", "#5B8DEF"),
        ("Setores efetivos", resumo["sector"], "🏭", "#38BDF8"),
        ("Países efetivos", resumo["country"], "🌎", "#34D399"),
        ("Classes efetivas", resumo["asset_class"], "🧩", "#FBBF24"),
    ]
    for coluna, (rotulo, dados, icone, cor) in zip(colunas, cartoes):
        maior = dados["maior_nome"] or "—"
        if rotulo == "Setores efetivos":
            maior = ROTULOS.get(dados["maior_nome"], maior)
        with coluna:
            st.markdown(
                _kpi_html(
                    rotulo,
                    _fmt(dados["numero_efetivo"], casas=1),
                    f'maior: {escape(str(maior))} · {dados["maior_peso"] * 100:.1f}%',
                    icone, cor,
                ),
                unsafe_allow_html=True,
            )


def _cards_de_metricas(df: pd.DataFrame) -> None:
    st.markdown("#### Métricas do patrimônio")
    pl = metrics.valuation_agregado(df, "pe")
    pvp = metrics.valuation_agregado(df, "pvp")
    dy = metrics.dy_consolidado(df)

    colunas = st.columns(3)
    cartoes = [
        ("P/L agregado", _fmt(pl.valor), _detalhe_cobertura(pl), "📊", "#5B8DEF"),
        ("P/VP agregado", _fmt(pvp.valor), _detalhe_cobertura(pvp), "📐", "#38BDF8"),
        ("Dividend yield", _fmt(dy.valor, "%"), _detalhe_cobertura(dy), "💰", "#34D399"),
    ]
    for coluna, (rotulo, valor, detalhe, icone, cor) in zip(colunas, cartoes):
        with coluna:
            st.markdown(_kpi_html(rotulo, valor, detalhe, icone, cor),
                        unsafe_allow_html=True)

    st.caption(
        "O P/L e o P/VP agregados usam **earnings yield ponderado**, invertido ao final. "
        "A média aritmética de múltiplos é matematicamente incorreta e distorce para cima "
        "quando há empresa de lucro pequeno."
    )


def _qualidade(df: pd.DataFrame) -> None:
    st.markdown("#### Qualidade por classe")
    st.caption(
        "Não existe um número único de qualidade para o patrimônio: score B3, score "
        "americano e score FII vêm de metodologias e escalas diferentes, e agregá-los "
        "produziria um valor sem significado."
    )
    por_classe = metrics.qualidade_por_classe(df)
    if not por_classe:
        st.info("Sem score disponível nas posições.")
        return
    colunas = st.columns(len(por_classe))
    for coluna, classe in zip(colunas, sorted(por_classe)):
        metrica = por_classe[classe]
        with coluna:
            st.markdown(
                _kpi_html(get_spec(classe).label, _fmt(metrica.valor, casas=1),
                          _detalhe_cobertura(metrica), "⭐", "#A78BFA"),
                unsafe_allow_html=True,
            )


def _tabelas(df: pd.DataFrame) -> None:
    st.markdown("#### Composição")
    aba_ativos, aba_setor, aba_pais = st.tabs(["Por ativo", "Por setor", "Por país"])

    with aba_ativos:
        visao = df[["symbol", "name", "asset_class", "sector", "weight_global",
                    "valor_brl"]].copy()
        visao["sector"] = visao["sector"].map(lambda s: ROTULOS.get(s, s))
        visao["weight_global"] = (visao["weight_global"] * 100).round(2)
        visao.columns = ["Ativo", "Nome", "Classe", "Setor", "Peso %", "Valor R$"]
        st.dataframe(visao, use_container_width=True, hide_index=True)

    with aba_setor:
        setores = concentration.por_dimensao(df, "sector")
        setores["sector"] = setores["sector"].map(lambda s: ROTULOS.get(s, s))
        setores["peso"] = (setores["peso"] * 100).round(2)
        setores.columns = ["Setor", "Peso %", "Ativos"]
        st.dataframe(setores, use_container_width=True, hide_index=True)

    with aba_pais:
        paises = concentration.por_dimensao(df, "country")
        paises["peso"] = (paises["peso"] * 100).round(2)
        paises.columns = ["País", "Peso %", "Ativos"]
        st.dataframe(paises, use_container_width=True, hide_index=True)


def render() -> None:
    st.markdown("## 🌐 Portfólio Global")
    st.caption("As três carteiras lidas como um único patrimônio.")

    try:
        snapshots = carregar_snapshots()
        alocacao = load_allocation_targets()
    except Exception as exc:  # noqa: BLE001 - fronteira de isolamento da rota
        st.error(f"Não foi possível ler os dados do portfólio: {exc}")
        return

    alvos = alocacao.get("targets") or {}
    _editor_de_alocacao(alvos)

    aviso = estado_vazio(snapshots, alvos)
    if aviso:
        st.info(aviso)
        return

    df = montar_posicoes(snapshots, alvos, total_brl=alocacao.get("total_brl"))
    if df.empty:
        st.info(MSG_SEM_SNAPSHOT)
        return

    sem_mapa = nao_mapeados(df.to_dict(orient="records"))
    if sem_mapa:
        st.warning(
            "Setores sem mapeamento canônico (contabilizados como Outros): "
            + ", ".join(f"{c}/{s}" for c, s in sem_mapa)
        )

    _cards_de_concentracao(concentration.resumo(df))
    _cards_de_metricas(df)
    _qualidade(df)
    _tabelas(df)
```

- [ ] **Step 4: Registrar a rota em `app.py`**

Duas alterações, e nada mais. Em `_ROTAS`, após a linha de `"🏬 Seleção de FIIs"`:

```python
    "🌐 Portfólio Global":    "portfolio_global",
```

E na lista `opcoes_invest`, acrescentar o mesmo rótulo ao final:

```python
    opcoes_invest = ["📈 Investimentos", "🏢 Empresas B3",
                     "🌎 Empresas Americanas",
                     "🏬 Seleção de FIIs",
                     "🌐 Portfólio Global"]
```

- [ ] **Step 5: Rodar o teste e confirmar que passa**

Run: `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_portfolio_global_view.py -v`
Expected: 8 passed

- [ ] **Step 6: Rodar a suíte da fase e a suíte completa**

Run: `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_portfolio_repository_global.py tests/test_global_taxonomy.py tests/test_global_fields.py tests/test_global_aggregate.py tests/test_global_concentration.py tests/test_global_metrics.py tests/test_portfolio_global_view.py -v`
Expected: 95 passed (9 + 28 + 16 + 11 + 13 + 10 + 8)

Run: `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/ -q --tb=short`
Expected: `1634 passed, 3 skipped, 0 failed` (baseline 1539 + 95). Qualquer falha é regressão.

- [ ] **Step 7: Commit**

```bash
git add views/portfolio_global.py app.py tests/test_portfolio_global_view.py
git commit -m "feat(global): secao Portfolio Global com composicao, concentracao e metricas"
```

---

## Auto-revisão deste plano

**Cobertura da spec (§6, parte 2a):**

| Requisito | Task |
|---|---|
| §6.1 agregação com peso alvo × peso do modelo | 4 |
| §6.1 look-through de FII | **fora de 2a** — depende de `fii_lookthrough`, entra na 2b junto com correlação |
| §6.2 taxonomia comum | 2 |
| §6.3 concentração: HHI, número efetivo, top-N, Lorenz | 5 |
| §6.4 correlação | **Fase 2b** |
| §6.5 exposição a fatores | **Fase 2b** |
| §6.6 valuation por earnings yield | 6 |
| §6.6 DY consolidado | 6 |
| §6.6 qualidade | 6, com a correção metodológica registrada acima |
| §6.6 cobertura por métrica | 6 |
| §6.6 crescimento, volatilidade, VaR | **Fase 2b** — dependem de série |
| §13 interface em cards CSS | 7 |
| Alocação-alvo (tabela criada na Fase 1) | 1 |

**Consistência de nomes verificada:** `montar_posicoes` (Task 4) é consumida com a mesma assinatura nas Tasks 5, 6 e 7. `MetricaAgregada` (Task 6) tem `valor`/`cobertura`/`n_ativos`/`confiavel` e é lida assim na Task 7. `setor_canonico` e `ROTULOS` (Task 2) são usados nas Tasks 4 e 7. `valor` de `fields` (Task 3) é importado como `campo_valor` na Task 6 para não colidir com a variável local. `load_active_snapshots` e `load_allocation_targets` (Task 1) são consumidos na Task 7 com os mesmos parâmetros nomeados.

**Ponto de atenção para quem executar a Task 7:** `_kpi_html` é copiado de `views/dashboard_geral.py`, não importado — a função é privada àquele módulo e importá-la criaria acoplamento entre duas views. A duplicação é deliberada e está comentada no código.

**Costura real, não fake:** a Task 1 testa o repositório contra SQLite de verdade, incluindo as três tabelas de modelo. A Task 7 testa `carregar_snapshots` verificando que as três classes são consultadas, e verifica a rota lendo `app.py`. Nenhum teste desta fase substitui um módulo desta fase por um fake — a lição da Fase 1.
