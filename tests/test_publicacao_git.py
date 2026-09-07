"""O commit automático do artefato, contra repositórios git de verdade.

Não há mock de `git` aqui de propósito. O que este módulo promete -- recusar
branch errada, não varrer o índice alheio, não forçar push -- só é verificável
contra o git de verdade, porque é o comportamento do git que está sendo
restringido. Um dublê responderia o que eu tivesse programado nele.

Cada teste monta um repositório descartável com um remoto `--bare` local, então
`git push` acontece mesmo, sem rede e sem tocar o repositório do projeto.
"""
from __future__ import annotations

import subprocess

import pytest

from core.publicacao_git import (
    artefatos_mudados,
    operacao_em_curso,
    publicar_artefatos,
    ramo_atual,
)

ARTEFATO = "data/public/vitrine.json.gz"
MENSAGEM = "chore(fii): republica vitrine"


def _git(raiz, *argumentos):
    proc = subprocess.run(["git", *argumentos], cwd=str(raiz), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", check=False)
    assert proc.returncode == 0, f"git {' '.join(argumentos)}: {proc.stderr}"
    return proc.stdout


def _escrever(raiz, caminho, conteudo):
    destino = raiz / caminho
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(conteudo, encoding="utf-8")


@pytest.fixture
def repositorio(tmp_path):
    """Clone de trabalho na `main`, com remoto `origin` que aceita push."""
    if subprocess.run(["git", "--version"], capture_output=True,
                      check=False).returncode != 0:
        pytest.skip("git não disponível")

    remoto = tmp_path / "remoto.git"
    _git(tmp_path, "init", "--bare", "--initial-branch=main", str(remoto))

    raiz = tmp_path / "trabalho"
    raiz.mkdir()
    _git(raiz, "init", "--initial-branch=main")
    _git(raiz, "config", "user.email", "teste@exemplo.invalid")
    _git(raiz, "config", "user.name", "Teste")
    _git(raiz, "remote", "add", "origin", str(remoto))
    _escrever(raiz, "README.md", "base\n")
    _git(raiz, "add", "README.md")
    _git(raiz, "commit", "-m", "base")
    _git(raiz, "push", "-u", "origin", "main")
    return raiz


def _commits_do_remoto(raiz):
    return _git(raiz, "log", "--format=%s", "origin/main").splitlines()


def test_artefato_novo_e_commitado_e_empurrado(repositorio):
    """O caso que a rotina existe para cobrir: arquivo que ainda não é rastreado.

    Se `artefatos_mudados` olhasse só `git diff HEAD`, um artefato inédito não
    apareceria como mudado e a rotina o pularia calada para sempre.
    """
    _escrever(repositorio, ARTEFATO, "primeira versao")

    resultado = publicar_artefatos(repositorio, [ARTEFATO], MENSAGEM)

    assert resultado.ok and resultado.commitou and resultado.empurrou
    assert resultado.arquivos == (ARTEFATO,)
    _git(repositorio, "fetch", "origin")
    assert _commits_do_remoto(repositorio)[0] == MENSAGEM
    assert (_git(repositorio, "show", f"origin/main:{ARTEFATO}").strip()
            == "primeira versao")


def test_republicacao_leva_o_conteudo_novo(repositorio):
    _escrever(repositorio, ARTEFATO, "as_of_date=2026-09-01")
    publicar_artefatos(repositorio, [ARTEFATO], MENSAGEM)
    _escrever(repositorio, ARTEFATO, "as_of_date=2026-09-02")

    resultado = publicar_artefatos(repositorio, [ARTEFATO], MENSAGEM + " 2")

    assert resultado.ok and resultado.empurrou
    _git(repositorio, "fetch", "origin")
    assert (_git(repositorio, "show", f"origin/main:{ARTEFATO}").strip()
            == "as_of_date=2026-09-02")


def test_artefato_identico_nao_gera_commit_vazio(repositorio):
    """Publicar duas vezes o mesmo conteúdo não pode encher a `main` de ruído."""
    _escrever(repositorio, ARTEFATO, "igual")
    publicar_artefatos(repositorio, [ARTEFATO], MENSAGEM)
    antes = _commits_do_remoto(repositorio)

    resultado = publicar_artefatos(repositorio, [ARTEFATO], MENSAGEM)

    assert resultado.ok and not resultado.commitou
    assert "idêntico" in resultado.motivo
    _git(repositorio, "fetch", "origin")
    assert _commits_do_remoto(repositorio) == antes


def test_fora_da_main_recusa_e_avisa(repositorio):
    """A Streamlit Cloud publica da `main`.

    Commitar o artefato numa branch de trabalho o esconderia: a `main` seguiria
    com o arquivo velho, e ninguém saberia, porque o commit teria dado certo.
    """
    _git(repositorio, "checkout", "-b", "trabalho")
    _escrever(repositorio, ARTEFATO, "conteudo")

    resultado = publicar_artefatos(repositorio, [ARTEFATO], MENSAGEM)

    assert not resultado.ok and not resultado.commitou
    assert "trabalho" in resultado.motivo and "main" in resultado.motivo
    # E o artefato continua ali, intocado, para ser commitado à mão.
    assert (repositorio / ARTEFATO).exists()
    assert artefatos_mudados(repositorio, [ARTEFATO]) == [ARTEFATO]


def test_merge_pela_metade_recusa(repositorio, tmp_path):
    _escrever(repositorio, "conflito.txt", "versao main\n")
    _git(repositorio, "add", "conflito.txt")
    _git(repositorio, "commit", "-m", "main mexe")
    _git(repositorio, "checkout", "-b", "outro", "HEAD~1")
    _escrever(repositorio, "conflito.txt", "versao outro\n")
    _git(repositorio, "add", "conflito.txt")
    _git(repositorio, "commit", "-m", "outro mexe")
    _git(repositorio, "checkout", "main")
    subprocess.run(["git", "merge", "outro"], cwd=str(repositorio),
                   capture_output=True, check=False)  # conflita de propósito
    assert operacao_em_curso(repositorio) == "merge"
    _escrever(repositorio, ARTEFATO, "conteudo")

    resultado = publicar_artefatos(repositorio, [ARTEFATO], MENSAGEM)

    assert not resultado.ok and not resultado.commitou
    assert "merge" in resultado.motivo


def test_nao_leva_junto_o_que_estava_no_indice(repositorio):
    """A rotina roda de madrugada, sobre a árvore de quem estava trabalhando.

    Se ela usasse `git add -A` ou `git commit -a`, o commit automático levaria
    para a `main` o que a pessoa tinha preparado e ainda não commitou.
    """
    _escrever(repositorio, "meu_trabalho.py", "print('em andamento')\n")
    _git(repositorio, "add", "meu_trabalho.py")
    _escrever(repositorio, "nao_rastreado.txt", "rascunho\n")
    _escrever(repositorio, ARTEFATO, "conteudo")

    resultado = publicar_artefatos(repositorio, [ARTEFATO], MENSAGEM)

    assert resultado.ok and resultado.arquivos == (ARTEFATO,)
    arquivos = _git(repositorio, "show", "--name-only", "--format=", "HEAD")
    assert arquivos.split() == [ARTEFATO]
    # O trabalho da pessoa continua onde estava: preparado, não commitado.
    assert "meu_trabalho.py" in _git(repositorio, "diff", "--cached", "--name-only")
    assert (repositorio / "nao_rastreado.txt").exists()


def test_push_rejeitado_avisa_sem_forcar(repositorio, tmp_path):
    """`main` remota andou. Forçar aqui apagaria o commit de outra pessoa."""
    outro = tmp_path / "outro"
    _git(tmp_path, "clone", str(tmp_path / "remoto.git"), str(outro))
    _git(outro, "config", "user.email", "outro@exemplo.invalid")
    _git(outro, "config", "user.name", "Outro")
    _escrever(outro, "de_outra_pessoa.txt", "commit que nao pode sumir\n")
    _git(outro, "add", "de_outra_pessoa.txt")
    _git(outro, "commit", "-m", "trabalho de outra pessoa")
    _git(outro, "push", "origin", "main")

    _escrever(repositorio, ARTEFATO, "conteudo")
    resultado = publicar_artefatos(repositorio, [ARTEFATO], MENSAGEM)

    assert resultado.commitou and not resultado.empurrou
    assert not resultado.ok
    assert "push falhou" in resultado.resumo()
    # O commit alheio continua sendo a ponta do remoto.
    _git(repositorio, "fetch", "origin")
    assert _commits_do_remoto(repositorio)[0] == "trabalho de outra pessoa"


def test_alvo_sem_artefato_nao_toca_no_git(repositorio):
    """A maioria dos alvos publica só no Supabase; para eles isto é um no-op."""
    resultado = publicar_artefatos(repositorio, [], MENSAGEM)

    assert resultado.ok and not resultado.commitou
    assert resultado.motivo == "alvo não declara artefato"


def test_sem_empurrar_commita_e_para(repositorio):
    _escrever(repositorio, ARTEFATO, "conteudo")

    resultado = publicar_artefatos(repositorio, [ARTEFATO], MENSAGEM,
                                   empurrar=False)

    assert resultado.ok and resultado.commitou and not resultado.empurrou
    assert "NÃO empurrado" in resultado.resumo()
    assert ramo_atual(repositorio) == "main"
