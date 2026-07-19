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


def test_todas_migrations_market_us_sao_aplicadas_e_nao_destrutivas():
    """init-schema deve aplicar 040..043 — não só a base."""
    from data_pipeline.us.ingest import schema_files
    nomes = [p.name for p in schema_files()]
    assert any("040_market_us_schema" in n for n in nomes)
    assert any("041_market_us_portfolio" in n for n in nomes)
    assert any("042_market_us_outliers" in n for n in nomes)
    assert any("043_market_us_retained_earnings" in n for n in nomes)
    assert nomes == sorted(nomes)          # ordem de aplicação determinística
    for path in schema_files():
        code = "\n".join(l for l in path.read_text(encoding="utf-8").splitlines()
                         if not l.lstrip().startswith("--"))
        up = code.upper()
        assert "DROP TABLE" not in up and "TRUNCATE" not in up and "DELETE FROM" not in up


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
                "core.us_portfolio", "core.us_portfolio_creation", "core.us_backtest",
                "core.us_asymmetry", "core.us_outlier_backtest", "core.us_advanced",
                "data_pipeline.us.providers", "data_pipeline.us.normalize",
                "data_pipeline.us.identity", "data_pipeline.us.repository",
                "data_pipeline.us.quality", "data_pipeline.us.ingest",
                "data_pipeline.us.scoring_history", "data_pipeline.us.edgar",
                "data_pipeline.us.edgar_facts", "data_pipeline.us.prices_yf",
                "data_pipeline.us.snapshot",
                "views.empresas_americanas", "views.empresas_fora_da_curva"):
        assert importlib.import_module(mod) is not None


# ── separação das seções (propósitos distintos) ──────────────────────────────
def test_fora_da_curva_e_secao_propria_e_nao_aba():
    """Fora da Curva vive em seção própria — não pode voltar a ser aba do módulo EUA."""
    americanas = (_ROOT / "views" / "empresas_americanas.py").read_text(encoding="utf-8")
    fora = (_ROOT / "views" / "empresas_fora_da_curva.py").read_text(encoding="utf-8")
    # a view de Empresas Americanas não renderiza mais a trilha assimétrica
    assert "_tab_fora_da_curva" not in americanas
    assert "asymmetry_universe" not in americanas
    # e a seção própria tem seu próprio render()
    assert "def render()" in fora
    assert "asymmetry_universe" in fora


def test_rota_registrada_no_app():
    app = (_ROOT / "app.py").read_text(encoding="utf-8")
    assert '"empresas_fora_da_curva"' in app
    assert "Empresas Fora da Curva" in app


def test_sem_emoji_de_bandeira():
    """Windows não renderiza 🇺🇸 (vira letras 'US') — não usar em UI."""
    flag = "\U0001F1FA\U0001F1F8"
    for name in ("app.py", "views/empresas_americanas.py",
                 "views/empresas_fora_da_curva.py"):
        content = (_ROOT / name).read_text(encoding="utf-8")
        # permitido apenas em comentário explicando a proibição
        for line in content.splitlines():
            if flag in line:
                assert line.lstrip().startswith("#") or "não usar" in line, name


def test_data_status_explica_ambiente(monkeypatch):
    """Sem schema: instrução local fala do init-schema; na nuvem explica que os
    dados dos EUA não vão para o Supabase."""
    class Eng:
        def connect(self):
            raise AssertionError("não deve conectar neste teste")
    monkeypatch.setattr(ur, "_engine", lambda: object())
    monkeypatch.setattr(ur, "schema_ready", lambda: False)

    monkeypatch.setattr(ur, "_db_is_local", lambda: True)
    st_local = ur.data_status()
    assert "init-schema" in st_local["reason"]

    monkeypatch.setattr(ur, "_db_is_local", lambda: False)
    st_cloud = ur.data_status()
    assert "Supabase" in st_cloud["reason"] or "LOCAL" in st_cloud["reason"]
    assert "init-schema" not in st_cloud["reason"]
