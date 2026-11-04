"""Frescor da Selecao de FIIs mede a idade mediana do dado que a tela le.

Ate 03/11/2026 era ``max(updated_at)`` de ``market.fiis``: um unico fundo
tocado hoje dava 100% de frescor a secao inteira, com centenas de linhas
paradas atras dele, e a tabela nem era a vitrine que a selecao e a carteira
leem. Agora, com vitrine, mede ``reference_date`` da vitrine (o dado mais
recente de cada payload), nunca ``as_of_date``: o publicador carimba
``as_of_date`` com o instante da publicacao, e republicar um armazem parado
renovaria o carimbo sem renovar o dado.
"""
from datetime import date, timedelta

import pytest

import core.confianca_secao as cs


class _Resultado:
    def __init__(self, linha):
        self._linha = linha

    def one(self):
        return self._linha


class _Conn:
    """Conexao falsa: devolve uma linha fixa e guarda o SQL executado."""

    def __init__(self, mediana, linhas=394):
        self._linha = (mediana, linhas)
        self.sql: list[str] = []

    def execute(self, clausula, *_args, **_kwargs):
        self.sql.append(str(clausula))
        return _Resultado(self._linha)


@pytest.fixture
def com_vitrine(monkeypatch):
    monkeypatch.setattr("core.universo_decisao._tem_vitrine_fii", lambda _conn: True)


@pytest.fixture
def sem_vitrine(monkeypatch):
    monkeypatch.setattr("core.universo_decisao._tem_vitrine_fii", lambda _conn: False)


def test_com_vitrine_mede_a_vitrine_pela_data_do_dado(com_vitrine):
    conn = _Conn(date.today() - timedelta(days=2))
    comp = cs._frescor_fii(conn)

    sql = conn.sql[-1]
    assert "market.fii_selection_inputs" in sql
    assert "reference_date" in sql
    # O carimbo da publicacao nao mede o dado.
    assert "as_of_date" not in sql and "generated_at" not in sql
    assert comp.nome == "Frescor"
    assert comp.pct == 100.0
    assert "vitrine" in comp.evidencia and "394 fundos" in comp.evidencia


def test_mediana_e_nao_maximo(com_vitrine):
    conn = _Conn(date.today())
    cs._frescor_fii(conn)
    sql = conn.sql[-1].lower()

    # max() deixava um fundo recem-tocado esconder centenas de linhas velhas.
    assert "max(" not in sql
    assert "percentile_disc(0.5)" in sql


def test_sem_vitrine_cai_no_cadastro_e_diz_isso(sem_vitrine):
    conn = _Conn(date.today() - timedelta(days=10))
    comp = cs._frescor_fii(conn)

    sql = conn.sql[-1]
    assert "market.fiis" in sql and "market.fii_selection_inputs" not in sql
    assert "max(" not in sql.lower()
    assert "cadastro" in comp.evidencia and "vitrine indisponivel" in comp.evidencia
    assert 0.0 < comp.pct < 100.0


def test_dado_parado_zera_e_nomeia_a_ingestao(com_vitrine):
    comp = cs._frescor_fii(_Conn(date.today() - timedelta(days=45)))

    assert comp.pct == 0.0
    assert "ingestao parada" in comp.evidencia


def test_sem_data_nenhuma_e_nao_medido_e_nunca_zero(com_vitrine):
    comp = cs._frescor_fii(_Conn(None, linhas=0))

    assert comp.pct is None
    assert not comp.medido
