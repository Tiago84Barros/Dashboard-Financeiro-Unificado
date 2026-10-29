"""Posição resgatada sai da carteira pela Movimentação da B3.

2026-10-29: três CDBs resgatados antecipadamente em outubro (R$ 23,9 mil)
continuavam na renda fixa. O relatório de Posição novo da B3 (`b3_posicao`)
não publica linha zerada -- só deixa de listar o ativo --, então
`b3_encerrados` não tinha o que ler, e o DENSE_RANK caía na foto da Rico de
2026-09-30, que ainda mostrava os três. A saída estava gravada na Movimentação
desde o upload de 25/10.

A primeira metade roda as duas consultas de verdade contra o armazém local,
num schema descartável, porque a regra mora num HAVING que só o Postgres
avalia. Sem armazém, ela é pulada e sobra a segunda metade, estrutural.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text

import core.investimentos as inv
from tests.apoio_armazem import opcoes_conexao, schema_descartavel

SCHEMA = schema_descartavel("app4_mov_encerrados_teste")
U = str(uuid.uuid4())

_DDL = [
    f"""CREATE TABLE {SCHEMA}.assets (
        id uuid PRIMARY KEY, ticker text, class text, sector text, currency text)""",
    f"""CREATE TABLE {SCHEMA}.portfolio_position_snapshots (
        user_id uuid, asset_id uuid, source_system text, source_table text,
        source_id text, report_date date, quantity numeric, market_price numeric,
        market_value numeric, invested_value numeric, is_loaned boolean,
        asset_type text, asset_name text, currency text, country text)""",
    f"""CREATE TABLE {SCHEMA}.portfolio_positions (
        user_id uuid, asset_id uuid, quantity numeric, total_invested numeric)""",
    f"""CREATE TABLE {SCHEMA}.asset_quotes (
        asset_id uuid, close numeric, timestamp timestamptz)""",
    f"""CREATE TABLE {SCHEMA}.investment_movement_events (
        user_id uuid, event_date date, movement text, direction text,
        ticker text, product text, quantity numeric)""",
]

# ticker -> (classe, fonte, data da foto, quantidade, valor de mercado)
_FOTOS = {
    "BBAS3": ("stock", "b3_posicao", "2026-10-27", 100, 2500.00),
    "CDB8266EXTI": ("fixed_income", "xp_consolidado", "2026-09-30", 12101, 12324.09),
    "CDB9260UQ0S": ("fixed_income", "xp_consolidado", "2026-09-30", 10000, 10104.58),
    "CDB826EAKY2": ("fixed_income", "xp_consolidado", "2026-09-30", 1500, 1516.48),
    "ITSA4": ("stock", "xp_consolidado", "2026-09-30", 100, 1000.00),
    "KNCR12": ("reit", "b3_posicao_detalhada", "2026-10-06", 18, 7.92),
}

# (data, movimento, sentido, ticker, produto, quantidade)
_EVENTOS = [
    # resgate integral depois da foto: encerra
    ("2026-10-07", "resgate antecipado", "Debito", "CDB",
     "CDB - CDB8266EXTI - BANCO A", 12101),
    # resgate parcial: não encerra
    ("2026-10-07", "resgate antecipado", "Debito", "CDB",
     "CDB - CDB9260UQ0S - BANCO B", 4000),
    # resgate no próprio dia da foto: a foto já pode tê-lo; não encerra
    ("2026-09-30", "resgate antecipado", "Debito", "CDB",
     "CDB - CDB826EAKY2 - BANCO C", 1500),
    # ação: a regra da renda fixa não vale, nem com rótulo de venda
    ("2026-10-10", "venda", "Debito", "ITSA4", "ITSA4 - ITAUSA S.A.", 100),
    # direito que venceu sem exercício: encerra, qualquer que seja a classe
    ("2026-10-19", "direitos de subscrição - não exercido", "Debito", "KNCR12",
     "KNCR12 - KINEA RENDIMENTOS IMOBILIÁRIOS FII", 18),
]


@pytest.fixture()
def banco():
    try:
        from scripts.publish_fii_selection_from_local import _warehouse_url

        motor = create_engine(_warehouse_url(), connect_args=opcoes_conexao(SCHEMA))
        with motor.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
            conn.execute(text(f"CREATE SCHEMA {SCHEMA}"))
            for ddl in _DDL:
                conn.execute(text(ddl))
    except Exception as exc:  # noqa: BLE001 - sem armazém, não medimos
        pytest.skip(f"armazém local indisponível: {exc}")
    ids = {t: str(uuid.uuid4()) for t in _FOTOS}
    with motor.begin() as conn:
        for ticker, (classe, fonte, data, qtd, vm) in _FOTOS.items():
            conn.execute(text(
                "INSERT INTO assets VALUES (:id, :t, :c, 'other', 'BRL')"
            ), {"id": ids[ticker], "t": ticker, "c": classe})
            conn.execute(text("""
                INSERT INTO portfolio_position_snapshots
                    (user_id, asset_id, source_system, source_table, source_id,
                     report_date, quantity, market_value, asset_type, asset_name,
                     currency, country)
                VALUES (:u, :a, 'b3', :f, :sid, :d, :q, :vm, :c, :t, 'BRL', 'BR')
            """), {"u": U, "a": ids[ticker], "f": fonte, "sid": f"teste-{ticker}",
                   "d": data, "q": qtd, "vm": vm, "c": classe, "t": ticker})
        conn.execute(text("""
            INSERT INTO investment_movement_events
                (user_id, event_date, movement, direction, ticker, product, quantity)
            VALUES (:u, :d, :m, :s, :t, :p, :q)
        """), [{"u": U, "d": d, "m": m, "s": s, "t": t, "p": p, "q": q}
               for d, m, s, t, p, q in _EVENTOS])
    yield motor
    with motor.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
    motor.dispose()


def _carteira(conn, sql) -> set[str]:
    return {r.ticker for r in conn.execute(text(sql), {"uid": U})}


def _curva(conn, sql) -> dict[str, float]:
    return {str(r.mes): round(float(r.valor_mercado), 2)
            for r in conn.execute(text(sql), {"uid": U})}


def test_so_a_saida_provada_sai_da_carteira_de_hoje(banco):
    with banco.connect() as conn:
        assert inv._tem_movimentacao(conn)
        tickers = _carteira(conn, inv._SQL_POSICOES_SNAPSHOT)
    assert tickers == {"BBAS3", "CDB9260UQ0S", "CDB826EAKY2", "ITSA4"}, (
        "sai o resgate integral posterior a foto e o direito vencido; ficam o "
        "resgate parcial, o resgate do proprio dia e a acao"
    )


def test_curva_so_tira_o_cdb_do_corte_posterior_ao_resgate(banco):
    with banco.connect() as conn:
        curva = _curva(conn, inv._SQL_EVOLUCAO_SNAPSHOTS)
    # Setembro ainda tinha o CDB resgatado em outubro.
    assert curva["2026-09-30"] == round(12324.09 + 10104.58 + 1516.48 + 1000.00, 2)
    # Outubro: sem o CDB resgatado e sem o direito vencido.
    assert curva["2026-10-27"] == round(2500.00 + 10104.58 + 1516.48 + 1000.00, 2)


def test_sem_a_tabela_da_movimentacao_a_carteira_continua_de_pe(banco):
    with banco.begin() as conn:
        conn.execute(text("DROP TABLE investment_movement_events"))
    with banco.connect() as conn:
        assert not inv._tem_movimentacao(conn)
        tickers = _carteira(conn, inv._SQL_POSICOES_SNAPSHOT_SEM_MOVIMENTACAO)
        curva = _curva(conn, inv._SQL_EVOLUCAO_SNAPSHOTS_SEM_MOVIMENTACAO)
    assert "CDB8266EXTI" in tickers
    assert "2026-10-27" in curva


# ── Estrutura: vale também onde não há armazém (CI).


def test_as_duas_consultas_filtram_pela_mesma_prova():
    for sql in (inv._SQL_POSICOES_SNAPSHOT, inv._SQL_EVOLUCAO_SNAPSHOTS):
        assert "mov_encerrados AS (" in sql
        assert inv._SQL_MOV_PROVA_ENCERRAMENTO in sql, (
            "a prova e uma so: carteira de hoje e curva nao podem divergir"
        )
    assert "NOT IN (SELECT asset_id FROM mov_encerrados)" in inv._SQL_POSICOES_SNAPSHOT
    assert "FROM mov_encerrados e" in inv._SQL_EVOLUCAO_SNAPSHOTS, (
        "a CTE existir nao basta: ela precisa filtrar a curva"
    )


def test_curva_le_so_o_evento_ate_o_corte():
    trecho = inv._SQL_EVOLUCAO_SNAPSHOTS.split("mov_encerrados AS (")[1].split("xp_snaps AS (")[0]
    assert "m.event_date > r.report_date" in trecho
    assert "m.event_date <= r.corte" in trecho


def test_data_do_evento_e_estrita_e_a_regra_liquida_e_so_da_renda_fixa():
    assert "m.event_date > r.report_date" in inv._CTE_MOV_ENCERRADOS
    assert "m.event_date >= r.report_date" not in inv._CTE_MOV_ENCERRADOS
    assert "a.class = 'fixed_income'" in inv._SQL_MOV_PROVA_ENCERRAMENTO


def test_variante_sem_tabela_nao_cita_a_tabela():
    for sql in (inv._SQL_POSICOES_SNAPSHOT_SEM_MOVIMENTACAO,
                inv._SQL_EVOLUCAO_SNAPSHOTS_SEM_MOVIMENTACAO):
        assert "investment_movement_events" not in sql
        assert "mov_encerrados AS (" in sql
