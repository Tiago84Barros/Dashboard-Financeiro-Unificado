"""A poda remota do cache BRAPI: o que ela preserva, e por que nao recria a tabela."""
from scripts.compact_remote_brapi_raw import (
    _colunas_que_referenciam,
    _sql_de_poda,
)

REFS = [("market", "income_statements"), ("market", "balance_sheets")]


class _Resultado:
    def __init__(self, linhas):
        self._linhas = linhas

    def all(self):
        return self._linhas


class _Conexao:
    """Conexao de mentira: guarda o SQL executado e devolve linhas fixas."""

    def __init__(self, linhas):
        self._linhas = linhas
        self.sql = ""

    def execute(self, stmt, *args):
        self.sql = str(stmt)
        return _Resultado(self._linhas)


def test_poda_nao_recria_a_tabela():
    """A recriacao era a causa raiz: DROP/CREATE reinicia o BIGSERIAL.

    Medido em 01/09/2026, depois de uma recriacao: 6.851 ids do manifesto
    colidindo com outra geracao e 84.116 linhas das demonstracoes apontando para
    um payload coletado DEPOIS da propria linha. Nenhum dos dois dava erro.
    """
    sql = _sql_de_poda(REFS).upper()
    assert "DROP TABLE" not in sql
    assert "TRUNCATE" not in sql
    assert "CREATE TABLE" not in sql
    assert sql.count("DELETE FROM MARKET.BRAPI_RAW_PAYLOADS") == 1


def test_poda_preserva_o_ultimo_payload_de_cada_endpoint_e_ticker():
    """renormalize() e o fallback de FII so leem o mais recente por ticker."""
    sql = _sql_de_poda(REFS)
    assert "DISTINCT ON (endpoint, ticker) id" in sql
    assert "p.id NOT IN (SELECT id FROM ultimo_por_chave)" in sql


def test_poda_preserva_a_janela_recente_e_o_lote_de_fii_em_andamento():
    sql = _sql_de_poda(REFS)
    assert "p.fetched_at < now() - make_interval(hours => :janela)" in sql
    assert "quote_fii_full" in sql and "lote_fii" in sql


def test_poda_preserva_todo_payload_referenciado_por_outra_tabela():
    sql = _sql_de_poda(REFS)
    for schema, tabela in REFS:
        assert f"SELECT raw_payload_id AS id FROM {schema}.{tabela}" in sql
    assert "p.id NOT IN (SELECT id FROM referenciados WHERE id IS NOT NULL)" in sql


def test_tabela_nova_entra_na_preservacao_sem_editar_o_script():
    """A lista branca ja apagou dado neste projeto (a coorte preferida).

    Quem escreve `raw_payload_id` amanha tem de ser preservado hoje, sem depender
    de alguem lembrar de acrescentar o nome aqui.
    """
    sql = _sql_de_poda(REFS + [("market_us", "tabela_que_ainda_nao_existe")])
    assert "market_us.tabela_que_ainda_nao_existe" in sql


def test_sem_referencia_nenhuma_a_consulta_continua_valida():
    """Zero tabelas referenciando nao pode virar `NOT IN ()` -- erro de sintaxe."""
    sql = _sql_de_poda([])
    assert "SELECT NULL::bigint AS id WHERE false" in sql
    assert "referenciados AS (\n            \n        )" not in sql


def test_simulacao_e_execucao_medem_exatamente_a_mesma_selecao():
    """Um dry run que monta a propria consulta mede outra coisa que nao a poda."""
    poda = _sql_de_poda(REFS)
    conta = _sql_de_poda(REFS, contar=True)
    assert conta.startswith(poda.split("DELETE FROM")[0])
    assert (poda.split("DELETE FROM market.brapi_raw_payloads p", 1)[1]
            == conta.split("SELECT count(*) FROM market.brapi_raw_payloads p", 1)[1])


def test_referencias_saem_do_catalogo_e_ignoram_os_esquemas_do_sistema():
    conn = _Conexao([("market", "income_statements"), ("market_us", "prices_daily")])
    assert _colunas_que_referenciam(conn) == [
        ("market", "income_statements"), ("market_us", "prices_daily")]
    assert "column_name = 'raw_payload_id'" in conn.sql
    assert "pg_catalog" in conn.sql and "information_schema" in conn.sql
