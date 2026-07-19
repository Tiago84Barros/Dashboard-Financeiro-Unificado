import pandas as pd

from core.us_company_analysis import (
    annual_dividends,
    annual_price_returns,
    derive_metric_history,
    regression_cagr,
)


def _financials():
    return pd.DataFrame([
        {"fiscal_year": 2022, "revenue": 100, "operating_income": 20,
         "ebit": 20, "net_income": 10, "total_assets": 200, "total_equity": 80,
         "total_debt": 40, "invested_capital": 100, "current_assets": 60,
         "current_liabilities": 30, "shares_outstanding": 10,
         "free_cash_flow": 8, "dividends_paid": -2},
        {"fiscal_year": 2023, "revenue": 121, "operating_income": 24.2,
         "ebit": 24.2, "net_income": 12.1, "total_assets": 220, "total_equity": 88,
         "total_debt": 44, "invested_capital": 110, "current_assets": 66,
         "current_liabilities": 33, "shares_outstanding": 10,
         "free_cash_flow": 9.68, "dividends_paid": -2.2},
    ])


def test_regression_cagr_replica_metodologia_b3():
    assert round(regression_cagr(_financials(), "revenue"), 4) == 0.21
    assert regression_cagr(_financials(), "campo_ausente") is None


def test_retornos_e_dividendos_anuais():
    prices = pd.DataFrame({
        "date": ["2022-01-01", "2022-12-31", "2023-12-31"],
        "price": [10, 20, 30],
    })
    returns = annual_price_returns(prices)
    assert returns.to_dict("records") == [{"Ano": 2023, "Retorno": 50.0}]
    dividends = pd.DataFrame({
        "date": ["2023-03-01", "2023-09-01"], "amount": [0.20, 0.30],
    })
    annual = annual_dividends(_financials(), dividends)
    assert annual.to_dict("records") == [
        {"fiscal_year": 2023, "dividends_per_share": 0.5}
    ]


def test_metricas_gaap_historicas_preservam_ausencia():
    prices = pd.DataFrame({"date": ["2022-12-31", "2023-12-31"], "price": [10, 12]})
    history = derive_metric_history(_financials(), prices)
    latest = history.iloc[-1]
    assert round(latest["operating_margin"], 4) == 0.20
    assert round(latest["net_margin"], 4) == 0.10
    assert round(latest["current_ratio"], 4) == 2.0
    assert round(latest["debt_to_equity"], 4) == 0.50
    assert round(latest["pe"], 4) == round(120 / 12.1, 4)


def test_aba_americana_renderiza_mesmos_blocos_da_analise_b3():
    from streamlit.testing.v1 import AppTest
    import views.empresas_americanas as american_view

    originals = {name: getattr(american_view.us, name) for name in (
        "data_status", "scored_universe", "company_financials",
        "company_market_data", "dossie",
    )}
    try:
        app = AppTest.from_string(r'''
import pandas as pd
import views.empresas_americanas as view

scored = pd.DataFrame([{
    "symbol":"AAPL", "name":"Apple Inc.", "sector":"Technology",
    "industry":"Consumer Electronics", "score":82.0, "coverage":100.0,
    "score_quality":85.0, "score_growth":80.0, "score_solidity":78.0,
    "score_capital_efficiency":88.0, "score_valuation":65.0,
    "score_shareholder":75.0, "operating_margin":.30, "net_margin":.25,
    "roe":.45, "roa":.20, "roic":.35, "current_ratio":1.1,
    "debt_to_equity":1.5, "pe":25.0, "ev_ebit":20.0, "p_fcf":22.0,
    "fcf_yield":.045, "revenue_cagr_3y":.08, "net_debt_ebitda":.5,
}])
financials = pd.DataFrame([
    {"fiscal_year":2022,"revenue":100,"operating_income":30,"ebit":30,"ebitda":35,
     "net_income":25,"total_assets":200,"total_equity":80,"cash_and_equivalents":20,
     "short_term_debt":5,"long_term_debt":35,"total_debt":40,"net_debt":20,
     "current_assets":60,"current_liabilities":30,"invested_capital":100,
     "shares_outstanding":10,"operating_cash_flow":30,"investing_cash_flow":-12,
     "free_cash_flow":20,"dividends_paid":-5,"dividends_per_share":.5},
    {"fiscal_year":2023,"revenue":110,"operating_income":33,"ebit":33,"ebitda":38,
     "net_income":27,"total_assets":220,"total_equity":90,"cash_and_equivalents":22,
     "short_term_debt":6,"long_term_debt":34,"total_debt":40,"net_debt":18,
     "current_assets":66,"current_liabilities":33,"invested_capital":108,
     "shares_outstanding":10,"operating_cash_flow":33,"investing_cash_flow":-13,
     "free_cash_flow":22,"dividends_paid":-5.5,"dividends_per_share":.55},
])
market = {
    "prices": pd.DataFrame({"date":["2022-12-31","2023-12-31"],"price":[120,150]}),
    "dividends": pd.DataFrame({"date":["2022-12-31","2023-12-31"],"amount":[.5,.55]}),
    "metrics": pd.DataFrame(),
}
view.us.data_status = lambda: {"offline":False,"schema_ready":True,"mode":"snapshot"}
view.us.scored_universe = lambda: scored
view.us.company_financials = lambda symbol: financials
view.us.company_market_data = lambda symbol: market
view.us.dossie = lambda symbol: {"classification":"consolidada","classification_reason":"ok",
    "red_flags":[],"notes":{}}
view.st.session_state["us_active_tab"] = 1
view.st.session_state["us_selected_ticker"] = "AAPL"
view.render()
''').run(timeout=30)
        assert not app.exception
        rendered = "\n".join(element.value for element in app.markdown)
        for label in (
            "Preço da Ação", "Retorno Anual do Preço", "Crescimento Médio Anual",
            "Último Exercício Disponível", "Demonstrações Financeiras", "Dividendos por ação",
            "Gráfico de Múltiplos", "Fluxo de Caixa", "Estrutura de Capital e Dívida",
            "Rentabilidade", "Valuation", "Score e critérios de avaliação",
        ):
            assert label in rendered
        assert "Eletrônicos de Consumo" in rendered
    finally:
        for name, original in originals.items():
            setattr(american_view.us, name, original)


def test_retorno_anual_em_ordem_cronologica():
    """O gráfico de retorno anual precisa respeitar a cronologia.

    px.bar com color= divide os dados em DOIS traces (positivo/negativo). O eixo
    categórico do Plotly usa categoryorder='trace' por padrão, o que desenha um
    trace inteiro e depois o outro — agrupando os anos por SINAL e quebrando a
    linha do tempo (bug real observado no deploy). A correção fixa a ordem das
    categorias; este teste trava a regressão.
    """
    from pathlib import Path

    import plotly.express as px

    import views.empresas_americanas as american_view

    # anos com retornos positivos e negativos intercalados (cenário do bug)
    df = pd.DataFrame({
        "Ano": [2007, 2008, 2009, 2015, 2018, 2022, 2024, 2026],
        "Retorno": [133.47, -56.91, 146.90, -3.01, -5.39, -26.40, 30.71, 22.99],
    }).sort_values("Ano")
    df["Ano"] = df["Ano"].astype(str)
    df["Positivo"] = df["Retorno"] >= 0

    fig = px.bar(df, x="Retorno", y="Ano", orientation="h", color="Positivo")
    # sem a correção o Plotly agruparia por trace (sinal), não por ano
    assert fig.layout.yaxis.categoryarray is None
    fig.update_yaxes(categoryorder="array", categoryarray=df["Ano"].tolist())
    ordem = list(fig.layout.yaxis.categoryarray)
    assert ordem == sorted(ordem), "eixo Y deve estar em ordem cronológica"
    assert ordem[0] == "2007" and ordem[-1] == "2026"

    # As DUAS seções (EUA e B3) usam o mesmo padrão e precisam fixar a ordem.
    raiz = Path(american_view.__file__).resolve().parent
    for arquivo in ("empresas_americanas.py", "empresas_b3.py"):
        fonte = (raiz / arquivo).read_text(encoding="utf-8")
        trecho = fonte.split("Retorno Anual do Preço", 1)[1][:1400]
        assert 'categoryorder="array"' in trecho, (
            f"{arquivo}: gráfico de retorno anual não fixa a ordem das categorias — "
            "os anos voltariam a ser agrupados por sinal")
