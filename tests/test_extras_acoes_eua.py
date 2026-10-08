"""Ação americana da Nomad é "Ações EUA", não "ETF Internacional".

Com o rótulo de ETF, a Inteligência dos Ativos lia MELI pelo catálogo de ETF
e mostrava fundamentos, valuation e pares vazios.
"""
from types import SimpleNamespace

import pytest

import core.investimentos as investimentos
from core import classe_exterior, stress_tests
from core.global_portfolio import carteira_real
from core.inteligencia_ativos import calculos, fundamentos


def _carteira_vazia():
    return {"posicoes": [], "total_investido": 0.0, "total_mercado": 0.0,
            "num_ativos": 0, "rentabilidade_total_pct": 0.0,
            "por_classe": [], "por_setor": []}


def _linha(ticker, asset_class):
    return SimpleNamespace(
        ticker=ticker, quantity=1.0, average_price=100.0,
        total_invested=100.0, current_price=110.0, usd_brl_rate=6.0,
        asset_name=ticker, asset_class=asset_class, currency="USD",
        sector=None)


def _posicao(ticker, asset_class):
    carteira = _carteira_vazia()
    investimentos._adicionar_extras_ao_snapshot(
        carteira, [_linha(ticker, asset_class)])
    return carteira["posicoes"][0]


def test_acao_americana_vira_acoes_eua_e_e_lida_como_acao():
    pos = _posicao("MELI", "stock")
    assert pos["classe"] == "Ações EUA"
    assert fundamentos.tipo_do_ativo(pos["classe"], pos["moeda"]) \
        == fundamentos.ACAO


@pytest.mark.parametrize("asset_class", ["etf", "etf_intl", None, ""])
def test_etf_e_classe_desconhecida_seguem_etf_internacional(asset_class):
    pos = _posicao("SPY", asset_class)
    assert pos["classe"] == "ETF Internacional"
    assert fundamentos.tipo_do_ativo(pos["classe"], pos["moeda"]) \
        == fundamentos.ETF


def test_acoes_eua_entra_nos_mapas_de_classe():
    pos = _posicao("MELI", "stock")
    assert calculos.CLASSE_POLITICA["Ações EUA"] == "exterior"
    assert stress_tests._CLASSE_TO_SHOCK["Ações EUA"] == "shock_etf_intl"
    assert carteira_real.classe_global(pos) == "us"
    assert "Ações EUA" in investimentos._CLASSES_COM_PROVENTO


# ── Cadastro com classe errada ──────────────────────────────────────────────
# O importador de PDF da Nomad gravava todo ativo como "etf", e o cadastro
# nunca é reescrito: a classe do banco não basta para separar ação de ETF.

def _posicao_nomeada(ticker, asset_class, nome):
    linha = _linha(ticker, asset_class)
    linha.asset_name = nome
    carteira = _carteira_vazia()
    investimentos._adicionar_extras_ao_snapshot(carteira, [linha])
    return carteira


def test_acao_cadastrada_como_etf_vira_acoes_eua_no_grafico_por_classe():
    carteira = _posicao_nomeada("MELI", "etf", "MERCADOLIBRE INC")
    assert carteira["posicoes"][0]["classe"] == "Ações EUA"
    nomes = {c["nome"] for c in carteira["por_classe"]}
    assert nomes == {"Ações EUA"}


@pytest.mark.parametrize("ticker,nome,classe,esperado", [
    ("MELI", "MELI", "etf", "stock_us"),                 # universo da SEC
    ("XYZQ", "Some Company Inc", "etf", "stock_us"),     # sufixo de empresa
    ("XYZQ", "XYZQ", "stock", "stock_us"),               # cadastro de ação
    ("SPY", "SPY", "stock", "etf_intl"),                 # ETF conhecido
    ("XYZQ", "Some Bond ETF", "stock", "etf_intl"),      # nome de fundo
    ("XYZQ", "XYZQ", "etf", "etf_intl"),                 # sem sinal: ETF
    ("SCHW", "The Charles Schwab Corporation", "stock", "stock_us"),
    ("WT", "WisdomTree, Inc.", "stock", "stock_us"),     # gestora, mas empresa
    ("XYZQ", "Vanguard Total Market", "etf", "etf_intl"),  # marca de gestora
    ("MSDL", "Morgan Stanley Direct Lending Fund", "stock", "stock_us"),
    ("MSDL", "Morgan Stanley Direct Lending Fund", None, "etf_intl"),
    ("PSA", "Public Storage", "etf", "stock_us"),         # REIT conhecido
    ("XYZR", "EQUITY RESIDENTIAL", "etf", "stock_us"),    # termo de REIT
    ("XYZR", "Some Realty Trust", None, "stock_us"),
    ("GBTC", "Grayscale Bitcoin Trust", None, "etf_intl"),
    ("XYZQ", None, None, "etf_intl"),
])
def test_classe_ativo_usd(ticker, nome, classe, esperado):
    assert classe_exterior.classe_ativo_usd(
        ticker, nome, classe, universo={"MELI", "AAPL"}) == esperado


def test_universo_publicado_tem_acoes_e_nao_tem_etfs():
    universo = classe_exterior.universo_acoes_eua()
    assert {"MELI", "AAPL"} <= universo
    assert not {"SPY", "IEFA", "SGOV"} & universo


@pytest.mark.parametrize("raw,ticker,nome,esperado", [
    ("stock", "AAPL", "Apple Inc", "stock_us"),
    ("etf", "MELI", "MercadoLibre Inc", "stock_us"),
    ("etf", "SPY", "SPDR S&P 500 ETF Trust", "etf_intl"),
    ("", "SPY", None, "etf_intl"),                       # tipo ausente, ETF
    ("", "AAPL", "Apple Inc", "stock_us"),
    (None, "XYZQ", "XYZQ", "other"),                     # sem sinal: desconhecido
])
def test_snapshot_do_exterior_separa_acao_de_etf(raw, ticker, nome, esperado):
    assert investimentos._class_key_from_snapshot(raw, ticker, "US", nome) \
        == esperado


# ── Proventos e correção do cadastro ────────────────────────────────────────

def _provento(ticker, nome, classe, moeda):
    return SimpleNamespace(
        id="1", type="dividend", amount_per_unit=0.25, quantity=10.0,
        total_amount=2.5, ex_date=None, payment_date=None, external_id=None,
        ticker=ticker, asset_name=nome, asset_class=classe, currency=moeda)


def test_provento_em_dolar_separa_acao_eua_de_etf(monkeypatch):
    from core import database, proventos

    linhas = [_provento("AAPL", "Apple Inc", "stock", "USD"),
              _provento("SPY", "SPDR S&P 500 ETF Trust", "etf", "USD"),
              _provento("PETR4", "Petrobras", "stock", "BRL")]

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, *a, **k):
            return SimpleNamespace(fetchall=lambda: linhas)

    monkeypatch.setattr(proventos.settings, "OWNER_USER_ID", "u")
    monkeypatch.setattr(database, "get_engine",
                        lambda: SimpleNamespace(connect=_Conn))
    classes = {e["ticker"]: e["classe"]
               for e in proventos._proventos_real()["eventos"]}
    assert classes == {"AAPL": "Ações EUA", "SPY": "ETF Internacional",
                       "PETR4": "Ações BR"}


def test_script_regrava_so_acao_cadastrada_como_etf():
    import importlib.util
    from pathlib import Path

    caminho = Path(__file__).resolve().parents[1] / "scripts" / \
        "corrige_classe_acoes_eua.py"
    spec = importlib.util.spec_from_file_location("corrige_classe", caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    linhas = [{"id": "1", "ticker": "MELI", "name": "MELI"},
              {"id": "2", "ticker": "XYZQ", "name": "Some Company Inc"},
              {"id": "3", "ticker": "SPY", "name": "SPDR S&P 500 ETF Trust"},
              {"id": "4", "ticker": "IEFA", "name": "IEFA"},
              {"id": "5", "ticker": "XYZW", "name": "XYZW"},
              {"id": "6", "ticker": "EQR", "name": "EQUITY RESIDENTIAL"}]
    acoes, etfs = mod.separar(linhas, {"MELI", "AAPL"})
    assert [a["ticker"] for a in acoes] == ["MELI", "XYZQ", "EQR"]
    assert [e["ticker"] for e in etfs] == ["SPY", "IEFA", "XYZW"]
    assert "class = 'etf'" in mod.SQL_REGRAVA  # guarda de idempotência


def _posicao_db(asset_id, ticker, nome, classe, moeda, investido, mercado,
                fx=None):
    return SimpleNamespace(
        asset_id=asset_id, ticker=ticker, asset_name=nome, asset_class=classe,
        currency=moeda, total_invested=investido, current_market_value=mercado,
        usd_brl_rate=fx)


def test_resumo_do_financeiro_separa_classes_e_converte_dolar():
    from core import financeiro

    linhas = [
        _posicao_db("1", "PETR4", "Petrobras", "stock", "BRL", 100.0, 120.0),
        _posicao_db("2", "AAPL", "Apple Inc", "stock", "USD", 50.0, 60.0, 5.0),
        _posicao_db("3", "MELI", "MERCADOLIBRE INC", "etf", "USD", 50.0, 40.0, 5.0),
        _posicao_db("4", "SPY", "SPDR S&P 500 ETF Trust", "etf", "USD", 30.0, 30.0, 5.0),
        _posicao_db("5", "BOVA11", "iShares Ibovespa", "etf", "BRL", 10.0, 10.0),
        _posicao_db("5", "BOVA11", "iShares Ibovespa", "etf", "BRL", 10.0, 10.0),
    ]
    classes, num_ativos, total = financeiro._resumo_por_classe(
        linhas, universo={"AAPL", "MELI"},
        fx_compra={"AAPL": {"taxa_media": 4.0, "cobertura": 1.0}})
    por_nome = {c["nome"]: c for c in classes}
    assert [c["nome"] for c in classes] == \
        ["Ações EUA", "ETF Internacional", "Ações BR", "ETF"]
    # mercado pelo câmbio de hoje (5): (60 + 40) * 5
    assert por_nome["Ações EUA"]["valor"] == 500.0
    # custo: AAPL pelo câmbio da compra (4), MELI sem ele pelo de hoje (5)
    assert por_nome["Ações EUA"]["rentab_mes_pct"] == round((500 - 450) / 450 * 100, 2)
    assert por_nome["ETF Internacional"]["valor"] == 150.0
    assert por_nome["Ações BR"]["valor"] == 120.0
    assert por_nome["ETF"]["valor"] == 20.0          # duas carteiras, um ativo
    assert total == 790.0
    assert round(sum(c["pct_carteira"] for c in classes)) == 100
    assert num_ativos == 5


def test_resumo_do_financeiro_sem_cambio_deixa_dolar_de_fora(monkeypatch):
    from core import financeiro

    monkeypatch.setattr(investimentos, "_get_usd_brl_live", lambda: None)
    linhas = [
        _posicao_db("1", "PETR4", "Petrobras", "stock", "BRL", 100.0, 120.0),
        _posicao_db("2", "AAPL", "Apple Inc", "stock", "USD", 50.0, 60.0, None),
    ]
    classes, num_ativos, total = financeiro._resumo_por_classe(
        linhas, universo={"AAPL"})
    assert [c["nome"] for c in classes] == ["Ações BR"]
    assert (num_ativos, total) == (1, 120.0)


def test_resumo_do_financeiro_usa_cambio_ao_vivo_sem_cotacao_no_banco(monkeypatch):
    from core import financeiro

    monkeypatch.setattr(investimentos, "_get_usd_brl_live", lambda: 5.5)
    linhas = [_posicao_db("2", "AAPL", "Apple Inc", "stock", "USD", 50.0, 60.0)]
    classes, _, total = financeiro._resumo_por_classe(linhas, universo={"AAPL"})
    assert classes[0]["nome"] == "Ações EUA"
    assert total == 330.0


def test_visao_geral_sem_carteira_migrada_soma_patrimonio_em_reais(monkeypatch):
    """v_net_worth soma dólar como real; o fallback usa o total convertido."""
    import core.investimentos as inv_mod
    import core.proventos as prov_mod
    from core import classe_exterior, database, financeiro

    posicoes = [
        _posicao_db("1", "PETR4", "Petrobras", "stock", "BRL", 100.0, 120.0),
        _posicao_db("2", "AAPL", "Apple Inc", "etf", "USD", 50.0, 60.0, 5.0),
    ]

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, *a, **k):
            q = str(sql)
            if "v_net_worth" in q:
                linha = SimpleNamespace(bank_balance=1000, investment_total=180,
                                        net_worth=1180)
                return SimpleNamespace(fetchone=lambda: linha)
            if "portfolio_positions" in q:
                return SimpleNamespace(fetchall=lambda: posicoes)
            return SimpleNamespace(fetchall=lambda: [], fetchone=lambda: None)

    monkeypatch.setattr(financeiro.settings, "OWNER_USER_ID", "u")
    monkeypatch.setattr(database, "get_engine",
                        lambda: SimpleNamespace(connect=_Conn))
    monkeypatch.setattr(inv_mod, "get_carteira", lambda: {"data_source": "mock"})
    monkeypatch.setattr(prov_mod, "get_proventos", lambda: {})
    monkeypatch.setattr(classe_exterior, "universo_acoes_eua",
                        lambda: frozenset({"AAPL"}))

    dados = financeiro._visao_geral_real()
    assert dados["patrimonio"]["investido"] == 420.0     # 120 + 60 * 5
    assert dados["patrimonio"]["total"] == 1420.0
    nomes = [c["nome"] for c in dados["classes_ativo"]]
    assert nomes == ["Ações EUA", "Ações BR"]


def test_migration_081_views_em_reais_preservam_contrato_da_007():
    """CREATE OR REPLACE VIEW exige as mesmas colunas, na mesma ordem.

    A 081 foi validada contra um Postgres 16 ao ser escrita; aqui ficam as
    garantias que o texto consegue dar.
    """
    from pathlib import Path

    schema = Path(__file__).resolve().parents[1] / "supabase_unificado" / "schema"
    sql = (schema / "081_views_investimento_em_reais.sql").read_text(encoding="utf-8")

    def _select_final(view: str) -> str:
        corpo = sql.split(f"CREATE OR REPLACE VIEW {view}", 1)[1].split(";", 1)[0]
        return corpo.rsplit("\nSELECT", 1)[1].split("\nFROM", 1)[0]

    def _em_ordem(texto: str, colunas: list[str]) -> bool:
        posicoes = [texto.find(c) for c in colunas]
        return -1 not in posicoes and posicoes == sorted(posicoes)

    assert _em_ordem(_select_final("v_investment_summary"), [
        "user_id", "asset_class", "AS asset_count", "AS total_invested",
        "AS current_market_value", "AS unrealized_pnl", "AS return_pct"])
    assert _em_ordem(_select_final("v_net_worth"), [
        "AS user_id", "AS bank_balance", "AS investment_total", "AS net_worth",
        "AS usd_positions_without_fx", "AS accounts_without_fx"])
    # a 027 deixou as views como security_invoker; CREATE OR REPLACE desfaria
    for view in ("v_investment_summary", "v_net_worth"):
        assert f"VIEW {view}\nWITH (security_invoker = true) AS" in sql
    assert "fx.taxa >= 2.0" in sql                      # câmbio corrompido fica fora
    assert "WHEN 'USD' THEN fx.taxa" in sql              # conta em dólar convertida
    assert "HAVING count(*) = count(taxa)" in sql        # só cobertura total
    assert "::VARCHAR(50) AS asset_class" in sql         # mesmo tipo da 007
