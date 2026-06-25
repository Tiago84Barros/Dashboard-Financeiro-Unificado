import datetime as dt

import data_pipeline.market.fii as fii

_REF = dt.date(2026, 6, 25)


def _fii_quote(tk, sector="Fundos Imobiliários"):
    return {"symbol": tk, "longName": f"{tk} FII",
            "regularMarketPrice": 100.0,
            "summaryProfile": {"sector": sector, "industry": "Logística"},
            "defaultKeyStatistics": {"priceToBook": 0.95},
            "dividendsData": {"cashDividends": [
                {"paymentDate": "2026-06-15T03:00:00.000Z", "rate": 0.8},
                {"paymentDate": "2026-01-15T03:00:00.000Z", "rate": 0.8},
                {"paymentDate": "2024-01-15T03:00:00.000Z", "rate": 5.0},  # fora de 12m
            ]},
            "historicalDataPrice": [
                {"close": 100.0, "volume": 10000},
                {"close": 101.0, "volume": 12000},
            ]}


def test_is_fii_distingue_etf():
    assert fii.is_fii(_fii_quote("HGLG11")) is True
    assert fii.is_fii(_fii_quote("BOVA11", sector="Miscellaneous")) is False


def test_dy_12m_janela_e_preco():
    q = _fii_quote("HGLG11")
    dy = fii.dy_12m(q["dividendsData"]["cashDividends"], 100.0, _REF)
    assert abs(dy - 0.016) < 1e-9   # (0.8+0.8)/100, ignora o de 2024


def test_liquidez_mediana():
    liq = fii.liquidez_diaria([{"close": 10, "volume": 100},
                               {"close": 10, "volume": 300}])  # 1000, 3000 -> média 2000
    assert liq == 2000.0


def test_compute_fii_none_para_etf():
    assert fii.compute_fii(_fii_quote("BOVA11", sector="ETF"), _REF) is None
    m = fii.compute_fii(_fii_quote("HGLG11"), _REF)
    assert m["ticker"] == "HGLG11" and m["pvp"] == 0.95 and m["segmento"] == "Logística"


def test_rank_filtra_e_ordena():
    rows = [
        {"ticker": "BOM", "price": 100, "dy_12m": 0.12, "pvp": 0.90, "liquidez_diaria": 1e6},
        {"ticker": "CARO", "price": 100, "dy_12m": 0.07, "pvp": 1.20, "liquidez_diaria": 1e6},
        {"ticker": "ILIQ", "price": 100, "dy_12m": 0.15, "pvp": 0.80, "liquidez_diaria": 1000},  # ilíquido
        {"ticker": "ABSURDO", "price": 100, "dy_12m": 0.90, "pvp": 0.50, "liquidez_diaria": 1e6},  # DY absurdo
    ]
    out = fii.rank_fiis(rows, liq_min=200_000, pvp_max=1.30, dy_max=0.30)
    tks = [r["ticker"] for r in out]
    assert "ILIQ" not in tks and "ABSURDO" not in tks   # filtrados
    assert out[0]["ticker"] == "BOM"                    # melhor DY+P/VP
    assert all(0 <= r["score"] <= 100 for r in out)
