"""derive_market_cap_history: idade das ações, split e fóssil (A2/A3, 10/11/2026).

Postgres em schema descartável do armazém local (precedente:
tests/test_us_ultimo_preco_nao_nulo.py); sem armazém, pulado. Dados sintéticos.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from tests.apoio_armazem import opcoes_conexao, schema_descartavel

SCHEMA = schema_descartavel("app4_us_mcap")

_DDL = """
CREATE TABLE {s}.prices_monthly (symbol text, month_end date, close numeric,
    adjusted_close numeric, PRIMARY KEY (symbol, month_end));
CREATE TABLE {s}.balance_sheets (symbol text, reference_date date, available_at date,
    shares_outstanding numeric, quality_status text DEFAULT 'validated');
CREATE TABLE {s}.splits (symbol text, split_date date, numerator numeric,
    denominator numeric);
CREATE TABLE {s}.market_cap_history (symbol text, date date, market_cap numeric,
    source text, ingested_at timestamptz DEFAULT now(), PRIMARY KEY (symbol, date));
"""


def _sql(sql: str) -> str:
    return sql.replace("market_us.", f"{SCHEMA}.")


@pytest.fixture()
def conn():
    try:
        from scripts.publish_fii_selection_from_local import _warehouse_url
        motor = create_engine(_warehouse_url(), connect_args=opcoes_conexao(SCHEMA))
        with motor.begin() as c:
            c.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
            c.execute(text(f"CREATE SCHEMA {SCHEMA}"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"armazém local indisponível: {exc}")
    with motor.begin() as c:
        for stmt in _DDL.format(s=SCHEMA).split(";"):
            if stmt.strip():
                c.execute(text(stmt))
        yield c
        c.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
    motor.dispose()


def _roda(c):
    from data_pipeline.us.enrichment import _SQL_MARKET_CAP_UPSERT
    c.execute(text(_sql(_SQL_MARKET_CAP_UPSERT)))


def _mcap(c, simbolo):
    return {str(r[0]): float(r[1]) for r in c.execute(text(
        f"SELECT date, market_cap FROM {SCHEMA}.market_cap_history WHERE symbol=:s"),
        {"s": simbolo})}


def test_acoes_de_2012_com_preco_de_2026_nao_geram_valor_de_mercado(conn):
    conn.execute(text(f"""
        INSERT INTO {SCHEMA}.prices_monthly VALUES ('WMT','2026-10-31',100,100);
        INSERT INTO {SCHEMA}.balance_sheets VALUES ('WMT','2012-01-31','2012-03-27',3418000000);
        INSERT INTO {SCHEMA}.splits VALUES ('WMT','2024-02-26',3,1)"""))
    _roda(conn)
    assert _mcap(conn, "WMT") == {}


def test_balanco_recente_pre_split_e_ajustado_pelo_fator(conn):
    conn.execute(text(f"""
        INSERT INTO {SCHEMA}.prices_monthly VALUES ('X','2024-06-28',10,10);
        INSERT INTO {SCHEMA}.balance_sheets VALUES ('X','2024-01-31','2024-02-20',1000);
        INSERT INTO {SCHEMA}.splits VALUES ('X','2024-02-26',3,1)"""))
    _roda(conn)
    assert _mcap(conn, "X") == {"2024-06-28": pytest.approx(10 * 1000 * 3)}


def test_split_entre_referencia_e_entrega_descarta_o_balanco(conn):
    conn.execute(text(f"""
        INSERT INTO {SCHEMA}.prices_monthly VALUES ('Y','2024-06-28',10,10);
        INSERT INTO {SCHEMA}.balance_sheets VALUES ('Y','2024-01-31','2024-03-15',1000);
        INSERT INTO {SCHEMA}.splits VALUES ('Y','2024-02-26',3,1)"""))
    _roda(conn)
    assert _mcap(conn, "Y") == {}


def test_sem_split_e_dentro_de_18_meses_mantem_o_calculo_original(conn):
    conn.execute(text(f"""
        INSERT INTO {SCHEMA}.prices_monthly VALUES ('Z','2026-10-30',20,20);
        INSERT INTO {SCHEMA}.balance_sheets VALUES ('Z','2026-06-30','2026-08-01',500)"""))
    _roda(conn)
    assert _mcap(conn, "Z") == {"2026-10-30": pytest.approx(10000)}


def test_linha_derivada_que_deixou_de_qualificar_e_removida(conn):
    from data_pipeline.us.enrichment import _SQL_MARKET_CAP_FOSSEIS
    conn.execute(text(f"""
        INSERT INTO {SCHEMA}.market_cap_history VALUES
          ('WMT','2026-10-31',1,'derived_price_x_pit_shares', now() - interval '1 day'),
          ('WMT','2026-09-30',1,'provedor', now() - interval '1 day');
        INSERT INTO {SCHEMA}.prices_monthly VALUES ('Z','2026-10-30',20,20);
        INSERT INTO {SCHEMA}.balance_sheets VALUES ('Z','2026-06-30','2026-08-01',500)"""))
    _roda(conn)
    conn.execute(text(_sql(_SQL_MARKET_CAP_FOSSEIS)))
    assert _mcap(conn, "WMT") == {"2026-09-30": 1.0}   # derivada some; do provedor fica
    assert "2026-10-30" in _mcap(conn, "Z")


def test_piso_de_cardinalidade_protege_contra_regeneracao_vazia_ou_parcial():
    from data_pipeline.us.enrichment import pode_remover_fosseis
    assert pode_remover_fosseis(0, 10)
    assert pode_remover_fosseis(100, 100)
    assert pode_remover_fosseis(100, 50)
    assert not pode_remover_fosseis(100, 0)
    assert not pode_remover_fosseis(100, 10)
