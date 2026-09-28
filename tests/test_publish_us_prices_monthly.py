"""Publicacao da serie mensal dos EUA do warehouse local para o Supabase."""
import pytest
from sqlalchemy import create_engine, text

from scripts import publish_us_prices_monthly as pub

LINHAS = [
    ("AAPL", "2024-01-31", 100.0, 99.0, 1000, 0.01),
    ("AAPL", "2024-02-29", 110.0, 109.0, 1100, 0.10),
    ("MSFT", "2024-01-31", 200.0, 199.0, 2000, 0.02),
]


def _cria_prices(conn):
    conn.execute(text("""
        CREATE TABLE prices_monthly (
            symbol TEXT NOT NULL, month_end TEXT NOT NULL,
            close REAL, adjusted_close REAL, volume INTEGER, total_return REAL,
            source TEXT DEFAULT 'local', ingested_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (symbol, month_end)
        )
    """))


@pytest.fixture()
def local():
    eng = create_engine("sqlite:///:memory:")
    with eng.begin() as c:
        _cria_prices(c)
        for r in LINHAS:
            c.execute(text("INSERT INTO prices_monthly (symbol, month_end, close, "
                           "adjusted_close, volume, total_return) "
                           "VALUES (:a,:b,:c,:d,:e,:f)"), dict(zip("abcdef", r)))
    return eng


@pytest.fixture()
def remoto():
    eng = create_engine("sqlite:///:memory:")
    with eng.begin() as c:
        _cria_prices(c)
        c.execute(text("CREATE TABLE us_portfolio_model_items "
                       "(model_id TEXT, symbol TEXT, weight REAL)"))
        for s in ("AAPL", "MSFT", "AAPL"):
            c.execute(text("INSERT INTO us_portfolio_model_items VALUES ('m1', :s, 0.5)"),
                      {"s": s})
    return eng


def test_simbolos_vem_das_carteiras_sem_repetir(remoto):
    assert pub.simbolos_das_carteiras(engine=remoto) == ["AAPL", "MSFT"]


def test_simbolos_com_tabela_ausente_devolve_vazio():
    assert pub.simbolos_das_carteiras(engine=create_engine("sqlite:///:memory:")) == []


def test_ler_do_local_traz_as_colunas_esperadas(local):
    df = pub.ler_do_local(["AAPL"], engine=local)
    assert list(df.columns) == ["symbol", "month_end", "close",
                                "adjusted_close", "volume", "total_return"]
    assert len(df) == 2


def test_ler_do_local_sem_simbolos_devolve_vazio(local):
    assert pub.ler_do_local([], engine=local).empty


def test_simulacao_nao_grava_nada(local, remoto):
    resumo = pub.publicar(local=local, remoto=remoto, apply=False)
    assert resumo == {"AAPL": 2, "MSFT": 1}
    with remoto.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM prices_monthly")).scalar() == 0


def test_apply_grava_e_e_idempotente(local, remoto):
    pub.publicar(local=local, remoto=remoto, apply=True)
    pub.publicar(local=local, remoto=remoto, apply=True)
    with remoto.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM prices_monthly")).scalar() == 3


def test_simbolo_sem_serie_no_local_aparece_com_zero(local, remoto):
    with remoto.begin() as c:
        c.execute(text("INSERT INTO us_portfolio_model_items VALUES ('m1','ZZZZ',0.1)"))
    resumo = pub.publicar(local=local, remoto=remoto, apply=False)
    assert resumo["ZZZZ"] == 0, "simbolo sem serie precisa aparecer, nao sumir"


def test_publicar_respeita_lista_explicita_de_simbolos(local, remoto):
    resumo = pub.publicar(local=local, remoto=remoto, apply=False, simbolos=["MSFT"])
    assert set(resumo) == {"MSFT"}


def test_nan_vira_none_antes_de_gravar():
    """NaN numa coluna numeric do PostgreSQL grava 'NaN', que passa por número.

    O SQLite do teste guardaria NaN como NULL de qualquer jeito, então a guarda
    é conferida no que sai para o banco, não no que o SQLite devolve.
    """
    import math

    import pandas as pd

    df = pd.DataFrame([("AAPL", "2024-01-31", 100.0, 99.0, 1000, math.nan)],
                      columns=pub._COLUNAS)
    (reg,) = pub._registros(df)
    assert reg["total_return"] is None
    assert reg["close"] == 100.0 and reg["volume"] == 1000


def test_grava_em_lotes_menores_que_o_total(local, remoto):
    df = pub.ler_do_local(["AAPL", "MSFT"], engine=local)
    pub._gravar(df, engine=remoto, lote=2)
    with remoto.connect() as c:
        linhas = c.execute(text("SELECT symbol, month_end, close FROM prices_monthly "
                                "ORDER BY symbol, month_end")).all()
    assert [tuple(r) for r in linhas] == [("AAPL", "2024-01-31", 100.0),
                                          ("AAPL", "2024-02-29", 110.0),
                                          ("MSFT", "2024-01-31", 200.0)]


def test_regravar_atualiza_o_valor_existente(local, remoto):
    pub.publicar(local=local, remoto=remoto, apply=True)
    with local.begin() as c:
        c.execute(text("UPDATE prices_monthly SET close = 111.0 "
                       "WHERE symbol='AAPL' AND month_end='2024-02-29'"))
    pub.publicar(local=local, remoto=remoto, apply=True)
    with remoto.connect() as c:
        assert c.execute(text("SELECT close FROM prices_monthly WHERE symbol='AAPL' "
                              "AND month_end='2024-02-29'")).scalar() == 111.0


def test_lote_que_falha_nao_desfaz_os_anteriores(local, remoto, monkeypatch):
    """Cada lote tem a sua transação: uma queda no meio deixa o que já entrou,
    e rodar de novo completa o resto (o INSERT é idempotente)."""
    df = pub.ler_do_local(["AAPL", "MSFT"], engine=local)
    reais = remoto.begin
    chamadas = {"n": 0}

    def _begin():
        chamadas["n"] += 1
        if chamadas["n"] == 2:
            raise RuntimeError("conexão caiu")
        return reais()

    monkeypatch.setattr(remoto, "begin", _begin)
    with pytest.raises(RuntimeError):
        pub._gravar(df, engine=remoto, lote=2)
    monkeypatch.setattr(remoto, "begin", reais)
    with remoto.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM prices_monthly")).scalar() == 2
    pub._gravar(df, engine=remoto, lote=2)
    with remoto.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM prices_monthly")).scalar() == 3
