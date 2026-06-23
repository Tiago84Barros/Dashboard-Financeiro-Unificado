from datetime import date
from pathlib import Path

import pandas as pd

from core.cross_source import append_validation_history, load_validation_history
from core.fama_macbeth import blend_with_base_weights, estimate_fama_macbeth_weights
from core.survivorship_ingestion import load_cvm_cancelamentos


def _synthetic_hist_batch() -> dict[str, pd.DataFrame]:
    hist: dict[str, pd.DataFrame] = {}
    for i in range(12):
        quality = i / 11
        rows = []
        for year in range(2018, 2024):
            rows.append({
                "Data": f"{year}-12-31",
                "ROE": 0.03 + quality * 0.25 + (year - 2018) * 0.002,
                "ROIC": 0.02 + quality * 0.20,
                "Margem_Liquida": 0.05 + quality * 0.18,
                "Endividamento_Total": 2.0 - quality,
                "P/L": 20 - quality * 10,
            })
        hist[f"TST{i:02d}3"] = pd.DataFrame(rows)
    return hist


def test_fama_macbeth_estimates_and_blends_weights():
    result = estimate_fama_macbeth_weights(
        _synthetic_hist_batch(),
        ["ROE", "ROIC", "Margem_Liquida", "Endividamento_Total", "P/L"],
        min_years=3,
        min_obs_per_year=8,
    )

    assert result.ok
    blended = blend_with_base_weights(
        {"ROE": (0.5, True), "Endividamento_Total": (0.5, False)},
        result,
        alpha=0.35,
    )
    assert blended
    assert abs(sum(weight for weight, _ in blended.values()) - 1.0) < 1e-9


def test_cross_source_history_roundtrip(tmp_path: Path):
    path = tmp_path / "cross_source_history.jsonl"
    saved = append_validation_history(
        [{"Ticker": "TEST3", "Indicador": "ROE", "Severidade": "warn"}],
        path=path,
    )

    assert saved == 1
    rows = load_validation_history(path=path)
    assert rows[0]["Ticker"] == "TEST3"
    assert rows[0]["Severidade"] == "warn"


def test_cvm_cancelamentos_maps_alias_to_ticker(tmp_path: Path):
    cvm_path = tmp_path / "cad_cia_aberta.csv"
    alias_path = tmp_path / "aliases.csv"
    cvm_path.write_text(
        "CNPJ_CIA;DENOM_SOCIAL;DENOM_COMERC;DT_CANCEL;MOTIVO_CANCEL;SIT;CD_CVM\n"
        "00.000.000/0001-91;Empresa Teste SA;TESTE;31/12/2020;OPA;CANCELADA;12345\n",
        encoding="latin-1",
    )
    alias_path.write_text(
        "ticker,cnpj_cia,cd_cvm,nome_regex,ultimo_preco\n"
        "TSTE3,00.000.000/0001-91,,,10.50\n",
        encoding="utf-8",
    )

    mapped = load_cvm_cancelamentos(
        cache_path=cvm_path,
        alias_path=alias_path,
        ttl_days=9999,
    )

    assert len(mapped) == 1
    assert mapped[0].ticker == "TSTE3"
    assert mapped[0].data_delisting == date(2020, 12, 31)
