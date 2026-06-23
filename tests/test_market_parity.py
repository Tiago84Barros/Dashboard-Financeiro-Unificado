import pandas as pd

import data_pipeline.market.parity as par


def _frame(rows):
    return pd.DataFrame(rows)


def test_compare_multiplos_agree_within_tol():
    legacy = _frame([{"Ticker": "PETR4", "P/L": 5.00, "ROE": 0.260}])
    market = _frame([{"Ticker": "PETR4", "P/L": 5.20, "ROE": 0.265}])  # ~4% e ~2%
    rep = par.compare_multiplos(legacy, market, metrics=["P/L", "ROE"], rel_tol=0.10)
    assert rep["coverage"]["common"] == 1
    assert rep["metrics"]["P/L"]["agree"] == 1
    assert rep["metrics"]["ROE"]["agree"] == 1
    assert rep["overall"]["agree_rate"] == 1.0


def test_compare_multiplos_diverge_beyond_tol():
    legacy = _frame([{"Ticker": "UGPA3", "Margem_Liquida": 0.021}])
    market = _frame([{"Ticker": "UGPA3", "Margem_Liquida": 1.90}])  # bug clássico
    rep = par.compare_multiplos(legacy, market, metrics=["Margem_Liquida"], rel_tol=0.10)
    m = rep["metrics"]["Margem_Liquida"]
    assert m["compared"] == 1 and m["diverge"] == 1 and m["agree"] == 0
    assert m["worst"][0]["ticker"] == "UGPA3"


def test_compare_multiplos_abs_floor_rescues_small_values():
    # valores minúsculos: rel_diff alto mas |a-b| <= abs_floor → concorda
    legacy = _frame([{"Ticker": "X", "DY": 0.001}])
    market = _frame([{"Ticker": "X", "DY": 0.004}])  # rel ~75% mas |diff|=0.003<=0.005
    rep = par.compare_multiplos(legacy, market, metrics=["DY"],
                                rel_tol=0.10, abs_floor=0.005)
    assert rep["metrics"]["DY"]["agree"] == 1


def test_compare_multiplos_missing_and_coverage():
    legacy = _frame([{"Ticker": "A", "P/L": 5.0}, {"Ticker": "B", "P/L": 8.0}])
    market = _frame([{"Ticker": "A", "P/L": None}, {"Ticker": "C", "P/L": 9.0}])
    rep = par.compare_multiplos(legacy, market, metrics=["P/L"])
    cov = rep["coverage"]
    assert cov["legacy"] == 2 and cov["market"] == 2 and cov["common"] == 1  # só A
    # A existe nos dois mas market é nulo → nada comparado
    assert rep["metrics"]["P/L"]["compared"] == 0


def test_compare_multiplos_missing_column():
    legacy = _frame([{"Ticker": "A", "P/L": 5.0}])
    market = _frame([{"Ticker": "A", "P/L": 5.1}])
    rep = par.compare_multiplos(legacy, market, metrics=["Liquidez_Corrente"])
    assert rep["metrics"]["Liquidez_Corrente"]["compared"] == 0


def test_compare_multiplos_flags_legacy_constant_and_zero():
    # legado: Endividamento constante 1.0 (placeholder) e Payout sempre 0 (bug);
    # market traz valores reais -> diagnóstico aponta o legado como culpado.
    legacy = _frame([{"Ticker": t, "Endividamento_Total": 1.0, "Payout": 0.0}
                     for t in ("A", "B", "C", "D")])
    market = _frame([{"Ticker": "A", "Endividamento_Total": 0.5, "Payout": 0.3},
                     {"Ticker": "B", "Endividamento_Total": 0.6, "Payout": 0.4},
                     {"Ticker": "C", "Endividamento_Total": 0.2, "Payout": 0.5},
                     {"Ticker": "D", "Endividamento_Total": 0.3, "Payout": 0.6}])
    rep = par.compare_multiplos(legacy, market,
                                metrics=["Endividamento_Total", "Payout"])
    end = rep["metrics"]["Endividamento_Total"]
    pay = rep["metrics"]["Payout"]
    assert end["legacy_constant"] is True and end["legacy_constant_value"] == 1.0
    assert pay["legacy_zero_rate"] == 1.0
    assert end["agree"] == 0  # nenhuma concordância porque o legado é placeholder


def test_compare_setores_mismatch():
    legacy = _frame([{"ticker": "A", "SETOR": "Energia"},
                     {"ticker": "B", "SETOR": "Bancos"}])
    market = _frame([{"ticker": "A", "SETOR": "Energia"},
                     {"ticker": "B", "SETOR": "Financeiro"}])
    rep = par.compare_setores(legacy, market)
    assert rep["common"] == 2 and rep["setor_mismatch"] == 1
    assert rep["exemplos_mismatch"][0]["ticker"] == "B"
