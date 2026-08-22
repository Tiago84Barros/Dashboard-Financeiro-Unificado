from pathlib import Path

import pandas as pd

from core.market_companies import (
    filter_market_companies,
    is_valid_us_equity,
    localize_us_company_frame,
    normalize_b3_companies,
    normalize_us_companies,
    translate_us_industry,
    translate_us_sector,
)


def test_normalize_us_preserva_classes_e_exclui_nao_acoes():
    source = pd.DataFrame([
        {"symbol": "GOOG", "name": "Alphabet Class C", "sector": "Technology",
         "industry": "Internet Content", "exchange": "NASDAQ", "security_type": "Stock",
         "is_active": True},
        {"symbol": "GOOGL", "name": "Alphabet Class A", "sector": "Technology",
         "industry": "Internet Content", "exchange": "NASDAQ", "security_type": "Stock",
         "is_active": True},
        {"symbol": "SPY", "name": "SPDR S&P 500 ETF Trust", "sector": "",
         "industry": "", "exchange": "NYSE Arca", "security_type": "ETF", "is_active": True},
        {"symbol": "XYZW", "name": "Example Warrant", "sector": "Industrials",
         "industry": "", "exchange": "NASDAQ", "security_type": "Warrant", "is_active": True},
    ])
    out = normalize_us_companies(source)
    assert set(out["ticker"]) == {"GOOG", "GOOGL"}
    assert set(out["sector"]) == {"Tecnologia"}
    assert set(out["industry"]) == {"Conteúdo na Internet"}
    assert out["logo_url"].str.contains("company-logos").all()
    assert (out["currency"] == "USD").all()
    assert (out["country"] == "Estados Unidos").all()
    assert (out["asset_type"] == "Ação").all()


def test_united_company_nao_e_confundida_com_spac_unit():
    assert is_valid_us_equity({
        "symbol": "UNH", "name": "UnitedHealth Group", "exchange": "NYSE",
        "security_type": "Common Stock", "is_active": True,
    })
    assert not is_valid_us_equity({
        "symbol": "SPACU", "name": "Example Acquisition", "exchange": "NASDAQ",
        "security_type": "Unit", "is_active": True,
    })


def test_search_usa_ticker_nome_setor_e_industria():
    frame = normalize_us_companies(pd.DataFrame([
        {"symbol": "AAPL", "name": "Apple Inc.", "sector": "Technology",
         "industry": "Consumer Electronics", "exchange": "NASDAQ",
         "security_type": "Stock", "is_active": True},
        {"symbol": "JPM", "name": "JPMorgan Chase", "sector": "Financial Services",
         "industry": "Banks - Diversified", "exchange": "NYSE",
         "security_type": "Stock", "is_active": True},
    ]))
    assert filter_market_companies(frame, "AAPL").iloc[0]["ticker"] == "AAPL"
    assert filter_market_companies(frame, "apple").iloc[0]["ticker"] == "AAPL"
    assert filter_market_companies(frame, "Tecnologia").iloc[0]["ticker"] == "AAPL"
    assert filter_market_companies(frame, "tecnologia").iloc[0]["ticker"] == "AAPL"
    assert filter_market_companies(frame, "Banks").iloc[0]["ticker"] == "JPM"
    assert filter_market_companies(frame, "Bancos").iloc[0]["ticker"] == "JPM"


def test_classificacoes_sec_sao_consolidadas_e_traduzidas_para_pt_br():
    source = pd.DataFrame([
        {"symbol": "A", "name": "Agilent", "sector": "Laboratory Analytical Instruments",
         "industry": "Laboratory Analytical Instruments", "exchange": "NYSE",
         "security_type": "Stock", "is_active": True},
        {"symbol": "AA", "name": "Alcoa", "sector": "Primary Production of Aluminum",
         "industry": "Primary Production of Aluminum", "exchange": "NYSE",
         "security_type": "Stock", "is_active": True},
        {"symbol": "AACB", "name": "Artius", "sector": "Blank Checks",
         "industry": "Blank Checks", "exchange": "NASDAQ",
         "security_type": "Stock", "is_active": True},
        {"symbol": "AFL", "name": "Aflac", "sector": "Accident & Health Insurance",
         "industry": "Accident & Health Insurance", "exchange": "NYSE",
         "security_type": "Stock", "is_active": True},
        {"symbol": "FUL", "name": "H.B. Fuller", "sector": "Adhesives & Sealants",
         "industry": "Adhesives & Sealants", "exchange": "NYSE",
         "security_type": "Stock", "is_active": True},
        {"symbol": "AVD", "name": "American Vanguard", "sector": "Agricultural Chemicals",
         "industry": "Agricultural Chemicals", "exchange": "NYSE",
         "security_type": "Stock", "is_active": True},
    ])
    out = normalize_us_companies(source).set_index("ticker")
    assert out.loc["A", "sector"] == "Saúde"
    assert out.loc["A", "industry"] == "Instrumentos Analíticos de Laboratório"
    assert out.loc["AA", "sector"] == "Materiais Básicos"
    assert out.loc["AA", "industry"] == "Produção Primária de Alumínio"
    assert out.loc["AACB", "sector"] == "Serviços Financeiros"
    assert out.loc["AACB", "industry"] == "Empresas de Cheque em Branco"
    assert out.loc["AFL", "sector"] == "Serviços Financeiros"
    assert out.loc["AFL", "industry"] == "Seguros de Acidentes e Saúde"
    assert out.loc["FUL", "sector"] == "Materiais Básicos"
    assert out.loc["FUL", "industry"] == "Adesivos e Selantes"
    assert out.loc["AVD", "sector"] == "Materiais Básicos"
    assert out.loc["AVD", "industry"] == "Produtos Químicos Agrícolas"


def test_localizacao_preserva_colunas_originais_para_busca_e_calculos():
    frame = localize_us_company_frame(pd.DataFrame([{
        "symbol": "JPM", "sector": "Financial Services", "industry": "Banks - Diversified",
        "score": 82.0,
    }]))
    assert frame.iloc[0]["sector"] == "Serviços Financeiros"
    assert frame.iloc[0]["industry"] == "Bancos Diversificados"
    assert frame.iloc[0]["sector_raw"] == "Financial Services"
    assert frame.iloc[0]["industry_raw"] == "Banks - Diversified"
    assert frame.iloc[0]["score"] == 82.0
    assert translate_us_sector("Real Estate Investment Trusts") == "Imobiliário"
    assert translate_us_industry("Laboratory Analytical Instruments") == (
        "Instrumentos Analíticos de Laboratório"
    )


def test_normalize_b3_mantem_tag_e_classes():
    source = pd.DataFrame([
        {"ticker": "PETR3", "nome_empresa": "Petrobras", "SETOR": "Petróleo",
         "SUBSETOR": "Petróleo e Gás", "SEGMENTO": "Exploração"},
        {"ticker": "PETR4", "nome_empresa": "Petrobras", "SETOR": "Petróleo",
         "SUBSETOR": "Petróleo e Gás", "SEGMENTO": "Exploração"},
    ])
    out = normalize_b3_companies(source, lambda ticker: f"logo/{ticker}.png")
    assert set(out["ticker"]) == {"PETR3", "PETR4"}
    assert out.iloc[0]["card_tag"] == "Petróleo e Gás · Exploração"


def test_duas_views_usam_componentes_compartilhados_e_eua_nao_usa_tabela_na_vitrine():
    root = Path(__file__).resolve().parents[1]
    b3 = (root / "views" / "empresas_b3.py").read_text(encoding="utf-8")
    usa = (root / "views" / "empresas_americanas.py").read_text(encoding="utf-8")
    for content in (b3, usa):
        assert "render_market_tabs" in content
        assert "render_sector_grid" in content
    # A busca por ticker existe só na vitrine americana. Na B3 a navegação é
    # por setor: o card já leva à Análise de Empresa, e o campo duplicava o
    # caminho. Importar o componente sem usá-lo é o que o teste barra aqui.
    assert "render_company_search" in usa
    assert "render_company_search" not in b3
    assert "filter_market_companies" not in b3
    start = usa.index("def _tab_empresas_setor")
    end = usa.index("def _render_score_dashboard", start)
    assert "st.dataframe" not in usa[start:end]
    assert "Cobertura do warehouse local" not in usa
    assert "def _tab_visao_geral" not in usa
    assert "def _tab_explorar" not in usa
    for english_label in (
        '"FCF Yield"', '"Shareholder Yield"', '"Hit rate"',
        '"Máx. drawdown"', '"Turnover médio"', '"Backtest"',
    ):
        assert english_label not in usa


def test_componentes_renderizam_quatro_cards_e_navegacao_sem_excecao():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string("""
import pandas as pd
from design.market_companies import render_market_css, render_market_tabs, render_sector_grid
render_market_css()
render_market_tabs(state_key='test_active', key_prefix='test')
df = pd.DataFrame([
    {'ticker':'AAPL','company_name':'Apple','sector':'Tecnologia','industry':'Eletrônicos','card_tag':'Tecnologia · Eletrônicos','logo_url':''},
    {'ticker':'MSFT','company_name':'Microsoft','sector':'Tecnologia','industry':'Software','card_tag':'Tecnologia · Software','logo_url':''},
    {'ticker':'JPM','company_name':'JPMorgan','sector':'Serviços Financeiros','industry':'Bancos','card_tag':'Serviços Financeiros · Bancos','logo_url':''},
    {'ticker':'XOM','company_name':'Exxon Mobil','sector':'Energia','industry':'Petróleo','card_tag':'Energia · Petróleo','logo_url':''},
])
render_sector_grid(df, key_prefix='test_cards', selected_ticker=None,
                   selected_state_key='test_ticker', active_state_key='test_active')
""").run(timeout=60)
    assert not app.exception
    labels = [button.label for button in app.button]
    assert labels[:5] == [
        "🏢 Empresas por Setor", "🔍 Análise de Empresa", "🔬 Análise Avançada",
        "🚀 Criação de Portfólio", "🧠 Avaliação de Portfólio",
    ]
    assert labels.count("Analisar") == 4


def test_view_americana_card_analisar_seleciona_ticker_e_muda_aba():
    from streamlit.testing.v1 import AppTest

    import views.empresas_americanas as american_view

    originals = {name: getattr(american_view.us, name) for name in (
        "data_status", "companies", "scored_universe", "company_financials",
        "company_market_data", "dossie",
    )}
    try:
        app = AppTest.from_string("""
import pandas as pd
import views.empresas_americanas as view
companies = pd.DataFrame([
    {'symbol':'AAPL','name':'Apple Inc.','sector':'Technology','industry':'Consumer Electronics','exchange':'NASDAQ','security_type':'Stock','is_active':True},
    {'symbol':'MSFT','name':'Microsoft Corp.','sector':'Technology','industry':'Software','exchange':'NASDAQ','security_type':'Stock','is_active':True},
])
scored = companies.copy()
for col in ('score','score_quality','score_growth','score_solidity','score_capital_efficiency','score_valuation','score_shareholder','coverage'):
    scored[col] = 75.0
for col in ('roic','fcf_yield','operating_margin','net_margin','revenue_cagr_3y'):
    scored[col] = 0.15
scored['net_debt_ebitda'] = 1.0
view.us.data_status = lambda: {'offline':False,'schema_ready':True,'mode':'snapshot','companies':2}
view.us.companies = lambda limit=5000: companies
view.us.scored_universe = lambda: scored
view.us.company_financials = lambda symbol: pd.DataFrame()
view.us.company_market_data = lambda symbol: {'prices': pd.DataFrame(), 'dividends': pd.DataFrame()}
view.us.dossie = lambda symbol: {'classification':'consolidada','classification_reason':'ok','red_flags':[],'notes':{}}
view.render()
""").run(timeout=60)
        assert not app.exception
        html_rendered = "\n".join(element.value for element in app.markdown)
        assert "Eletrônicos de Consumo" in html_rendered
        assert "Consumer Electronics" not in html_rendered
        analyze = next(button for button in app.button if button.label == "Analisar")
        analyze.click().run(timeout=60)
        assert not app.exception
        assert app.session_state["us_selected_ticker"] == "AAPL"
        assert app.session_state["us_active_tab"] == 1
    finally:
        for name, original in originals.items():
            setattr(american_view.us, name, original)


def test_view_b3_continua_renderizando_cards_compartilhados():
    from streamlit.testing.v1 import AppTest

    import views.empresas_b3 as b3_view

    original_load_setores = b3_view._db.load_setores
    try:
        app = AppTest.from_string("""
import pandas as pd
import views.empresas_b3 as view
view._db.load_setores = lambda: pd.DataFrame([
    {'ticker':'PETR3','nome_empresa':'Petrobras','SETOR':'Petróleo','SUBSETOR':'Petróleo e Gás','SEGMENTO':'Exploração'},
    {'ticker':'PETR4','nome_empresa':'Petrobras','SETOR':'Petróleo','SUBSETOR':'Petróleo e Gás','SEGMENTO':'Exploração'},
])
view.render()
""").run(timeout=60)
        assert not app.exception
        assert [button.label for button in app.button].count("Analisar") == 2
    finally:
        b3_view._db.load_setores = original_load_setores
