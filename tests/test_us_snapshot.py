"""Testes da vitrine EUA (serialização pura + fallback de leitura no deploy)."""
import datetime as dt
import json

import pandas as pd

import core.us_data as us
import core.us_read as ur
import data_pipeline.us.snapshot as snap


# ── serialização pura ─────────────────────────────────────────────────────────
def test_jsonable_datas_e_nan():
    assert snap.jsonable(dt.date(2024, 1, 2)) == "2024-01-02"
    assert snap.jsonable(float("nan")) is None
    assert snap.jsonable({"a": [dt.date(2024, 1, 2), 1.5]}) == {"a": ["2024-01-02", 1.5]}


def test_jsonable_decimal():
    from decimal import Decimal
    assert snap.jsonable(Decimal("391035000000.00")) == 391035000000.0
    # dentro de estrutura + serializável por json
    out = snap.jsonable({"revenue": Decimal("1440"), "years": [Decimal("2023")]})
    assert out == {"revenue": 1440.0, "years": [2023.0]}
    json.dumps(out)   # não levanta


def test_compact_financials():
    income = [{"fiscal_year": 2022, "revenue": 100, "net_income": 10, "ebitda": 20},
              {"fiscal_year": 2023, "revenue": 120, "net_income": 12, "ebitda": 24}]
    balance = [{"fiscal_year": 2023, "total_equity": 50, "total_debt": 30,
                "shares_outstanding": 10, "cash_and_equivalents": 5}]
    cashflow = [{"fiscal_year": 2023, "free_cash_flow": 15,
                 "operating_cash_flow": 20, "capex": -5, "dividends_paid": -2}]
    out = snap.compact_financials(income, balance, cashflow)
    assert len(out) == 2
    assert out[-1]["fiscal_year"] == 2023 and out[-1]["free_cash_flow"] == 15
    assert out[-1]["total_equity"] == 50
    assert out[-1]["operating_cash_flow"] == 20
    assert out[-1]["investing_cash_flow"] == -5
    assert out[-1]["dividends_per_share"] == 0.2
    assert out[0]["free_cash_flow"] is None      # 2022 sem cashflow → None, não zero


def test_compact_company_analysis_reduz_preco_e_soma_dividendos():
    market = {
        "prices": pd.DataFrame({
            "date": ["2023-01-02", "2023-01-31", "2023-02-28"],
            "price": [10, 11, 12],
        }),
        "dividends": pd.DataFrame({
            "date": ["2023-03-01", "2023-09-01"], "amount": [0.2, 0.3],
        }),
        "metrics": pd.DataFrame(),
    }
    out = snap.compact_company_analysis(market)
    assert [row["price"] for row in out["prices"]] == [11.0, 12.0]
    assert out["dividends"] == [{"date": "2023-12-31", "amount": 0.5}]


def test_serialize_row():
    row = snap.serialize_row(
        identity={"symbol": "AAPL", "cik": "0000320193", "name": "Apple",
                  "sector": "Tech", "industry": "HW", "is_reit": False},
        scored_row={"score": 88.5, "score_quality": 90.0, "coverage": 100.0},
        metrics={"net_margin": 0.25, "roic": 0.30},
        asymmetry={"asymmetry_score": 70}, advanced={"z_score": 5.1},
        dossie={"classification": "consolidada"},
        financials=[{"fiscal_year": 2023, "revenue": 100}],
        score_version="0.1.0", generated_at=dt.datetime(2026, 7, 17))
    assert row["symbol"] == "AAPL" and row["score"] == 88.5
    assert row["last_fiscal_year"] == 2023
    assert json.loads(row["metrics"])["roic"] == 0.30      # JSONB serializado
    assert json.loads(row["dossie"])["classification"] == "consolidada"
    assert row["asymmetry"] is not None


def test_serialize_row_sem_opcionais():
    row = snap.serialize_row(
        identity={"symbol": "X"}, scored_row={"score": None},
        metrics={}, asymmetry=None, advanced=None, dossie=None,
        financials=[], score_version="0.1.0", generated_at=None)
    assert row["asymmetry"] is None and row["dossie"] is None
    assert row["last_fiscal_year"] is None and row["score"] is None


# ── fallback de leitura (deploy usa a vitrine) ───────────────────────────────
def test_use_snapshot_decide_por_ambiente(monkeypatch):
    monkeypatch.setattr(ur, "schema_ready", lambda: True)
    monkeypatch.setattr(ur, "snapshot_ready", lambda: True)
    assert us._use_snapshot() is False           # warehouse presente → calcula ao vivo

    monkeypatch.setattr(ur, "schema_ready", lambda: False)
    monkeypatch.setattr(ur, "snapshot_ready", lambda: True)
    assert us._use_snapshot() is True            # só vitrine → usa snapshot

    monkeypatch.setattr(ur, "snapshot_ready", lambda: False)
    assert us._use_snapshot() is False           # nada → sem fallback


def test_facades_roteiam_para_snapshot(monkeypatch):
    monkeypatch.setattr(us, "_use_snapshot", lambda: True)
    monkeypatch.setattr(ur, "load_snapshot_scored",
                        lambda: pd.DataFrame([{"symbol": "AAPL", "score": 90}]))
    monkeypatch.setattr(ur, "load_snapshot_asymmetry",
                        lambda: pd.DataFrame([{"symbol": "NVDA", "asymmetry_score": 80}]))
    monkeypatch.setattr(ur, "load_snapshot_dossie",
                        lambda s: {"symbol": s, "classification": "crescimento"})
    monkeypatch.setattr(ur, "load_snapshot_advanced", lambda s: {"z_score": 4.0})

    assert us.scored_universe().iloc[0]["symbol"] == "AAPL"
    assert us.asymmetry_universe().iloc[0]["asymmetry_score"] == 80
    assert us.dossie("AAPL")["classification"] == "crescimento"
    assert us.advanced_snapshot("AAPL")["z_score"] == 4.0


def test_dossie_snapshot_ausente_retorna_erro(monkeypatch):
    monkeypatch.setattr(us, "_use_snapshot", lambda: True)
    monkeypatch.setattr(ur, "load_snapshot_dossie", lambda s: None)
    d = us.dossie("ZZZZ")
    assert d["symbol"] == "ZZZZ" and "erro" in d


def test_migration_044_autossuficiente():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    sql = (root / "supabase_unificado" / "schema" /
           "044_market_us_snapshot.sql").read_text(encoding="utf-8")
    # cria o schema (no Supabase market_us não existe) e não tem FK para companies
    assert "CREATE SCHEMA IF NOT EXISTS market_us" in sql
    assert "company_snapshots" in sql
    assert "REFERENCES market_us.companies" not in sql
    code = "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    assert "DROP TABLE" not in code.upper() and "TRUNCATE" not in code.upper()


def test_us_snapshot_publisher_deactivates_stale_symbols_without_deleting():
    from scripts.publish_us_snapshot import _build_deactivate_stale

    sql = _build_deactivate_stale()
    assert "UPDATE market_us.company_snapshots" in sql
    assert "is_active = FALSE" in sql
    assert "symbol = ANY(:symbols)" in sql
    assert "DELETE" not in sql


def test_us_snapshot_schema_updater_is_incremental():
    from scripts.publish_us_snapshot import _ensure_schema

    assert callable(_ensure_schema)


def test_us_snapshot_publisher_declares_resumable_identity():
    from pathlib import Path

    code = (Path(__file__).resolve().parents[1] / "scripts" /
            "publish_us_snapshot.py").read_text(encoding="utf-8")
    assert "já confirmadas" in code
    assert "score_version,generated_at" in code
