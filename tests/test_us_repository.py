"""Testes do construtor de upsert e dos checks de qualidade (puros, sem DB)."""
import data_pipeline.us.quality as q
import data_pipeline.us.repository as repo


def test_build_upsert_do_update():
    sql = repo.build_upsert("companies", ["cik", "name", "sector"],
                            conflict=["cik"])
    assert "INSERT INTO market_us.companies" in sql
    assert "(cik, name, sector)" in sql
    assert "VALUES (:cik, :name, :sector)" in sql
    assert "ON CONFLICT (cik) DO UPDATE SET" in sql
    assert "name = EXCLUDED.name" in sql
    assert "cik = EXCLUDED.cik" not in sql   # coluna de conflito não é atualizada


def test_build_upsert_do_nothing():
    sql = repo.build_upsert("dividends", ["symbol", "amount"],
                            conflict=["symbol", "amount"], update=[])
    assert "DO NOTHING" in sql


def test_build_upsert_update_explicito():
    sql = repo.build_upsert("assets", ["symbol", "exchange", "is_active"],
                            conflict=["symbol", "exchange"], update=["is_active"])
    assert "DO UPDATE SET is_active = EXCLUDED.is_active" in sql


def test_build_upsert_rejeita_vazio():
    import pytest
    with pytest.raises(ValueError):
        repo.build_upsert("x", [], conflict=["a"])


# ── checks de qualidade ───────────────────────────────────────────────────────
def test_balance_identity():
    assert q.check_balance_identity(500, 300, 200) is True
    assert q.check_balance_identity(500, 300, 100) is False
    assert q.check_balance_identity(None, 300, 200) is None   # ausente → skip


def test_fcf_coherence():
    assert q.check_fcf_coherence(100, -30, 70) is True        # capex negativo
    assert q.check_fcf_coherence(100, -30, 10) is False
    assert q.check_fcf_coherence(100, None, 70) is None


def test_market_cap_coherence():
    assert q.check_market_cap_coherence(1000, 10, 100) is True
    assert q.check_market_cap_coherence(1000, 10, 50) is False
    assert q.check_market_cap_coherence(1000, 10, 0) is None  # shares 0 → skip


def test_margin_plausible():
    assert q.check_margin_plausible(0.3) is True
    assert q.check_margin_plausible(2.0) is False
    assert q.check_margin_plausible(None) is None
