"""Testes de integração leve: config, schema SQL, leitura offline e imports (regressão)."""
from pathlib import Path

import core.us_read as ur
from core.config import Settings

_ROOT = Path(__file__).resolve().parents[1]


# ── config ────────────────────────────────────────────────────────────────────
def test_config_has_fmp():
    s = Settings()
    s.FMP_API_KEY = ""
    assert s.has_fmp is False
    s.FMP_API_KEY = "abc"
    assert s.has_fmp is True


# ── schema SQL ────────────────────────────────────────────────────────────────
def test_schema_040_conteudo():
    sql = (_ROOT / "supabase_unificado" / "schema" /
           "040_market_us_schema.sql").read_text(encoding="utf-8")
    assert "CREATE SCHEMA IF NOT EXISTS market_us" in sql
    for token in ("market_us.companies", "market_us.ticker_aliases",
                  "market_us.income_statements", "market_us.ingestion_runs",
                  "market_us.data_quality_audit", "market_us.score_vintages"):
        assert token in sql, token
    # colunas PIT obrigatórias
    for col in ("reference_date", "published_date", "available_at", "content_hash", "cik"):
        assert col in sql, col
    # sem operações destrutivas (ignora comentários -- ...)
    code = "\n".join(l for l in sql.splitlines() if not l.lstrip().startswith("--"))
    up = code.upper()
    assert "DROP TABLE" not in up
    assert "TRUNCATE" not in up
    assert "DELETE FROM" not in up


# ── leitura offline blindada (nunca levanta para a UI) ────────────────────────
def test_us_read_sem_engine(monkeypatch):
    monkeypatch.setattr(ur, "_engine", lambda: None)
    assert ur.schema_ready() is False
    st = ur.data_status()
    assert st["offline"] is True and st["companies"] == 0
    ov = ur.load_overview()
    assert ov["companies"] == 0
    assert ur.load_companies().empty
    assert ur.load_company_financials("AAPL").empty


def test_us_read_engine_com_erro_nao_propaga(monkeypatch):
    class BoomEngine:
        def connect(self):
            raise RuntimeError("db caiu")
    monkeypatch.setattr(ur, "_engine", lambda: BoomEngine())
    # schema_ready engole a exceção → False; funções retornam vazio
    assert ur.schema_ready() is False
    assert ur.load_overview()["companies"] == 0
    assert ur.load_companies(search="AAP").empty


# ── imports (regressão: novos módulos não quebram e a facade carrega) ─────────
def test_imports_dos_modulos_novos():
    import importlib
    for mod in ("core.us_data", "core.us_methodology", "core.us_read",
                "core.us_metrics", "core.us_score", "core.us_dossie",
                "core.us_portfolio", "core.us_backtest",
                "data_pipeline.us.providers", "data_pipeline.us.normalize",
                "data_pipeline.us.identity", "data_pipeline.us.repository",
                "data_pipeline.us.quality", "data_pipeline.us.ingest",
                "data_pipeline.us.scoring_history",
                "views.empresas_americanas"):
        assert importlib.import_module(mod) is not None
