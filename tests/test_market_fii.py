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
    # 1000, 3000 -> mediana (média dos 2) = 2000 MENSAL -> /21 pregões = diário
    liq = fii.liquidez_diaria([{"close": 10, "volume": 100},
                               {"close": 10, "volume": 300}])
    assert liq == 2000.0 / fii._PREGOES_MES


def test_compute_fii_none_para_etf():
    assert fii.compute_fii(_fii_quote("BOVA11", sector="ETF"), _REF) is None
    m = fii.compute_fii(_fii_quote("HGLG11"), _REF)
    assert m["ticker"] == "HGLG11" and m["pvp"] == 0.95 and m["segmento"] == "Logística"


def test_build_portfolio_diversifica_e_pesa():
    rows = [  # já "rankeadas" (score desc)
        {"ticker": "A1", "score": 90, "tipo": "tijolo", "liquidez_diaria": 1e6, "dy_12m": .1, "pvp": .9},
        {"ticker": "A2", "score": 85, "tipo": "tijolo", "liquidez_diaria": 1e6, "dy_12m": .1, "pvp": .9},
        {"ticker": "A3", "score": 80, "tipo": "tijolo", "liquidez_diaria": 1e6, "dy_12m": .1, "pvp": .9},
        {"ticker": "P1", "score": 70, "tipo": "papel", "liquidez_diaria": 1e6, "dy_12m": .12, "pvp": .95},
        {"ticker": "IL", "score": 95, "tipo": "papel", "liquidez_diaria": 1000, "dy_12m": .1, "pvp": .9},  # ilíquido
    ]
    p = fii.build_portfolio(rows, n_max=4, max_weight=0.40, max_tipo_frac=0.50, liq_min=200_000)
    tks = [x["ticker"] for x in p]
    assert "IL" not in tks                       # ilíquido fora
    assert tks.count("A1") == 1
    # diversificação: máx 50% de 4 = 2 por tipo -> só 2 tijolo (A1,A2), depois papel
    tipos = [x["tipo"] for x in p]
    assert tipos.count("tijolo") <= 2 and "papel" in tipos
    assert abs(sum(x["peso"] for x in p) - 1.0) < 5e-3   # pesos somam ~1 (arred. 4 casas)
    assert all(x["peso"] <= 0.40 + 1e-9 for x in p)      # teto respeitado


def test_backtest_retorno_total():
    import datetime as dt
    # 1 ativo, preço sobe 100->110 em 1 ano + 1 provento 5 -> retorno ~ (110+5)/100-1
    price = {"X": [(dt.date(2025, 1, 31), 100.0), (dt.date(2025, 6, 30), 105.0),
                   (dt.date(2026, 1, 31), 110.0)]}
    divs = {"X": [(dt.date(2025, 7, 15), 5.0)]}
    serie, met = fii.backtest({"X": 1.0}, price, divs)
    assert not serie.empty and met["n_ativos"] == 1
    assert met["retorno_total"] > 0.14            # ~15% (preço + provento)


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


def test_price_metrics_regressao_recupera_taxa():
    # série log-linear exata a 10% a.a. → o slope da regressão recupera 10%.
    import math
    rate = math.log(1.10)
    prices = [(dt.date(2022, 1, 1) + dt.timedelta(days=30 * i),
               100 * math.exp(rate * (30 * i / 365.25))) for i in range(37)]
    m = fii.price_metrics(prices)
    assert abs(m["cagr"] - 0.10) < 1e-4          # regressão robusta recupera a taxa
    assert m["max_drawdown"] == 0.0              # monotônica crescente
    assert m["meses"] == 37


def test_price_metrics_drawdown():
    # 25 pontos ~mensais: 100 → pico 110 → vale 80 → recupera até 121.
    vals = [100, 104, 108, 110, 100, 90, 80,
            85, 90, 95, 100, 105, 110, 113, 116, 119, 121,
            121, 121, 121, 121, 121, 121, 121, 121]
    prices = [(dt.date(2023, 1, 1) + dt.timedelta(days=30 * i), v)
              for i, v in enumerate(vals)]
    m = fii.price_metrics(prices)
    assert m["max_drawdown"] == round(80 / 110 - 1, 4)   # pico 110 → vale 80 = -0.2727
    assert m["cagr"] is not None
    assert m["meses"] == len(vals)


def test_price_metrics_curta_ou_vazia():
    assert fii.price_metrics([]) == {"cagr": None, "max_drawdown": None,
                                     "anos": 0.0, "meses": 0}
    poucos = [(dt.date(2024, 1, 1), 100), (dt.date(2024, 2, 1), 101)]
    assert fii.price_metrics(poucos)["cagr"] is None


def test_build_portfolio_min_por_tipo_forca_mix():
    # pool: muitos 'papel' de score alto e poucos 'tijolo'/'fof' de score menor.
    rows = ([{"ticker": f"P{i}", "score": 90 - i, "tipo": "papel",
              "liquidez_diaria": 5e6, "dy_12m": 0.12, "pvp": 0.9} for i in range(8)]
            + [{"ticker": "T1", "score": 50, "tipo": "tijolo", "liquidez_diaria": 5e6,
                "dy_12m": 0.11, "pvp": 0.8},
               {"ticker": "F1", "score": 40, "tipo": "fof", "liquidez_diaria": 5e6,
                "dy_12m": 0.10, "pvp": 0.95}])
    # sem piso: só papel entraria (scores dominam) — teto por tipo limita a 4
    port0 = fii.build_portfolio(rows, n_max=6, max_tipo_frac=0.7, min_por_tipo=0)
    # com piso 1: garante ao menos 1 tijolo e 1 fof
    port1 = fii.build_portfolio(rows, n_max=6, max_tipo_frac=0.7, min_por_tipo=1)
    tipos1 = {p["tipo"] for p in port1}
    assert {"tijolo", "fof", "papel"}.issubset(tipos1)
    assert sum(p["tipo"] == "tijolo" for p in port1) >= 1
    assert abs(sum(p["peso"] for p in port1) - 1.0) < 1e-6


def test_effective_n():
    assert fii.effective_n({"a": 0.5, "b": 0.5}) == 2.0
    assert fii.effective_n({"a": 1.0}) == 1.0
    assert fii.effective_n({"a": 0.4, "b": 0.4, "c": 0.2}) == round(1 / 0.36, 2)
    assert fii.effective_n({}) is None


def test_risk_curve_diminui_com_diversificacao():
    import pandas as pd
    # A e B perfeitamente anticorrelacionados → 50/50 zera a volatilidade.
    a = [0.1, -0.1] * 6
    b = [-0.1, 0.1] * 6
    rets = pd.DataFrame({"A": a, "B": b})
    curve = fii.risk_curve(rets, {"A": 0.5, "B": 0.5})
    assert [c["n"] for c in curve] == [1, 2]
    assert curve[0]["vol"] > 0
    assert curve[1]["vol"] < curve[0]["vol"]     # diversificar reduziu o risco


def test_mean_correlation():
    import pandas as pd
    corr = pd.DataFrame([[1.0, 0.5, 0.2],
                         [0.5, 1.0, 0.8],
                         [0.2, 0.8, 1.0]], columns=list("ABC"), index=list("ABC"))
    # off-diagonal: 0.5, 0.2, 0.5, 0.8, 0.2, 0.8 -> média = 0.5
    assert fii.mean_correlation(corr) == 0.5
    assert fii.mean_correlation(pd.DataFrame([[1.0]])) is None
