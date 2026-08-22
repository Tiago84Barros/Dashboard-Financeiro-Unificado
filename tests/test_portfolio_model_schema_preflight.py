"""Contrato: persistência de carteiras não pode alterar schema em runtime."""
from __future__ import annotations

import inspect
import re
import uuid

import pytest

from core import b3_portfolio_model, fii_portfolio_model, us_portfolio_model
from core.portfolio import capture as portfolio_capture

_DDL = re.compile(r"\b(?:CREATE|ALTER|DROP|DO)\b", re.IGNORECASE)


class _Result:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = list(rows or [])

    def mappings(self):
        return self

    def one(self):
        return self._row

    def fetchone(self):
        return self._row

    def all(self):
        return self._rows

    def scalars(self):
        return iter(
            row[0] if isinstance(row, tuple) and len(row) == 1 else row
            for row in self._rows
        )

    def __iter__(self):
        return iter(self._rows)


class _SqlCapture:
    def __init__(self, row, rows=None):
        self.row = row
        self.rows = rows or []
        self.statements = []

    def execute(self, statement, _parameters=None):
        self.statements.append(str(statement))
        return _Result(self.row, self.rows)


class _Transaction:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, *_exc):
        return False


class _Engine:
    def __init__(self, conn):
        self.conn = conn

    def begin(self):
        return _Transaction(self.conn)


def _assert_no_runtime_ddl(conn):
    sql = "\n".join(conn.statements)
    assert not _DDL.search(sql), sql


@pytest.mark.parametrize(
    ("module", "tables", "migration"),
    [
        (b3_portfolio_model, ("b3_portfolio_models", "b3_portfolio_model_items"),
         "011_b3_portfolio_models.sql"),
        (fii_portfolio_model, ("fii_portfolio_models", "fii_portfolio_model_items"),
         "018_fiis_hardening.sql"),
        (us_portfolio_model, ("us_portfolio_models", "us_portfolio_model_items"),
         "047_us_portfolio_models.sql"),
    ],
)
def test_preflight_consulta_catalogo_sem_ddl(module, tables, migration):
    row = {table: f"public.{table}" for table in tables}
    row.update({f"{table}_rls": True for table in tables})
    row.update({f"{table}_policy": True for table in tables})
    conn = _SqlCapture(row)

    module._preflight_schema(conn)

    sql = "\n".join(conn.statements).upper()
    assert "TO_REGCLASS" in sql
    assert "PG_CLASS" in sql
    assert "PG_POLICIES" in sql
    _assert_no_runtime_ddl(conn)


@pytest.mark.parametrize(
    ("module", "tables", "migration"),
    [
        (b3_portfolio_model, ("b3_portfolio_models", "b3_portfolio_model_items"),
         "011_b3_portfolio_models.sql"),
        (fii_portfolio_model, ("fii_portfolio_models", "fii_portfolio_model_items"),
         "018_fiis_hardening.sql"),
        (us_portfolio_model, ("us_portfolio_models", "us_portfolio_model_items"),
         "047_us_portfolio_models.sql"),
    ],
)
def test_preflight_falha_com_migration_acionavel(module, tables, migration):
    row = {tables[0]: f"public.{tables[0]}", tables[1]: None}
    row.update({f"{table}_rls": True for table in tables})
    row.update({f"{table}_policy": True for table in tables})
    conn = _SqlCapture(row)

    with pytest.raises(RuntimeError, match=migration) as exc_info:
        module._preflight_schema(conn)

    assert tables[1] in str(exc_info.value)


@pytest.mark.parametrize(
    ("module", "tables", "migration"),
    [
        (b3_portfolio_model, ("b3_portfolio_models", "b3_portfolio_model_items"),
         "018_rls_portfolio_models.sql"),
        (fii_portfolio_model, ("fii_portfolio_models", "fii_portfolio_model_items"),
         "018_fiis_hardening.sql"),
        (us_portfolio_model, ("us_portfolio_models", "us_portfolio_model_items"),
         "047_us_portfolio_models.sql"),
    ],
)
def test_preflight_falha_quando_rls_esta_desabilitado(module, tables, migration):
    row = {table: f"public.{table}" for table in tables}
    row.update({f"{table}_rls": True for table in tables})
    row.update({f"{table}_policy": True for table in tables})
    row[f"{tables[1]}_rls"] = False

    with pytest.raises(RuntimeError, match=migration) as exc_info:
        module._preflight_schema(_SqlCapture(row))

    assert "RLS desabilitado" in str(exc_info.value)
    assert tables[1] in str(exc_info.value)


@pytest.mark.parametrize(
    ("module", "tables", "migration"),
    [
        (b3_portfolio_model, ("b3_portfolio_models", "b3_portfolio_model_items"),
         "018_rls_portfolio_models.sql"),
        (fii_portfolio_model, ("fii_portfolio_models", "fii_portfolio_model_items"),
         "018_fiis_hardening.sql"),
        (us_portfolio_model, ("us_portfolio_models", "us_portfolio_model_items"),
         "047_us_portfolio_models.sql"),
    ],
)
def test_preflight_falha_quando_policy_do_dono_esta_ausente(module, tables, migration):
    row = {table: f"public.{table}" for table in tables}
    row.update({f"{table}_rls": True for table in tables})
    row.update({f"{table}_policy": True for table in tables})
    row[f"{tables[0]}_policy"] = False

    with pytest.raises(RuntimeError, match=migration) as exc_info:
        module._preflight_schema(_SqlCapture(row))

    assert "policy de dono ausente" in str(exc_info.value)
    assert tables[0] in str(exc_info.value)


@pytest.mark.parametrize(
    ("module", "expected"),
    [
        (b3_portfolio_model, {
            "b3_portfolio_models": "b3_portfolio_models_owner_all",
            "b3_portfolio_model_items": "b3_portfolio_model_items_owner_all",
        }),
        (fii_portfolio_model, {
            "fii_portfolio_models": "fii_models_owner_all",
            "fii_portfolio_model_items": "fii_model_items_owner_all",
        }),
        (us_portfolio_model, {
            "us_portfolio_models": "us_portfolio_models_owner_all",
            "us_portfolio_model_items": "us_portfolio_model_items_owner_all",
        }),
    ],
)
def test_preflight_exige_as_policies_de_dono_declaradas_nas_migrations(module, expected):
    assert module._OWNER_POLICIES == expected


@pytest.mark.parametrize(
    "module",
    [b3_portfolio_model, fii_portfolio_model, us_portfolio_model],
)
def test_modulo_de_persistencia_nao_contem_ddl_runtime(module):
    source = inspect.getsource(module).upper()

    assert "CREATE TABLE" not in source
    assert "ALTER TABLE" not in source
    assert "CREATE POLICY" not in source
    assert "DO $$" not in source


@pytest.mark.parametrize(
    ("module", "save", "items"),
    [
        (b3_portfolio_model, "save_b3_portfolio_model", [{"ticker": "PETR4", "weight": 1}]),
        (fii_portfolio_model, "save_fii_portfolio_model", [{"ticker": "HGLG11", "weight": 1}]),
        (us_portfolio_model, "save_us_portfolio_model", [{"symbol": "MSFT", "weight": 1}]),
    ],
)
def test_salvamento_com_schema_existente_nao_executa_ddl_runtime(
    monkeypatch, module, save, items
):
    tables = module._REQUIRED_TABLES
    row = {table: f"public.{table}" for table in tables}
    row.update({f"{table}_rls": True for table in tables})
    row.update({f"{table}_policy": True for table in tables})
    conn = _SqlCapture(row)
    monkeypatch.setattr(module, "get_engine", lambda: _Engine(conn))
    monkeypatch.setattr(module, "_owner_id", lambda: "owner-sintetico")
    if module is fii_portfolio_model:
        weights = {"HGLG11": 1.0}
        model = {
            "id": "modelo-sintetico", "status": "active", "params_json": {},
            "items": [{"ticker": "HGLG11", "weight": 1.0}],
        }
        model["plan_hash"] = module._fii_plan_hash(model["items"], {}, weights)
        monkeypatch.setattr(module, "_model_from_connection", lambda *_a, **_k: model)
    else:
        monkeypatch.setattr(
            portfolio_capture, "capture_snapshots", lambda *_a, **_k: None
        )

    model_id = getattr(module, save)(items, params={"origem": "teste-sintetico"})

    assert uuid.UUID(model_id)
    _assert_no_runtime_ddl(conn)


def test_restauracao_fii_com_schema_existente_nao_executa_ddl_runtime(monkeypatch):
    target_id = str(uuid.uuid4())
    tables = fii_portfolio_model._REQUIRED_TABLES
    row = {table: f"public.{table}" for table in tables}
    row.update({f"{table}_rls": True for table in tables})
    row.update({f"{table}_policy": True for table in tables})
    conn = _SqlCapture(row, rows=[(target_id,)])
    model = {
        "id": target_id, "status": "archived", "params_json": {},
        "items": [{"ticker": "HGLG11", "weight": 1.0}],
    }
    model["plan_hash"] = fii_portfolio_model._fii_plan_hash(
        model["items"], {}, {"HGLG11": 1.0}
    )
    monkeypatch.setattr(fii_portfolio_model, "get_engine", lambda: _Engine(conn))
    monkeypatch.setattr(fii_portfolio_model, "_owner_id", lambda: "owner-sintetico")
    restored_model = {**model, "status": "active"}
    models = iter((model, restored_model))
    monkeypatch.setattr(fii_portfolio_model, "_model_from_connection", lambda *_a, **_k: next(models))

    assert fii_portfolio_model.restore_fii_portfolio_model(target_id) == target_id
    _assert_no_runtime_ddl(conn)


@pytest.mark.parametrize(
    ("module", "restore", "validate", "items"),
    [
        (b3_portfolio_model, "restore_b3_portfolio_model",
         "validate_b3_portfolio_model", [{"ticker": "PETR4", "weight": 1.0}]),
        (us_portfolio_model, "restore_us_portfolio_model",
         "validate_us_portfolio_model", [{"symbol": "MSFT", "weight": 1.0}]),
    ],
)
def test_restauracao_b3_e_us_com_schema_existente_nao_executa_ddl_runtime(
    monkeypatch, module, restore, validate, items,
):
    """Regressao do achado A-006: B3 e EUA ganharam o mesmo par
    restore/list_versions que FIIs ja tinha; a restauracao precisa continuar
    sem DDL em runtime, igual ao caso ja coberto para FIIs.
    """
    target_id = str(uuid.uuid4())
    tables = module._REQUIRED_TABLES
    row = {table: f"public.{table}" for table in tables}
    row.update({f"{table}_rls": True for table in tables})
    row.update({f"{table}_policy": True for table in tables})
    conn = _SqlCapture(row, rows=[(target_id,)])
    model = {"id": target_id, "status": "archived", "params_json": {}, "items": items}
    monkeypatch.setattr(module, "get_engine", lambda: _Engine(conn))
    monkeypatch.setattr(module, "_owner_id", lambda: "owner-sintetico")
    restored_model = {**model, "status": "active"}
    models = iter((model, restored_model))
    monkeypatch.setattr(module, "_model_from_connection", lambda *_a, **_k: next(models))

    assert getattr(module, restore)(target_id) == target_id
    _assert_no_runtime_ddl(conn)


@pytest.mark.parametrize(
    ("module", "restore"),
    [
        (b3_portfolio_model, "restore_b3_portfolio_model"),
        (us_portfolio_model, "restore_us_portfolio_model"),
    ],
)
def test_restauracao_b3_e_us_rejeita_versao_de_outro_dono(monkeypatch, module, restore):
    target_id = str(uuid.uuid4())
    tables = module._REQUIRED_TABLES
    row = {table: f"public.{table}" for table in tables}
    row.update({f"{table}_rls": True for table in tables})
    row.update({f"{table}_policy": True for table in tables})
    # rows vazio: FOR UPDATE nao devolve o id -> nao pertence ao dono
    conn = _SqlCapture(row, rows=[])
    monkeypatch.setattr(module, "get_engine", lambda: _Engine(conn))
    monkeypatch.setattr(module, "_owner_id", lambda: "owner-sintetico")

    with pytest.raises(ValueError, match="outro usuário"):
        getattr(module, restore)(target_id)


@pytest.mark.parametrize(
    ("module", "validate", "items"),
    [
        (b3_portfolio_model, "validate_b3_portfolio_model",
         [{"ticker": "PETR4", "weight": 0.6}, {"ticker": "VALE3", "weight": 0.4}]),
        (us_portfolio_model, "validate_us_portfolio_model",
         [{"symbol": "MSFT", "weight": 0.6}, {"symbol": "AAPL", "weight": 0.4}]),
    ],
)
def test_validate_b3_e_us_aceita_pesos_completos_e_rejeita_duplicados(
    module, validate, items,
):
    ok_model = {"items": items}
    assert getattr(module, validate)(ok_model)["ok"] is True

    key = "ticker" if module is b3_portfolio_model else "symbol"
    dup_model = {"items": [
        {key: items[0][key], "weight": 0.5},
        {key: items[0][key], "weight": 0.5},
    ]}
    result = getattr(module, validate)(dup_model)
    assert result["ok"] is False
    assert any("duplicad" in reason for reason in result["reasons"])
