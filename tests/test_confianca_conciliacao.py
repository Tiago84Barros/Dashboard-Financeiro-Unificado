# -*- coding: utf-8 -*-
"""A-145: a medicao de confianca punia quem tinha a evidencia.

`ConfiancaSecao.pct` e media ponderada dos componentes MEDIDOS, com pesos
renormalizados -- entao `pct=None` e neutro (sai da conta) e `pct=0.0` e a pior
nota possivel. O extrato bancario usava as duas coisas ao contrario: quem nunca
importou extrato ficava com `None` (neutro) e quem importou 891 movimentos ha
dez meses ficava com `0.0` no peso cheio de 0,20.

Resultado medido no Supabase em 27/08/2026: 891 movimentos, todos conferidos,
ultimo em 2025-10-13. O usuario que fez a conciliacao tirava nota MENOR do que
o que nunca fez -- e `confianca_dashboard_geral` propaga isso pelo `min`.

Conciliacao velha nao esta errada, esta menos relevante para o mes corrente. O
frescor passou a decidir o PESO, e no limite o caso reencontra exatamente o
"nunca importou" em vez de cair abaixo dele.
"""
from __future__ import annotations

from datetime import date, timedelta

from core import confianca_secao as cs


class _Conn:
    def __init__(self, movimentos: int, confirmados: int, dias: int | None):
        self.movimentos = movimentos
        self.confirmados = confirmados
        self.dias = dias

    def execute(self, stmt, *a, **k):
        sql = " ".join(str(stmt).split()).lower()
        if "max(data_movimento)" in sql:
            valor = (None if self.dias is None
                     else date.today() - timedelta(days=self.dias))
            return _R(valor)
        if "bank_statement_movements" in sql and "confirmada" in sql:
            return _R(self.confirmados)
        if "bank_statement_movements" in sql:
            return _R(self.movimentos)
        if "max(due_date)" in sql:
            return _R(date.today())
        return _R(0)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _R:
    def __init__(self, valor):
        self.valor = valor

    def scalar(self):
        return self.valor

    def fetchone(self):
        return (self.valor,)

    def one(self):
        return (self.valor,)


class _Engine:
    def __init__(self, conn):
        self._conn = conn

    def connect(self):
        return self._conn


def _secao(movimentos, confirmados, dias):
    return cs.confianca_controle_financeiro(
        _Engine(_Conn(movimentos, confirmados, dias)))


def _comp(secao, prefixo):
    return next((c for c in secao.componentes
                 if c.nome.lower().startswith(prefixo)), None)


def test_conciliacao_recente_entra_com_o_peso_cheio():
    c = _comp(_secao(891, 891, 5), "concilia")
    assert c is not None
    assert c.pct == 100.0
    assert c.peso == 0.20


def test_conciliacao_antiga_perde_peso_e_nao_vira_zero():
    """O caso real: 891 movimentos conferidos, ultimo ha 318 dias."""
    c = _comp(_secao(891, 891, 318), "concilia")
    assert c.pct == 100.0          # os 891 continuam conferidos
    assert c.peso == 0.0           # mas nao falam do mes corrente


def test_quem_conciliou_nao_tira_nota_menor_do_que_quem_nunca_conciliou():
    """A monotonicidade que faltava: evidencia velha nunca pior que ausencia."""
    com_extrato_velho = _secao(891, 891, 318).pct
    sem_extrato = _secao(0, 0, None).pct
    assert com_extrato_velho is not None and sem_extrato is not None
    assert com_extrato_velho >= sem_extrato - 1e-9


def test_conciliacao_incompleta_e_recente_reprova_de_verdade():
    """Perder peso por idade nao pode virar imunidade: metade conferida ha
    poucos dias e um defeito atual, e tem de aparecer."""
    c = _comp(_secao(800, 400, 5), "concilia")
    assert c.pct == 50.0
    assert c.peso == 0.20


def test_extrato_parado_continua_declarado_nas_notas():
    secao = _secao(891, 891, 318)
    assert any("318" in n for n in secao.notas)
    assert "318" in _comp(secao, "concilia").evidencia


def test_sem_extrato_o_componente_continua_nao_medido():
    c = _comp(_secao(0, 0, None), "extrato")
    assert c is not None and c.pct is None
