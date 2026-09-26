"""Mantém o serviço só-leitura do armazém de pé, para a tarefa de logon.

``servir_armazem_leitura.py`` sobe o servidor e para quando ele para. Para o
túnel ser permanente, alguém precisa subi-lo sozinho ao ligar o PC e levantá-lo
de novo se cair. É isto aqui, chamado pela tarefa ``DFU - Armazem leitura``
(registrada por ``scripts/registrar_armazem_leitura.ps1``).

Três decisões:

* **Não espera o Docker.** O servidor sobe sem banco e responde 503 em
  ``/saude`` até o armazém aparecer; a engine tem ``pool_pre_ping``, então a
  primeira consulta depois que o container sobe já funciona. Esperar aqui só
  atrasaria o 503 nomeado que o app transforma em aviso.
* **O ``.env`` vem de fora.** A pasta do serviço é um worktree separado (fica
  sempre na ``main``, qualquer que seja a branch da árvore de trabalho), e
  ``load_dotenv()`` só procura subindo diretórios -- não acharia o ``.env`` da
  árvore principal. Ele é carregado aqui pelo caminho explícito, sem
  sobrescrever o que já estiver no ambiente, e o filho herda. Um ``.env`` só,
  um token só: trocar o token continua sendo editar um arquivo.
* **Atualiza só worktree destacado e limpo.** ``--atualizar`` leva a pasta para
  ``origin/main`` antes de subir, para uma rota nova chegar ao serviço sem
  ninguém lembrar de reiniciá-lo. Numa pasta com branch ou com alteração, não
  toca em nada: isso protege a árvore de trabalho de quem rodar o script nela.

Saída 2 do servidor é configuração (token ausente ou curto): reiniciar não
resolve, então o supervisor para e deixa a tarefa marcada como falha.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVIDOR = ROOT / "scripts" / "servir_armazem_leitura.py"
LOG_PADRAO = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DFU" / "armazem_leitura.log"
LOG_MAX_BYTES = 5_000_000
#: Saída do servidor que significa configuração errada, não queda.
SAIDA_CONFIGURACAO = 2
ESPERA_REINICIO_S = 30


def carregar_env(caminho: Path) -> int:
    """Põe no ambiente as chaves do ``.env`` que ainda não estão lá.

    Devolve quantas chaves entraram. Nunca imprime valores.
    """
    from dotenv import dotenv_values

    novas = 0
    for chave, valor in dotenv_values(caminho).items():
        if valor is not None and chave not in os.environ:
            os.environ[chave] = valor
            novas += 1
    return novas


def _git(*args: str, pasta: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(pasta), *args],
                          capture_output=True, text=True, timeout=120)


def atualizar_para_main(pasta: Path, *, git=_git) -> str:
    """Leva ``pasta`` para ``origin/main`` se ela for worktree destacado e limpo.

    Devolve uma frase para o log. Falha de rede não impede o serviço de subir
    com o código que já está no disco.
    """
    if git("symbolic-ref", "-q", "HEAD", pasta=pasta).returncode == 0:
        return "atualização pulada: a pasta está numa branch, não destacada"
    if git("status", "--porcelain", pasta=pasta).stdout.strip():
        return "atualização pulada: a pasta tem alterações locais"
    busca = git("fetch", "-q", "origin", "main", pasta=pasta)
    if busca.returncode != 0:
        return f"atualização pulada: git fetch falhou ({busca.stderr.strip()[:200]})"
    # Só avança. Uma pasta à frente da main (ex.: no commit de um PR ainda não
    # mergeado) voltaria para um código sem este supervisor, e o próximo logon
    # não acharia o script.
    if git("merge-base", "--is-ancestor", "HEAD", "origin/main", pasta=pasta).returncode != 0:
        return "atualização pulada: a pasta tem commits que origin/main ainda não tem"
    antes = git("rev-parse", "--short", "HEAD", pasta=pasta).stdout.strip()
    troca = git("checkout", "-q", "--detach", "origin/main", pasta=pasta)
    if troca.returncode != 0:
        return f"atualização falhou: {troca.stderr.strip()[:200]}"
    depois = git("rev-parse", "--short", "HEAD", pasta=pasta).stdout.strip()
    return (f"código já em origin/main ({depois})" if antes == depois
            else f"código atualizado de {antes} para {depois}")


def _log(arquivo: Path, mensagem: str) -> None:
    with arquivo.open("a", encoding="utf-8") as fh:
        fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} [supervisor] {mensagem}\n")


def _girar(arquivo: Path) -> None:
    if arquivo.exists() and arquivo.stat().st_size > LOG_MAX_BYTES:
        anterior = arquivo.with_suffix(".log.1")
        anterior.unlink(missing_ok=True)
        arquivo.rename(anterior)


def _job_que_mata_ao_fechar():
    """Job do Windows cujos processos morrem quando o último handle fecha.

    ``Stop-ScheduledTask`` encerra o supervisor com ``TerminateProcess``: não há
    ``finally`` que rode. Sem o job, o servidor filho ficava órfão segurando a
    porta 8787 com o código antigo, e o supervisor seguinte entrava em laço de
    reinício sem conseguir escutar. O handle do job só existe neste processo;
    quando ele morre, por qualquer via, o Windows fecha o handle e leva o filho.

    Devolve ``None`` fora do Windows ou se a API recusar -- aí vale o
    comportamento anterior, sem derrubar o serviço.
    """
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class _Basica(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _Io(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _Estendida(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _Basica),
                    ("IoInfo", _Io),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                            ctypes.c_void_p, wintypes.DWORD]
    job = k32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = _Estendida()
    info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
    # 9 = JobObjectExtendedLimitInformation
    if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
        return None
    return job


def _prender_ao_job(job, processo: subprocess.Popen) -> bool:
    if job is None:
        return False
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    return bool(k32.AssignProcessToJobObject(job, int(processo._handle)))


def _executar_no_job(job):
    def _executar(comando, **kw) -> subprocess.CompletedProcess:
        processo = subprocess.Popen(comando, **kw)
        _prender_ao_job(job, processo)
        return subprocess.CompletedProcess(comando, processo.wait())
    return _executar


def supervisionar(comando: list[str], log: Path, *, executar=subprocess.run,
                  dormir=time.sleep, max_execucoes: int | None = None) -> int:
    """Roda ``comando`` e o levanta de novo quando cai.

    Para só na saída de configuração (ou em ``max_execucoes``, para teste).
    """
    execucoes = 0
    while True:
        _girar(log)
        with log.open("a", encoding="utf-8") as saida:
            codigo = executar(comando, stdout=saida, stderr=subprocess.STDOUT).returncode
        execucoes += 1
        if codigo == SAIDA_CONFIGURACAO:
            _log(log, "servidor recusou a configuração (token); não vou reiniciar")
            return codigo
        if max_execucoes is not None and execucoes >= max_execucoes:
            return codigo
        _log(log, f"servidor saiu com código {codigo}; reinício em {ESPERA_REINICIO_S}s")
        dormir(ESPERA_REINICIO_S)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--env", type=Path, default=ROOT / ".env",
                   help="Arquivo .env com ARMAZEM_API_TOKEN e as URLs do armazém.")
    p.add_argument("--porta", type=int, default=8787)
    p.add_argument("--log", type=Path, default=LOG_PADRAO)
    p.add_argument("--atualizar", action="store_true",
                   help="Levar a pasta para origin/main antes de subir.")
    args = p.parse_args(argv)

    args.log.parent.mkdir(parents=True, exist_ok=True)
    _log(args.log, f"iniciando em {ROOT}")
    if args.atualizar:
        _log(args.log, atualizar_para_main(ROOT))
    if not args.env.is_file():
        _log(args.log, f"arquivo de ambiente não encontrado: {args.env}")
        return SAIDA_CONFIGURACAO
    _log(args.log, f"{carregar_env(args.env)} chave(s) lidas de {args.env}")

    # O log é UTF-8; sem isto o filho escreve na página de código do Windows
    # (cp1252) e os acentos viram lixo no arquivo.
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    # -u: sem buffer, para o log mostrar a requisição quando ela acontece.
    comando = [sys.executable, "-u", str(SERVIDOR), "--porta", str(args.porta)]
    job = _job_que_mata_ao_fechar()
    if job is None and os.name == "nt":
        _log(args.log, "job do Windows indisponível: parar a tarefa pode deixar o servidor órfão")
    return supervisionar(comando, args.log, executar=_executar_no_job(job))


if __name__ == "__main__":
    sys.exit(main())
