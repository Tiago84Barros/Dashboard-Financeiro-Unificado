"""O upsert do pipeline de mercado não reescreve a linha que não mudou.

Sem a guarda, cada re-ingestão fazia UPDATE da linha idêntica: no Supabase
eram 3,25 milhões de UPDATEs em `market.dividends` e 718 mil em
`market.historical_prices`, cada um deixando uma tupla morta. O conteúdo da
tabela sai igual com ou sem a guarda, então só o `xmin` (a transação que
escreveu a versão viva da linha) denuncia a reescrita.

Rodam contra um banco descartável no armazém local (porta 5433): a cláusula
WHERE do ON CONFLICT só existe executando de verdade. Sem armazém, são pulados.
"""
import uuid

import pytest
from sqlalchemy import create_engine, text

from data_pipeline.market import repository as repo

_DDL = """
CREATE SCHEMA market;
CREATE TABLE market.historical_prices (
    ticker text, date date, open numeric, high numeric, low numeric,
    close numeric, adjusted_close numeric, volume bigint,
    UNIQUE (ticker, date));
CREATE TABLE market.dividends (
    ticker text, event_date date, ex_date date, payment_date date, type text,
    amount numeric(18, 6), source text,
    UNIQUE (ticker, event_date, type, amount));
CREATE TABLE market.assets (
    ticker text UNIQUE, company_id int, asset_type text, exchange text,
    currency text, is_active boolean);
"""


@pytest.fixture
def banco():
    try:
        from scripts.construir_memoria_mercado import warehouse_url
        url = warehouse_url()
    except Exception as erro:  # noqa: BLE001
        pytest.skip(f"sem armazem local ({erro})")
    nome = f"teste_upsert_{uuid.uuid4().hex[:10]}"
    administrador = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with administrador.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{nome}"'))
    except Exception as erro:  # noqa: BLE001
        administrador.dispose()
        pytest.skip(f"armazem local nao respondeu ({erro})")
    engine = create_engine(url.rsplit("/", 1)[0] + f"/{nome}")
    with engine.begin() as conn:
        conn.exec_driver_sql(_DDL)
    repo.reset_db_cols_cache()
    try:
        yield engine
    finally:
        repo.reset_db_cols_cache()
        engine.dispose()
        with administrador.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{nome}" WITH (FORCE)'))
        administrador.dispose()


def _gravar(engine, tabela, linhas):
    with engine.begin() as conn:
        repo.upsert(conn, tabela, linhas)


def _versao(engine, tabela, where):
    with engine.connect() as conn:
        return conn.execute(text(
            f"SELECT xmin::text AS x, * FROM market.{tabela} WHERE {where}")).mappings().one()


def _preco(close=10.5):
    return {"ticker": "PETR4", "date": "2026-09-25", "open": 10.0, "high": 11.0,
            "low": 9.5, "close": close, "adjusted_close": close, "volume": 1000}


def test_preco_igual_nao_reescreve_e_preco_novo_reescreve(banco):
    _gravar(banco, "historical_prices", [_preco()])
    antes = _versao(banco, "historical_prices", "ticker = 'PETR4'")

    _gravar(banco, "historical_prices", [_preco()])
    assert _versao(banco, "historical_prices", "ticker = 'PETR4'")["x"] == antes["x"]

    _gravar(banco, "historical_prices", [_preco(close=10.7)])
    depois = _versao(banco, "historical_prices", "ticker = 'PETR4'")
    assert depois["x"] != antes["x"]
    assert float(depois["close"]) == pytest.approx(10.7)


def test_valor_que_vira_nulo_conta_como_mudanca(banco):
    """`=` daria NULL (nem verdadeiro nem falso) e pularia a atualização."""
    _gravar(banco, "historical_prices", [_preco()])
    _gravar(banco, "historical_prices", [{**_preco(), "adjusted_close": None}])
    assert _versao(banco, "historical_prices", "ticker = 'PETR4'")["adjusted_close"] is None


def test_provento_com_a_mesma_fonte_nao_reescreve(banco):
    linha = {"ticker": "ITSA4", "event_date": "2026-08-01", "ex_date": "2026-07-20",
             "payment_date": "2026-08-01", "type": "JCP", "amount": 0.0215,
             "source": "brapi"}
    _gravar(banco, "dividends", [linha])
    antes = _versao(banco, "dividends", "ticker = 'ITSA4'")
    _gravar(banco, "dividends", [linha])
    assert _versao(banco, "dividends", "ticker = 'ITSA4'")["x"] == antes["x"]
    _gravar(banco, "dividends", [{**linha, "source": "cvm"}])
    assert _versao(banco, "dividends", "ticker = 'ITSA4'")["source"] == "cvm"


def test_classificacao_forte_de_ativo_segue_protegida(banco):
    """A guarda compara com o valor que o CASE gravaria, não com EXCLUDED."""
    ativo = {"ticker": "HGLG11", "company_id": None, "asset_type": "fii",
             "exchange": "B3", "currency": "BRL", "is_active": True}
    _gravar(banco, "assets", [ativo])
    antes = _versao(banco, "assets", "ticker = 'HGLG11'")

    _gravar(banco, "assets", [{**ativo, "asset_type": "stock"}])
    depois = _versao(banco, "assets", "ticker = 'HGLG11'")
    assert depois["asset_type"] == "fii"
    assert depois["x"] == antes["x"]  # rebaixamento recusado = nada a gravar

    _gravar(banco, "assets", [{**ativo, "is_active": False}])
    assert _versao(banco, "assets", "ticker = 'HGLG11'")["is_active"] is False
