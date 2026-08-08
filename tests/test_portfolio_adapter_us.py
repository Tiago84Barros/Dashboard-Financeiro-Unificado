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
