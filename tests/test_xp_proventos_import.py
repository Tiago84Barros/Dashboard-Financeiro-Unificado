from __future__ import annotations

import io
from contextlib import nullcontext
from datetime import date

import openpyxl
from sqlalchemy import create_engine, text

from data_pipeline.importers.investments import common
from data_pipeline.importers.investments import xp_consolidado as xp


def _sqlite_dividends_engine():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE dividends (
                id TEXT PRIMARY KEY DEFAULT (
                    lower(hex(randomblob(16)))
                ),
                user_id TEXT NOT NULL,
                asset_id TEXT NOT NULL,
                type TEXT NOT NULL,
                amount_per_unit NUMERIC NOT NULL,
                quantity NUMERIC NOT NULL,
                total_amount NUMERIC NOT NULL,
                ex_date DATE,
                payment_date DATE NOT NULL,
                external_id TEXT
            )
        """))
        conn.execute(text("""
            CREATE UNIQUE INDEX ux_dividends_external_id
            ON dividends(external_id)
            WHERE external_id IS NOT NULL
        """))
    return engine


def test_insert_dividend_deduplica_fontes_diferentes():
    engine = _sqlite_dividends_engine()
    params = {
        "user_id": "user-1",
        "asset_id": "asset-1",
        "div_type": "reit_income",
        "amount_per_unit": 35.20,
        "quantity": 1.0,
        "total_amount": 35.20,
        "ex_date": None,
        "payment_date": date(2026, 6, 12),
    }

    with engine.begin() as conn:
        first = common.insert_dividend(
            conn, external_id="b3mov-abc", **params,
        )
        duplicate = common.insert_dividend(
            conn, external_id="xpcsl-inc-xyz", **params,
        )
        count = conn.execute(text("SELECT COUNT(*) FROM dividends")).scalar()

    assert first is not None
    assert duplicate is None
    assert count == 1


def test_batch_insert_dividends_deduplica_chave_canonica():
    engine = _sqlite_dividends_engine()
    base = {
        "user_id": "user-1",
        "asset_id": "asset-1",
        "type": "reit_income",
        "amount_per_unit": 35.20,
        "quantity": 1.0,
        "total_amount": 35.20,
        "ex_date": None,
        "payment_date": date(2026, 6, 12),
    }
    rows = [
        {**base, "external_id": "b3mov-abc"},
        {**base, "external_id": "xpcsl-inc-xyz"},
    ]

    with engine.begin() as conn:
        common.batch_insert_dividends(conn, rows)
        count = conn.execute(text("SELECT COUNT(*) FROM dividends")).scalar()

    assert count == 1


class _FakeConnection:
    def begin(self):
        return nullcontext()

    def begin_nested(self):
        return nullcontext()


class _FakeEngine:
    def __init__(self):
        self.connection = _FakeConnection()

    def connect(self):
        return nullcontext(self.connection)


def _xp_workbook_bytes() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Proventos Recebidos"
    ws.append([
        "Produto", "Pagamento", "Tipo de Evento", "Instituição",
        "Quantidade", "Preço unitário", "Valor líquido",
    ])
    ws.append([
        "KNCR11 - KINEA RENDIMENTOS", "12/06/2026", "Rendimento",
        "XP INVESTIMENTOS", 32, 1.10, 35.20,
    ])
    ws.append([
        "CSMG3 - COPASA", "30/06/2026", "Juros Sobre Capital Próprio",
        "XP INVESTIMENTOS", 53, 0.36, 16.40,
    ])
    payload = io.BytesIO()
    wb.save(payload)
    return payload.getvalue()


def test_importador_xp_processa_aba_proventos(monkeypatch):
    inserted = []

    monkeypatch.setattr(xp.settings, "OWNER_USER_ID", "user-1")
    monkeypatch.setattr(xp, "ensure_external_id_columns", lambda engine: None)
    monkeypatch.setattr(xp, "_ensure_portfolio", lambda conn, uid: "portfolio-1")
    monkeypatch.setattr(xp, "_ensure_xp_account", lambda conn, uid: "account-1")
    monkeypatch.setattr(
        xp, "get_or_create_asset",
        lambda conn, ticker, name, asset_class: f"asset-{ticker}",
    )

    def _capture_insert(conn, **kwargs):
        inserted.append(kwargs)
        return f"dividend-{len(inserted)}"

    monkeypatch.setattr(xp, "insert_dividend", _capture_insert)

    summary = xp._parse_single(
        (
            "relatorio-consolidado-mensal-2026-junho.xlsx",
            _xp_workbook_bytes(),
        ),
        _FakeEngine(),
    )

    assert summary["status"] == "success"
    assert summary["incomes_imported"] == 2
    assert [row["payment_date"] for row in inserted] == [
        date(2026, 6, 12),
        date(2026, 6, 30),
    ]
    assert [row["total_amount"] for row in inserted] == [35.20, 16.40]
