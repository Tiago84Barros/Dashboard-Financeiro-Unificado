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
