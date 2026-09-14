import json

import numpy as np
import pandas as pd

from data_pipeline.market.fii_pit import (
    _features_as_of,
    _json_safe,
    _monthly_market_features,
    reconstruct_snapshots,
)


def test_reconstruction_respects_knowledge_at_and_builds_monthly_scores():
    dates = pd.date_range("2023-01-02", "2025-03-31", freq="B")
    prices = pd.DataFrame({
        "ticker": "TEST11", "date": dates, "close": 100.0,
        "adjusted_close": 100.0 + pd.Series(range(len(dates))), "volume": 20_000,
        "source": "b3_cotahist",
    })
    dividends = pd.DataFrame({
        "ticker": ["TEST11"] * 24,
        "event_date": pd.date_range("2023-04-15", periods=24, freq="ME"),
        "ex_date": [None] * 24, "payment_date": [None] * 24, "amount": [1.0] * 24,
    })
    observations = pd.DataFrame([
        {"ticker": "TEST11", "metric_name": "nav_per_share", "value_numeric": 110,
         "value_text": None, "value_json": None, "reference_date": "2024-01-31",
         "knowledge_at": "2024-02-15T23:59:59Z", "availability_quality": "verified_publication",
         "source": "cvm_informe_mensal"},
    ])
    exposures = pd.DataFrame([
        {"ticker": "TEST11", "exposure_type": "asset_class", "exposure_name": "real_estate",
         "exposure_weight": .9, "reference_date": "2024-01-31",
         "knowledge_at": "2024-02-15T23:59:59Z", "availability_quality": "verified_publication",
         "source": "cvm_informe_mensal"},
    ])
    funds = pd.DataFrame([{"ticker": "TEST11", "tipo": "papel"}])
    snapshots = reconstruct_snapshots(prices, dividends, observations, exposures, funds,
                                      start="2024-01-31", end="2024-03-31")
    assert snapshots
    january = [row for row in snapshots if row["reference_date"] == "2024-01-31"]
    february = [row for row in snapshots if row["reference_date"] == "2024-02-29"]
    assert january and january[0]["availability_quality"] == "first_observed_proxy"
    assert february and february[0]["fii_type"] == "tijolo"
    assert "pvp" in february[0]["inputs_json"]


def test_pit_liquidity_converts_monthly_bar_volume_to_daily_unit():
    prices = pd.DataFrame({
        "ticker": ["TEST11"] * 6,
        "date": pd.date_range("2026-01-31", periods=6, freq="ME"),
        "close": [10.0] * 6,
        "adjusted_close": [10.0] * 6,
        "volume": [21_000.0] * 6,
        "source": ["brapi_legacy_quote"] * 6,
    })
    bundle = _monthly_market_features(prices, pd.DataFrame())["TEST11"]

    result = _features_as_of(bundle, pd.Timestamp("2026-06-30"))

    assert result["liquidez_diaria"] == 10_000.0
    assert result["liquidity_method"] == "monthly_financial_volume_div_21"


def test_pit_liquidity_keeps_daily_b3_volume_in_daily_unit():
    dates = pd.date_range("2026-04-01", periods=63, freq="B")
    prices = pd.DataFrame({
        "ticker": ["TEST11"] * len(dates),
        "date": dates,
        "close": [10.0] * len(dates),
        "adjusted_close": [10.0] * len(dates),
        "volume": [21_000.0] * len(dates),
        "source": ["b3_cotahist"] * len(dates),
    })
    bundle = _monthly_market_features(prices, pd.DataFrame())["TEST11"]

    result = _features_as_of(bundle, dates.max())

    assert result["liquidez_diaria"] == 210_000.0
    assert result["liquidity_method"] == "daily_financial_volume_median_63"


def test_snapshot_batches_respeita_limite_de_bytes_do_postgres():
    """O parâmetro jsonb tem teto de 256 MB; a safra de 10 anos passa disso."""
    from data_pipeline.market.fii_pit import _snapshot_batches

    linhas = [{"ticker": f"AAA{i}11", "inputs_json": {"pad": "x" * 900}}
              for i in range(40)]
    lotes = list(_snapshot_batches(linhas, max_bytes=4_000))
    assert len(lotes) > 1
    assert [linha for lote in lotes for linha in lote] == linhas
    for lote in lotes:
        serializado = json.dumps(lote, ensure_ascii=False).encode("utf-8")
        assert len(serializado) <= 4_000


def test_snapshot_batches_nao_descarta_linha_maior_que_o_lote():
    """Recusar a linha gigante perderia a safra em silêncio."""
    from data_pipeline.market.fii_pit import _snapshot_batches

    gigante = {"ticker": "BBB11", "inputs_json": {"pad": "y" * 5_000}}
    pequena = {"ticker": "CCC11"}
    lotes = list(_snapshot_batches([pequena, gigante, pequena], max_bytes=1_000))
    assert [linha for lote in lotes for linha in lote] == [pequena, gigante, pequena]
    assert any(lote == [gigante] for lote in lotes)


def test_cessao_de_protecao_sobrevive_a_serializacao_do_metrics_json():
    """A proteção cedida tem de chegar inteira ao artefato persistido.

    O escritor grava `json.dumps(_json_safe(backtest))` sem lista branca, mas
    "sem lista branca" não basta: `_json_safe` converte tipo a tipo, e uma
    safra cuja carteira só existe porque fundos tiveram o portão cedido não
    pode gravar histórico limpo. Aqui a estrutura é a que o backtest emite,
    com os tipos que ele realmente produz (numpy, Timestamp).
    """
    backtest = {
        "concession_periods": np.int64(2),
        "concession_period_fraction": np.float64(0.5),
        "concession_details": [{
            "decision_date": pd.Timestamp("2026-08-31").date(),
            "protecao_cedida": [{
                "ticker": "SNEL11",
                "motivos": ["renda recorrente abaixo do mínimo"],
                "severidade": np.int64(1),
                "na_carteira": True,
            }],
            "viability_notes": [
                "proteção ao investidor cedida na elegibilidade para "
                "viabilizar a carteira. Proteção cedida não é ausência de risco."
            ],
        }],
    }

    gravado = json.loads(json.dumps(_json_safe(backtest)))

    assert gravado["concession_periods"] == 2
    assert gravado["concession_period_fraction"] == 0.5
    cedido = gravado["concession_details"][0]
    assert cedido["decision_date"] == "2026-08-31"
    assert cedido["protecao_cedida"][0]["ticker"] == "SNEL11"
    assert cedido["protecao_cedida"][0]["motivos"] == [
        "renda recorrente abaixo do mínimo"]
    assert cedido["protecao_cedida"][0]["na_carteira"] is True
    assert "não é ausência de risco" in cedido["viability_notes"][0]
