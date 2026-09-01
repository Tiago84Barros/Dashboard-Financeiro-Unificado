"""Procedencia das demonstracoes: ponteiro existir nao e ponteiro levar a algum lugar."""
from sqlalchemy import create_engine, text

from core.b3_validation import lineage_counts


def _base():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    conn = engine.connect()
    conn.execute(text("ATTACH DATABASE ':memory:' AS market"))
    conn.execute(text("""
        CREATE TABLE market.brapi_raw_payloads (
            id INTEGER PRIMARY KEY, endpoint TEXT, fetched_at TEXT)"""))
    conn.execute(text("""
        CREATE TABLE market.income_statements (
            ticker TEXT, period TEXT, first_seen_at TEXT, raw_payload_id INTEGER)"""))
    return conn


def _payload(conn, pid, quando):
    conn.execute(text("INSERT INTO market.brapi_raw_payloads VALUES (:i,'quote',:q)"),
                 {"i": pid, "q": quando})


def _linha(conn, ticker, quando, pid):
    conn.execute(text("INSERT INTO market.income_statements VALUES (:t,'annual',:q,:p)"),
                 {"t": ticker, "q": quando, "p": pid})


def test_procedencia_valida_conta_como_rastreada():
    conn = _base()
    _payload(conn, 10, "2026-07-01")
    _linha(conn, "PETR4", "2026-07-06", 10)
    c = lineage_counts(conn, "market.income_statements")
    assert c["traced_rows"] == 1
    assert c["impossible_rows"] == 0 and c["dangling_rows"] == 0


def test_payload_coletado_depois_da_linha_nao_e_procedencia():
    """O caso real: a sequencia reiniciou e o ponteiro caiu noutra geracao.

    Medido no Supabase em 01/09/2026: 84.116 de 85.889 linhas apontavam para um
    payload coletado DEPOIS da propria linha. A causa nao pode ser posterior ao
    efeito. Contando nao-nulos, as 85.889 apareciam como linhagem completa.
    """
    conn = _base()
    _payload(conn, 10, "2026-08-28")          # geracao nova, mesmo id
    _linha(conn, "PETR4", "2026-07-06", 10)   # linha escrita em julho
    c = lineage_counts(conn, "market.income_statements")
    assert c["pointer_rows"] == 1             # o ponteiro existe...
    assert c["traced_rows"] == 0              # ...e nao sustenta procedencia
    assert c["impossible_rows"] == 1


def test_ponteiro_orfao_aparece_separado_de_impossivel():
    conn = _base()
    _linha(conn, "VALE3", "2026-07-06", 99999)
    c = lineage_counts(conn, "market.income_statements")
    assert c["dangling_rows"] == 1
    assert c["traced_rows"] == 0 and c["impossible_rows"] == 0


def test_linha_sem_ponteiro_nao_entra_em_nenhum_balde_de_defeito():
    conn = _base()
    _linha(conn, "ITUB4", "2026-07-06", None)
    c = lineage_counts(conn, "market.income_statements")
    assert c["rows"] == 1
    assert (c["pointer_rows"], c["traced_rows"],
            c["dangling_rows"], c["impossible_rows"]) == (0, 0, 0, 0)


def test_os_baldes_somam_o_total_de_ponteiros():
    """Nenhuma linha com ponteiro pode sumir da contabilidade."""
    conn = _base()
    _payload(conn, 1, "2026-07-01")
    _payload(conn, 2, "2026-08-28")
    _linha(conn, "PETR4", "2026-07-06", 1)      # rastreada
    _linha(conn, "VALE3", "2026-07-06", 2)      # impossivel
    _linha(conn, "ITUB4", "2026-07-06", 77)     # orfa
    _linha(conn, "BBAS3", "2026-07-06", None)   # sem ponteiro
    c = lineage_counts(conn, "market.income_statements")
    assert c["rows"] == 4 and c["pointer_rows"] == 3
    assert c["traced_rows"] + c["impossible_rows"] + c["dangling_rows"] == 3


def test_trimestral_nao_entra_na_contagem_anual():
    conn = _base()
    _payload(conn, 1, "2026-07-01")
    conn.execute(text("INSERT INTO market.income_statements "
                      "VALUES ('PETR4','quarterly','2026-07-06',1)"))
    assert lineage_counts(conn, "market.income_statements")["rows"] == 0
