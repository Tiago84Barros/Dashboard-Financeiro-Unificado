"""A retenção da trilha de auditoria, e quem a executa.

``trilha.expurgar`` existia desde que a trilha nasceu e **nunca teve chamador**
-- em nenhum job, script ou view. Uma política de retenção sem execução é um
parágrafo de documentação em cima de uma tabela que só cresce, e o Supabase
está em 427 MB de 500.

Nenhum teste toca banco: a engine é falsa e registra o SQL que recebeu. O que
importa aqui não é o DELETE funcionar (isso o Postgres garante) -- é o expurgo
recusar entrada errada, não mentir sobre o que apagou, e ter de fato quem o
chame.
"""
from __future__ import annotations

import datetime as dt

from core.auditoria import trilha as T
from data_pipeline import orchestrator, update_registry
from data_pipeline.jobs import update_retencao as J

AGORA = dt.datetime(2026, 9, 3, 12, 0, tzinfo=dt.timezone.utc)


class EngineFalsa:
    """Devolve ``quantas`` na contagem e anota todo SQL executado."""

    def __init__(self, quantas: int = 7):
        self.quantas = quantas
        self.sql: list[str] = []

    def begin(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, clausula, params=None):
        texto = str(clausula)
        self.sql.append(texto)
        engine = self

        class Resultado:
            rowcount = engine.quantas

            @staticmethod
            def scalar_one():
                return engine.quantas

        return Resultado()

    @property
    def deletes(self) -> list[str]:
        return [s for s in self.sql if "DELETE" in s]


# ── O expurgo ────────────────────────────────────────────────────────────────
def test_janela_curta_demais_e_recusada_antes_de_tocar_no_banco():
    """Guardar a **entrada** e não o tamanho da saída.

    ``dias=0`` não é uma retenção mais rigorosa: é valor errado chegando por
    configuração, e apagaria a trilha inteira -- inclusive as linhas que
    explicariam por que ela sumiu. Um teto sobre a *saída* recusaria também a
    fatia legitimamente grande (o job dormiu meses e voltou), e aí a dívida
    ficaria de pé sem ninguém ver.
    """
    eng = EngineFalsa()
    r = T.expurgar(engine=eng, dias=0, agora=AGORA, aplicar=True)
    assert r["aplicado"] is False and r["removidos"] == 0
    assert "piso" in r["recusado"]
    assert eng.sql == []          # nem a contagem chegou a rodar


def test_simulacao_conta_o_alcance_e_nao_apaga():
    eng = EngineFalsa(quantas=7)
    r = T.expurgar(engine=eng, agora=AGORA)
    assert r["alcance"] == 7
    assert r["removidos"] == 0    # alcance e remoção são grandezas diferentes
    assert r["aplicado"] is False
    assert eng.deletes == []


def test_com_aplicar_o_numero_devolvido_e_o_que_saiu():
    eng = EngineFalsa(quantas=7)
    r = T.expurgar(engine=eng, agora=AGORA, aplicar=True)
    assert r["removidos"] == 7 and r["aplicado"] is True
    assert len(eng.deletes) == 1
    assert r["corte"].startswith("2025-09-03")   # 365 dias antes


# ── O job ────────────────────────────────────────────────────────────────────
def test_erro_de_digitacao_no_interruptor_nao_apaga_auditoria():
    """Ler valor desconhecido como "sim" deixaria um typo apagar auditoria."""
    assert J.aplicar_ligado("true") and J.aplicar_ligado("1")
    for valor in ("ture", "sim", "", None, "false", "0"):
        assert not J.aplicar_ligado(valor), valor


def test_job_simulando_sai_como_sucesso_mas_escreve_o_que_ficou_pendente():
    """Silêncio faria "não autorizado" parecer "nada a remover".

    Aqui o ``expurgar`` de verdade roda contra a engine falsa: o que se quer
    verificar é justamente a costura entre o job e ele -- um duplo do módulo
    testaria o duplo.
    """
    res = _rodar(engine=EngineFalsa(quantas=12), ligado=False)
    assert res["status"] == "success" and res["records_updated"] == 0
    assert "12 registro(s)" in res["error_message"]
    assert J.VAR_APLICAR in res["error_message"]


def test_job_com_autorizacao_reporta_o_que_removeu():
    eng = EngineFalsa(quantas=12)
    res = _rodar(engine=eng, ligado=True)
    assert res["status"] == "success" and res["records_updated"] == 12
    assert res["error_message"] is None
    assert len(eng.deletes) == 1


def test_job_sem_banco_falha_em_vez_de_dizer_que_expurgou():
    res = _rodar(engine=None, ligado=False)
    assert res["status"] == "failed" and res["records_updated"] == 0


def _rodar(*, engine, ligado: bool) -> dict:
    """Executa ``J.run`` com banco e interruptor sob controle do teste."""
    from data_pipeline.utils import db_utils

    original_engine = db_utils.get_pipeline_engine
    original_var = J.os.environ.get(J.VAR_APLICAR)
    db_utils.get_pipeline_engine = lambda: engine
    J.os.environ[J.VAR_APLICAR] = "true" if ligado else "false"
    try:
        return J.run()
    finally:
        db_utils.get_pipeline_engine = original_engine
        if original_var is None:
            J.os.environ.pop(J.VAR_APLICAR, None)
        else:
            J.os.environ[J.VAR_APLICAR] = original_var


# ── O registro: derivado da estrutura que executa, não de uma lista à parte ──
def test_todo_job_ativo_do_registry_tem_modulo_no_orquestrador():
    """``memoria: verificador-e-escritor-listas-diferentes``.

    Registrar o job numa lista e esquecê-lo na outra produz exatamente o caso
    do A-140: a rotina existe, está declarada, e nunca roda. A checagem sai da
    própria estrutura, e não de uma cópia que alguém precisa lembrar de manter.
    """
    ativos = {j["job_name"] for j in update_registry._DEFAULT_REGISTRY
              if j.get("is_active") and j.get("frequency") != "manual"}
    assert "update_retencao" in ativos
    assert ativos <= set(orchestrator._JOB_MAP), ativos - set(orchestrator._JOB_MAP)
