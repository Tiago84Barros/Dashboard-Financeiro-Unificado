# -*- coding: utf-8 -*-
"""A-161: linhas com chaves diferentes nao podem ser decididas pela primeira.

O defeito apareceu ao ingerir empresa deslistada: `filed_at` so existe onde a
SEC datou o arquivamento, entao as linhas de demonstracao chegam heterogeneas.
`_exec_many` montava o INSERT com `rows[0].keys()`, e isso falhava dos dois
lados -- um barulhento e um silencioso. O silencioso e o que estes testes
existem para travar.
"""
from __future__ import annotations

from data_pipeline.us import repository as repo


class _ConnFake:
    """Registra o SQL e o payload; nao valida bind (o objetivo e ver as colunas)."""

    def __init__(self):
        self.sql = None
        self.payload = None

    def execute(self, sql, payload=None):
        self.sql = str(sql)
        self.payload = payload
        return self


def test_primeira_linha_sem_a_chave_nao_aborta_a_empresa():
    """O lado barulhento: era InvalidRequestError e a empresa inteira caia."""
    conn = _ConnFake()
    n = repo._exec_many(conn, "income_statements",
                        [{"company_id": 1, "fiscal_year": 2019},
                         {"company_id": 1, "fiscal_year": 2020,
                          "filed_at": '{"2020": "2021-02-01"}'}],
                        conflict=["company_id", "fiscal_year"])
    assert n == 2
    assert "filed_at" in conn.sql
    # a linha que nao tinha a chave vai com NULL explicito, nao some do lote
    assert conn.payload[0]["filed_at"] is None


def test_coluna_que_so_aparece_depois_nao_e_descartada():
    """O lado silencioso, e o motivo real do teste.

    Com a primeira linha COMPLETA e as seguintes sem a chave, o comportamento
    antigo nao levantava nada -- so gravava menos coluna do que recebeu. Erro
    que nao aparece nao e revisto.
    """
    conn = _ConnFake()
    repo._exec_many(conn, "balance_sheets",
                    [{"company_id": 7, "fiscal_year": 2021},
                     {"company_id": 7, "fiscal_year": 2022, "total_equity": 42.0}],
                    conflict=["company_id", "fiscal_year"])
    assert "total_equity" in conn.sql, "coluna da segunda linha sumiu do INSERT"
    assert conn.payload[1]["total_equity"] == 42.0
    assert conn.payload[0]["total_equity"] is None


def test_ordem_das_colunas_segue_a_primeira_aparicao():
    """Determinismo: mesmo lote, mesmo SQL. Sem isto o cache de plano do
    Postgres e o diff de log viram ruido a cada execucao."""
    linhas = [{"b": 1}, {"a": 2}, {"c": 3, "a": 4}]
    c1, c2 = _ConnFake(), _ConnFake()
    repo._exec_many(c1, "t", linhas, conflict=["b"])
    repo._exec_many(c2, "t", list(reversed([dict(r) for r in reversed(linhas)])),
                    conflict=["b"])
    assert c1.sql == c2.sql
    assert c1.sql.index("(b, a, c)") > 0


def test_lote_vazio_continua_sendo_zero():
    assert repo._exec_many(_ConnFake(), "t", [], conflict=["x"]) == 0
