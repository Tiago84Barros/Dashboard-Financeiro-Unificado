"""Barra sem fechamento não pode virar "último preço".

Em 02/10/2026 a barra de ``market_us.prices_daily`` chegou com 2.560 de 2.627
fechamentos nulos (abertura, máxima, mínima e volume presentes: o provedor
devolveu o pregão sem o ``Close``). As leituras de "último preço" ordenam por
data e pegam a primeira linha -- inclusive a nula: ``_latest_close('AAPL')``
devolvia ``None`` e o próximo ``load_scoring_frame`` apagaria o valor de mercado
derivado de ~97% do universo. A derivação mensal tinha o mesmo DISTINCT ON e
descartava o mês inteiro em vez de cair no pregão anterior.

As consultas com DISTINCT ON são de Postgres; rodam num schema descartável do
armazém local (``tests/apoio_armazem.py``), com o ``market_us.`` do SQL trocado
pelo schema do teste. Sem armazém, esses testes são pulados.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import StaticPool

from tests.apoio_armazem import opcoes_conexao, schema_descartavel

SCHEMA = schema_descartavel("app4_us_ultimo_preco")

# (symbol, date, close, adjusted_close): AAPL e MSFT com a barra de 02/10 nula,
# KO com a barra de 02/10 inteira -- o filtro não pode esconder barra boa.
BARRAS = [
    ("AAPL", "2026-09-30", 333.02, 333.02),
    ("AAPL", "2026-10-01", 330.32, 330.32),
    ("AAPL", "2026-10-02", None, None),
    ("MSFT", "2026-10-01", 500.10, 500.10),
    ("MSFT", "2026-10-02", None, None),
    ("KO", "2026-10-01", 70.00, 70.00),
    ("KO", "2026-10-02", 71.00, 71.00),
]

_DDL_DIARIO = """
    CREATE TABLE {schema}.prices_daily (
        symbol text NOT NULL, date date NOT NULL,
        open numeric, high numeric, low numeric,
        close numeric, adjusted_close numeric, volume bigint,
        source text, ingested_at timestamptz DEFAULT now(),
        PRIMARY KEY (symbol, date)
    )
"""


def _insere_barras(conn, schema: str | None = None):
    tabela = f"{schema}.prices_daily" if schema else "market_us.prices_daily"
    for symbol, dia, close, adj in BARRAS:
        conn.execute(text(
            f"INSERT INTO {tabela} (symbol, date, open, high, low, close, "
            "adjusted_close, volume, source) "
            "VALUES (:s, :d, 1, 1, 1, :c, :a, 1000, 'fmp')"),
            {"s": symbol, "d": dia, "c": close, "a": adj})


# ── _latest_close: consulta simples, roda em SQLite ───────────────────────────
@pytest.fixture()
def sqlite_market_us():
    motor = create_engine("sqlite://", poolclass=StaticPool)

    @event.listens_for(motor, "connect")
    def _anexa(dbapi_conn, _record):
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS market_us")

    with motor.begin() as conn:
        conn.execute(text(
            "CREATE TABLE market_us.prices_daily (symbol TEXT, date TEXT, open REAL, "
            "high REAL, low REAL, close REAL, adjusted_close REAL, volume INTEGER, "
            "source TEXT, PRIMARY KEY (symbol, date))"))
        _insere_barras(conn)
    yield motor
    motor.dispose()


def test_ultimo_fechamento_pula_a_barra_sem_close(sqlite_market_us):
    from core.us_read import _latest_close

    with sqlite_market_us.connect() as conn:
        assert _latest_close(conn, "AAPL") == pytest.approx(330.32)
        assert _latest_close(conn, "KO") == pytest.approx(71.00)


# ── DISTINCT ON: Postgres, schema descartável no armazém ──────────────────────
@pytest.fixture(scope="module")
def armazem():
    try:
        from scripts.publish_fii_selection_from_local import _warehouse_url
        motor = create_engine(_warehouse_url(), connect_args=opcoes_conexao(SCHEMA))
        with motor.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
            conn.execute(text(f"CREATE SCHEMA {SCHEMA}"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"armazém local indisponível: {exc}")
    with motor.begin() as conn:
        conn.execute(text(_DDL_DIARIO.format(schema=SCHEMA)))
        conn.execute(text(f"""
            CREATE TABLE {SCHEMA}.prices_monthly (
                symbol text NOT NULL, month_end date NOT NULL,
                close numeric, adjusted_close numeric, volume bigint,
                total_return numeric, source text,
                PRIMARY KEY (symbol, month_end)
            )
        """))
        _insere_barras(conn, SCHEMA)
    yield motor
    with motor.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
    motor.dispose()


def _no_schema_do_teste(sql: str) -> str:
    assert "market_us." in sql
    return sql.replace("market_us.", f"{SCHEMA}.")


def test_ultimo_preco_do_scoring_pula_a_barra_sem_close(armazem):
    from core.us_read import _SQL_ULTIMO_PRECO_POR_SIMBOLO

    with armazem.connect() as conn:
        linhas = dict(conn.execute(text(
            _no_schema_do_teste(_SQL_ULTIMO_PRECO_POR_SIMBOLO))).all())

    assert linhas == {"AAPL": Decimal("330.32"), "MSFT": Decimal("500.10"),
                      "KO": Decimal("71.00")}


def test_derivacao_mensal_cai_no_ultimo_pregao_com_close(armazem):
    from data_pipeline.us.scoring_history import _SQL_DERIVA_MENSAL

    with armazem.begin() as conn:
        conn.execute(text(_no_schema_do_teste(_SQL_DERIVA_MENSAL)))
        outubro = dict(conn.execute(text(
            f"SELECT symbol, month_end FROM {SCHEMA}.prices_monthly "
            "WHERE month_end >= '2026-10-01'")).all())

    assert outubro == {"AAPL": date(2026, 10, 1), "MSFT": date(2026, 10, 1),
                       "KO": date(2026, 10, 2)}


# ── Ingestão: barra sem close não entra ───────────────────────────────────────
def test_ingestao_pula_barra_sem_close_e_grava_as_outras(monkeypatch):
    from data_pipeline.us import repository as repo

    gravadas: list[dict] = []

    def _captura(conn, table, rows, conflict, update=None):
        gravadas.extend(rows)
        return len(rows)

    monkeypatch.setattr(repo, "_exec_many", _captura)
    n = repo.upsert_prices_daily(None, "AAPL", [
        {"date": "2026-10-01", "open": 330.0, "close": 330.32, "adjClose": 330.32,
         "volume": 36306300},
        # O que o provedor devolveu em 02/10: pregão sem fechamento.
        {"date": "2026-10-02", "open": 333.2, "high": 334.5, "low": 330.6,
         "close": None, "adjClose": None, "volume": 31878433},
    ])

    assert n == 1
    assert [r["date"] for r in gravadas] == ["2026-10-01"]
