"""A coleta de documentos da CVM (IPE e FNET) entra na rotina agendada.

Em 29/09/2026 o IPE parava em 04/09 e o FNET em 15/07: os coletores existiam e
só rodavam à mão. Agendá-los só resolve se a falha sair com código diferente de
zero -- senão a rotina carimba em dia o dia em que a CVM não respondeu.
"""
import sys
from contextlib import nullcontext
from datetime import date
from unittest.mock import MagicMock

import pytest

from core.publicacao_agenda import ALVOS, POR_CHAVE


def test_alvos_de_documentos_sao_diarios_e_vem_antes_de_quem_os_le():
    ordem = [a.chave for a in ALVOS]
    for chave in ("fii_documentos", "cvm_ipe"):
        assert POR_CHAVE[chave].cadencia_dias == 1
        assert ordem.index(chave) < ordem.index("informacoes_recentes")
    assert ordem.index("fii_documentos") < ordem.index("fii_ingest")


def test_ipe_coleta_o_ano_corrente_e_depois_extrai_o_texto():
    passos = POR_CHAVE["cvm_ipe"].passos
    assert passos[0] == ("scripts/backfill_cvm_ipe.py", "--years", "recentes", "--apply")
    assert passos[1][0] == "scripts/drenar_cvm_fulltext.py"


def test_fnet_le_o_arquivo_estruturado_da_cvm_no_armazem():
    (passo,) = POR_CHAVE["fii_documentos"].passos
    assert passo[:2] == ("run_market_ingest.py", "fiis-cvm-structured")
    assert "--warehouse" in passo


def test_anos_recentes_do_ipe_incluem_o_anterior_so_no_comeco_do_ano():
    from scripts.backfill_cvm_ipe import anos_do_argumento

    assert anos_do_argumento("recentes", date(2026, 9, 29)) == [2026]
    assert anos_do_argumento("recentes", date(2027, 2, 10)) == [2026, 2027]
    assert anos_do_argumento("2024,2025") == [2024, 2025]


def _motor_sem_documentos():
    conexao = MagicMock()
    conexao.execute.return_value.fetchall.return_value = []
    motor = MagicMock()
    motor.connect.return_value = nullcontext(conexao)
    return motor


@pytest.mark.parametrize("aplicar", [False, True])
def test_ipe_sem_csv_sai_com_erro(monkeypatch, aplicar):
    from scripts import backfill_cvm_ipe as mod

    monkeypatch.setattr(mod, "_engine", lambda destino: _motor_sem_documentos())
    monkeypatch.setattr(mod, "_codigo_to_ticker", lambda conn: {9512: "PETR4"})
    monkeypatch.setattr(mod.ipe, "fetch_ipe_csv", lambda ano: None)
    assert mod.run([2026], 30, apply=aplicar, clean_previous=False) == 1


def test_ipe_com_csv_e_nada_novo_sai_com_sucesso(monkeypatch):
    from scripts import backfill_cvm_ipe as mod

    monkeypatch.setattr(mod, "_engine", lambda destino: _motor_sem_documentos())
    monkeypatch.setattr(mod, "_codigo_to_ticker", lambda conn: {9512: "PETR4"})
    monkeypatch.setattr(mod.ipe, "fetch_ipe_csv", lambda ano: b"csv")
    monkeypatch.setattr(mod.ipe, "parse_ipe_csv", lambda conteudo: [])
    assert mod.run([2026], 30, apply=False, clean_previous=False) == 0


@pytest.mark.parametrize("status,esperado", [("failed", 1), ("success", 0)])
def test_drenagem_bloqueada_sai_com_erro(monkeypatch, status, esperado):
    from data_pipeline.jobs import update_cvm_fulltext
    from scripts import drenar_cvm_fulltext as mod

    monkeypatch.setattr(mod, "_warehouse_url", lambda: "postgresql://x@localhost:5433/db")
    monkeypatch.setattr(mod, "create_engine", lambda *a, **k: MagicMock())
    monkeypatch.setattr(mod, "exigir_local", lambda *a, **k: None)
    restantes = iter([10, 10, 0])
    monkeypatch.setattr(mod, "_pendentes", lambda url: next(restantes))
    monkeypatch.setattr(update_cvm_fulltext, "run", lambda: {"status": status})
    for chave in ("SUPABASE_DB_URL_B3", "CVM_FULLTEXT_MAX", "CVM_FULLTEXT_DELAY"):
        monkeypatch.delenv(chave, raising=False)
    monkeypatch.setattr(sys, "argv", ["drenar", "--ciclos", "2"])
    assert mod.main() == esperado


@pytest.mark.parametrize("status,esperado", [("partial", 1), ("completed", 0)])
def test_carga_estruturada_com_erro_sai_com_erro(monkeypatch, status, esperado):
    import run_market_ingest as cli
    from data_pipeline.market import fii_cvm_structured

    local = "postgresql://postgres:x@127.0.0.1:5433/postgres"
    for chave in ("SUPABASE_UNIFICADO_URL", "DATABASE_URL", "SUPABASE_DB_URL"):
        monkeypatch.setenv(chave, local)
    monkeypatch.setattr(cli, "_point_to_warehouse", lambda: True)
    monkeypatch.setattr(fii_cvm_structured, "ingest_cvm_structured",
                        lambda years: {"status": status, "errors": []})
    monkeypatch.setattr(sys, "argv", ["run_market_ingest.py", "fiis-cvm-structured",
                                      "--warehouse", "--years", "2"])
    assert cli.main() == esperado


def test_texto_com_nul_vira_gravavel():
    """O PostgreSQL recusa NUL; o documento ficava preso no topo da fila."""
    import inspect

    from data_pipeline.jobs import update_cvm_fulltext as job

    assert job._texto_gravavel("a\x00b\x00") == "ab"
    assert job._texto_gravavel(None) == ""
    assert "_texto_gravavel(ipe.extract_text(content))" in inspect.getsource(job.run)
