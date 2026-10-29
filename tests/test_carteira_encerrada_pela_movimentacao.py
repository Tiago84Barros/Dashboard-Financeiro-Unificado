"""Posição resgatada sai da carteira pela Movimentação da B3.

CDB resgatado antecipadamente continuava na renda fixa. O relatório de
Posição novo da B3 (`b3_posicao`) não publica linha zerada -- só deixa de
listar o ativo --, então `b3_encerrados` não tinha o que ler, e o DENSE_RANK
caía na foto anterior da corretora, que ainda o mostrava. A saída estava
gravada na Movimentação.

A primeira metade roda as duas consultas de verdade contra o armazém local,
num schema descartável, porque a regra mora num HAVING que só o Postgres
avalia. Sem armazém, ela é pulada e sobra a segunda metade, estrutural.

Os dados são sintéticos (AGENTS.md): tickers, datas e valores fictícios, que
só preservam as relações de data e quantidade de cada caso.
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
    "ACAO3": ("stock", "b3_posicao", "2021-06-25", 10, 300.00),
    "CDBRESGTOTAL": ("fixed_income", "xp_consolidado", "2021-05-31", 1000, 1010.00),
    "CDBRESGPARC": ("fixed_income", "xp_consolidado", "2021-05-31", 2000, 2020.00),
    "CDBMESMODIA": ("fixed_income", "xp_consolidado", "2021-05-31", 400, 404.00),
    "ACAO4": ("stock", "xp_consolidado", "2021-05-31", 20, 200.00),
    "FUND12": ("reit", "b3_posicao_detalhada", "2021-06-04", 10, 5.00),
    # resgate depois da última Posição da B3: só a corretora o via, nada confirma
    "CDBOUTRACORR": ("fixed_income", "xp_consolidado", "2021-05-31", 800, 808.00),
    # a foto vencedora já é a custódia da B3: mesmo escopo da Movimentação
    "CDBCUSTODIAB3": ("fixed_income", "b3_posicao", "2021-06-25", 500, 505.00),
    # recebe transferência de fora e resgata só o que chegou
    "CDBCHEGOU": ("fixed_income", "b3_posicao", "2021-06-25", 300, 303.00),
    # troca de corretora e depois resgata tudo
    "CDBTROCOU": ("fixed_income", "b3_posicao", "2021-06-25", 200, 202.00),
}

# (data, movimento, sentido, ticker, produto, quantidade)
_EVENTOS = [
    # resgate integral depois da foto: encerra
    ("2021-06-07", "resgate antecipado", "Debito", "CDB",
     "CDB - CDBRESGTOTAL - EMISSOR A", 1000),
    # resgate parcial: não encerra
    ("2021-06-07", "resgate antecipado", "Debito", "CDB",
     "CDB - CDBRESGPARC - EMISSOR B", 800),
    # resgate no próprio dia da foto: a foto já pode tê-lo; não encerra
    ("2021-05-31", "resgate antecipado", "Debito", "CDB",
     "CDB - CDBMESMODIA - EMISSOR C", 400),
    # ação: a regra da renda fixa não vale, nem com rótulo de venda
    ("2021-06-10", "venda", "Debito", "ACAO4", "ACAO4 - EMPRESA FICTICIA S.A.", 20),
    # direito que venceu sem exercício: encerra, qualquer que seja a classe
    ("2021-06-15", "direitos de subscrição - não exercido", "Debito", "FUND12",
     "FUND12 - FUNDO FICTICIO FII", 10),
    # resgate integral depois da última Posição da B3 (25/06), foto da
    # corretora: pode ter sido o mesmo código em outra corretora; não encerra
    ("2021-06-28", "resgate antecipado", "Debito", "CDB",
     "CDB - CDBOUTRACORR - EMISSOR D", 800),
    # resgate integral depois de foto que é da própria B3: encerra
    ("2021-06-28", "resgate antecipado", "Debito", "CDB",
     "CDB - CDBCUSTODIAB3 - EMISSOR E", 500),
    # entraram 300 de fora e saíram 300: os 300 da foto continuam
    ("2021-06-28", "transferencia", "Credito", "CDB",
     "CDB - CDBCHEGOU - EMISSOR F", 300),
    ("2021-06-28", "resgate antecipado", "Debito", "CDB",
     "CDB - CDBCHEGOU - EMISSOR F", 300),
    # a troca de corretora soma zero, e o resgate integral encerra
    ("2021-06-28", "transf. sem financeiro", "Debito", "CDB",
     "CDB - CDBTROCOU - EMISSOR G", 200),
    ("2021-06-28", "transf. sem financeiro", "Credito", "CDB",
     "CDB - CDBTROCOU - EMISSOR G", 200),
    ("2021-06-28", "resgate antecipado", "Debito", "CDB",
     "CDB - CDBTROCOU - EMISSOR G", 200),
]

# Segundo investidor: a Posição da B3 posterior ao resgate veio só com ações
# (a aba Renda Fixa falhou ou não foi exportada), e o extrato antigo, que
# nunca lista renda fixa, também é posterior. Nenhum dos dois lista CDB, então
# nenhum confirma a saída: o CDB que só a corretora via fica.
U_SEM_RF = str(uuid.uuid4())
_FOTOS_SEM_RF = {
    "CDBSEMCOBERTURA": ("fixed_income", "xp_consolidado", "2021-05-31", 1000, 1010.00),
    "ACAO5": ("stock", "b3_posicao", "2021-06-30", 10, 100.00),
    "ACAO6": ("stock", "b3_posicao_detalhada", "2021-06-29", 10, 60.00),
}
_EVENTOS_SEM_RF = [
    ("2021-06-07", "resgate antecipado", "Debito", "CDB",
     "CDB - CDBSEMCOBERTURA - EMISSOR H", 1000),
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
    with motor.begin() as conn:
        for usuario, fotos, eventos in ((U, _FOTOS, _EVENTOS),
                                        (U_SEM_RF, _FOTOS_SEM_RF, _EVENTOS_SEM_RF)):
            for ticker, (classe, fonte, data, qtd, vm) in fotos.items():
                ativo = str(uuid.uuid4())
                conn.execute(text(
                    "INSERT INTO assets VALUES (:id, :t, :c, 'other', 'BRL')"
                ), {"id": ativo, "t": ticker, "c": classe})
                conn.execute(text("""
                    INSERT INTO portfolio_position_snapshots
                        (user_id, asset_id, source_system, source_table, source_id,
                         report_date, quantity, market_value, asset_type, asset_name,
                         currency, country)
                    VALUES (:u, :a, 'b3', :f, :sid, :d, :q, :vm, :c, :t, 'BRL', 'BR')
                """), {"u": usuario, "a": ativo, "f": fonte, "sid": f"teste-{ticker}",
                       "d": data, "q": qtd, "vm": vm, "c": classe, "t": ticker})
            conn.execute(text("""
                INSERT INTO investment_movement_events
                    (user_id, event_date, movement, direction, ticker, product, quantity)
                VALUES (:u, :d, :m, :s, :t, :p, :q)
            """), [{"u": usuario, "d": d, "m": m, "s": s, "t": t, "p": p, "q": q}
                   for d, m, s, t, p, q in eventos])
    yield motor
    with motor.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
    motor.dispose()


def _carteira(conn, sql, usuario=U) -> set[str]:
    return {r.ticker for r in conn.execute(text(sql), {"uid": usuario})}


def _curva(conn, sql, usuario=U) -> dict[str, float]:
    return {str(r.mes): round(float(r.valor_mercado), 2)
            for r in conn.execute(text(sql), {"uid": usuario})}


def test_so_a_saida_provada_sai_da_carteira_de_hoje(banco):
    with banco.connect() as conn:
        assert inv._tem_movimentacao(conn)
        tickers = _carteira(conn, inv._SQL_POSICOES_SNAPSHOT)
    assert tickers == {"ACAO3", "CDBRESGPARC", "CDBMESMODIA", "ACAO4",
                       "CDBOUTRACORR", "CDBCHEGOU"}, (
        "sai o resgate integral que a custodia da B3 confirma (com ou sem "
        "troca de corretora antes) e o direito vencido; ficam o resgate "
        "parcial, o do proprio dia, a acao, o resgate que so a foto da "
        "corretora enxergava e o resgate do que chegou por transferencia"
    )


def test_curva_so_tira_o_cdb_do_corte_posterior_ao_resgate(banco):
    with banco.connect() as conn:
        curva = _curva(conn, inv._SQL_EVOLUCAO_SNAPSHOTS)
    # Maio ainda tinha o CDB resgatado em junho.
    assert curva["2021-05-31"] == round(
        1010.00 + 2020.00 + 404.00 + 200.00 + 808.00, 2)
    # Junho: sem o CDB resgatado e sem o direito vencido. Os eventos de
    # 28/06 sao posteriores ao corte de 25/06 e nao entram nele.
    assert curva["2021-06-25"] == round(
        300.00 + 2020.00 + 404.00 + 200.00 + 808.00 + 505.00
        + 303.00 + 202.00, 2)


def test_posicao_da_b3_sem_a_aba_renda_fixa_nao_confirma_a_saida(banco):
    with banco.connect() as conn:
        tickers = _carteira(conn, inv._SQL_POSICOES_SNAPSHOT, U_SEM_RF)
        curva = _curva(conn, inv._SQL_EVOLUCAO_SNAPSHOTS, U_SEM_RF)
    assert tickers == {"CDBSEMCOBERTURA", "ACAO5", "ACAO6"}, (
        "Posicao so com acoes e extrato antigo nao listam CDB: a data deles "
        "nao prova que o CDB saiu da custodia"
    )
    assert curva["2021-06-30"] == round(1010.00 + 100.00 + 60.00, 2)


def test_sem_a_tabela_da_movimentacao_a_carteira_continua_de_pe(banco):
    with banco.begin() as conn:
        conn.execute(text("DROP TABLE investment_movement_events"))
    with banco.connect() as conn:
        assert not inv._tem_movimentacao(conn)
        tickers = _carteira(conn, inv._SQL_POSICOES_SNAPSHOT_SEM_MOVIMENTACAO)
        curva = _curva(conn, inv._SQL_EVOLUCAO_SNAPSHOTS_SEM_MOVIMENTACAO)
    assert "CDBRESGTOTAL" in tickers
    assert "2021-06-25" in curva


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


def test_transferencia_de_custodia_entra_na_conta_e_a_de_liquidacao_nao():
    # As tres grafias que a B3 usa para custodia; a de liquidacao e a venda
    # da Negociacao.
    for rotulo in ("'transferência'", "'transferencia'", "'transf. sem financeiro'"):
        assert rotulo in inv._SQL_MOV_QUANTIDADE
    assert "liquidação" not in inv._SQL_MOV_QUANTIDADE


def test_variante_sem_tabela_nao_cita_a_tabela():
    for sql in (inv._SQL_POSICOES_SNAPSHOT_SEM_MOVIMENTACAO,
                inv._SQL_EVOLUCAO_SNAPSHOTS_SEM_MOVIMENTACAO):
        assert "investment_movement_events" not in sql
        assert "mov_encerrados AS (" in sql


def test_saida_da_renda_fixa_so_vale_com_a_custodia_da_b3_confirmando():
    # A Movimentação soma todas as corretoras; a foto de uma corretora vê só ela.
    assert "r.foto_da_custodia_b3" in inv._SQL_MOV_PROVA_ENCERRAMENTO
    assert "<= r.custodia_b3" in inv._SQL_MOV_PROVA_ENCERRAMENTO
    trecho = inv._SQL_EVOLUCAO_SNAPSHOTS.split("mov_encerrados AS (")[1].split("xp_snaps AS (")[0]
    assert "s.report_date <= rs.corte" in trecho, (
        "na curva, so a Posicao da B3 ate o corte confirma a saida"
    )


def test_so_a_posicao_da_b3_com_renda_fixa_confirma_a_saida():
    # Exportação só com Ações (ou com a aba Renda Fixa falhando) e o extrato
    # antigo não listam CDB: a data deles não prova nada sobre CDB.
    assert "s.asset_type = 'fixed_income'" in inv._SQL_B3_COM_RENDA_FIXA
    trecho = inv._SQL_EVOLUCAO_SNAPSHOTS.split("mov_encerrados AS (")[1].split("xp_snaps AS (")[0]
    for cte in (inv._CTE_MOV_ENCERRADOS, trecho):
        # Toda leitura da data da B3 leva o filtro; sem ele, a data de
        # qualquer Posicao voltaria a confirmar.
        assert cte.count("WHERE " + inv._SQL_B3_COM_RENDA_FIXA) == 1
        assert cte.count("WHERE s." + inv._SQL_FONTE_CUSTODIA_B3) == 1


# ── Carteira inteira encerrada: vazia, não o recálculo de portfolio_positions.


class _Res:
    def __init__(self, linhas=(), escalar=None):
        self._linhas, self._escalar = list(linhas), escalar

    def fetchall(self):
        return list(self._linhas)

    def scalar(self):
        return self._escalar


class _ConexaoSemPosicao:
    """A consulta das fotos volta vazia; `tem_foto` diz se havia foto."""

    def __init__(self, tem_foto):
        self.tem_foto, self.sqls = tem_foto, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.sqls.append(sql)
        if "information_schema.tables" in sql or "to_regclass" in sql:
            return _Res(escalar=True)
        if sql == inv._SQL_TEM_FOTO:
            return _Res(escalar=self.tem_foto)
        return _Res()


def _sem_posicao(monkeypatch, tem_foto):
    from types import SimpleNamespace

    from core import database

    conn = _ConexaoSemPosicao(tem_foto)
    monkeypatch.setattr(inv.settings, "OWNER_USER_ID", "u1")
    monkeypatch.setattr(database, "get_engine", lambda: SimpleNamespace(connect=lambda: conn))
    monkeypatch.setattr(inv, "_carteira_snapshot_consolidada",
                        lambda conn, owner, rows: {"posicoes": list(rows), "pela_foto": True})
    return conn


def test_foto_toda_encerrada_e_carteira_vazia(monkeypatch):
    conn = _sem_posicao(monkeypatch, tem_foto=True)

    carteira = inv._carteira_real()

    assert carteira == {"posicoes": [], "pela_foto": True}
    assert inv._SQL_POSICOES not in conn.sqls, (
        "portfolio_positions traria de volta o que a foto e a Movimentacao fecharam"
    )


def test_sem_foto_nenhuma_o_recalculo_continua(monkeypatch):
    conn = _sem_posicao(monkeypatch, tem_foto=False)
    monkeypatch.setattr(inv, "cambio_medio_de_aquisicao", lambda conn, owner: {})
    monkeypatch.setattr(inv.cotacao_viva, "buscar", lambda simbolos: {})

    # Sem foto e sem posição recalculada, o caminho antigo segue até o erro
    # que ele sempre deu -- o que importa é que foi ele que rodou.
    with pytest.raises(RuntimeError, match="portfolio_positions"):
        inv._carteira_real()

    assert inv._SQL_POSICOES in conn.sqls
