"""Leitura da senha do armazém local: um leitor só, por ``docker inspect``.

Em 13/11 e 14/11/2026 o ``docker compose exec ... printenv`` dos
``run_*_ingest.py`` falhou com o container saudável, e a ingestão caiu em
"senha indisponível" enquanto o ``docker inspect`` da mesma rotina respondia.
Nada aqui chama o Docker: o ``subprocess.run`` é trocado por dublê. O que se
prende é que a senha nunca aparece numa mensagem de erro, que a falha diz o
motivo e que todos os caminhos leem pela mesma função.
"""
from __future__ import annotations

import json
import logging
import subprocess
from types import SimpleNamespace

import pytest

import run_market_ingest as mkt
import run_us_ingest as us
from scripts import backfill_fii_documents_local as backfill
from scripts import construir_memoria_mercado as memoria
from scripts import publish_fii_selection_from_local as pub

SENHA = "s3gr3do com espaço&/@"
_URLS = ("SUPABASE_UNIFICADO_URL", "DATABASE_URL", "SUPABASE_DB_URL")


def _inspect(monkeypatch, *, rc=0, stdout=None, stderr="", erro=None):
    chamadas = []

    def _run(cmd, **kw):
        chamadas.append((list(cmd), kw))
        if erro is not None:
            raise erro
        saida = stdout if stdout is not None else json.dumps(
            ["PATH=/usr/bin", f"POSTGRES_PASSWORD={SENHA}"])
        return SimpleNamespace(returncode=rc, stdout=saida, stderr=stderr)

    monkeypatch.setattr(pub.subprocess, "run", _run)
    return chamadas


@pytest.fixture
def sem_urls(monkeypatch):
    # setenv antes de delenv: o monkeypatch só restaura a chave que registrou,
    # e as funções testadas gravam direto em os.environ.
    for chave in _URLS + ("WAREHOUSE_DB_URL",):
        monkeypatch.setenv(chave, "x")
        monkeypatch.delenv(chave)


def test_le_a_senha_do_inspect_com_prazo(monkeypatch):
    chamadas = _inspect(monkeypatch)
    assert pub.senha_do_warehouse() == SENHA
    cmd, kw = chamadas[0]
    assert cmd[:3] == ["docker", "inspect", "dfu_warehouse"]
    assert "compose" not in cmd
    assert kw["timeout"] == pub.PRAZO_INSPECAO and kw["check"] is False


@pytest.mark.parametrize("caso, kwargs, trecho", [
    ("prazo", {"erro": subprocess.TimeoutExpired(["docker"], 60)}, "sem resposta em 60s"),
    ("sem docker", {"erro": FileNotFoundError("docker")}, "docker indisponível"),
    ("rc", {"rc": 1, "stderr": "Error: No such object: dfu_warehouse\nmais"},
     "saiu com 1: Error: No such object"),
    ("json", {"stdout": "{quebrado " + SENHA}, "ilegível"),
    ("sem entrada", {"stdout": json.dumps(["PATH=/usr/bin"])}, "indisponivel"),
    ("vazia", {"stdout": json.dumps(["POSTGRES_PASSWORD="])}, "indisponivel"),
    ("nulo", {"stdout": "null"}, "indisponivel"),
])
def test_falha_diz_o_motivo_e_nunca_a_senha(monkeypatch, caso, kwargs, trecho):
    _inspect(monkeypatch, **kwargs)
    with pytest.raises(RuntimeError) as exc:
        pub.senha_do_warehouse()
    assert trecho in str(exc.value)
    assert SENHA not in str(exc.value) and exc.value.__cause__ is None


def test_rc_nao_zero_nao_carrega_o_stdout(monkeypatch):
    _inspect(monkeypatch, rc=1, stdout=json.dumps([f"POSTGRES_PASSWORD={SENHA}"]),
             stderr="")
    with pytest.raises(RuntimeError) as exc:
        pub.senha_do_warehouse()
    assert SENHA not in str(exc.value)


@pytest.mark.parametrize("modulo, chaves", [
    (mkt, _URLS), (us, ("SUPABASE_UNIFICADO_URL",))])
def test_ingestao_usa_o_inspect_e_nao_o_compose(monkeypatch, sem_urls, modulo, chaves):
    chamadas = _inspect(monkeypatch)
    assert modulo._point_to_warehouse() is True
    assert all("compose" not in cmd for cmd, _ in chamadas)
    for chave in chaves:
        url = modulo.os.environ[chave]
        assert url.startswith("postgresql://postgres:")
        assert url.endswith("@127.0.0.1:5433/postgres")
        assert SENHA not in url  # vai codificada


@pytest.mark.parametrize("modulo", [mkt, us])
def test_ingestao_cai_no_env_e_loga_o_motivo(monkeypatch, sem_urls, tmp_path, caplog,
                                             modulo):
    _inspect(monkeypatch, erro=subprocess.TimeoutExpired(["docker"], 60))
    (tmp_path / "warehouse").mkdir()
    (tmp_path / "warehouse" / ".env").write_text("WAREHOUSE_PASSWORD=reserva\n",
                                                encoding="utf-8")
    monkeypatch.setattr(modulo, "_ROOT", tmp_path)
    with caplog.at_level(logging.WARNING):
        assert modulo._point_to_warehouse() is True
    assert ":reserva@127.0.0.1:5433" in modulo.os.environ["SUPABASE_UNIFICADO_URL"]
    assert "sem resposta em 60s" in caplog.text


@pytest.mark.parametrize("modulo", [mkt, us])
def test_ingestao_sem_nenhuma_fonte_nomeia_as_duas(monkeypatch, sem_urls, tmp_path,
                                                   caplog, modulo):
    _inspect(monkeypatch, rc=1, stderr="Cannot connect to the Docker daemon")
    monkeypatch.setattr(modulo, "_ROOT", tmp_path)
    with caplog.at_level(logging.WARNING):
        assert modulo._point_to_warehouse() is False
    assert "Cannot connect to the Docker daemon" in caplog.text
    assert "docker inspect e warehouse/.env" in caplog.text


def test_todos_os_caminhos_dao_a_mesma_url(monkeypatch, sem_urls):
    _inspect(monkeypatch)
    url = pub._warehouse_url()
    assert memoria.warehouse_url() == url
    assert backfill._configure_database() == url
    assert url.endswith("@localhost:5433/postgres") and SENHA not in url


def test_backfill_respeita_warehouse_db_url(monkeypatch, sem_urls):
    _inspect(monkeypatch, erro=AssertionError("não deveria inspecionar"))
    monkeypatch.setenv("WAREHOUSE_DB_URL", "postgresql://x@localhost:5433/y")
    assert backfill._configure_database() == "postgresql://x@localhost:5433/y"
