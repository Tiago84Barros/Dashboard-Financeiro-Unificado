"""Leva ao repositório os artefatos que a publicação reescreve.

Por que isto existe
-------------------
`data/public/fii_selection_snapshot_v2.json.gz` é o fallback offline que o app
publicado lê quando o Supabase não responde (`core.market_read`). Ele é
reescrito a cada republicação da vitrine e **não** pode ir para o `.gitignore`:
o app lê o arquivo que está no repositório, e a Streamlit Cloud publica da
`main`. O resultado é que a rotina noturna sujava a árvore de trabalho todo dia
e alguém tinha de commitar à mão -- foi o que aconteceu em 31/08/2026
(`3cbf63c chore(fii): republicacao diaria da vitrine para ela nao vencer de
novo`), depois de a vitrine vencer e a tela reprovar os 394 fundos como se
fossem ruins.

Não existe o caso "só o carimbo mudou, não vale commitar". Medido em 02/09/2026
comparando o artefato em disco com o da `HEAD`: as 394 linhas mudam de
`as_of_date`, e `as_of_date` é exatamente o campo que o portão de validade do
leitor consulta (`_FII_SNAPSHOT_HARD_MAX_AGE_DAYS`). Republicar sem commitar
mantém no repositório um artefato que vai vencer.

O que este módulo NÃO faz, de propósito
---------------------------------------
* **Não usa `git add -A` nem varre diretório.** Commita a lista declarada pelo
  alvo em `core.publicacao_agenda`, e nada além dela. `data/public/` também
  guarda 25 MB de parquets do corpus RAG; uma varredura por diretório os levaria
  junto no dia em que o RAG fosse reconstruído.
* **Não usa force em hipótese alguma**, nem tenta rebase para "resolver" uma
  rejeição. Push rejeitado significa que a `main` remota andou; a decisão do que
  fazer com isso é de quem está trabalhando, não da rotina noturna. O commit
  fica local e o próximo empurrão leva os dois.
* **Não commita fora da `main`.** Se alguém está numa branch de trabalho quando
  a rotina dispara, o artefato iria para a branch errada -- e a Streamlit Cloud,
  que publica da `main`, continuaria com o arquivo velho sem ninguém notar.
* **Não falha calado.** Toda recusa volta com motivo, e o orquestrador a leva
  para a notificação. Um artefato que parou de ser commitado tem de aparecer.

Sem Streamlit, sem banco, sem rede própria -- só `git` no diretório que recebe.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

#: Push que pende esperando senha trava a rotina inteira. Melhor falhar rápido
#: e avisar do que segurar a fila até o teto de 90 min do orquestrador.
TIMEOUT_GIT = 180
RAMO_PUBLICADO = "main"


@dataclass
class ResultadoPublicacao:
    """O que aconteceu, em termos que a notificação possa repetir."""

    commitou: bool = False
    empurrou: bool = False
    arquivos: tuple[str, ...] = ()
    motivo: str = ""
    erros: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.erros

    def resumo(self) -> str:
        if self.erros:
            return "; ".join(self.erros)
        if not self.commitou:
            return self.motivo or "nada a commitar"
        alvo = ", ".join(self.arquivos)
        estado = "empurrado" if self.empurrou else "NÃO empurrado"
        return f"{alvo}: commitado e {estado}"


def _git(raiz: Path, *argumentos: str) -> subprocess.CompletedProcess:
    ambiente = dict(os.environ)
    # Sem isto, um push sem credencial guardada abre prompt e pendura o processo
    # até o timeout do orquestrador, sem dizer o motivo.
    ambiente["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(["git", *argumentos], cwd=str(raiz), env=ambiente,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=TIMEOUT_GIT, check=False)


def _ultima_linha(proc: subprocess.CompletedProcess) -> str:
    texto = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip()
    linhas = [linha for linha in texto.splitlines() if linha.strip()]
    return linhas[-1].strip() if linhas else ""


def ramo_atual(raiz: Path) -> str:
    proc = _git(raiz, "rev-parse", "--abbrev-ref", "HEAD")
    return (proc.stdout or "").strip() if proc.returncode == 0 else ""


def operacao_em_curso(raiz: Path) -> str:
    """Merge ou rebase pela metade. Commitar por cima disso confunde o estado."""
    git_dir = raiz / ".git"
    for marcador, nome in (("MERGE_HEAD", "merge"),
                           ("rebase-merge", "rebase"),
                           ("rebase-apply", "rebase"),
                           ("CHERRY_PICK_HEAD", "cherry-pick")):
        if (git_dir / marcador).exists():
            return nome
    return ""


def artefatos_mudados(raiz: Path, caminhos: list[str]) -> list[str]:
    """Quais dos caminhos declarados diferem do que está commitado.

    Cobre os dois casos: arquivo rastreado que mudou (`diff HEAD`) e artefato
    que ainda não existe no repositório (`ls-files --others`). Um artefato novo
    que nunca fosse commitado seria a falha silenciosa desta rotina.
    """
    mudados: set[str] = set()
    proc = _git(raiz, "diff", "--name-only", "HEAD", "--", *caminhos)
    if proc.returncode == 0:
        mudados.update(linha.strip() for linha in (proc.stdout or "").splitlines()
                       if linha.strip())
    proc = _git(raiz, "ls-files", "--others", "--exclude-standard", "--", *caminhos)
    if proc.returncode == 0:
        mudados.update(linha.strip() for linha in (proc.stdout or "").splitlines()
                       if linha.strip())
    return sorted(mudados)


def publicar_artefatos(raiz: Path, caminhos, mensagem: str,
                       *, empurrar: bool = True) -> ResultadoPublicacao:
    """Commita os caminhos declarados e empurra. Recusa tudo que não for isso."""
    resultado = ResultadoPublicacao()
    declarados = [str(caminho) for caminho in caminhos]
    if not declarados:
        resultado.motivo = "alvo não declara artefato"
        return resultado

    ramo = ramo_atual(raiz)
    if ramo != RAMO_PUBLICADO:
        # Não é acidente: é a rotina se recusando a levar o artefato para o
        # lugar errado. Mas precisa aparecer, porque enquanto durar a branch a
        # `main` fica com o artefato velho.
        resultado.motivo = (f"ramo atual é {ramo or 'desconhecido'}, não "
                            f"{RAMO_PUBLICADO}; artefato não commitado")
        resultado.erros.append(resultado.motivo)
        return resultado

    em_curso = operacao_em_curso(raiz)
    if em_curso:
        resultado.motivo = f"{em_curso} em andamento; artefato não commitado"
        resultado.erros.append(resultado.motivo)
        return resultado

    mudados = artefatos_mudados(raiz, declarados)
    if not mudados:
        resultado.motivo = "artefato idêntico ao commitado"
        return resultado

    proc = _git(raiz, "add", "--", *mudados)
    if proc.returncode != 0:
        resultado.erros.append(f"git add falhou: {_ultima_linha(proc)}")
        return resultado

    # Pathspec explícito: commita SÓ estes caminhos, mesmo que haja outra coisa
    # no índice. A rotina não pode varrer para dentro do commit o que a pessoa
    # estava preparando.
    proc = _git(raiz, "commit", "-m", mensagem, "--", *mudados)
    if proc.returncode != 0:
        resultado.erros.append(f"git commit falhou: {_ultima_linha(proc)}")
        return resultado
    resultado.commitou = True
    resultado.arquivos = tuple(mudados)

    if not empurrar:
        return resultado

    proc = _git(raiz, "push", "origin", RAMO_PUBLICADO)
    if proc.returncode != 0:
        # Sem force e sem rebase automático: ver o cabeçalho do módulo.
        resultado.erros.append(
            f"commitado localmente, mas o push falhou: {_ultima_linha(proc)}. "
            "O commit está na main local; nada foi forçado.")
        return resultado
    resultado.empurrou = True
    return resultado
