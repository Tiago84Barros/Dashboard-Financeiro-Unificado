"""Repositório do Controle CNPJ contra um Postgres de verdade.

Roda só com ``PJ_TEST_DATABASE_URL`` apontando para um banco DESCARTÁVEL (as
tabelas ``pj_*``, ``profiles`` e o esquema ``auth`` são criados e apagados).
Sem a variável, o módulo é pulado: o CI não tem Postgres. Dublê de conexão não
pega erro de SQL -- tipo de parâmetro, ``ANY(:lista)``, ``ON CONFLICT`` --, e é
exatamente aí que um repositório novo quebra.

    DFU_TESTES_PERMITEM_REDE=1 \
    PJ_TEST_DATABASE_URL=postgresql://postgres@/teste?host=/caminho/do/socket&port=55432
"""
from __future__ import annotations

import os
import uuid
from datetime import date

import pytest

URL = os.environ.get("PJ_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="sem PJ_TEST_DATABASE_URL (Postgres descartável)")

DONO_A = str(uuid.uuid4())
DONO_B = str(uuid.uuid4())


@pytest.fixture
def repo(monkeypatch):
    from sqlalchemy import create_engine, text

    import core.pj.repositorio as r

    engine = create_engine(URL)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS pj_regras, pj_saldos_extrato, pj_lancamentos, "
                          "pj_empresas, profiles CASCADE"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS auth"))
        conn.execute(text("CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid "
                          "LANGUAGE sql AS 'SELECT NULL::uuid'"))
        conn.execute(text("CREATE TABLE profiles (id uuid PRIMARY KEY)"))
        conn.execute(text("INSERT INTO profiles VALUES (CAST(:a AS uuid)), (CAST(:b AS uuid))"),
                     {"a": DONO_A, "b": DONO_B})
    monkeypatch.setattr("core.database.get_engine", lambda: engine)
    monkeypatch.setattr(r.settings, "MOCK_MODE", False, raising=False)
    monkeypatch.setattr(r.settings, "OWNER_USER_ID", DONO_A, raising=False)
    monkeypatch.setattr(r, "_criadas", False)
    for fn in (r.obter_empresa, r.listar_lancamentos, r.listar_saldos_extrato):
        monkeypatch.setattr(r, fn.__name__, fn.__wrapped__)
    monkeypatch.setattr(r, "limpar_caches", lambda: None)
    return r


def _mov(dia, valor, desc, fitid, contraparte=None):
    from core.pj.ofx import chave_movimento
    m = {"data": date(2026, 11, dia), "valor": valor, "descricao": desc,
         "contraparte": contraparte or desc, "fitid": fitid}
    m["chave"] = chave_movimento("conta-1", m)
    return m


def test_importa_reimporta_sem_duplicar_e_define_saldo_de_abertura(repo):
    movs = [dict(_mov(3, 5000.0, "Cliente Alfa", "F1"), categoria="receita_servicos"),
            dict(_mov(5, -300.0, "Simples Nacional", "F2"), categoria="das")]
    saldo = {"data": date(2026, 11, 6), "valor": 9700.0}
    r1 = repo.importar_extrato(movs, "nov.ofx", saldo)
    assert (r1["novos"], r1["repetidos"]) == (2, 0)
    r2 = repo.importar_extrato(movs, "nov.ofx", saldo)
    assert (r2["novos"], r2["repetidos"]) == (0, 2)

    emp = repo.obter_empresa()
    assert emp["saldo_inicial"] == 5000.0 and emp["data_saldo_inicial"] == date(2026, 11, 2)
    lancs = repo.listar_lancamentos()
    assert repo.saldo_calculado(emp, lancs, date(2026, 11, 6)) == 9700.0
    assert repo.listar_saldos_extrato()[0]["saldo"] == 9700.0


def test_previsto_recebe_baixa_pelo_extrato_em_vez_de_duplicar(repo):
    ok, msg = repo.criar_lancamento(date(2026, 11, 10), "Aluguel", 2000.0, entrada=False,
                                    categoria="aluguel", status="previsto", repeticoes=3)
    assert ok, msg
    mov = _mov(11, -2000.0, "Pix Imobiliaria", "F9")
    ctx = repo.preparar_importacao([mov])
    assert ctx["ok"] and 0 in ctx["vinculos"] and ctx["vinculos"][0]["previsto"]

    res = repo.importar_extrato([dict(mov, vincular_id=ctx["vinculos"][0]["transaction_id"],
                                      categoria=None)], "nov.ofx")
    assert res["vinculados"] == 1 and res["novos"] == 0
    lancs = repo.listar_lancamentos()
    pagos = [x for x in lancs if x["status"] == "pago"]
    assert len(pagos) == 1 and pagos[0]["data"] == date(2026, 11, 11)
    assert pagos[0]["categoria"] == "aluguel" and pagos[0]["chave"] == mov["chave"]
    assert sum(1 for x in lancs if x["status"] == "previsto") == 2
    # reimportar o mesmo arquivo não cria nada
    assert repo.importar_extrato([mov], "nov.ofx")["repetidos"] == 1


def test_categoria_escolhida_vira_regra_para_o_proximo_extrato(repo):
    m = dict(_mov(3, 800.0, "Pix recebido", "R1", contraparte="Cliente Beta"),
             categoria="receita_servicos")
    repo.importar_extrato([m], "a.ofx")
    ctx = repo.preparar_importacao([_mov(4, 900.0, "Pix recebido", "R2", contraparte="CLIENTE BETA")])
    assert ctx["regras"] == {"cliente beta": "receita_servicos"}


def test_outro_dono_nao_ve_nem_altera_nada(repo, monkeypatch):
    repo.importar_extrato([dict(_mov(3, 100.0, "X", "Z1"), categoria=None)], "a.ofx")
    lanc_id = repo.listar_lancamentos()[0]["id"]
    monkeypatch.setattr(repo.settings, "OWNER_USER_ID", DONO_B, raising=False)
    assert repo.listar_lancamentos() == []
    assert repo.obter_empresa() is None
    assert repo.classificar(lanc_id, "tarifas")[0] is False
    assert repo.excluir(lanc_id)[0] is False


def test_movimento_do_extrato_nao_se_exclui_e_baixa_mantem_o_sinal(repo):
    repo.importar_extrato([dict(_mov(3, -50.0, "Tarifa", "T1"), categoria="tarifas")], "a.ofx")
    extrato_id = repo.listar_lancamentos()[0]["id"]
    assert repo.excluir(extrato_id)[0] is False
    repo.criar_lancamento(date(2026, 11, 20), "Contador", 400.0, entrada=False,
                          categoria="contabilidade", status="previsto")
    prev_id = next(x["id"] for x in repo.listar_lancamentos() if x["status"] == "previsto")
    ok, _ = repo.dar_baixa(prev_id, date(2026, 11, 21), 420.0)
    assert ok
    baixado = next(x for x in repo.listar_lancamentos() if x["id"] == prev_id)
    assert baixado["valor"] == -420.0 and baixado["status"] == "pago"


def test_dados_da_empresa(repo):
    assert repo.salvar_empresa("12.345.678/0001-90", "Empresa Teste", "V") == (True, "")
    assert repo.obter_empresa()["anexo_simples"] == "V"
    assert repo.salvar_empresa("123", "x", "V")[0] is False
