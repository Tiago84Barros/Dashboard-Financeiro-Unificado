"""Lançamentos previstos (contas a pagar e a receber).

Regra que estes testes guardam: previsto não é dinheiro. Ele não entra no
mês, no saldo, no orçamento nem nos gastos por categoria até a baixa -- e a
baixa vem à mão ou da conciliação do extrato.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

import core.controle as controle
import core.previstos as prev
from core.conciliacao_bancaria import impacto_no_saldo, sugerir_vinculos
from core.llm_context_financeiro import _bloco_previstos
from core.previstos import (
    datas_mensais,
    projetar_saldo,
    resumir,
    situacao,
    sql_realizado,
)

HOJE = date(2026, 11, 10)


def _p(dia, valor, desc="Conta", mes=11):
    return {"id": f"{desc}-{dia}", "descricao": desc, "valor": valor,
            "vencimento": date(2026, mes, dia), "categoria": "Moradia",
            "parcela": 1, "total_parcelas": 1}


# ── consultas de realizado ignoram previsto ─────────────────────────────────

@pytest.mark.parametrize("nome", [
    "_SQL_TRANSACOES", "_SQL_GASTOS_CARTAO_MENSAL",
    "_SQL_GASTOS_CATEGORIA_ANUAL", "_SQL_TRANSACOES_FILTRADAS",
])
def test_consulta_de_realizado_exclui_previsto(nome):
    sql = " ".join(getattr(controle, nome).split())
    assert "NOT (t.status = 'pending'" in sql


def test_previsto_de_cartao_nao_existe_parcela_continua_nas_telas_do_cartao():
    """O predicado só tira pendente que NÃO é cartão: parcela de fatura com
    status pendente segue onde estava."""
    assert "<> 'credit_card'" in sql_realizado()


# ── funções puras ───────────────────────────────────────────────────────────

def test_serie_mensal_respeita_fim_de_mes_e_virada_de_ano():
    assert datas_mensais(date(2026, 12, 31), 3) == [
        date(2026, 12, 31), date(2027, 1, 31), date(2027, 2, 28)]
    assert datas_mensais(date(2027, 1, 31), 3)[2] == date(2027, 3, 31)


def test_serie_tem_teto():
    assert len(datas_mensais(date(2026, 1, 1), 999)) == prev.MAX_REPETICOES
    assert datas_mensais(date(2026, 1, 1), 0) == [date(2026, 1, 1)]


def test_situacao():
    assert situacao(date(2026, 11, 9), HOJE) == "vencido"
    assert situacao(HOJE, HOJE) == "hoje"
    assert situacao(date(2026, 11, 11), HOJE) == "em 1 dia"


def test_resumo_separa_vencido_pagar_e_receber():
    r = resumir([_p(5, -100.0), _p(15, -300.0), _p(20, 5000.0, "Salário"),
                 _p(25, -50.0, mes=12)], HOJE)
    assert r["n_vencidos"] == 1 and r["total_vencido"] == -100.0
    assert r["a_pagar"] == 300.0
    assert r["a_receber"] == 5000.0


def test_projecao_trata_vencido_como_pago_hoje_e_para_no_horizonte():
    linhas = projetar_saldo(1000.0, [_p(20, -300.0), _p(5, -100.0), _p(25, -50.0, mes=12)],
                            HOJE, HOJE + timedelta(days=30))
    assert [x["saldo_apos"] for x in linhas] == [900.0, 600.0]
    assert linhas[0]["data_efeito"] == HOJE


# ── gravação ────────────────────────────────────────────────────────────────

class _Conn:
    def __init__(self, tipo_conta="checking", amount_atual=-100.0, rowcount=1):
        self.tipo_conta, self.amount_atual, self.rowcount = tipo_conta, amount_atual, rowcount
        self.inserts: list[dict] = []
        self.updates: list[dict] = []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "FROM accounts" in sql and "SELECT id::text" in sql:
            return SimpleNamespace(fetchone=lambda: SimpleNamespace(id="acc", type=self.tipo_conta))
        if "INSERT INTO transactions" in sql:
            self.inserts.append(dict(params))
            return SimpleNamespace(rowcount=1)
        if "SELECT amount FROM transactions" in sql:
            return SimpleNamespace(fetchone=lambda: SimpleNamespace(amount=self.amount_atual))
        if "UPDATE transactions" in sql:
            self.updates.append(dict(params))
            return SimpleNamespace(rowcount=self.rowcount)
        return SimpleNamespace(rowcount=0, fetchone=lambda: None)


@pytest.fixture
def banco(monkeypatch):
    conn = _Conn()

    @contextmanager
    def _begin():
        yield conn

    monkeypatch.setattr(prev.settings, "MOCK_MODE", False, raising=False)
    monkeypatch.setattr(prev.settings, "OWNER_USER_ID", "owner-1", raising=False)
    monkeypatch.setattr(prev, "_engine_e_dono",
                        lambda: (SimpleNamespace(begin=_begin), "owner-1"))
    monkeypatch.setattr(prev, "_limpar_caches", lambda: None)
    return conn


def test_serie_de_previstos_grava_pendente_com_sinal_e_grupo(banco):
    ok, msg = prev.criar_previstos("Aluguel", 2500.0, "expense", date(2026, 11, 5),
                                   "cat", "acc", repeticoes=3)
    assert ok, msg
    assert [i["amount"] for i in banco.inserts] == [-2500.0] * 3
    assert [i["due_date"] for i in banco.inserts] == [
        date(2026, 11, 5), date(2026, 12, 5), date(2027, 1, 5)]
    assert len({i["grupo"] for i in banco.inserts}) == 1 and banco.inserts[0]["grupo"]
    assert [i["parcela"] for i in banco.inserts] == [1, 2, 3]


def test_previsto_unico_nao_tem_grupo_e_entrada_e_positiva(banco):
    prev.criar_previstos("Salário", 8000.0, "income", date(2026, 11, 5), None, "acc")
    assert banco.inserts[0]["amount"] == 8000.0
    assert banco.inserts[0]["grupo"] is None


def test_previsto_no_cartao_e_recusado(banco):
    banco.tipo_conta = "credit_card"
    ok, _ = prev.criar_previstos("X", 10.0, "expense", date(2026, 11, 5), None, "acc")
    assert not ok and not banco.inserts


def test_baixa_com_valor_diferente_mantem_o_sinal(banco):
    ok, _ = prev.dar_baixa("tx", date(2026, 11, 7), 105.0)
    assert ok
    assert banco.updates[0]["valor"] == -105.0
    assert banco.updates[0]["data"] == date(2026, 11, 7)


def test_baixa_de_algo_que_nao_e_previsto_falha(banco):
    banco.rowcount = 0
    ok, msg = prev.dar_baixa("tx", date(2026, 11, 7))
    assert not ok and "já baixado" in msg


# ── conciliação dá baixa ────────────────────────────────────────────────────

def test_conciliacao_marca_par_previsto():
    v = sugerir_vinculos(
        [{"data_movimento": date(2026, 11, 6), "valor": -2500.0, "descricao_original": "Aluguel"}],
        [{"id": "t", "due_date": date(2026, 11, 5), "amount": -2500.0,
          "description": "Aluguel", "category_id": "c", "status": "pending"}])
    assert v[0]["previsto"] is True


def test_previsto_vinculado_entra_no_saldo_projetado_da_importacao():
    """O previsto não está no saldo do app; a baixa é o que o põe lá."""
    rows = [
        {"data_movimento": date(2026, 11, 6), "valor": -2500.0, "categoria_id": None,
         "vincular_transaction_id": "t", "vincular_previsto": True},
        {"data_movimento": date(2026, 11, 6), "valor": -80.0, "categoria_id": "c",
         "vincular_transaction_id": "t2"},
    ]
    assert impacto_no_saldo(rows, date(2026, 11, 30)) == (-2500.0, 0.0)


# ── contexto da IA ──────────────────────────────────────────────────────────

def test_ia_recebe_previstos_e_sabe_quando_a_leitura_falhou():
    texto = "\n".join(_bloco_previstos([_p(5, -100.0, "Escola")]))
    assert "Escola" in texto and "a pagar" in texto
    falhou = "\n".join(_bloco_previstos(None))
    assert "não lidos" in falhou


# ── aba ─────────────────────────────────────────────────────────────────────

def _app_aba():
    from datetime import date, timedelta

    import views.controle_previstos as cp

    hoje = date.today()
    cp.listar_previstos = lambda: [
        {"id": "a", "descricao": "Aluguel", "valor": -2500.0,
         "vencimento": hoje - timedelta(days=2), "categoria": "Moradia",
         "parcela": 3, "total_parcelas": 12},
        {"id": "b", "descricao": "Salário", "valor": 8000.0,
         "vencimento": hoje + timedelta(days=5), "categoria": "Salário",
         "parcela": 1, "total_parcelas": 1},
    ]
    cp.saldo_contas_movimentacao = lambda: 1000.0
    cp.render_previstos()


def test_aba_mostra_vencido_e_projecao():
    at = AppTest.from_function(_app_aba).run()
    assert not at.exception
    assert at.warning and "vencido" in at.warning[0].value
    html = " ".join(m.value for m in at.markdown)
    # 1000 − 2500 (vencido, pago hoje) + 8000 = 6500
    assert "6.500,00" in html


def _app_vazio():
    import views.controle_previstos as cp

    cp.listar_previstos = lambda: []
    cp.saldo_contas_movimentacao = lambda: 0.0
    cp.render_previstos()


def test_aba_vazia_ensina_onde_criar():
    at = AppTest.from_function(_app_vazio).run()
    assert not at.exception
    assert "Previsto" in " ".join(m.value for m in at.markdown)


# ── achados da revisão (Codex, PR #647) ─────────────────────────────────────

def test_projecao_ignora_previsto_de_conta_fora_do_saldo_base():
    """Conta de investimento com previsto não está no saldo de partida:
    somar o compromisso dela inventaria um rombo."""
    fora = {**_p(15, -100.0, "Taxa corretora"), "na_projecao": False}
    linhas = projetar_saldo(1000.0, [fora, _p(16, -50.0)], HOJE, HOJE + timedelta(days=30))
    assert [x["saldo_apos"] for x in linhas] == [950.0]


def test_previsto_em_conta_que_nao_e_de_movimentacao_e_recusado(banco):
    banco.tipo_conta = "investment"
    ok, msg = prev.criar_previstos("X", 10.0, "expense", date(2026, 11, 5), None, "acc")
    assert not ok and "conta corrente" in msg and not banco.inserts


class _ConnPrevistos:
    def __init__(self, total, atrasados):
        self.total, self.atrasados = total, atrasados

    def execute(self, stmt, params=None, *a, **k):
        sql = " ".join(str(stmt).split())
        assert (params or {}).get("uid") == "dono-1" and "t.user_id = CAST(:uid" in sql, sql
        v = self.atrasados if "CURRENT_DATE" in sql else self.total
        return SimpleNamespace(scalar=lambda: v)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _comp_previstos(monkeypatch, total, atrasados, dono="dono-1"):
    from core.confianca_secao import _componente_previstos_em_dia
    from core.config import settings
    monkeypatch.setattr(settings, "OWNER_USER_ID", dono, raising=False)
    notas: list[str] = []
    eng = SimpleNamespace(connect=lambda: _ConnPrevistos(total, atrasados))
    return _componente_previstos_em_dia(eng, notas), notas


def test_previstos_em_dia_conta_so_os_do_dono(monkeypatch):
    (c,), notas = _comp_previstos(monkeypatch, 4, 1)
    assert c.pct == 75.0 and notas


def test_previstos_em_dia_sem_dono_nao_mede(monkeypatch):
    assert _comp_previstos(monkeypatch, 4, 1, dono="") == ([], [])
