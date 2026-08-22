import inspect
from contextlib import contextmanager
from datetime import date

import pandas as pd
from sqlalchemy import create_engine, text

import etl.importacao as importacao
from core.bank_statement_import import (
    import_bank_statement_pdf,
    import_bank_statement_rows,
)
from core.controle import importar_fatura_cartao_csv
from core.import_guard import (
    build_import_lock_key,
    import_payload_digest,
    serialized_import,
)
from etl.importacao import (
    ResultadoImportacao,
    _assign_deterministic_ids,
    _inserir_em_lote,
)
from views.configuracoes import _executar_importacao_investimento


def test_payload_digest_is_stable_for_same_files_in_any_selection_order():
    files_a = [("b.xlsx", b"dois"), ("a.xlsx", b"um")]
    files_b = [("a.xlsx", b"um"), ("b.xlsx", b"dois")]

    assert import_payload_digest(files_a) == import_payload_digest(files_b)
    assert build_import_lock_key("xp", files_a) == build_import_lock_key(
        "xp", files_b
    )


def test_generic_import_ids_are_stable_and_content_sensitive():
    original = [
        {
            "id": "aleatorio-1",
            "usuario_id": "user-1",
            "descricao": "Mercado",
            "valor": -100.0,
            "data_competencia": date(2026, 7, 1),
        },
        {
            "id": "aleatorio-2",
            "usuario_id": "user-1",
            "descricao": "Mercado",
            "valor": -100.0,
            "data_competencia": date(2026, 7, 1),
        },
    ]
    repeated = [dict(row) for row in original]

    _assign_deterministic_ids("transacoes", original)
    _assign_deterministic_ids("transacoes", repeated)

    assert [row["id"] for row in original] == [row["id"] for row in repeated]
    assert original[0]["id"] != original[1]["id"]

    changed = [dict(original[0], valor=-101.0)]
    _assign_deterministic_ids("transacoes", changed)
    assert changed[0]["id"] != original[0]["id"]


def test_generic_batch_locks_before_insert_and_reports_conflicts():
    events = []

    class FakeResult:
        def __init__(self, rowcount=0):
            self.rowcount = rowcount

    class FakeConnection:
        def execute(self, statement, params=None):
            sql = str(statement)
            if "pg_advisory_xact_lock" in sql:
                events.append("lock")
                return FakeResult()
            if "INSERT INTO" in sql:
                events.append("insert")
                return FakeResult(rowcount=0)
            raise AssertionError(f"SQL inesperado: {sql}")

    class FakeEngine:
        @contextmanager
        def begin(self):
            yield FakeConnection()

    records = [
        {"id": "id-1", "descricao": "Mercado", "valor": -100.0},
        {"id": "id-2", "descricao": "Farmácia", "valor": -50.0},
    ]
    result = ResultadoImportacao(
        fonte="CSV",
        tabela_destino="transacoes",
        dry_run=False,
    )

    _inserir_em_lote(
        FakeEngine(),
        "transacoes",
        ["id", "descricao", "valor"],
        records,
        result,
    )

    assert events == ["lock", "insert"]
    assert result.total_inseridos == 0
    assert result.total_ignorados == 2


def test_csv_transactions_reimport_is_idempotent(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE transacoes (
                id TEXT PRIMARY KEY,
                usuario_id TEXT NOT NULL,
                conta_id TEXT NOT NULL,
                categoria_id TEXT,
                descricao TEXT NOT NULL,
                valor REAL NOT NULL,
                data_competencia DATE NOT NULL,
                data_pagamento DATE,
                tipo TEXT NOT NULL,
                status TEXT NOT NULL,
                recorrente BOOLEAN NOT NULL,
                origem TEXT NOT NULL
            )
        """))
    monkeypatch.setattr(importacao, "get_engine", lambda: engine)
    frame = pd.DataFrame([
        {
            "descricao": "Mercado",
            "valor": -100.0,
            "data_competencia": "2026-07-01",
            "tipo": "despesa",
        },
        {
            "descricao": "Salário",
            "valor": 5000.0,
            "data_competencia": "2026-07-05",
            "tipo": "receita",
        },
    ])
    importer = importacao.ImportadorCSV()

    first = importer.importar_transacoes(
        frame, "user-1", "account-1", dry_run=False
    )
    second = importer.importar_transacoes(
        frame, "user-1", "account-1", dry_run=False
    )

    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM transacoes")).scalar()
    assert first.total_inseridos == 2
    assert second.total_inseridos == 0
    assert second.total_ignorados == 2
    assert count == 2


def test_serialized_import_holds_and_releases_session_lock():
    events = []

    class FakeConnection:
        def execute(self, statement, params=None):
            sql = str(statement)
            if "pg_advisory_unlock" in sql:
                events.append("unlock")
            elif "pg_advisory_lock" in sql:
                events.append("lock")

    class FakeEngine:
        @contextmanager
        def connect(self):
            yield FakeConnection()

    with serialized_import(FakeEngine(), "investment", "owner", b"file"):
        events.append("body")

    assert events == ["lock", "body", "unlock"]


def test_every_settings_import_entry_point_uses_a_concurrency_guard():
    guarded_transaction_imports = (
        importar_fatura_cartao_csv,
        import_bank_statement_pdf,
        import_bank_statement_rows,
        _inserir_em_lote,
    )
    for function in guarded_transaction_imports:
        assert "acquire_transaction_import_lock" in inspect.getsource(function)

    assert "serialized_import" in inspect.getsource(
        _executar_importacao_investimento
    )
