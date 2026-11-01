"""Medição do tempo de cada execução do app (core.medicao_rota)."""
from __future__ import annotations

import threading

import pytest
from sqlalchemy import create_engine, text

from core import medicao_rota as m


class Relogio:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _cron(relogio: Relogio) -> m.Cronometro:
    return m.Cronometro(inicio=relogio(), relogio=relogio)


def test_fases_somam_o_tempo_de_cada_trecho():
    r = Relogio()
    c = _cron(r)
    r.t = 0.2
    c.entrar("import")
    r.t = 0.5
    c.entrar("render")
    r.t = 1.5
    c.encerrar()
    assert round(c.fases["preambulo"].ms) == 200
    assert round(c.fases["import"].ms) == 300
    assert round(c.fases["render"].ms) == 1000
    assert round(c.total_ms) == 1500


def test_fase_desconhecida_e_recusada():
    with pytest.raises(ValueError):
        _cron(Relogio()).entrar("banco")


def test_sql_vai_para_a_fase_corrente_e_guarda_a_mais_lenta():
    c = _cron(Relogio())
    c.registrar_sql(10, "select 1")
    c.entrar("render")
    c.registrar_sql(300, "select   *\n from public.macro where x = :x")
    c.registrar_sql(20, "select 2")
    assert (c.fases["preambulo"].sql_n, c.fases["render"].sql_n) == (1, 2)
    assert c.fases["render"].sql_ms == 320
    assert c.sql_mais_lenta == "select * from public.macro where x = :x"


def test_sql_depois_de_encerrar_nao_conta():
    c = _cron(Relogio())
    c.encerrar()
    c.registrar_sql(50, "select 1")
    assert all(f.sql_n == 0 for f in c.fases.values())


def test_trecho_sql_e_cortado_numa_linha():
    longo = "select " + ", ".join(f"coluna_{i}" for i in range(50))
    trecho = m.trecho_sql(longo)
    assert len(trecho) == 90 and trecho.endswith("…") and "\n" not in trecho


@pytest.mark.parametrize(
    ("rota", "anterior", "tipo"),
    [("A", None, m.ABERTURA), ("A", "B", m.TROCA), ("A", "A", m.INTERACAO)],
)
def test_classificar(rota, anterior, tipo):
    assert m.classificar(rota, anterior) == tipo


def test_fechar_execucao_grava_historico_e_classifica_a_troca():
    estado: dict = {}
    for rota in ("Inicio", "Inicio", "FIIs"):
        r = Relogio()
        c = _cron(r)
        r.t = 0.1
        m.fechar_execucao(c, estado, rota=rota, import_frio=False, concluiu=True)
    tipos = [x["tipo"] for x in estado[m.CHAVE_HISTORICO]]
    assert tipos == [m.ABERTURA, m.INTERACAO, m.TROCA]
    assert estado[m.CHAVE_ULTIMA_ROTA] == "FIIs"
    assert m.atual() is None


def test_fechar_execucao_sem_session_state():
    linha = m.fechar_execucao(_cron(Relogio()), None, rota="X", import_frio=True, concluiu=False)
    assert linha["tipo"] == m.ABERTURA and linha["concluiu"] is False


def test_historico_tem_limite():
    hist: list[dict] = []
    for i in range(m.LIMITE_HISTORICO + 5):
        hist = m.anexar(hist, {"i": i})
    assert len(hist) == m.LIMITE_HISTORICO and hist[-1]["i"] == m.LIMITE_HISTORICO + 4


def _linha(rota, tipo, total, sql=0, concluiu=True):
    return {
        "rota": rota, "tipo": tipo, "concluiu": concluiu, "total_ms": total,
        "sql_ms": sql, "sql_n": 1, "fora_do_banco_ms": total - sql, "import_ms": 0,
    }


def test_agregar_usa_mediana_ignora_interrompidas_e_ordena_pela_mais_lenta():
    hist = [
        _linha("FIIs", "troca", 1000, 600),
        _linha("FIIs", "troca", 9000, 8000),  # um pico não define a tela
        _linha("FIIs", "troca", 1200, 700),
        _linha("FIIs", "troca", 99999, concluiu=False),
        _linha("Inicio", "troca", 3000, 100),
    ]
    agg = m.agregar(hist)
    assert [a["rota"] for a in agg] == ["Inicio", "FIIs"]
    fiis = agg[1]
    assert fiis["execucoes"] == 3
    assert fiis["total_mediana_ms"] == 1200 and fiis["total_max_ms"] == 9000
    assert fiis["sql_mediana_ms"] == 700


def test_ouvintes_contam_consultas_reais_e_sobrevivem_a_erro():
    engine = create_engine("sqlite://")
    c = m.Cronometro(inicio=0.0)
    m.ativar(c)
    try:
        c.entrar("render")
        with engine.connect() as conn:
            conn.execute(text("select 1"))
            with pytest.raises(Exception):
                conn.execute(text("select * from tabela_que_nao_existe"))
            conn.execute(text("select 2"))
            # A consulta que falhou não deixou início pendurado na pilha.
            assert conn.info.get(m._CHAVE_INICIO) == []
    finally:
        m.desativar()
    assert c.fases["render"].sql_n == 2


def test_consulta_sem_cronometro_ativo_nao_e_contada():
    engine = create_engine("sqlite://")
    c = m.Cronometro(inicio=0.0)
    m.ativar(c)
    m.desativar()
    with engine.connect() as conn:
        conn.execute(text("select 1"))
    assert all(f.sql_n == 0 for f in c.fases.values())


def test_cronometro_de_uma_sessao_nao_recebe_consulta_de_outra():
    engine = create_engine("sqlite://")
    c = m.Cronometro(inicio=0.0)
    m.ativar(c)
    try:
        def outra_sessao():
            with engine.connect() as conn:
                conn.execute(text("select 1"))

        t = threading.Thread(target=outra_sessao)
        t.start()
        t.join()
    finally:
        m.desativar()
    assert c.fases["preambulo"].sql_n == 0
