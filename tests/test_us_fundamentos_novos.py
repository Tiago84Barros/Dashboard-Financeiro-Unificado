"""Seleção dos fundamentos EUA a reingerir e teto de tempo (A1, 10/11/2026).

Postgres em schema descartável (sem armazém, pulado). Dados sintéticos.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from tests.apoio_armazem import opcoes_conexao, schema_descartavel

SCHEMA = schema_descartavel("app4_us_novos")

_DDL = """
CREATE TABLE {s}.assets (symbol text, company_id int, is_active bool,
    analysis_status text, security_type text);
CREATE TABLE {s}.income_statements (company_id int, available_at date, updated_at timestamptz DEFAULT now());
CREATE TABLE {s}.sec_filings (symbol text, form text, filing_date date);
"""


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


def _roda(c, limit=None):
    from data_pipeline.us import fundamentos_novos as fn
    sql = fn.SQL_SIMBOLOS_COM_FILING_NOVO.replace("market_us.", f"{SCHEMA}.")
    if limit:
        sql += f" LIMIT {limit}"
    return [r[0] for r in c.execute(text(sql), {"formularios": list(fn.FORMULARIOS), "desde": "2000-01-01"})]


def test_so_empresa_com_10k_ou_10q_posterior_a_ultima_extracao(conn):
    conn.execute(text(f"""
        INSERT INTO {SCHEMA}.assets VALUES
          ('NOVA',1,true,'eligible','common'), ('EMDIA',2,true,'eligible','common'),
          ('SO8K',3,true,'eligible','common'), ('ETF',4,true,'excluded','etf');
        INSERT INTO {SCHEMA}.income_statements (company_id, available_at) VALUES
          (1,'2026-10-02'),(2,'2026-11-09'),(3,'2026-10-02'),(4,'2026-10-02');
        INSERT INTO {SCHEMA}.sec_filings VALUES
          ('NOVA','10-K','2026-11-03'), ('EMDIA','10-Q','2026-10-20'),
          ('SO8K','8-K','2026-11-05'), ('ETF','10-K','2026-11-05')"""))
    assert _roda(conn) == ["NOVA"]


def test_mais_atrasada_primeiro_e_lote_limita(conn):
    conn.execute(text(f"""
        INSERT INTO {SCHEMA}.assets VALUES
          ('B',2,true,'eligible','common'), ('A',1,true,'eligible','common'),
          ('C',3,true,'eligible','common');
        INSERT INTO {SCHEMA}.income_statements (company_id, available_at) VALUES
          (1,'2026-10-02'),(2,'2026-10-02'),(3,'2026-10-02');
        INSERT INTO {SCHEMA}.sec_filings VALUES
          ('A','10-Q','2026-11-05'), ('B','10-K','2026-10-10'), ('C','10-Q','2026-11-01')"""))
    assert _roda(conn) == ["B", "C", "A"]
    assert _roda(conn, limit=2) == ["B", "C"]


def test_empresa_adiada_continua_na_fila_depois_do_enrich(conn):
    """O `enrich` renova `updated_at` de todas as linhas (trigger); a fila nao pode ler isso."""
    conn.execute(text(f"""
        INSERT INTO {SCHEMA}.assets VALUES ('X',1,true,'eligible','common');
        INSERT INTO {SCHEMA}.income_statements (company_id, available_at)
            VALUES (1,'2026-10-02');
        INSERT INTO {SCHEMA}.sec_filings VALUES ('X','10-K','2026-11-03')"""))
    assert _roda(conn) == ["X"]
    conn.execute(text(f"UPDATE {SCHEMA}.income_statements SET updated_at=now()"))  # enrich
    assert _roda(conn) == ["X"]
    # reingerida: o fato entra com available_at = data da entrega -> sai da fila
    conn.execute(text(f"UPDATE {SCHEMA}.income_statements SET available_at='2026-11-03'"))
    assert _roda(conn) == []


def test_teto_de_tempo_adia_o_resto_sem_chamar_o_provedor(monkeypatch):
    from data_pipeline.us import ingest

    chamados = []
    monkeypatch.setattr(ingest, "load_cik_hints", lambda *a, **k: 0)
    monkeypatch.setattr(ingest, "ingest_symbol",
                        lambda *a, **k: chamados.append(a[2]) or {"ok": True, "rows": 1})

    class Eng:
        def begin(self):
            raise RuntimeError("sem banco")
    monkeypatch.setattr(ingest.repo, "get_open_run", lambda *a, **k: None)
    monkeypatch.setattr(ingest.repo, "start_run", lambda *a, **k: 1)
    monkeypatch.setattr(ingest.repo, "checkpoint_run", lambda *a, **k: None)
    monkeypatch.setattr(ingest.repo, "finish_run", lambda *a, **k: None)

    class Ctx:
        def __enter__(self): return object()
        def __exit__(self, *a): return False

    class Eng2:
        def begin(self): return Ctx()

    class Prov:
        calls_made = 0

    # relogio: 1a leitura (calculo do prazo) = 0; a da 1a empresa = 5 (dentro);
    # a da 2a = 20 (fora do teto de 10 s)
    leituras = iter([0.0, 5.0, 20.0, 20.0, 20.0])
    monkeypatch.setattr(ingest.time, "monotonic", lambda: next(leituras))
    r = ingest.ingest_symbols(Prov(), Eng2(), ["AAA", "BBB"], resume=False,
                              with_prices=False, max_seconds=10)
    assert chamados == ["AAA"]
    assert r["processed"] == 1 and r["adiados_por_prazo"] == 1
