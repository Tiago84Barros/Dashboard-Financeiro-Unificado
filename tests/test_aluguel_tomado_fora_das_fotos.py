"""Aluguel tomado (BTC Tomador) fica fora de quem lê a foto como posse.

O Tomador entra negativo em ``portfolio_position_snapshots`` (B3 e XP): é
dívida. Quem soma quantidade ou valor por papel, ou usa a foto como evidência
de posse, tem que descartá-lo antes de agregar -- senão a dívida zera a ação
própria do mesmo papel na mesma corretora. Tickers e números são fictícios.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from core import carteira_atribuicao, carteira_risco, carteira_tabela, irpf_declaracao


@pytest.fixture
def engine():
    eng = create_engine("sqlite://")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE assets (id TEXT, ticker TEXT, currency TEXT)"))
        conn.execute(text("""
            CREATE TABLE portfolio_position_snapshots (
                user_id TEXT, asset_id TEXT, report_date TEXT, quantity NUMERIC,
                market_value NUMERIC, invested_value NUMERIC, asset_name TEXT,
                asset_type TEXT, currency TEXT, institution TEXT,
                is_loaned BOOLEAN, source_system TEXT, source_table TEXT,
                source_id TEXT)
        """))
        conn.execute(text("""
            INSERT INTO assets VALUES ('a1', 'AAAA3', 'BRL'), ('a2', 'BBBB3', 'BRL')
        """))
        conn.execute(text("""
            INSERT INTO portfolio_position_snapshots VALUES
              -- AAAA3: ação própria, Doador e Tomador na mesma corretora.
              ('u', 'a1', '2025-12-31', 10, 30, 25, 'A', 'stock', 'BRL', 'CORRETORA', 0, 'app4', 'xp_consolidado', 's1'),
              ('u', 'a1', '2025-12-31', 4, 12, NULL, 'A', 'stock', 'BRL', 'CORRETORA', 1, 'app4', 'xp_consolidado', 's2'),
              ('u', 'a1', '2025-12-31', -10, -30, NULL, 'A', 'stock', 'BRL', 'CORRETORA', 1, 'app4', 'xp_consolidado', 's3'),
              -- BBBB3: só o Tomador -- o papel nunca foi do investidor.
              ('u', 'a2', '2025-12-31', -7, -21, NULL, 'B', 'stock', 'BRL', 'CORRETORA', 1, 'app4', 'xp_consolidado', 's4')
        """))
    return eng


PERIODO = {"uid": "u", "ini": "2025-12-01", "fim": "2026-01-31"}


def test_risco_ancora_na_posicao_propria(engine):
    linhas = carteira_risco._ler(engine, carteira_risco._SQL_FOTOS, PERIODO)

    assert [(r.ticker, float(r.quantidade)) for r in linhas] == [("AAAA3", 14.0)]


def test_atribuicao_soma_so_o_valor_proprio(engine):
    linhas = carteira_risco._ler(engine, carteira_atribuicao._SQL_FOTOS_VALOR, PERIODO)

    assert [(r.ticker, float(r.valor)) for r in linhas] == [("AAAA3", 42.0)]


def test_tabela_nao_toma_o_tomador_como_posse(engine):
    with engine.connect() as conn:
        fotos = carteira_tabela._ler(conn, carteira_tabela._SQL_FOTOS, "u", "fotos")

    assert {f["ticker"] for f in fotos} == {"AAAA3"}


def test_irpf_custodia_de_dezembro_nao_abate_o_tomador(engine):
    # EXTRACT é do Postgres; o SQLite lê o mês pelo strftime. O resto da
    # consulta, filtro do Tomador incluído, roda como está.
    sql = irpf_declaracao._SQL_POSICOES.replace(
        "EXTRACT(MONTH FROM s.report_date) = 12",
        "strftime('%m', s.report_date) = '12'",
    )
    assert sql != irpf_declaracao._SQL_POSICOES
    with engine.connect() as conn:
        linhas = conn.execute(text(sql), {"uid": "u"}).fetchall()

    assert sorted((r.ticker, float(r.quantity)) for r in linhas) == [
        ("AAAA3", 4.0), ("AAAA3", 10.0),
    ]


def _ctes_de_encerramento(sql: str) -> str:
    """Só as CTEs b3_ultima_linha e b3_encerrados, sobre uma tabela crua."""
    ini = sql.index("    b3_ultima_linha AS (")
    fim = sql.index("    mov_encerrados AS (", ini)
    return sql[ini:fim].rstrip().rstrip(",")


@pytest.fixture
def custodia_b3():
    eng = create_engine("sqlite://")
    with eng.begin() as conn:
        conn.execute(text("""
            CREATE TABLE normalized_snapshots (
                asset_id TEXT, report_date TEXT, quantity NUMERIC,
                market_value NUMERIC, is_loaned BOOLEAN,
                effective_source_table TEXT)
        """))
        conn.execute(text("""
            INSERT INTO normalized_snapshots VALUES
              -- a1: ação própria e Tomador do mesmo tamanho -- não encerrou.
              ('a1', '2026-06-30', 10, 30, 0, 'b3_posicao_detalhada'),
              ('a1', '2026-06-30', -10, -30, 1, 'b3_posicao_detalhada'),
              -- a2: vendida (linha zerada) e ainda devendo o aluguel -- encerrou.
              ('a2', '2026-06-30', 0, 0, 0, 'b3_posicao_detalhada'),
              ('a2', '2026-06-30', -5, -15, 1, 'b3_posicao_detalhada')
        """))
    return eng


def test_ponto_de_hoje_nao_encerra_pela_soma_com_o_tomador(custodia_b3):
    from core.investimentos import _SQL_POSICOES_SNAPSHOT

    sql = ("WITH " + _ctes_de_encerramento(_SQL_POSICOES_SNAPSHOT)
           + " SELECT asset_id FROM b3_encerrados ORDER BY asset_id")
    with custodia_b3.connect() as conn:
        encerrados = [r.asset_id for r in conn.execute(text(sql))]

    assert encerrados == ["a2"]


def test_curva_nao_encerra_pela_soma_com_o_tomador(custodia_b3):
    from core.investimentos import _SQL_EVOLUCAO_SNAPSHOTS

    sql = ("WITH cortes AS (SELECT '2026-06-30' AS corte), "
           + _ctes_de_encerramento(_SQL_EVOLUCAO_SNAPSHOTS)
           + " SELECT asset_id FROM b3_encerrados ORDER BY asset_id")
    with custodia_b3.connect() as conn:
        encerrados = [r.asset_id for r in conn.execute(text(sql))]

    assert encerrados == ["a2"]
