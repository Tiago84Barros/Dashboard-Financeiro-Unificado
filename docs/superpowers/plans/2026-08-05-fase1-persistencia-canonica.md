# Fase 1 — Persistência Canônica de Carteiras: Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persistir, junto de cada carteira-modelo salva (B3, EUA, FIIs), um snapshot analítico rico por ativo, para que reabrir o sistema não exija reconstruir a análise.

**Architecture:** Camada canônica aditiva. Uma tabela nova `portfolio_asset_snapshots` guarda o payload JSON rico, referenciado por `(asset_class, model_id, symbol)`. Um registro de classes de ativo (`registry.py`) associa cada classe ao seu adaptador, moeda e país. Adaptadores por classe montam o payload lendo os módulos de dados já existentes. As três funções `save_*_portfolio_model` existentes ganham **apenas** uma chamada extra protegida por `try/except`; nenhuma lógica atual é reescrita.

**Tech Stack:** Python 3.11+, SQLAlchemy Core com SQL cru via `text()`, pandas, pytest, PostgreSQL (Supabase) em produção e SQLite em memória nos testes.

## Global Constraints

- **Aditividade:** nenhum arquivo existente pode ter lógica removida ou reescrita. As únicas alterações permitidas em arquivo existente nesta fase são as três chamadas descritas na Task 9.
- **Segurança de schema:** arquivos em `supabase_unificado/schema/` não podem conter `DROP TABLE`, `TRUNCATE` nem `DELETE`. Todo `CREATE` é idempotente (`IF NOT EXISTS`).
- **Degradação:** falha ao gravar snapshot nunca impede o salvamento da carteira.
- **Determinismo:** nenhuma saída pode depender de ordem de iteração de `dict`/`set`. Validar com `PYTHONHASHSEED` variado.
- **Versão do payload:** `SCHEMA_VERSION = 1`, gravado dentro do payload e na coluna `schema_version`.
- **Retenção:** `RETENTION_ARCHIVED = 5` versões arquivadas com payload por classe de ativo.
- **Teto de payload:** `MAX_PAYLOAD_BYTES = 120_000` por ativo. Acima disso, o payload é truncado nos blocos volumosos e marcado.
- **Idioma do código:** comentários e docstrings em português, sem acentuação em SQL de schema (padrão dos arquivos existentes em `supabase_unificado/schema/`).
- **Testes:** `pytest`. Não existe `conftest.py` nem configuração de pytest no repositório; testes são arquivos `tests/test_*.py` autocontidos.
- **Interpretador:** o `python` do PATH resolve para a venv do Hermes, que **não tem pytest**. Usar sempre o caminho completo:
  `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest ...`
  (Python 3.12.10, pytest 9.0.3, com pandas e sqlalchemy). Onde o plano escreve `python -m pytest`, leia este caminho.
- **Baseline da suíte antes desta fase:** `1431 passed, 3 skipped, 0 failed`. Nenhum teste que passava pode quebrar.

---

## Estrutura de arquivos

| Arquivo | Responsabilidade |
|---|---|
| `supabase_unificado/schema/049_portfolio_asset_snapshots.sql` | DDL declarativo das duas tabelas novas + índices + RLS |
| `core/portfolio/__init__.py` | Reexporta a API pública do pacote |
| `core/portfolio/models.py` | `AssetSnapshot`: dataclass do snapshot, sem I/O |
| `core/portfolio/snapshots.py` | Montagem, saneamento, digest e teto de tamanho do payload |
| `core/portfolio/registry.py` | `AssetClassSpec` e o registro das três classes |
| `core/portfolio/repository.py` | Gravação, leitura, retenção e poda de órfãos |
| `core/portfolio/adapters/b3.py` | Monta payloads das empresas B3 |
| `core/portfolio/adapters/us.py` | Monta payloads das empresas americanas |
| `core/portfolio/adapters/fii.py` | Monta payloads dos FIIs |
| `scripts/backfill_portfolio_snapshots.py` | Reconstrói snapshots das carteiras já salvas |

Fronteira: `models.py` e `snapshots.py` não tocam banco nem Streamlit. `registry.py` não executa SQL. `repository.py` é o único com SQL. Adaptadores leem dados mas não gravam.

---

### Task 1: Schema SQL das tabelas novas

**Files:**
- Create: `supabase_unificado/schema/049_portfolio_asset_snapshots.sql`
- Test: `tests/test_portfolio_snapshots_schema.py`

**Interfaces:**
- Consumes: nada.
- Produces: tabelas `portfolio_asset_snapshots` e `portfolio_allocation_targets`. A Task 5 depende dos nomes de coluna definidos aqui.

- [ ] **Step 1: Escrever o teste que falha**

```python
"""Garante que o schema 049 e aditivo e seguro."""
from pathlib import Path

import pytest

SCHEMA = Path(__file__).resolve().parents[1] / "supabase_unificado" / "schema" / "049_portfolio_asset_snapshots.sql"


@pytest.fixture(scope="module")
def sql() -> str:
    return SCHEMA.read_text(encoding="utf-8")


def test_arquivo_de_schema_existe():
    assert SCHEMA.is_file(), f"schema ausente: {SCHEMA}"


def test_schema_nao_contem_comando_destrutivo(sql):
    upper = sql.upper()
    for proibido in ("DROP TABLE", "TRUNCATE", "DELETE FROM"):
        assert proibido not in upper, f"comando destrutivo encontrado: {proibido}"


def test_criacoes_sao_idempotentes(sql):
    upper = sql.upper()
    assert upper.count("CREATE TABLE") == upper.count("CREATE TABLE IF NOT EXISTS")
    assert upper.count("CREATE INDEX") == upper.count("CREATE INDEX IF NOT EXISTS")


def test_tabelas_esperadas_declaradas(sql):
    assert "portfolio_asset_snapshots" in sql
    assert "portfolio_allocation_targets" in sql


def test_chave_natural_do_snapshot_e_unica(sql):
    assert "UNIQUE (asset_class, model_id, symbol)" in sql


def test_rls_habilitada_nas_duas_tabelas(sql):
    upper = sql.upper()
    assert upper.count("ENABLE ROW LEVEL SECURITY") == 2


def test_cascata_por_usuario_preservada(sql):
    # user_id sempre cascateia; model_id e polimorfico e por isso nao tem FK.
    assert sql.count("REFERENCES profiles(id) ON DELETE CASCADE") == 2
    assert "REFERENCES b3_portfolio_models" not in sql
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `python -m pytest tests/test_portfolio_snapshots_schema.py -v`
Expected: FAIL em `test_arquivo_de_schema_existe` com `assert False, schema ausente: ...`

- [ ] **Step 3: Escrever o schema**

```sql
-- ============================================================
-- 049_portfolio_asset_snapshots.sql
-- Snapshot analitico por ativo das carteiras-modelo do usuario.
-- Banco: Dashboard Financeiro Unificado (Supabase - schema public)
--
-- Espelha o padrao de 047_us_portfolio_models.sql (indices + RLS por dono).
--
-- NOTA DE MODELAGEM:
--   model_id e POLIMORFICO: aponta para b3_portfolio_models,
--   us_portfolio_models ou fii_portfolio_models conforme asset_class.
--   Por isso nao ha FOREIGN KEY nem ON DELETE CASCADE nessa coluna.
--   A limpeza de orfaos e feita em core/portfolio/repository.prune_orphans(),
--   chamada a cada gravacao. Foi uma troca deliberada: integridade
--   declarativa por extensibilidade (classe de ativo nova nao exige ALTER).
--
-- SEGURANCA:
--   Nao contem DROP TABLE, TRUNCATE ou DELETE.
--   CREATE sao idempotentes.
-- ============================================================

CREATE TABLE IF NOT EXISTS portfolio_asset_snapshots (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    asset_class    VARCHAR(16) NOT NULL,
    model_id       UUID NOT NULL,
    symbol         VARCHAR(16) NOT NULL,
    schema_version INTEGER NOT NULL,
    as_of_date     DATE NOT NULL,
    payload        JSONB NOT NULL,
    payload_digest TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (asset_class, model_id, symbol),
    CHECK (asset_class IN ('b3', 'us', 'fii'))
);

CREATE TABLE IF NOT EXISTS portfolio_allocation_targets (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    status       VARCHAR(20) NOT NULL DEFAULT 'active',
    total_brl    NUMERIC(18,2),
    targets_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    notes        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (status IN ('active', 'archived'))
);

COMMENT ON TABLE portfolio_asset_snapshots IS
    'Snapshot analitico por ativo (fundamentos, metricas, historico, premissas, evidencias) da carteira-modelo.';
COMMENT ON COLUMN portfolio_asset_snapshots.model_id IS
    'Polimorfico: id em b3_/us_/fii_portfolio_models conforme asset_class. Sem FK por decisao de projeto.';
COMMENT ON TABLE portfolio_allocation_targets IS
    'Alocacao-alvo por classe de ativo usada pelo Portfolio Global. Consumida a partir da Fase 2.';

CREATE INDEX IF NOT EXISTS idx_portfolio_asset_snapshots_lookup
    ON portfolio_asset_snapshots (user_id, asset_class, model_id);

CREATE INDEX IF NOT EXISTS idx_portfolio_asset_snapshots_symbol
    ON portfolio_asset_snapshots (asset_class, symbol, as_of_date DESC);

CREATE UNIQUE INDEX IF NOT EXISTS uq_portfolio_allocation_targets_active_per_user
    ON portfolio_allocation_targets (user_id)
    WHERE status = 'active';

-- RLS: protege o caminho HTTP (anon key). A conexao do app (role postgres) bypassa.
ALTER TABLE portfolio_asset_snapshots    ENABLE ROW LEVEL SECURITY;
ALTER TABLE portfolio_allocation_targets ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname='public'
          AND tablename='portfolio_asset_snapshots'
          AND policyname='portfolio_asset_snapshots_owner_all'
    ) THEN
        CREATE POLICY portfolio_asset_snapshots_owner_all ON portfolio_asset_snapshots
            USING (user_id = auth.uid())
            WITH CHECK (user_id = auth.uid());
    END IF;
END; $$;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname='public'
          AND tablename='portfolio_allocation_targets'
          AND policyname='portfolio_allocation_targets_owner_all'
    ) THEN
        CREATE POLICY portfolio_allocation_targets_owner_all ON portfolio_allocation_targets
            USING (user_id = auth.uid())
            WITH CHECK (user_id = auth.uid());
    END IF;
END; $$;

-- ============================================================
-- FIM 049.
-- ============================================================
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `python -m pytest tests/test_portfolio_snapshots_schema.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add supabase_unificado/schema/049_portfolio_asset_snapshots.sql tests/test_portfolio_snapshots_schema.py
git commit -m "feat(portfolio): schema das tabelas de snapshot analitico e alocacao-alvo"
```

---

### Task 2: Payload — saneamento, digest e teto de tamanho

**Files:**
- Create: `core/portfolio/__init__.py`, `core/portfolio/snapshots.py`
- Test: `tests/test_portfolio_snapshots_payload.py`

**Interfaces:**
- Consumes: `core.b3_portfolio_model._clean_nan` (função já existente que converte `NaN`/`Infinity`/tipos numpy em valores JSON válidos; reutilizada em vez de reescrita).
- Produces:
  - `SCHEMA_VERSION: int = 1`
  - `MAX_PAYLOAD_BYTES: int = 120_000`
  - `TRUNCAVEIS: tuple[str, ...] = ("history", "evidence", "fundamentals")`
  - `build_payload(blocks: dict) -> dict`
  - `payload_digest(payload: dict) -> str`
  - `payload_size_bytes(payload: dict) -> int`
  - `canonical_json(payload: dict) -> str`

- [ ] **Step 1: Escrever o teste que falha**

```python
"""Saneamento, digest estavel e teto de tamanho do payload de snapshot."""
import json

import numpy as np
import pytest

from core.portfolio.snapshots import (
    MAX_PAYLOAD_BYTES,
    SCHEMA_VERSION,
    build_payload,
    canonical_json,
    payload_digest,
    payload_size_bytes,
)


def test_build_payload_injeta_schema_version():
    out = build_payload({"identity": {"symbol": "PETR4"}})
    assert out["schema_version"] == SCHEMA_VERSION


def test_build_payload_saneia_nan_e_infinito():
    out = build_payload({"metrics": {"dy": float("nan"), "pl": float("inf"), "ok": 1.5}})
    assert out["metrics"]["dy"] is None
    assert out["metrics"]["pl"] is None
    assert out["metrics"]["ok"] == 1.5
    json.loads(canonical_json(out))  # nao deve levantar


def test_build_payload_converte_tipos_numpy():
    out = build_payload({"metrics": {"i": np.int64(7), "f": np.float64(2.5)}})
    assert out["metrics"]["i"] == 7 and isinstance(out["metrics"]["i"], int)
    assert out["metrics"]["f"] == 2.5


def test_build_payload_preenche_blocos_ausentes():
    out = build_payload({"identity": {"symbol": "PETR4"}})
    for bloco in ("fundamentals", "metrics", "classification", "history",
                  "assumptions", "evidence", "provenance"):
        assert bloco in out, f"bloco ausente: {bloco}"


def test_digest_independe_da_ordem_das_chaves():
    a = build_payload({"identity": {"symbol": "PETR4", "nome": "Petrobras"}})
    b = build_payload({"identity": {"nome": "Petrobras", "symbol": "PETR4"}})
    assert payload_digest(a) == payload_digest(b)


def test_digest_muda_quando_o_conteudo_muda():
    a = build_payload({"metrics": {"dy": 1.0}})
    b = build_payload({"metrics": {"dy": 2.0}})
    assert payload_digest(a) != payload_digest(b)


def test_payload_acima_do_teto_e_truncado_e_marcado():
    grande = {"linhas": ["x" * 1000 for _ in range(300)]}   # ~300 KB
    out = build_payload({"identity": {"symbol": "X"}, "history": grande})
    assert payload_size_bytes(out) <= MAX_PAYLOAD_BYTES
    assert out["provenance"]["truncated"] is True
    assert "history" in out["provenance"]["truncated_blocks"]


def test_payload_dentro_do_teto_nao_e_marcado():
    out = build_payload({"identity": {"symbol": "X"}, "metrics": {"dy": 1.0}})
    assert out["provenance"]["truncated"] is False
    assert out["provenance"]["truncated_blocks"] == []


def test_identity_e_obrigatorio():
    with pytest.raises(ValueError, match="identity"):
        build_payload({"metrics": {"dy": 1.0}})
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `python -m pytest tests/test_portfolio_snapshots_payload.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'core.portfolio'`

- [ ] **Step 3: Escrever a implementação**

Criar `core/portfolio/__init__.py`:

```python
"""Camada canonica de persistencia das carteiras-modelo.

Aditiva: nao substitui core/b3_portfolio_model.py e irmaos, apenas guarda o
snapshot analitico rico que eles nao guardavam.
"""
from core.portfolio.snapshots import SCHEMA_VERSION, build_payload, payload_digest

__all__ = ["SCHEMA_VERSION", "build_payload", "payload_digest"]
```

Criar `core/portfolio/snapshots.py`:

```python
"""Montagem, saneamento, digest e teto de tamanho do payload de snapshot.

Modulo puro: nao toca banco nem Streamlit. Coberto por
tests/test_portfolio_snapshots_payload.py.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

# Reaproveita o saneador ja validado (NaN/Infinity/numpy -> JSON valido) em vez
# de reescreve-lo. Ver tests/test_b3_portfolio_model.py.
from core.b3_portfolio_model import _clean_nan

SCHEMA_VERSION = 1
MAX_PAYLOAD_BYTES = 120_000

# Blocos podados quando o payload estoura o teto, na ordem em que sao podados.
TRUNCAVEIS: tuple[str, ...] = ("history", "evidence", "fundamentals")

_BLOCOS = (
    "identity", "fundamentals", "metrics", "classification",
    "history", "assumptions", "evidence", "notes", "provenance",
)


def canonical_json(payload: dict) -> str:
    """JSON deterministico: chaves ordenadas, sem espacos supérfluos."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def payload_size_bytes(payload: dict) -> int:
    return len(canonical_json(payload).encode("utf-8"))


def payload_digest(payload: dict) -> str:
    """SHA-256 do JSON canonico. Estavel para o mesmo conteudo."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def build_payload(blocks: dict) -> dict:
    """Monta o payload versionado a partir dos blocos fornecidos.

    Preenche blocos ausentes, saneia valores nao serializaveis e aplica o teto
    de tamanho podando os blocos volumosos, sempre registrando o que foi podado.
    """
    if not blocks.get("identity"):
        raise ValueError("bloco 'identity' e obrigatorio no payload de snapshot")

    payload: dict[str, Any] = {"schema_version": SCHEMA_VERSION}
    for nome in _BLOCOS:
        valor = blocks.get(nome)
        if nome == "notes":
            payload[nome] = valor if isinstance(valor, str) else ""
        else:
            payload[nome] = _clean_nan(valor) if valor else {}

    provenance = dict(payload.get("provenance") or {})
    provenance.setdefault("truncated", False)
    provenance.setdefault("truncated_blocks", [])
    payload["provenance"] = provenance

    podados: list[str] = []
    for bloco in TRUNCAVEIS:
        if payload_size_bytes(payload) <= MAX_PAYLOAD_BYTES:
            break
        if payload.get(bloco):
            payload[bloco] = {"_truncado": True}
            podados.append(bloco)

    payload["provenance"]["truncated"] = bool(podados)
    payload["provenance"]["truncated_blocks"] = podados
    return payload
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `python -m pytest tests/test_portfolio_snapshots_payload.py -v`
Expected: 9 passed

- [ ] **Step 5: Verificar determinismo**

Run: `PYTHONHASHSEED=1 python -m pytest tests/test_portfolio_snapshots_payload.py -q && PYTHONHASHSEED=99 python -m pytest tests/test_portfolio_snapshots_payload.py -q`
Expected: 9 passed nas duas execuções.

No PowerShell, use: `$env:PYTHONHASHSEED=1; python -m pytest tests/test_portfolio_snapshots_payload.py -q; $env:PYTHONHASHSEED=99; python -m pytest tests/test_portfolio_snapshots_payload.py -q`

- [ ] **Step 6: Commit**

```bash
git add core/portfolio/__init__.py core/portfolio/snapshots.py tests/test_portfolio_snapshots_payload.py
git commit -m "feat(portfolio): payload versionado com saneamento, digest estavel e teto de tamanho"
```

---

### Task 3: Modelo de dados do snapshot

**Files:**
- Create: `core/portfolio/models.py`
- Test: `tests/test_portfolio_models.py`

**Interfaces:**
- Consumes: `core.portfolio.snapshots.build_payload`, `payload_digest`, `SCHEMA_VERSION`.
- Produces: `AssetSnapshot` (dataclass congelada) com os campos `asset_class: str`, `model_id: str`, `symbol: str`, `as_of_date: date`, `payload: dict`; as propriedades `digest: str` e `schema_version: int`; e o construtor de classe `AssetSnapshot.from_blocks(asset_class, model_id, symbol, as_of_date, blocks) -> AssetSnapshot`.

- [ ] **Step 1: Escrever o teste que falha**

```python
"""Dataclass do snapshot: normalizacao e digest."""
import datetime as dt

import pytest

from core.portfolio.models import AssetSnapshot
from core.portfolio.snapshots import SCHEMA_VERSION


def _snap(symbol="petr4", asset_class="b3", **kw):
    return AssetSnapshot.from_blocks(
        asset_class=asset_class,
        model_id="11111111-1111-1111-1111-111111111111",
        symbol=symbol,
        as_of_date=dt.date(2026, 8, 5),
        blocks=kw or {"identity": {"symbol": "PETR4"}},
    )


def test_symbol_e_normalizado_para_maiusculo_sem_espaco():
    assert _snap(symbol="  petr4 ").symbol == "PETR4"


def test_asset_class_e_normalizada_para_minuscula():
    assert _snap(asset_class="B3").asset_class == "b3"


def test_schema_version_vem_do_payload():
    assert _snap().schema_version == SCHEMA_VERSION


def test_digest_e_estavel_entre_instancias_iguais():
    assert _snap().digest == _snap().digest


def test_snapshot_e_imutavel():
    snap = _snap()
    with pytest.raises(Exception):
        snap.symbol = "VALE3"


def test_symbol_vazio_e_rejeitado():
    with pytest.raises(ValueError, match="symbol"):
        _snap(symbol="   ")
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `python -m pytest tests/test_portfolio_models.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'core.portfolio.models'`

- [ ] **Step 3: Escrever a implementação**

```python
"""Modelo de dados do snapshot analitico. Sem I/O.

Coberto por tests/test_portfolio_models.py.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from core.portfolio.snapshots import build_payload, payload_digest


@dataclass(frozen=True)
class AssetSnapshot:
    """Snapshot analitico de um ativo dentro de uma carteira-modelo."""

    asset_class: str
    model_id: str
    symbol: str
    as_of_date: dt.date
    payload: dict = field(default_factory=dict)

    @classmethod
    def from_blocks(cls, *, asset_class: str, model_id: str, symbol: str,
                    as_of_date: dt.date, blocks: dict) -> "AssetSnapshot":
        simbolo = str(symbol or "").strip().upper()
        if not simbolo:
            raise ValueError("symbol vazio ao montar AssetSnapshot")
        return cls(
            asset_class=str(asset_class or "").strip().lower(),
            model_id=str(model_id),
            symbol=simbolo,
            as_of_date=as_of_date,
            payload=build_payload(blocks),
        )

    @property
    def digest(self) -> str:
        return payload_digest(self.payload)

    @property
    def schema_version(self) -> int:
        return int(self.payload.get("schema_version") or 0)
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `python -m pytest tests/test_portfolio_models.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add core/portfolio/models.py tests/test_portfolio_models.py
git commit -m "feat(portfolio): dataclass AssetSnapshot com normalizacao e digest"
```

---

### Task 4: Registro de classes de ativo

**Files:**
- Create: `core/portfolio/registry.py`
- Test: `tests/test_portfolio_registry.py`

**Interfaces:**
- Consumes: nada em tempo de import (os adaptadores são resolvidos preguiçosamente por nome de módulo, para que esta task não dependa das Tasks 6 a 8).
- Produces:
  - `AssetClassSpec` com campos `key: str`, `label: str`, `models_table: str`, `items_table: str`, `symbol_column: str`, `currency: str`, `country: str`, `adapter_module: str`.
  - `SPECS: dict[str, AssetClassSpec]`
  - `get_spec(key: str) -> AssetClassSpec`
  - `asset_classes() -> tuple[str, ...]` — ordenado alfabeticamente, para determinismo.
  - `load_adapter(key: str)` — importa e devolve o módulo do adaptador.

- [ ] **Step 1: Escrever o teste que falha**

```python
"""Registro de classes de ativo."""
import pytest

from core.portfolio.registry import SPECS, asset_classes, get_spec


def test_tres_classes_registradas():
    assert set(SPECS) == {"b3", "us", "fii"}


def test_asset_classes_e_deterministico():
    assert asset_classes() == ("b3", "fii", "us")


def test_get_spec_aceita_maiusculas_e_espacos():
    assert get_spec("  B3 ").key == "b3"


def test_classe_desconhecida_levanta_erro_claro():
    with pytest.raises(KeyError, match="cripto"):
        get_spec("cripto")


@pytest.mark.parametrize("key,moeda,pais", [
    ("b3", "BRL", "BR"),
    ("fii", "BRL", "BR"),
    ("us", "USD", "US"),
])
def test_moeda_e_pais_por_classe(key, moeda, pais):
    spec = get_spec(key)
    assert spec.currency == moeda
    assert spec.country == pais


@pytest.mark.parametrize("key,models,items,coluna", [
    ("b3", "b3_portfolio_models", "b3_portfolio_model_items", "ticker"),
    ("us", "us_portfolio_models", "us_portfolio_model_items", "symbol"),
    ("fii", "fii_portfolio_models", "fii_portfolio_model_items", "ticker"),
])
def test_tabelas_e_coluna_chave_batem_com_o_schema_existente(key, models, items, coluna):
    spec = get_spec(key)
    assert spec.models_table == models
    assert spec.items_table == items
    assert spec.symbol_column == coluna


def test_check_do_schema_cobre_exatamente_as_classes_registradas():
    # O CHECK em 049 lista as classes aceitas; registro e schema nao podem divergir.
    from pathlib import Path
    sql = (Path(__file__).resolve().parents[1] / "supabase_unificado" / "schema"
           / "049_portfolio_asset_snapshots.sql").read_text(encoding="utf-8")
    for key in SPECS:
        assert f"'{key}'" in sql
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `python -m pytest tests/test_portfolio_registry.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'core.portfolio.registry'`

- [ ] **Step 3: Escrever a implementação**

```python
"""Registro das classes de ativo suportadas pela camada canonica.

Adicionar uma classe nova significa acrescentar uma entrada em SPECS e criar o
adaptador correspondente. Nenhuma migracao de schema e necessaria.

Coberto por tests/test_portfolio_registry.py.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from types import ModuleType


@dataclass(frozen=True)
class AssetClassSpec:
    """Descreve onde vive a carteira-modelo de uma classe e como le-la."""

    key: str
    label: str
    models_table: str
    items_table: str
    symbol_column: str
    currency: str
    country: str
    adapter_module: str


SPECS: dict[str, AssetClassSpec] = {
    "b3": AssetClassSpec(
        key="b3",
        label="Empresas B3",
        models_table="b3_portfolio_models",
        items_table="b3_portfolio_model_items",
        symbol_column="ticker",
        currency="BRL",
        country="BR",
        adapter_module="core.portfolio.adapters.b3",
    ),
    "us": AssetClassSpec(
        key="us",
        label="Empresas Americanas",
        models_table="us_portfolio_models",
        items_table="us_portfolio_model_items",
        symbol_column="symbol",
        currency="USD",
        country="US",
        adapter_module="core.portfolio.adapters.us",
    ),
    "fii": AssetClassSpec(
        key="fii",
        label="FIIs",
        models_table="fii_portfolio_models",
        items_table="fii_portfolio_model_items",
        symbol_column="ticker",
        currency="BRL",
        country="BR",
        adapter_module="core.portfolio.adapters.fii",
    ),
}


def asset_classes() -> tuple[str, ...]:
    """Chaves registradas em ordem alfabetica (determinismo)."""
    return tuple(sorted(SPECS))


def get_spec(key: str) -> AssetClassSpec:
    normal = str(key or "").strip().lower()
    if normal not in SPECS:
        raise KeyError(f"classe de ativo desconhecida: {key!r}")
    return SPECS[normal]


def load_adapter(key: str) -> ModuleType:
    """Importa o adaptador da classe sob demanda."""
    return importlib.import_module(get_spec(key).adapter_module)
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `python -m pytest tests/test_portfolio_registry.py -v`
Expected: 11 passed (5 testes simples + 3 + 3 casos parametrizados)

- [ ] **Step 5: Commit**

```bash
git add core/portfolio/registry.py tests/test_portfolio_registry.py
git commit -m "feat(portfolio): registro de classes de ativo com tabelas, moeda e pais"
```

---

### Task 5: Repositório — gravação, leitura, retenção e poda de órfãos

**Files:**
- Create: `core/portfolio/repository.py`
- Test: `tests/test_portfolio_repository.py`

**Interfaces:**
- Consumes: `core.portfolio.models.AssetSnapshot`, `core.portfolio.registry.get_spec`, `core.database.get_engine`, `core.config.settings.OWNER_USER_ID`.
- Produces:
  - `RETENTION_ARCHIVED: int = 5`
  - `save_snapshots(snapshots: list[AssetSnapshot], *, engine=None, owner_id=None) -> int`
  - `load_snapshots(asset_class: str, model_id: str, *, engine=None) -> dict[str, dict]` — símbolo em maiúsculas → payload.
  - `prune_orphans(*, engine=None) -> int`
  - `apply_retention(asset_class: str, *, engine=None, keep: int = RETENTION_ARCHIVED) -> int`

**Nota de dialeto.** O projeto usa SQL cru via `text()`. Em PostgreSQL, inserir texto numa coluna `JSONB` exige `CAST(:payload AS jsonb)`; em SQLite esse cast não existe. Duas constantes escolhidas por `engine.dialect.name` resolvem isso e mantêm o idioma do projeto, além de tornar o round-trip testável contra SQLite em memória.

- [ ] **Step 1: Escrever o teste que falha**

```python
"""Repositorio de snapshots: round-trip, retencao e poda de orfaos (SQLite)."""
import datetime as dt
import json

import pytest
from sqlalchemy import create_engine, text

from core.portfolio.models import AssetSnapshot
from core.portfolio.repository import (
    RETENTION_ARCHIVED,
    apply_retention,
    load_snapshots,
    prune_orphans,
    save_snapshots,
)

OWNER = "22222222-2222-2222-2222-222222222222"


@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:")
    with eng.begin() as conn:
        conn.execute(text("""
            CREATE TABLE portfolio_asset_snapshots (
                id             TEXT PRIMARY KEY,
                user_id        TEXT NOT NULL,
                asset_class    TEXT NOT NULL,
                model_id       TEXT NOT NULL,
                symbol         TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                as_of_date     TEXT NOT NULL,
                payload        TEXT NOT NULL,
                payload_digest TEXT NOT NULL,
                created_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (asset_class, model_id, symbol)
            )
        """))
        conn.execute(text("""
            CREATE TABLE b3_portfolio_models (
                id TEXT PRIMARY KEY, user_id TEXT, status TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """))
    return eng


def _modelo(engine, model_id, status="active"):
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO b3_portfolio_models (id, user_id, status, created_at) "
                 "VALUES (:i, :u, :s, :c)"),
            {"i": model_id, "u": OWNER, "s": status, "c": f"2026-08-{int(model_id[-2:]):02d}"},
        )


def _snap(model_id, symbol, dy=1.0):
    return AssetSnapshot.from_blocks(
        asset_class="b3", model_id=model_id, symbol=symbol,
        as_of_date=dt.date(2026, 8, 5),
        blocks={"identity": {"symbol": symbol}, "metrics": {"dy": dy}},
    )


def test_round_trip_preserva_o_payload(engine):
    _modelo(engine, "m01")
    gravados = save_snapshots([_snap("m01", "PETR4"), _snap("m01", "VALE3", dy=2.0)],
                              engine=engine, owner_id=OWNER)
    assert gravados == 2

    lidos = load_snapshots("b3", "m01", engine=engine)
    assert set(lidos) == {"PETR4", "VALE3"}
    assert lidos["VALE3"]["metrics"]["dy"] == 2.0
    assert lidos["PETR4"]["schema_version"] == 1


def test_regravar_o_mesmo_ativo_atualiza_em_vez_de_duplicar(engine):
    _modelo(engine, "m01")
    save_snapshots([_snap("m01", "PETR4", dy=1.0)], engine=engine, owner_id=OWNER)
    save_snapshots([_snap("m01", "PETR4", dy=9.0)], engine=engine, owner_id=OWNER)

    lidos = load_snapshots("b3", "m01", engine=engine)
    assert len(lidos) == 1
    assert lidos["PETR4"]["metrics"]["dy"] == 9.0


def test_load_de_modelo_inexistente_devolve_vazio(engine):
    assert load_snapshots("b3", "nao-existe", engine=engine) == {}


def test_prune_remove_snapshot_de_modelo_apagado(engine):
    _modelo(engine, "m01")
    save_snapshots([_snap("m01", "PETR4")], engine=engine, owner_id=OWNER)

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM b3_portfolio_models WHERE id = 'm01'"))

    assert prune_orphans(engine=engine) == 1
    assert load_snapshots("b3", "m01", engine=engine) == {}


def test_prune_nao_remove_snapshot_de_modelo_vivo(engine):
    _modelo(engine, "m01")
    save_snapshots([_snap("m01", "PETR4")], engine=engine, owner_id=OWNER)
    assert prune_orphans(engine=engine) == 0
    assert len(load_snapshots("b3", "m01", engine=engine)) == 1


def test_retencao_mantem_a_ativa_mais_as_n_ultimas_arquivadas(engine):
    _modelo(engine, "m20", status="active")
    save_snapshots([_snap("m20", "PETR4")], engine=engine, owner_id=OWNER)
    for i in range(1, 9):                      # 8 arquivadas, da mais antiga a mais nova
        mid = f"m{i:02d}"
        _modelo(engine, mid, status="archived")
        save_snapshots([_snap(mid, "PETR4")], engine=engine, owner_id=OWNER)

    removidos = apply_retention("b3", engine=engine)
    assert removidos == 8 - RETENTION_ARCHIVED   # 3 arquivadas mais antigas perdem o payload

    assert len(load_snapshots("b3", "m20", engine=engine)) == 1   # ativa preservada
    assert load_snapshots("b3", "m01", engine=engine) == {}       # mais antiga podada
    assert len(load_snapshots("b3", "m08", engine=engine)) == 1   # recente preservada


def test_save_sem_owner_configurado_levanta_erro(engine):
    _modelo(engine, "m01")
    with pytest.raises(RuntimeError, match="OWNER_USER_ID"):
        save_snapshots([_snap("m01", "PETR4")], engine=engine, owner_id=None)


def test_save_de_lista_vazia_nao_toca_o_banco(engine):
    assert save_snapshots([], engine=engine, owner_id=OWNER) == 0
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `python -m pytest tests/test_portfolio_repository.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'core.portfolio.repository'`

- [ ] **Step 3: Escrever a implementação**

```python
"""Persistencia dos snapshots analiticos.

Unico modulo do pacote com SQL. Compativel com PostgreSQL (producao) e SQLite
(testes) por meio de dois fragmentos escolhidos pelo dialeto.

Coberto por tests/test_portfolio_repository.py.
"""
from __future__ import annotations

import json
import uuid

from sqlalchemy import text

from core.portfolio.models import AssetSnapshot
from core.portfolio.registry import SPECS, get_spec
from core.portfolio.snapshots import canonical_json

RETENTION_ARCHIVED = 5

_TABELA = "portfolio_asset_snapshots"


def _resolve_engine(engine):
    if engine is not None:
        return engine
    from core.database import get_engine
    eng = get_engine()
    if eng is None:
        raise RuntimeError("Banco unificado nao configurado.")
    return eng


def _resolve_owner(owner_id):
    if owner_id:
        return str(owner_id)
    from core.config import settings
    if not settings.OWNER_USER_ID:
        raise RuntimeError("OWNER_USER_ID nao configurado; snapshot nao pode ser gravado.")
    return str(settings.OWNER_USER_ID)


def _json_placeholder(engine) -> str:
    """PostgreSQL exige cast explicito de texto para JSONB; SQLite nao tem o tipo."""
    return "CAST(:payload AS jsonb)" if engine.dialect.name == "postgresql" else ":payload"


def _decode(valor):
    """Le a coluna payload: dict no PostgreSQL (JSONB), texto no SQLite."""
    if isinstance(valor, (dict, list)):
        return valor
    return json.loads(valor)


def save_snapshots(snapshots: list[AssetSnapshot], *, engine=None, owner_id=None) -> int:
    """Grava ou atualiza os snapshots. Retorna quantos foram persistidos."""
    if not snapshots:
        return 0

    eng = _resolve_engine(engine)
    owner = _resolve_owner(owner_id)
    placeholder = _json_placeholder(eng)

    sql = text(f"""
        INSERT INTO {_TABELA} (
            id, user_id, asset_class, model_id, symbol,
            schema_version, as_of_date, payload, payload_digest
        )
        VALUES (
            :id, :user_id, :asset_class, :model_id, :symbol,
            :schema_version, :as_of_date, {placeholder}, :payload_digest
        )
        ON CONFLICT (asset_class, model_id, symbol) DO UPDATE SET
            schema_version = EXCLUDED.schema_version,
            as_of_date     = EXCLUDED.as_of_date,
            payload        = EXCLUDED.payload,
            payload_digest = EXCLUDED.payload_digest
    """)

    with eng.begin() as conn:
        for snap in snapshots:
            get_spec(snap.asset_class)          # valida a classe antes de gravar
            conn.execute(sql, {
                "id": str(uuid.uuid4()),
                "user_id": owner,
                "asset_class": snap.asset_class,
                "model_id": str(snap.model_id),
                "symbol": snap.symbol,
                "schema_version": snap.schema_version,
                "as_of_date": snap.as_of_date.isoformat(),
                "payload": canonical_json(snap.payload),
                "payload_digest": snap.digest,
            })
    return len(snapshots)


def load_snapshots(asset_class: str, model_id: str, *, engine=None) -> dict[str, dict]:
    """Devolve {simbolo: payload} dos snapshots de um modelo."""
    spec = get_spec(asset_class)
    eng = _resolve_engine(engine)

    with eng.connect() as conn:
        linhas = conn.execute(
            text(f"""
                SELECT symbol, payload FROM {_TABELA}
                WHERE asset_class = :ac AND model_id = :mid
                ORDER BY symbol
            """),
            {"ac": spec.key, "mid": str(model_id)},
        ).mappings().all()

    return {linha["symbol"]: _decode(linha["payload"]) for linha in linhas}


def prune_orphans(*, engine=None) -> int:
    """Remove snapshots cujo modelo nao existe mais. Retorna quantos sairam.

    Necessario porque model_id e polimorfico e nao tem FK. Ver a nota de
    modelagem em supabase_unificado/schema/049_portfolio_asset_snapshots.sql.
    """
    eng = _resolve_engine(engine)
    removidos = 0

    with eng.begin() as conn:
        for key in sorted(SPECS):
            spec = SPECS[key]
            resultado = conn.execute(
                text(f"""
                    DELETE FROM {_TABELA}
                    WHERE asset_class = :ac
                      AND model_id NOT IN (SELECT id FROM {spec.models_table})
                """),
                {"ac": spec.key},
            )
            removidos += int(resultado.rowcount or 0)
    return removidos


def apply_retention(asset_class: str, *, engine=None, keep: int = RETENTION_ARCHIVED) -> int:
    """Descarta o payload das versoes arquivadas alem das `keep` mais recentes.

    A versao ativa nunca e afetada. Retorna quantos modelos perderam o payload.
    """
    spec = get_spec(asset_class)
    eng = _resolve_engine(engine)

    with eng.begin() as conn:
        arquivadas = [
            linha["id"] for linha in conn.execute(
                text(f"""
                    SELECT id FROM {spec.models_table}
                    WHERE status = 'archived'
                    ORDER BY created_at DESC, id DESC
                """)
            ).mappings().all()
        ]
        alvo = arquivadas[keep:]
        if not alvo:
            return 0

        for model_id in alvo:
            conn.execute(
                text(f"DELETE FROM {_TABELA} WHERE asset_class = :ac AND model_id = :mid"),
                {"ac": spec.key, "mid": str(model_id)},
            )
    return len(alvo)
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `python -m pytest tests/test_portfolio_repository.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add core/portfolio/repository.py tests/test_portfolio_repository.py
git commit -m "feat(portfolio): repositorio de snapshots com retencao e poda de orfaos"
```

---

### Task 6: Adaptador B3

**Files:**
- Create: `core/portfolio/adapters/__init__.py`, `core/portfolio/adapters/_frames.py`, `core/portfolio/adapters/b3.py`
- Test: `tests/test_portfolio_adapter_frames.py`, `tests/test_portfolio_adapter_b3.py`

**Interfaces:**
- Consumes: `core.market_read.load_multiplos_historico_batch(tickers: tuple[str, ...]) -> dict[str, pd.DataFrame]`, `core.market_read.load_demonstracoes_batch(tickers: tuple[str, ...]) -> dict[str, pd.DataFrame]`, `core.portfolio.models.AssetSnapshot`.
- Produces:
  - `core.portfolio.adapters._frames.registros(frame) -> list[dict]` — usado também pelas Tasks 7 e 8.
  - `core.portfolio.adapters._frames.indexar(frame, coluna: str) -> dict[str, dict]` — usado também pelas Tasks 7 e 8.
  - `build_snapshots(items: list[dict], *, model_id: str, params: dict, as_of: date, loaders: dict | None = None) -> list[AssetSnapshot]`.

Os dois helpers de DataFrame vivem em `_frames.py` e **não** são reimplementados nos adaptadores das Tasks 7 e 8 — os três leem tabelas com formatos diferentes, mas a conversão para dicts com `NaN → None` é idêntica.

O parâmetro `loaders` existe para injeção nos testes: um dicionário com as chaves `"multiplos"` e `"demonstracoes"`, cada uma uma função `tuple[str, ...] -> dict[str, pd.DataFrame]`. Quando `None`, usa `core.market_read`. Ambos os adaptadores seguintes têm o mesmo contrato.

Leitura em lote e não por ativo: montar o snapshot acrescenta segundos ao salvamento e ler ticker a ticker multiplicaria isso pelo tamanho da carteira.

- [ ] **Step 1a: Escrever o teste dos helpers compartilhados**

Criar `tests/test_portfolio_adapter_frames.py`:

```python
"""Helpers de DataFrame compartilhados pelos adaptadores."""
import pandas as pd

from core.portfolio.adapters._frames import indexar, registros

DF = pd.DataFrame({"Ticker": ["petr4", " vale3"], "P/L": [4.1, None]})


def test_registros_converte_para_lista_de_dicts():
    assert registros(DF)[0]["Ticker"] == "petr4"


def test_registros_converte_nan_para_none():
    assert registros(DF)[1]["P/L"] is None


def test_registros_tolera_none_e_dataframe_vazio():
    assert registros(None) == []
    assert registros(pd.DataFrame()) == []


def test_indexar_normaliza_a_chave_para_maiusculo_sem_espaco():
    assert set(indexar(DF, "Ticker")) == {"PETR4", "VALE3"}


def test_indexar_devolve_vazio_quando_a_coluna_nao_existe():
    assert indexar(DF, "Symbol") == {}


def test_indexar_tolera_none_e_dataframe_vazio():
    assert indexar(None, "Ticker") == {}
    assert indexar(pd.DataFrame(), "Ticker") == {}
```

- [ ] **Step 1b: Escrever o teste do adaptador B3**

```python
"""Adaptador B3: montagem do payload a partir dos itens da carteira."""
import datetime as dt

import pandas as pd

from core.portfolio.adapters.b3 import build_snapshots

ITENS = [
    {"tk": "PETR4", "nome": "Petrobras", "setor": "Petroleo", "subsetor": "E&P",
     "segmento": "Exploracao", "score": 82.5, "alpha_selic": 3.2, "alpha_ew": 1.1,
     "rank_score": 1, "ano_lider": 2025, "motivos": ["Lider de score"],
     "quali": {"classificacao": "aprovada", "motivo": "governanca ok"}, "peso": 0.6},
    {"tk": "VALE3", "nome": "Vale", "setor": "Mineracao", "score": 75.0,
     "rank_score": 2, "motivos": [], "peso": 0.4},
]

MULT = {"PETR4": pd.DataFrame({"ano": [2024, 2025], "P/L": [4.1, 5.0], "DY": [12.0, 10.5]})}
DEMO = {"PETR4": pd.DataFrame({"ano": [2024, 2025], "Receita": [500.0, 520.0],
                               "Lucro": [90.0, 95.0]})}


def _loaders():
    return {"multiplos": lambda tks: {k: v for k, v in MULT.items() if k in tks},
            "demonstracoes": lambda tks: {k: v for k, v in DEMO.items() if k in tks}}


def _build():
    return build_snapshots(ITENS, model_id="m01", params={"top_n": 2},
                           as_of=dt.date(2026, 8, 5), loaders=_loaders())


def test_gera_um_snapshot_por_item():
    assert [s.symbol for s in _build()] == ["PETR4", "VALE3"]


def test_classe_e_moeda_vem_do_registro():
    snap = _build()[0]
    assert snap.asset_class == "b3"
    assert snap.payload["identity"]["currency"] == "BRL"
    assert snap.payload["identity"]["country"] == "BR"


def test_identity_carrega_a_taxonomia_de_origem():
    ident = _build()[0].payload["identity"]
    assert ident["symbol"] == "PETR4"
    assert ident["name"] == "Petrobras"
    assert ident["sector"] == "Petroleo"
    assert ident["subsector"] == "E&P"
    assert ident["segment"] == "Exploracao"


def test_metrics_preserva_os_numeros_da_selecao():
    metrics = _build()[0].payload["metrics"]
    assert metrics["score"] == 82.5
    assert metrics["alpha_selic"] == 3.2
    assert metrics["rank_score"] == 1


def test_history_traz_as_series_anuais_como_registros():
    history = _build()[0].payload["history"]
    assert history["multiplos_anuais"][-1]["ano"] == 2025
    assert history["demonstracoes_anuais"][-1]["Lucro"] == 95.0


def test_ativo_sem_dado_historico_gera_snapshot_com_history_vazio():
    vale = _build()[1]
    assert vale.payload["history"]["multiplos_anuais"] == []
    assert vale.payload["classification"]["has_history"] is False


def test_assumptions_guarda_os_parametros_do_modelo():
    assert _build()[0].payload["assumptions"]["params"]["top_n"] == 2


def test_classification_carrega_o_parecer_qualitativo():
    quali = _build()[0].payload["classification"]["quali"]
    assert quali["classificacao"] == "aprovada"


def test_provenance_registra_origem_e_data():
    prov = _build()[0].payload["provenance"]
    assert prov["source"] == "criacao_portfolio_b3"
    assert prov["as_of_date"] == "2026-08-05"
    assert prov["backfilled"] is False


def test_item_sem_ticker_e_ignorado_sem_quebrar():
    itens = ITENS + [{"nome": "sem ticker", "peso": 0.1}]
    out = build_snapshots(itens, model_id="m01", params={}, as_of=dt.date(2026, 8, 5),
                          loaders=_loaders())
    assert [s.symbol for s in out] == ["PETR4", "VALE3"]
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `python -m pytest tests/test_portfolio_adapter_frames.py tests/test_portfolio_adapter_b3.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'core.portfolio.adapters'`

- [ ] **Step 3: Escrever a implementação**

Criar `core/portfolio/adapters/__init__.py` vazio com docstring:

```python
"""Adaptadores que montam o payload de snapshot de cada classe de ativo."""
```

Criar `core/portfolio/adapters/_frames.py`:

```python
"""Conversao de DataFrame compartilhada pelos tres adaptadores.

As tabelas de origem tem formatos diferentes, mas a conversao para dicts com
NaN -> None e identica. Coberto por tests/test_portfolio_adapter_frames.py.
"""
from __future__ import annotations

import pandas as pd


def registros(frame) -> list[dict]:
    """DataFrame -> lista de dicts, tolerante a None e vazio. NaN vira None."""
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    return frame.where(pd.notna(frame), None).to_dict(orient="records")


def indexar(frame, coluna: str) -> dict[str, dict]:
    """DataFrame -> {chave normalizada: linha}. Vazio se a coluna nao existir."""
    if not isinstance(frame, pd.DataFrame) or frame.empty or coluna not in frame:
        return {}
    limpo = frame.where(pd.notna(frame), None)
    return {str(linha[coluna]).strip().upper(): dict(linha)
            for linha in limpo.to_dict(orient="records")}
```

Criar `core/portfolio/adapters/b3.py`:

```python
"""Adaptador de snapshot das empresas B3.

Le em lote (nao ticker a ticker) porque montar o snapshot acrescenta tempo ao
salvamento da carteira. Coberto por tests/test_portfolio_adapter_b3.py.
"""
from __future__ import annotations

import datetime as dt

from core.portfolio.adapters._frames import registros
from core.portfolio.models import AssetSnapshot
from core.portfolio.registry import get_spec

SPEC = get_spec("b3")


def _default_loaders() -> dict:
    from core import market_read
    return {
        "multiplos": lambda tks: market_read.load_multiplos_historico_batch(tks),
        "demonstracoes": lambda tks: market_read.load_demonstracoes_batch(tks),
    }


def _ticker(item: dict) -> str:
    return str(item.get("tk") or item.get("ticker") or "").strip().upper()


def build_snapshots(items: list[dict], *, model_id: str, params: dict,
                    as_of: dt.date, loaders: dict | None = None) -> list[AssetSnapshot]:
    """Monta um AssetSnapshot por item valido da carteira B3."""
    loaders = loaders or _default_loaders()
    validos = [(item, _ticker(item)) for item in items]
    validos = [(item, tk) for item, tk in validos if tk]
    if not validos:
        return []

    tickers = tuple(sorted({tk for _, tk in validos}))
    multiplos = loaders["multiplos"](tickers) or {}
    demonstracoes = loaders["demonstracoes"](tickers) or {}

    saida: list[AssetSnapshot] = []
    for item, tk in validos:
        mult = registros(multiplos.get(tk))
        demo = registros(demonstracoes.get(tk))
        saida.append(AssetSnapshot.from_blocks(
            asset_class=SPEC.key,
            model_id=model_id,
            symbol=tk,
            as_of_date=as_of,
            blocks={
                "identity": {
                    "symbol": tk,
                    "name": item.get("nome") or tk,
                    "asset_class": SPEC.key,
                    "currency": SPEC.currency,
                    "country": SPEC.country,
                    "sector": item.get("setor"),
                    "subsector": item.get("subsetor"),
                    "segment": item.get("segmento"),
                },
                "fundamentals": mult[-1] if mult else {},
                "metrics": {
                    "score": item.get("score"),
                    "alpha_selic": item.get("alpha_selic"),
                    "alpha_ew": item.get("alpha_ew"),
                    "rank_score": item.get("rank_score"),
                    "weight": item.get("peso") if item.get("peso") is not None
                              else item.get("weight"),
                },
                "classification": {
                    "ano_lider": item.get("ano_lider"),
                    "motivos": list(item.get("motivos") or []),
                    "quali": item.get("quali") or {},
                    "has_history": bool(mult or demo),
                },
                "history": {
                    "multiplos_anuais": mult,
                    "demonstracoes_anuais": demo,
                },
                "assumptions": {"params": dict(params or {})},
                "evidence": {},
                "notes": "",
                "provenance": {
                    "source": "criacao_portfolio_b3",
                    "as_of_date": as_of.isoformat(),
                    "backfilled": False,
                },
            },
        ))
    return saida
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `python -m pytest tests/test_portfolio_adapter_frames.py tests/test_portfolio_adapter_b3.py -v`
Expected: 16 passed (6 dos helpers + 10 do adaptador)

- [ ] **Step 5: Commit**

```bash
git add core/portfolio/adapters/ tests/test_portfolio_adapter_frames.py tests/test_portfolio_adapter_b3.py
git commit -m "feat(portfolio): helpers de DataFrame e adaptador de snapshot das empresas B3"
```

---

### Task 7: Adaptador EUA

**Files:**
- Create: `core/portfolio/adapters/us.py`
- Test: `tests/test_portfolio_adapter_us.py`

**Interfaces:**
- Consumes: `core.portfolio.adapters._frames.registros` e `indexar` (Task 6 — **não** reimplementar), `core.us_read.load_snapshot_scored() -> pd.DataFrame` (uma linha por símbolo, coluna `symbol`), `core.us_read.load_company_financials(symbol: str) -> pd.DataFrame`, `core.portfolio.models.AssetSnapshot`.
- Produces: `build_snapshots(items, *, model_id, params, as_of, loaders=None) -> list[AssetSnapshot]` — mesma assinatura do adaptador B3. `loaders` aceita as chaves `"scored"` (função sem argumentos devolvendo `pd.DataFrame`) e `"financials"` (função `str -> pd.DataFrame`).

A chave do item americano é `symbol` (e não `tk`/`ticker`), conforme `core/us_portfolio_model.py:_symbol_of`.

- [ ] **Step 1: Escrever o teste que falha**

```python
"""Adaptador EUA: montagem do payload a partir dos itens da carteira."""
import datetime as dt

import pandas as pd

from core.portfolio.adapters.us import build_snapshots

ITENS = [
    {"symbol": "AAPL", "nome": "Apple Inc.", "setor": "Technology",
     "industria": "Consumer Electronics", "entry_score": 71.0,
     "fundamental_score": 83.0, "coverage": 92.0, "rank_score": 1, "peso": 0.7},
    {"symbol": "KO", "nome": "Coca-Cola", "setor": "Consumer Defensive",
     "entry_score": 58.0, "coverage": 40.0, "rank_score": 2, "peso": 0.3},
]

SCORED = pd.DataFrame({
    "symbol": ["AAPL", "KO"],
    "pe_ratio": [28.4, 24.1],
    "dividend_yield": [0.5, 3.1],
    "score_confidence": [0.91, 0.62],
    "status": ["ok", "parcial"],
})

FIN = {"AAPL": pd.DataFrame({"fiscal_year": [2024, 2025],
                             "revenue": [383.0, 401.0], "net_income": [97.0, 102.0]})}


def _loaders():
    return {"scored": lambda: SCORED,
            "financials": lambda sym: FIN.get(sym, pd.DataFrame())}


def _build():
    return build_snapshots(ITENS, model_id="m01", params={"top_n": 2},
                           as_of=dt.date(2026, 8, 5), loaders=_loaders())


def test_gera_um_snapshot_por_item():
    assert [s.symbol for s in _build()] == ["AAPL", "KO"]


def test_classe_moeda_e_pais_vem_do_registro():
    snap = _build()[0]
    assert snap.asset_class == "us"
    assert snap.payload["identity"]["currency"] == "USD"
    assert snap.payload["identity"]["country"] == "US"


def test_identity_usa_setor_e_industria():
    ident = _build()[0].payload["identity"]
    assert ident["sector"] == "Technology"
    assert ident["subsector"] == "Consumer Electronics"


def test_fundamentals_vem_da_vitrine_com_score():
    fund = _build()[0].payload["fundamentals"]
    assert fund["pe_ratio"] == 28.4
    assert fund["dividend_yield"] == 0.5


def test_metrics_preserva_os_scores_da_selecao():
    metrics = _build()[0].payload["metrics"]
    assert metrics["entry_score"] == 71.0
    assert metrics["fundamental_score"] == 83.0
    assert metrics["coverage"] == 92.0


def test_classification_carrega_confianca_e_status_da_vitrine():
    cls = _build()[0].payload["classification"]
    assert cls["score_confidence"] == 0.91
    assert cls["status"] == "ok"


def test_history_traz_os_demonstrativos_anuais():
    history = _build()[0].payload["history"]
    assert history["financials_anuais"][-1]["net_income"] == 102.0


def test_simbolo_ausente_na_vitrine_nao_quebra():
    itens = ITENS + [{"symbol": "ZZZZ", "nome": "Desconhecida", "peso": 0.1}]
    out = build_snapshots(itens, model_id="m01", params={}, as_of=dt.date(2026, 8, 5),
                          loaders=_loaders())
    zzz = [s for s in out if s.symbol == "ZZZZ"][0]
    assert zzz.payload["fundamentals"] == {}
    assert zzz.payload["classification"]["has_history"] is False


def test_provenance_registra_origem():
    assert _build()[0].payload["provenance"]["source"] == "criacao_portfolio_us"
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `python -m pytest tests/test_portfolio_adapter_us.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'core.portfolio.adapters.us'`

- [ ] **Step 3: Escrever a implementação**

```python
"""Adaptador de snapshot das empresas americanas.

A vitrine (load_snapshot_scored) e lida uma unica vez e indexada em memoria;
os demonstrativos sao lidos por simbolo porque nao ha versao em lote em
core/us_read.py. Coberto por tests/test_portfolio_adapter_us.py.
"""
from __future__ import annotations

import datetime as dt

from core.portfolio.adapters._frames import indexar, registros
from core.portfolio.models import AssetSnapshot
from core.portfolio.registry import get_spec

SPEC = get_spec("us")

# Campos da vitrine que entram como classificacao, e nao como fundamento.
_CAMPOS_CLASSIFICACAO = ("score_confidence", "status", "critical_missing")


def _default_loaders() -> dict:
    from core import us_read
    return {
        "scored": lambda: us_read.load_snapshot_scored(),
        "financials": lambda sym: us_read.load_company_financials(sym),
    }


def _symbol(item: dict) -> str:
    return str(item.get("symbol") or item.get("tk") or item.get("ticker") or "").strip().upper()


def build_snapshots(items: list[dict], *, model_id: str, params: dict,
                    as_of: dt.date, loaders: dict | None = None) -> list[AssetSnapshot]:
    """Monta um AssetSnapshot por item valido da carteira americana."""
    loaders = loaders or _default_loaders()
    validos = [(item, _symbol(item)) for item in items]
    validos = [(item, sym) for item, sym in validos if sym]
    if not validos:
        return []

    vitrine = indexar(loaders["scored"](), "symbol")

    saida: list[AssetSnapshot] = []
    for item, sym in validos:
        linha = dict(vitrine.get(sym) or {})
        classificacao = {campo: linha.pop(campo, None) for campo in _CAMPOS_CLASSIFICACAO}
        linha.pop("symbol", None)
        financials = registros(loaders["financials"](sym))

        saida.append(AssetSnapshot.from_blocks(
            asset_class=SPEC.key,
            model_id=model_id,
            symbol=sym,
            as_of_date=as_of,
            blocks={
                "identity": {
                    "symbol": sym,
                    "name": item.get("nome") or sym,
                    "asset_class": SPEC.key,
                    "currency": SPEC.currency,
                    "country": SPEC.country,
                    "sector": item.get("setor"),
                    "subsector": item.get("industria"),
                    "segment": None,
                },
                "fundamentals": linha,
                "metrics": {
                    "entry_score": item.get("entry_score"),
                    "fundamental_score": item.get("fundamental_score"),
                    "coverage": item.get("coverage"),
                    "rank_score": item.get("rank_score"),
                    "weight": item.get("peso") if item.get("peso") is not None
                              else item.get("weight"),
                },
                "classification": {**classificacao,
                                   "has_history": bool(financials)},
                "history": {"financials_anuais": financials},
                "assumptions": {"params": dict(params or {})},
                "evidence": {},
                "notes": "",
                "provenance": {
                    "source": "criacao_portfolio_us",
                    "as_of_date": as_of.isoformat(),
                    "backfilled": False,
                },
            },
        ))
    return saida
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `python -m pytest tests/test_portfolio_adapter_us.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add core/portfolio/adapters/us.py tests/test_portfolio_adapter_us.py
git commit -m "feat(portfolio): adaptador de snapshot das empresas americanas"
```

---

### Task 8: Adaptador FII

**Files:**
- Create: `core/portfolio/adapters/fii.py`
- Test: `tests/test_portfolio_adapter_fii.py`

**Interfaces:**
- Consumes: `core.portfolio.adapters._frames.indexar` (Task 6 — **não** reimplementar), `core.market_read.load_fiis() -> pd.DataFrame` (colunas com inicial maiúscula: `Ticker`, `Nome`, `Segmento`, `Tipo`, `Preço`, `P/VP`, `DY_12m`, `Liquidez_Diaria`, `Patrimonio`, `VPA`, `Cotistas`, `Gestao`, `Pct_Imoveis`, `Pct_Papel`, `Pct_Caixa`, `Pct_Fundos`, `Score`), `core.portfolio.models.AssetSnapshot`.
- Produces: `build_snapshots(items, *, model_id, params, as_of, loaders=None) -> list[AssetSnapshot]`. `loaders` aceita a chave `"fiis"` (função sem argumentos devolvendo `pd.DataFrame`).

A composição por tipo de ativo (`Pct_Imoveis`, `Pct_Papel`, `Pct_Caixa`, `Pct_Fundos`) vai para um bloco `composition` dentro de `classification` — é o que a Fase 2 usará no look-through.

- [ ] **Step 1: Escrever o teste que falha**

```python
"""Adaptador FII: montagem do payload a partir dos itens da carteira."""
import datetime as dt

import pandas as pd

from core.portfolio.adapters.fii import build_snapshots

ITENS = [
    {"ticker": "HGLG11", "nome": "CSHG Logistica", "segmento": "Logistica",
     "score": 78.0, "peso": 0.6},
    {"tk": "KNCR11", "nome": "Kinea Rendimentos", "segmento": "Papel",
     "score": 71.0, "peso": 0.4},
]

FIIS = pd.DataFrame({
    "Ticker": ["HGLG11", "KNCR11"],
    "Nome": ["CSHG Logistica", "Kinea Rendimentos"],
    "Segmento": ["Logistica", "Papel"],
    "Tipo": ["Tijolo", "Papel"],
    "Preço": [160.0, 102.0],
    "P/VP": [0.95, 1.01],
    "DY_12m": [8.4, 12.1],
    "Liquidez_Diaria": [3_000_000.0, 5_000_000.0],
    "Patrimonio": [3.2e9, 6.1e9],
    "VPA": [168.0, 101.0],
    "Cotistas": [250_000, 410_000],
    "Gestao": ["Ativa", "Ativa"],
    "Pct_Imoveis": [96.0, 0.0],
    "Pct_Papel": [0.0, 94.0],
    "Pct_Caixa": [4.0, 6.0],
    "Pct_Fundos": [0.0, 0.0],
    "Score": [78.0, 71.0],
})


def _loaders():
    return {"fiis": lambda: FIIS}


def _build():
    return build_snapshots(ITENS, model_id="m01", params={"top_n": 2},
                           as_of=dt.date(2026, 8, 5), loaders=_loaders())


def test_gera_um_snapshot_por_item_aceitando_ticker_e_tk():
    assert [s.symbol for s in _build()] == ["HGLG11", "KNCR11"]


def test_classe_moeda_e_pais_vem_do_registro():
    snap = _build()[0]
    assert snap.asset_class == "fii"
    assert snap.payload["identity"]["currency"] == "BRL"
    assert snap.payload["identity"]["country"] == "BR"


def test_identity_usa_segmento_como_setor():
    ident = _build()[0].payload["identity"]
    assert ident["sector"] == "Logistica"
    assert ident["segment"] == "Tijolo"


def test_fundamentals_traz_pvp_dy_e_patrimonio():
    fund = _build()[0].payload["fundamentals"]
    assert fund["pvp"] == 0.95
    assert fund["dy_12m"] == 8.4
    assert fund["patrimonio_liquido"] == 3.2e9


def test_composicao_por_tipo_de_ativo_fica_em_classification():
    comp = _build()[1].payload["classification"]["composition"]
    assert comp["pct_papel"] == 94.0
    assert comp["pct_imoveis"] == 0.0


def test_metrics_preserva_score_e_peso():
    metrics = _build()[0].payload["metrics"]
    assert metrics["score"] == 78.0
    assert metrics["weight"] == 0.6


def test_fii_ausente_da_base_gera_snapshot_degradado():
    itens = ITENS + [{"ticker": "XXXX11", "nome": "Fora da base", "peso": 0.1}]
    out = build_snapshots(itens, model_id="m01", params={}, as_of=dt.date(2026, 8, 5),
                          loaders=_loaders())
    xxxx = [s for s in out if s.symbol == "XXXX11"][0]
    assert xxxx.payload["fundamentals"] == {}
    assert xxxx.payload["classification"]["composition"] == {}


def test_provenance_registra_origem():
    assert _build()[0].payload["provenance"]["source"] == "selecao_fiis"
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `python -m pytest tests/test_portfolio_adapter_fii.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'core.portfolio.adapters.fii'`

- [ ] **Step 3: Escrever a implementação**

```python
"""Adaptador de snapshot dos FIIs.

A composicao por tipo de ativo (imoveis, papel, caixa, fundos) e guardada em
classification.composition porque e o insumo do look-through da Fase 2.
Coberto por tests/test_portfolio_adapter_fii.py.
"""
from __future__ import annotations

import datetime as dt

from core.portfolio.adapters._frames import indexar
from core.portfolio.models import AssetSnapshot
from core.portfolio.registry import get_spec

SPEC = get_spec("fii")

# Coluna da base (market_read.load_fiis) -> chave no bloco fundamentals.
_FUNDAMENTOS = {
    "Preço": "preco",
    "P/VP": "pvp",
    "DY_12m": "dy_12m",
    "Liquidez_Diaria": "liquidez_diaria",
    "Patrimonio": "patrimonio_liquido",
    "VPA": "vpa",
    "Cotistas": "num_cotistas",
    "Gestao": "tipo_gestao",
}

_COMPOSICAO = {
    "Pct_Imoveis": "pct_imoveis",
    "Pct_Papel": "pct_papel",
    "Pct_Caixa": "pct_caixa",
    "Pct_Fundos": "pct_fundos",
}


def _default_loaders() -> dict:
    from core import market_read
    return {"fiis": lambda: market_read.load_fiis()}


def _ticker(item: dict) -> str:
    return str(item.get("ticker") or item.get("tk") or "").strip().upper()


def build_snapshots(items: list[dict], *, model_id: str, params: dict,
                    as_of: dt.date, loaders: dict | None = None) -> list[AssetSnapshot]:
    """Monta um AssetSnapshot por item valido da carteira de FIIs."""
    loaders = loaders or _default_loaders()
    validos = [(item, _ticker(item)) for item in items]
    validos = [(item, tk) for item, tk in validos if tk]
    if not validos:
        return []

    base = indexar(loaders["fiis"](), "Ticker")

    saida: list[AssetSnapshot] = []
    for item, tk in validos:
        linha = base.get(tk) or {}
        fundamentals = {destino: linha[origem]
                        for origem, destino in _FUNDAMENTOS.items() if origem in linha}
        composition = {destino: linha[origem]
                       for origem, destino in _COMPOSICAO.items() if origem in linha}

        saida.append(AssetSnapshot.from_blocks(
            asset_class=SPEC.key,
            model_id=model_id,
            symbol=tk,
            as_of_date=as_of,
            blocks={
                "identity": {
                    "symbol": tk,
                    "name": item.get("nome") or linha.get("Nome") or tk,
                    "asset_class": SPEC.key,
                    "currency": SPEC.currency,
                    "country": SPEC.country,
                    "sector": item.get("segmento") or linha.get("Segmento"),
                    "subsector": None,
                    "segment": linha.get("Tipo"),
                },
                "fundamentals": fundamentals,
                "metrics": {
                    "score": item.get("score") if item.get("score") is not None
                             else linha.get("Score"),
                    "weight": item.get("peso") if item.get("peso") is not None
                              else item.get("weight"),
                },
                "classification": {"composition": composition},
                "history": {},
                "assumptions": {"params": dict(params or {})},
                "evidence": {},
                "notes": "",
                "provenance": {
                    "source": "selecao_fiis",
                    "as_of_date": as_of.isoformat(),
                    "backfilled": False,
                },
            },
        ))
    return saida
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `python -m pytest tests/test_portfolio_adapter_fii.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add core/portfolio/adapters/fii.py tests/test_portfolio_adapter_fii.py
git commit -m "feat(portfolio): adaptador de snapshot dos FIIs"
```

---

### Task 9: Gancho de captura nas três funções de salvamento

**Files:**
- Create: `core/portfolio/capture.py`
- Modify: `core/b3_portfolio_model.py` (uma chamada antes de `return model_id`, em `save_b3_portfolio_model`)
- Modify: `core/us_portfolio_model.py` (idem em `save_us_portfolio_model`)
- Modify: `core/fii_portfolio_model.py` (idem em `save_fii_portfolio_model`)
- Test: `tests/test_portfolio_capture.py`

**Interfaces:**
- Consumes: `core.portfolio.registry.load_adapter`, `core.portfolio.repository.save_snapshots`, `prune_orphans`, `apply_retention`.
- Produces: `capture_snapshots(asset_class: str, model_id: str, items: list[dict], params: dict, *, as_of=None, engine=None, owner_id=None) -> int` — devolve quantos snapshots gravou e **nunca levanta exceção**: qualquer falha é registrada em log e devolve `0`.

Esta é a **única** alteração em arquivo existente na Fase 1. As três chamadas são idênticas em forma, mudando apenas a chave da classe.

- [ ] **Step 1: Escrever o teste que falha**

```python
"""Gancho de captura: nunca propaga excecao e nunca impede o salvamento."""
import datetime as dt

import pytest

from core.portfolio import capture


def test_captura_grava_e_devolve_a_contagem(monkeypatch):
    chamadas = {}

    class FakeAdapter:
        @staticmethod
        def build_snapshots(items, *, model_id, params, as_of, loaders=None):
            chamadas["items"] = items
            return ["snap1", "snap2"]

    monkeypatch.setattr(capture, "load_adapter", lambda key: FakeAdapter)
    monkeypatch.setattr(capture, "save_snapshots", lambda snaps, **kw: len(snaps))
    monkeypatch.setattr(capture, "prune_orphans", lambda **kw: 0)
    monkeypatch.setattr(capture, "apply_retention", lambda ac, **kw: 0)

    n = capture.capture_snapshots("b3", "m01", [{"tk": "PETR4"}], {},
                                  as_of=dt.date(2026, 8, 5))
    assert n == 2
    assert chamadas["items"] == [{"tk": "PETR4"}]


def test_falha_no_adaptador_nao_propaga(monkeypatch, caplog):
    def explode(key):
        raise RuntimeError("adaptador quebrado")

    monkeypatch.setattr(capture, "load_adapter", explode)
    assert capture.capture_snapshots("b3", "m01", [{"tk": "PETR4"}], {}) == 0
    assert "snapshot" in caplog.text.lower()


def test_falha_na_gravacao_nao_propaga(monkeypatch):
    class FakeAdapter:
        @staticmethod
        def build_snapshots(items, **kw):
            return ["snap1"]

    def explode(snaps, **kw):
        raise RuntimeError("banco fora")

    monkeypatch.setattr(capture, "load_adapter", lambda key: FakeAdapter)
    monkeypatch.setattr(capture, "save_snapshots", explode)
    assert capture.capture_snapshots("b3", "m01", [{"tk": "PETR4"}], {}) == 0


def test_falha_na_retencao_nao_anula_a_gravacao(monkeypatch):
    class FakeAdapter:
        @staticmethod
        def build_snapshots(items, **kw):
            return ["snap1"]

    def explode(ac, **kw):
        raise RuntimeError("retencao quebrada")

    monkeypatch.setattr(capture, "load_adapter", lambda key: FakeAdapter)
    monkeypatch.setattr(capture, "save_snapshots", lambda snaps, **kw: len(snaps))
    monkeypatch.setattr(capture, "prune_orphans", lambda **kw: 0)
    monkeypatch.setattr(capture, "apply_retention", explode)

    assert capture.capture_snapshots("b3", "m01", [{"tk": "PETR4"}], {}) == 1


def test_lista_vazia_nao_chama_o_adaptador(monkeypatch):
    def nao_deve_ser_chamado(key):
        raise AssertionError("adaptador nao deveria ser carregado")

    monkeypatch.setattr(capture, "load_adapter", nao_deve_ser_chamado)
    assert capture.capture_snapshots("b3", "m01", [], {}) == 0


@pytest.mark.parametrize("modulo,funcao,classe", [
    ("core.b3_portfolio_model", "save_b3_portfolio_model", "b3"),
    ("core.us_portfolio_model", "save_us_portfolio_model", "us"),
    ("core.fii_portfolio_model", "save_fii_portfolio_model", "fii"),
])
def test_as_tres_funcoes_de_salvamento_chamam_a_captura(modulo, funcao, classe):
    """Regressao: a captura precisa estar ligada, senao nada e persistido."""
    import importlib
    import inspect

    fonte = inspect.getsource(getattr(importlib.import_module(modulo), funcao))
    assert "capture_snapshots" in fonte, f"{funcao} nao chama capture_snapshots"
    assert f'"{classe}"' in fonte or f"'{classe}'" in fonte
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `python -m pytest tests/test_portfolio_capture.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'core.portfolio.capture'`

- [ ] **Step 3: Criar `core/portfolio/capture.py`**

```python
"""Gancho de captura de snapshot chamado ao salvar uma carteira-modelo.

Contrato central: NUNCA levanta excecao. Persistir o snapshot e um beneficio
adicional; falhar nele nao pode impedir o salvamento da carteira, que e a
funcionalidade que ja existia e continua sendo a prioridade.

Coberto por tests/test_portfolio_capture.py.
"""
from __future__ import annotations

import datetime as dt
import logging

from core.portfolio.registry import load_adapter
from core.portfolio.repository import apply_retention, prune_orphans, save_snapshots

logger = logging.getLogger(__name__)


def capture_snapshots(asset_class: str, model_id: str, items: list[dict],
                      params: dict, *, as_of: dt.date | None = None,
                      engine=None, owner_id=None) -> int:
    """Monta e grava os snapshots da carteira. Devolve quantos gravou (0 em falha)."""
    if not items:
        return 0

    gravados = 0
    try:
        adapter = load_adapter(asset_class)
        snapshots = adapter.build_snapshots(
            items,
            model_id=model_id,
            params=params or {},
            as_of=as_of or dt.date.today(),
        )
        gravados = save_snapshots(snapshots, engine=engine, owner_id=owner_id)
    except Exception:
        logger.warning("Falha ao capturar snapshot da carteira %s/%s; "
                       "a carteira foi salva normalmente.",
                       asset_class, model_id, exc_info=True)
        return 0

    # Limpeza e retencao sao oportunistas: falhar aqui nao invalida a gravacao.
    try:
        prune_orphans(engine=engine)
        apply_retention(asset_class, engine=engine)
    except Exception:
        logger.warning("Falha na manutencao de snapshots (%s).", asset_class, exc_info=True)

    return gravados
```

- [ ] **Step 4: Ligar o gancho em `core/b3_portfolio_model.py`**

Localizar, no final de `save_b3_portfolio_model`, as duas linhas:

```python
    load_active_b3_portfolio_model.clear()
    return model_id
```

Substituir por:

```python
    # Persiste o snapshot analitico (fundamentos, historico, premissas) da
    # carteira. Aditivo: capture_snapshots nunca levanta excecao, entao uma
    # falha aqui deixa o salvamento exatamente como era antes.
    from core.portfolio.capture import capture_snapshots
    capture_snapshots("b3", model_id, items, params, owner_id=owner)

    load_active_b3_portfolio_model.clear()
    return model_id
```

O import é local à função, e não no topo do módulo, para evitar ciclo: `core/portfolio/snapshots.py` importa `_clean_nan` de `core/b3_portfolio_model.py`.

- [ ] **Step 5: Ligar o gancho em `core/us_portfolio_model.py`**

Localizar o final de `save_us_portfolio_model`, imediatamente antes de `load_active_us_portfolio_model.clear()`, e inserir:

```python
    # Ver nota em core/b3_portfolio_model.py: captura aditiva, nunca bloqueante.
    from core.portfolio.capture import capture_snapshots
    capture_snapshots("us", model_id, items, params, owner_id=owner)
```

- [ ] **Step 6: Ligar o gancho em `core/fii_portfolio_model.py`**

Localizar o final de `save_fii_portfolio_model`, imediatamente antes da chamada que limpa os caches de portfólio, e inserir:

```python
    # Ver nota em core/b3_portfolio_model.py: captura aditiva, nunca bloqueante.
    from core.portfolio.capture import capture_snapshots
    capture_snapshots("fii", model_id, items, params, owner_id=owner)
```

- [ ] **Step 7: Rodar o teste e confirmar que passa**

Run: `python -m pytest tests/test_portfolio_capture.py -v`
Expected: 8 passed

- [ ] **Step 8: Confirmar que nada existente regrediu**

Run: `python -m pytest tests/test_b3_portfolio_model.py tests/test_fii_portfolio_model.py tests/test_us_module.py -v`
Expected: todos passam, mesmos resultados de antes da alteração.

- [ ] **Step 9: Commit**

```bash
git add core/portfolio/capture.py core/b3_portfolio_model.py core/us_portfolio_model.py core/fii_portfolio_model.py tests/test_portfolio_capture.py
git commit -m "feat(portfolio): captura de snapshot ao salvar carteira B3, EUA e FII"
```

---

### Task 10: Script de backfill das carteiras já salvas

**Files:**
- Create: `scripts/backfill_portfolio_snapshots.py`
- Test: `tests/test_backfill_portfolio_snapshots.py`

**Interfaces:**
- Consumes: `core.portfolio.registry.SPECS`, `get_spec`, `load_adapter`; `core.portfolio.repository.save_snapshots`; `core.database.get_engine`.
- Produces:
  - `read_model_items(asset_class: str, model_id: str, *, engine) -> list[dict]`
  - `active_models(asset_class: str, *, engine, owner_id: str) -> list[dict]` — cada dict com `id` e `params_json`.
  - `backfill(*, engine, owner_id, apply: bool, classes=None) -> dict[str, int]` — classe → número de snapshots que seriam gravados (ou foram, com `apply=True`).
  - `main(argv=None) -> int` — CLI com `--apply` e `--classe`.

Segue o padrão do projeto: simulação por padrão, gravação apenas com `--apply`. Os payloads gerados levam `provenance.backfilled = True`, porque o dado lido é o de hoje, não o da data da seleção.

- [ ] **Step 1: Escrever o teste que falha**

```python
"""Backfill de snapshots das carteiras ja salvas."""
import datetime as dt

import pytest
from sqlalchemy import create_engine, text

from scripts import backfill_portfolio_snapshots as bf

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
            CREATE TABLE b3_portfolio_models (
                id TEXT PRIMARY KEY, user_id TEXT, status TEXT, params_json TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE b3_portfolio_model_items (
                model_id TEXT, ticker TEXT, nome TEXT, setor TEXT, subsetor TEXT,
                segmento TEXT, weight REAL, score REAL, alpha_selic REAL, alpha_ew REAL,
                rank_score INTEGER, ano_lider INTEGER
            )
        """))
        conn.execute(text("INSERT INTO b3_portfolio_models (id, user_id, status, params_json) "
                          "VALUES ('m01', :u, 'active', '{\"top_n\": 2}')"), {"u": OWNER})
        for tk, nome, peso in [("PETR4", "Petrobras", 0.6), ("VALE3", "Vale", 0.4)]:
            conn.execute(
                text("INSERT INTO b3_portfolio_model_items "
                     "(model_id, ticker, nome, weight, score) VALUES ('m01', :t, :n, :w, 70)"),
                {"t": tk, "n": nome, "w": peso},
            )
    return eng


def test_le_os_itens_do_modelo(engine):
    itens = bf.read_model_items("b3", "m01", engine=engine)
    assert [i["ticker"] for i in itens] == ["PETR4", "VALE3"]
    assert itens[0]["nome"] == "Petrobras"


def test_lista_apenas_o_modelo_ativo_do_dono(engine):
    modelos = bf.active_models("b3", engine=engine, owner_id=OWNER)
    assert [m["id"] for m in modelos] == ["m01"]
    assert modelos[0]["params_json"]["top_n"] == 2


def test_simulacao_nao_grava_nada(engine, monkeypatch):
    monkeypatch.setattr(bf, "load_adapter", lambda key: _FakeAdapter)
    resumo = bf.backfill(engine=engine, owner_id=OWNER, apply=False, classes=["b3"])
    assert resumo["b3"] == 2

    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM portfolio_asset_snapshots")).scalar() == 0


def test_apply_grava_e_marca_backfilled(engine, monkeypatch):
    monkeypatch.setattr(bf, "load_adapter", lambda key: _FakeAdapter)
    resumo = bf.backfill(engine=engine, owner_id=OWNER, apply=True, classes=["b3"])
    assert resumo["b3"] == 2

    from core.portfolio.repository import load_snapshots
    lidos = load_snapshots("b3", "m01", engine=engine)
    assert set(lidos) == {"PETR4", "VALE3"}
    assert lidos["PETR4"]["provenance"]["backfilled"] is True


def test_classe_sem_carteira_nao_quebra(engine, monkeypatch):
    monkeypatch.setattr(bf, "load_adapter", lambda key: _FakeAdapter)
    resumo = bf.backfill(engine=engine, owner_id=OWNER, apply=False, classes=["b3", "us"])
    assert resumo["us"] == 0


class _FakeAdapter:
    """Adaptador sem acesso a rede: monta payload minimo a partir do item."""

    @staticmethod
    def build_snapshots(items, *, model_id, params, as_of, loaders=None):
        from core.portfolio.models import AssetSnapshot
        return [
            AssetSnapshot.from_blocks(
                asset_class="b3", model_id=model_id, symbol=i["ticker"],
                as_of_date=as_of,
                blocks={"identity": {"symbol": i["ticker"]},
                        "provenance": {"backfilled": True}},
            )
            for i in items
        ]
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `python -m pytest tests/test_backfill_portfolio_snapshots.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'scripts.backfill_portfolio_snapshots'`

Se falhar antes com `No module named 'scripts'`, criar `scripts/__init__.py` vazio.

- [ ] **Step 3: Escrever o script**

```python
"""Reconstroi o snapshot analitico das carteiras-modelo ja salvas.

Simulacao por padrao; grava somente com --apply (padrao dos scripts do projeto).

LIMITE CONHECIDO: as vintages point-in-time em market.calculated_metric_vintages
sao hoje praticamente todas baseline, entao o backfill grava o valor ATUAL, nao
o da data da selecao. Por isso todo payload gerado aqui leva
provenance.backfilled = True. As gravacoes feitas a partir de agora, pelo
gancho em core/portfolio/capture.py, capturam o valor correto no momento certo.

Uso:
    python -m scripts.backfill_portfolio_snapshots
    python -m scripts.backfill_portfolio_snapshots --apply
    python -m scripts.backfill_portfolio_snapshots --apply --classe b3
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from sqlalchemy import text

from core.portfolio.registry import SPECS, get_spec, load_adapter
from core.portfolio.repository import save_snapshots


def _parse_json(valor, default):
    if isinstance(valor, dict):
        return valor
    if not valor:
        return default
    try:
        return json.loads(valor)
    except (TypeError, ValueError):
        return default


def active_models(asset_class: str, *, engine, owner_id: str) -> list[dict]:
    """Modelos ativos do dono para a classe. Lista vazia se a tabela nao existir."""
    spec = get_spec(asset_class)
    try:
        with engine.connect() as conn:
            linhas = conn.execute(
                text(f"""
                    SELECT id, params_json FROM {spec.models_table}
                    WHERE user_id = :uid AND status = 'active'
                    ORDER BY created_at DESC, id DESC
                """),
                {"uid": str(owner_id)},
            ).mappings().all()
    except Exception:
        return []
    return [{"id": str(l["id"]), "params_json": _parse_json(l["params_json"], {})}
            for l in linhas]


def read_model_items(asset_class: str, model_id: str, *, engine) -> list[dict]:
    """Itens gravados do modelo, na ordem de peso decrescente."""
    spec = get_spec(asset_class)
    with engine.connect() as conn:
        linhas = conn.execute(
            text(f"""
                SELECT * FROM {spec.items_table}
                WHERE model_id = :mid
                ORDER BY weight DESC, {spec.symbol_column}
            """),
            {"mid": str(model_id)},
        ).mappings().all()
    return [dict(l) for l in linhas]


def backfill(*, engine, owner_id: str, apply: bool,
             classes: list[str] | None = None) -> dict[str, int]:
    """Reconstroi os snapshots. Devolve {classe: quantidade}."""
    alvo = sorted(classes) if classes else sorted(SPECS)
    resumo: dict[str, int] = {}
    hoje = dt.date.today()

    for key in alvo:
        total = 0
        for modelo in active_models(key, engine=engine, owner_id=owner_id):
            itens = read_model_items(key, modelo["id"], engine=engine)
            if not itens:
                continue
            snapshots = load_adapter(key).build_snapshots(
                itens, model_id=modelo["id"], params=modelo["params_json"], as_of=hoje,
            )
            for snap in snapshots:
                snap.payload["provenance"]["backfilled"] = True
            if apply:
                save_snapshots(snapshots, engine=engine, owner_id=owner_id)
            total += len(snapshots)
        resumo[key] = total
    return resumo


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Backfill de snapshots das carteiras-modelo.")
    parser.add_argument("--apply", action="store_true",
                        help="grava de fato (sem esta flag, apenas simula)")
    parser.add_argument("--classe", action="append", choices=sorted(SPECS),
                        help="limita a uma ou mais classes; pode repetir")
    args = parser.parse_args(argv)

    from core.config import settings
    from core.database import get_engine

    engine = get_engine()
    if engine is None:
        print("Banco unificado nao configurado (DATABASE_URL ausente).", file=sys.stderr)
        return 2
    if not settings.OWNER_USER_ID:
        print("OWNER_USER_ID nao configurado.", file=sys.stderr)
        return 2

    resumo = backfill(engine=engine, owner_id=str(settings.OWNER_USER_ID),
                      apply=args.apply, classes=args.classe)

    modo = "GRAVADO" if args.apply else "SIMULACAO (use --apply para gravar)"
    print(f"[{modo}]")
    for classe in sorted(resumo):
        print(f"  {classe:>4}: {resumo[classe]} snapshots")
    print("  Payloads marcados com provenance.backfilled = True "
          "(valor de hoje, nao da data da selecao).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `python -m pytest tests/test_backfill_portfolio_snapshots.py -v`
Expected: 5 passed

- [ ] **Step 5: Rodar a suíte completa da fase**

Run: `python -m pytest tests/test_portfolio_snapshots_schema.py tests/test_portfolio_snapshots_payload.py tests/test_portfolio_models.py tests/test_portfolio_registry.py tests/test_portfolio_repository.py tests/test_portfolio_adapter_frames.py tests/test_portfolio_adapter_b3.py tests/test_portfolio_adapter_us.py tests/test_portfolio_adapter_fii.py tests/test_portfolio_capture.py tests/test_backfill_portfolio_snapshots.py -v`
Expected: 87 passed (7 + 9 + 6 + 11 + 8 + 6 + 10 + 9 + 8 + 8 + 5)

- [ ] **Step 6: Rodar a suíte inteira do repositório para confirmar ausência de regressão**

Run: `python -m pytest tests/ -q --tb=no`
Expected: `1518 passed, 3 skipped` (baseline de 1431 + os 87 desta fase), zero falhas.

- [ ] **Step 7: Commit**

```bash
git add scripts/backfill_portfolio_snapshots.py tests/test_backfill_portfolio_snapshots.py
git commit -m "feat(portfolio): script de backfill dos snapshots das carteiras ja salvas"
```

---

## Aplicação em produção

Depois do merge na `main`:

1. Executar `supabase_unificado/schema/049_portfolio_asset_snapshots.sql` no Supabase.
2. Simular o backfill: `python -m scripts.backfill_portfolio_snapshots`
3. Conferir a contagem por classe e então aplicar: `python -m scripts.backfill_portfolio_snapshots --apply`
4. Salvar uma carteira nova em qualquer das três seções e confirmar, no Supabase, que a linha correspondente tem `provenance.backfilled = false`.

## Auto-revisão deste plano

**Cobertura da spec (Fase 1, seções 4 e 5 do design):**

| Requisito da spec | Task |
|---|---|
| §5.1 schema das duas tabelas | 1 |
| §5.2 poda de órfãos compensando a ausência de FK | 5 |
| §5.3 payload com os nove blocos e teto de tamanho | 2, 6, 7, 8 |
| §5.4 retenção de 5 arquivadas | 5 |
| §5.5 backfill com `backfilled = true` | 10 |
| §5.6 compatibilidade e degradação | 9 |
| §4 estrutura de módulos do pacote `core/portfolio/` | 2, 3, 4, 5, 6, 7, 8, 9 |

Fora do escopo desta fase, por decisão registrada na spec: `core/global_portfolio/`, `views/portfolio_global.py`, `llm_*`, `etl/bcb_sgs.py` e a deduplicação dos três `*_portfolio_model.py` (Fase 5, opcional).

**Consistência de nomes verificada:** `build_snapshots` tem assinatura idêntica nas Tasks 6, 7 e 8 e é chamada com os mesmos argumentos nas Tasks 9 e 10. `save_snapshots`, `load_snapshots`, `prune_orphans` e `apply_retention` (Task 5) são consumidos com as mesmas assinaturas nas Tasks 9 e 10. `AssetSnapshot.from_blocks` (Task 3) é usado com os mesmos parâmetros nomeados nas Tasks 6, 7, 8 e nos testes da Task 10. `build_payload` e `payload_digest` (Task 2) alimentam a Task 3.

**Ponto de atenção para quem executar a Task 9:** `core/portfolio/snapshots.py` importa `_clean_nan` de `core/b3_portfolio_model.py`, e `core/b3_portfolio_model.py` passa a importar `core.portfolio.capture`. O ciclo só não ocorre porque o import na Task 9 é **local à função**, não no topo do módulo. Não promover esse import para o topo.
