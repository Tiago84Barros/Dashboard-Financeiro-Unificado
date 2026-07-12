"""_infer_asset_type: classe vem do sufixo FINAL do ticker, não de dígitos na raiz."""
from data_pipeline.market import normalize as nz


def test_b3sa3_e_stock_nao_bdr():
    # "B3SA3" tem o "3" da raiz (B3); antes virava "33" -> BDR (bug).
    assert nz._infer_asset_type("B3SA3", {}) == "stock"


def test_on_pn_sao_stock():
    for tk in ("PETR3", "PETR4", "CSNA3", "BBAS3"):
        assert nz._infer_asset_type(tk, {}) == "stock"


def test_bdr_de_verdade():
    for tk in ("AAPL34", "GOGL35", "NUBR33"):
        assert nz._infer_asset_type(tk, {}) == "bdr"


def test_unit_sufixo_11():
    assert nz._infer_asset_type("BPAC11", {"summaryProfile": {"sector": "Financials"},
                                           "longName": "Banco BTG Pactual Unit"}) == "unit"
