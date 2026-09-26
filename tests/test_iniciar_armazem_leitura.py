"""Supervisor do serviço só-leitura do armazém (tarefa de logon).

O que se prende:

* o ``.env`` explícito entra no ambiente sem sobrescrever o que já está lá;
* a atualização só mexe em worktree destacado e limpo;
* o supervisor levanta o servidor que cai e para na saída de configuração.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import time

import pytest

_RAIZ = pathlib.Path(__file__).resolve().parents[1]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from scripts import iniciar_armazem_leitura as sup  # noqa: E402


def test_env_explicito_nao_sobrescreve(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("DFU_TESTE_A=do_arquivo\nDFU_TESTE_B=do_arquivo\n", encoding="utf-8")
    monkeypatch.setenv("DFU_TESTE_A", "do_ambiente")
    monkeypatch.delenv("DFU_TESTE_B", raising=False)

    assert sup.carregar_env(env) == 1
    assert os.environ["DFU_TESTE_A"] == "do_ambiente"
    assert os.environ["DFU_TESTE_B"] == "do_arquivo"
    monkeypatch.delenv("DFU_TESTE_B")


def _git_falso(respostas: dict[str, tuple[int, str]], chamadas: list):
    def _git(*args, pasta):
        chamadas.append(args[0])
        codigo, saida = respostas.get(args[0], (0, ""))
        return subprocess.CompletedProcess(args, codigo, stdout=saida, stderr="")
    return _git


def test_atualizacao_nao_mexe_em_branch(tmp_path):
    chamadas: list = []
    msg = sup.atualizar_para_main(tmp_path, git=_git_falso(
        {"symbolic-ref": (0, "refs/heads/trabalho")}, chamadas))
    assert "branch" in msg
    assert "checkout" not in chamadas and "fetch" not in chamadas


def test_atualizacao_nao_mexe_em_pasta_suja(tmp_path):
    chamadas: list = []
    msg = sup.atualizar_para_main(tmp_path, git=_git_falso(
        {"symbolic-ref": (1, ""), "status": (0, " M core/x.py\n")}, chamadas))
    assert "alterações" in msg
    assert "checkout" not in chamadas


def test_atualizacao_em_destacado_limpo_vai_para_main(tmp_path):
    chamadas: list = []
    msg = sup.atualizar_para_main(tmp_path, git=_git_falso(
        {"symbolic-ref": (1, ""), "status": (0, "")}, chamadas))
    assert chamadas.count("fetch") == 1 and chamadas.count("checkout") == 1
    assert "origin/main" in msg


def test_atualizacao_nao_recua_pasta_a_frente_da_main(tmp_path):
    chamadas: list = []
    msg = sup.atualizar_para_main(tmp_path, git=_git_falso(
        {"symbolic-ref": (1, ""), "status": (0, ""), "merge-base": (1, "")}, chamadas))
    assert "origin/main ainda não tem" in msg
    assert "checkout" not in chamadas


def test_fetch_falho_nao_impede_subir(tmp_path):
    chamadas: list = []
    msg = sup.atualizar_para_main(tmp_path, git=_git_falso(
        {"symbolic-ref": (1, ""), "fetch": (1, "")}, chamadas))
    assert "pulada" in msg and "checkout" not in chamadas


def _executar(codigos: list[int], feitos: list):
    def _run(comando, **kw):
        feitos.append(comando)
        return subprocess.CompletedProcess(comando, codigos[len(feitos) - 1])
    return _run


def test_supervisor_levanta_quem_cai(tmp_path):
    feitos: list = []
    esperas: list = []
    codigo = sup.supervisionar(["srv"], tmp_path / "a.log",
                               executar=_executar([1, 1, 0], feitos),
                               dormir=esperas.append, max_execucoes=3)
    assert len(feitos) == 3 and codigo == 0
    assert esperas == [sup.ESPERA_REINICIO_S] * 2
    assert "reinício" in (tmp_path / "a.log").read_text(encoding="utf-8")


def test_supervisor_para_em_erro_de_configuracao(tmp_path):
    feitos: list = []
    codigo = sup.supervisionar(["srv"], tmp_path / "a.log",
                               executar=_executar([sup.SAIDA_CONFIGURACAO], feitos),
                               dormir=lambda s: None)
    assert codigo == sup.SAIDA_CONFIGURACAO and len(feitos) == 1


def test_filho_escreve_o_log_em_utf8(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    visto: dict = {}

    def _sup(comando, log, **kw):
        visto["enc"] = os.environ.get("PYTHONIOENCODING")
        return 0

    monkeypatch.setattr(sup, "supervisionar", _sup)
    assert sup.main(["--env", str(env), "--log", str(tmp_path / "a.log")]) == 0
    assert visto["enc"] == "utf-8"


def _vivo(pid: int) -> bool:
    import ctypes

    k32 = ctypes.WinDLL("kernel32")
    k32.OpenProcess.restype = ctypes.c_void_p
    h = k32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not h:
        return False
    codigo = ctypes.c_ulong()
    k32.GetExitCodeProcess(ctypes.c_void_p(h), ctypes.byref(codigo))
    k32.CloseHandle(ctypes.c_void_p(h))
    return codigo.value == 259  # STILL_ACTIVE


@pytest.mark.skipif(os.name != "nt", reason="Job Object é do Windows")
def test_filho_morre_quando_o_supervisor_e_morto(tmp_path):
    """O cenário do Stop-ScheduledTask: TerminateProcess no supervisor."""
    pidfile = tmp_path / "filho.pid"
    filho = ("import os,time,pathlib;"
             f"pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid()));"
             "time.sleep(120)")
    supervisor = (
        f"import sys; sys.path.insert(0, {str(_RAIZ)!r});"
        "from scripts import iniciar_armazem_leitura as s;"
        "job = s._job_que_mata_ao_fechar(); assert job is not None;"
        f"s._executar_no_job(job)([sys.executable, '-c', {filho!r}])")
    proc = subprocess.Popen([sys.executable, "-c", supervisor])
    try:
        for _ in range(100):
            if pidfile.exists() and pidfile.read_text():
                break
            time.sleep(0.1)
        pid = int(pidfile.read_text())
        assert _vivo(pid)
        proc.kill()  # TerminateProcess: nenhum finally roda
        proc.wait(10)
        for _ in range(50):
            if not _vivo(pid):
                break
            time.sleep(0.1)
        assert not _vivo(pid), "servidor filho ficou órfão"
    finally:
        proc.kill()


def test_env_ausente_nao_sobe(tmp_path):
    assert sup.main(["--env", str(tmp_path / "nao.env"),
                     "--log", str(tmp_path / "a.log")]) == sup.SAIDA_CONFIGURACAO
