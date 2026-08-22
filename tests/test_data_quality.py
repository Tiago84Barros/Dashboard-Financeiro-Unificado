import numpy as np
import pandas as pd

import core.data_quality as dq

# ── faixas coerentes / outliers ───────────────────────────────────────────────

def test_tightened_margin_rejects_impossible_value():
    # UGPA3: 190% de margem é impossível → inválido; 2,1% é válido
    assert dq.is_valid_value("Margem_Liquida", 1.904) is False
    assert dq.is_valid_value("Margem_Liquida", 0.021) is True


def test_zero_dy_is_missing_not_value():
    # PETR3: DY=0 é dado faltante, não "não paga dividendos"
    assert dq.is_valid_value("DY", 0.0) is False
    assert dq.is_valid_value("DY", 0.069) is True


def test_clean_value_out_of_range_becomes_none_not_zero():
    assert dq.clean_value("Margem_Liquida", 1.904) is None
    assert dq.clean_value("DY", 0.0) is None
    assert dq.clean_value("ROE", 0.18) == 0.18


def test_missing_distinct_from_zero():
    assert dq.is_missing(None) is True
    assert dq.is_missing(np.nan) is True
    assert dq.is_missing(0.0) is False


# ── DataFrame cleaning ────────────────────────────────────────────────────────

def _sample_df():
    return pd.DataFrame({
        "Ticker": ["UGPA3", "PETR3", "WEGE3"],
        "Margem_Liquida": [1.904, 0.217, 0.18],   # UGPA3 corrompido
        "DY": [0.054, 0.0, 0.012],                # PETR3 zero = faltante
        "ROE": [0.182, 0.242, 0.28],
        "P/L": [9.0, 5.16, 30.0],
        "P/VP": [1.64, 1.25, 12.0],
    })


def test_clean_multiples_frame_nans_outliers_and_zero():
    out = dq.clean_multiples_frame(_sample_df())
    assert pd.isna(out.loc[0, "Margem_Liquida"])   # 190% → NaN
    assert pd.isna(out.loc[1, "DY"])               # DY=0 → NaN
    assert out.loc[1, "Margem_Liquida"] == 0.217   # válido preservado
    assert out.loc[2, "ROE"] == 0.28


def test_detect_missing_critical_fields_separates_zero():
    missing = dq.detect_missing_critical_fields(_sample_df())
    assert "DY" in missing.get("PETR3", [])            # DY=0 conta como ausente
    assert "Margem_Liquida" in missing.get("UGPA3", [])  # 190% inválido


def test_critical_completeness_fraction():
    comp = dq.critical_completeness(_sample_df())
    # WEGE3 tem todos os 5 críticos válidos
    assert comp["WEGE3"] == 1.0
    # PETR3 perde DY (1 de 5 ausente)
    assert comp["PETR3"] < 1.0


# ── duplicatas / setor ausente ────────────────────────────────────────────────

def test_detect_duplicate_tickers():
    df = pd.DataFrame({"Ticker": ["AAA3", "AAA3", "BBB3"]})
    assert dq.detect_duplicate_tickers(df) == ["AAA3"]


def test_detect_missing_sector():
    df = pd.DataFrame({"Ticker": ["AAA3", "BBB3"], "SETOR": ["Energia", ""], "SEGMENTO": ["X", ""]})
    assert dq.detect_missing_sector(df) == ["BBB3"]


# ── DRE / macro ───────────────────────────────────────────────────────────────

def test_validate_dre_flags_incomplete():
    df = pd.DataFrame({"Data": pd.to_datetime(["2024-12-31"]), "Receita_Liquida": [100.0]})
    rep = dq.validate_dre_data(df)
    assert "Lucro_Liquido" in rep["faltando"]
    assert rep["ok"] is False


def test_validate_macro():
    assert dq.validate_macro_data({})["ok"] is False
    ok = dq.validate_macro_data({2025: {"selic": 0.12, "ipca": 0.045, "cambio": 5.1}})
    assert ok["ok"] is True


# ── relatório consolidado ─────────────────────────────────────────────────────

def test_quality_report_flags_insufficient_companies():
    rep = dq.generate_data_quality_report(_sample_df(), completeness_threshold=0.9)
    # UGPA3 (margem inválida) e PETR3 (DY faltante) ficam abaixo de 90%
    assert "UGPA3" in rep["empresas_insuficientes"]
    assert "PETR3" in rep["empresas_insuficientes"]
    assert rep["outliers"]  # tem ao menos o 190% e o DY=0
