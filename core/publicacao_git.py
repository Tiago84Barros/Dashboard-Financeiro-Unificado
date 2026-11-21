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

A `main` local anda, mas só para a frente
-----------------------------------------
Todo trabalho entra na `origin/main` por PR, de outros worktrees; ninguém faz
`pull` no checkout onde a rotina roda. Sem atualizar, ele fica para trás, o
push do artefato passa a ser rejeitado todo dia e os publicadores rodam com o
código velho -- em 01/10/2026 o checkout estava 63 commits atrás, com cinco
republicações de 29 e 30/09 commitadas e nunca empurradas.
`atualizar_main` resolve isso antes de publicar, com ``merge --ff-only``: não
reescreve histórico, não cria commit de merge e não sobrescreve trabalho local
(o git recusa se o avanço pisaria num arquivo modificado). Divergência -- a
`main` local com commits que a remota não tem -- continua sendo de quem está
trabalhando: a função recusa e diz quantos commits de cada lado.

Sem a origin/main inteira no HEAD, a rotina não publica
-------------------------------------------------------
Até 28/10/2026 a recusa acima só ia para a notificação, e a rotina publicava
assim mesmo com o código que estivesse no disco. Em 26 e 27/10 o checkout
estava divergente e sem o PR #558, e os publicadores regravaram ~26,7 mil
linhas de `market.income_statements` com o LPA 1000x que aquele PR corrigia
(a limpeza foi o PR #590). Uma correção mergeada que a rotina não roda não
corrigiu nada; pior, ela desfaz a correção que outra pessoa já aplicou nos
dados.

Por isso `ResultadoAtualizacao.pode_publicar` só é verdadeiro quando a função
**comprovou** que o HEAD contém a origin/main recém-buscada. O padrão é falso:
qualquer caminho novo, ou esquecido, bloqueia em vez de liberar. Estar à
frente é normal -- são os commits de artefato que a própria rotina faz e
ainda não subiu -- e não bloqueia. O que bloqueia é faltar commit da origin:
divergência, fast-forward recusado, merge pela metade e fetch que não
completou. Sem rede não dá para saber se a origin andou; e os publicadores
precisariam da rede de qualquer jeito, para o Supabase e para as fontes.
Bloquear ali não custa uma vitrine que fosse sair, e o alvo segue vencido
para a execução seguinte.

Sem Streamlit, sem banco, sem rede própria -- só `git` no diretório que recebe.
"""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

#: Push que pende esperando senha trava a rotina inteira. Melhor falhar rápido
#: e avisar do que segurar a fila até o teto de 90 min do orquestrador.
TIMEOUT_GIT = 180
#: O push tem prazo próprio. Em 13/11/2026 o commit do corpus RAG (24 parquets,
#: ~147 MB) não subiu em `TIMEOUT_GIT`: upload grande é lento, não pendurado.
#: Prompt de senha não é o risco aqui -- `GIT_TERMINAL_PROMPT=0` o recusa na hora.
TIMEOUT_PUSH = 900
#: Código de saída de um comando que não respondeu no prazo (o do `timeout(1)`).
RC_TIMEOUT = 124
RAMO_PUBLICADO = "main"
#: Segundos entre tentativas de `git fetch`. Fetch que falha agora bloqueia a
#: rotina inteira, então um soluço de DNS na hora do disparo não pode custar a
#: noite; cinco minutos de espera cabem no prazo de 225 min da rotina.
PAUSAS_FETCH = (30, 90, 180)


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
        alvo = ", ".join(self.arquivos)
        if self.commitou:
            estado = "empurrado" if self.empurrou else "NÃO empurrado"
            texto = f"{alvo}: commitado e {estado}"
        elif self.empurrou:
            texto = f"{alvo}: commit anterior empurrado"
        else:
            return self.motivo or "nada a commitar"
        return f"{texto} ({self.motivo})" if self.motivo else texto


def _git(raiz: Path, *argumentos: str,
         timeout: int = TIMEOUT_GIT) -> subprocess.CompletedProcess:
    """Roda o git. Comando que não responde no prazo volta como falha
    (`RC_TIMEOUT`), não como exceção.

    Até 13/11/2026 a `TimeoutExpired` escapava: o push do corpus RAG estourou o
    prazo, a exceção derrubou a rotina antes da verificação e da notificação,
    e o estado já dizia "ok". Quem chama já trata código de saída não zero.
    """
    ambiente = dict(os.environ)
    # Sem isto, um push sem credencial guardada abre prompt e pendura o processo
    # até o timeout do orquestrador, sem dizer o motivo.
    ambiente["GIT_TERMINAL_PROMPT"] = "0"
    argv = ["git", *argumentos]
    try:
        return subprocess.run(argv, cwd=str(raiz), env=ambiente,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, RC_TIMEOUT, "",
                                           f"sem resposta em {timeout} s")


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


def artefatos_mudados(raiz: Path, caminhos: list[str]) -> list[str] | None:
    """Quais dos caminhos declarados diferem do que está commitado.

    Cobre os dois casos: arquivo rastreado que mudou (`diff HEAD`) e artefato
    que ainda não existe no repositório (`ls-files --others`). Um artefato novo
    que nunca fosse commitado seria a falha silenciosa desta rotina.

    ``None`` quando qualquer um dos dois comandos falha ou fica sem resposta.
    Lista vazia afirma "nada mudou"; um git que não respondeu não prova isso, e
    tratá-lo como lista vazia deixava a publicação dar ``ok`` com o artefato
    novo fora do commit (revisão do PR #743).
    """
    mudados: set[str] = set()
    for argumentos in (("diff", "--name-only", "HEAD", "--", *caminhos),
                       ("ls-files", "--others", "--exclude-standard", "--", *caminhos)):
        proc = _git(raiz, *argumentos)
        if proc.returncode != 0:
            return None
        mudados.update(linha.strip() for linha in (proc.stdout or "").splitlines()
                       if linha.strip())
    return sorted(mudados)


@dataclass
class ResultadoAtualizacao:
    """O que `atualizar_main` fez com a `main` local, e se dá para publicar."""

    avancou: int = 0
    #: Commits do HEAD que a origin/main não tem, e da origin/main que o HEAD
    #: não tem, medidos depois do fetch e de um eventual avanço.
    frente: int = 0
    atras: int = 0
    motivo: str = ""
    erro: str = ""
    #: Só `atualizar_main` liga, e só depois de comprovar. Ver `pode_publicar`.
    contem_origin: bool = False

    @property
    def ok(self) -> bool:
        return not self.erro

    @property
    def pode_publicar(self) -> bool:
        """Se o HEAD comprovadamente contém a origin/main recém-buscada.

        Falso por padrão: um resultado que ninguém confirmou bloqueia. `ok` é
        outra pergunta -- um ramo de trabalho que já contém a origin pode
        publicar, mas segue com `erro`, porque o artefato não será commitado.
        """
        return self.contem_origin

    def resumo(self) -> str:
        if self.erro:
            return self.erro
        if self.avancou:
            return f"main local avançou {self.avancou} commit(s) até a origin/main"
        return self.motivo or "main local já em dia"


def _contagem(raiz: Path) -> tuple[int, int] | None:
    """(à frente, atrás) do HEAD em relação à `origin/main`.

    HEAD, e não `main`: os publicadores executam o código do HEAD, esteja o
    checkout na `main` ou num ramo de trabalho.
    """
    proc = _git(raiz, "rev-list", "--left-right", "--count",
                f"HEAD...origin/{RAMO_PUBLICADO}")
    partes = (proc.stdout or "").split()
    if proc.returncode != 0 or len(partes) != 2:
        return None
    return int(partes[0]), int(partes[1])


def _buscar_origin(raiz: Path, pausas) -> tuple[subprocess.CompletedProcess, int]:
    """`git fetch` com novas tentativas. Devolve a última resposta e quantas foram.

    Fetch que estoura `TIMEOUT_GIT` conta como tentativa falha, não como exceção:
    rede pela metade costuma pendurar em vez de recusar. `_git` já devolve o
    estouro como falha; o `except` fica para quem o substitui (testes).
    """
    tentativas = 0
    for pausa in (0, *pausas):
        if tentativas:
            time.sleep(pausa)
        tentativas += 1
        try:
            proc = _git(raiz, "fetch", "origin", RAMO_PUBLICADO)
        except subprocess.TimeoutExpired:
            proc = subprocess.CompletedProcess(
                ["git", "fetch"], 1, "", f"sem resposta em {TIMEOUT_GIT} s")
        if proc.returncode == 0:
            break
    return proc, tentativas


def _motivo_da_recusa(proc: subprocess.CompletedProcess) -> str:
    """O que o git disse ao recusar o merge, com os arquivos que ele nomeou.

    `merge --ff-only` barrado por arquivo modificado termina em "Aborting", e
    a última linha não diz nada. O que serve é o cabeçalho do erro e a lista
    indentada logo abaixo dele -- quem lê o Telegram precisa saber qual
    arquivo destravar.
    """
    linhas = [linha for linha in (proc.stderr or "").splitlines() if linha.strip()]
    arquivos = [linha.strip() for linha in linhas if linha.startswith("\t")]
    cabecalho = next((linha.strip() for linha in linhas
                      if not linha.startswith("\t")), "")
    if arquivos:
        return f"{cabecalho} {', '.join(arquivos)}".strip()
    return cabecalho or _ultima_linha(proc)


def atualizar_main(raiz: Path, *, pausas=PAUSAS_FETCH) -> ResultadoAtualizacao:
    """Avança a `main` local até a `origin/main`, só por fast-forward.

    Roda antes dos publicadores, para eles usarem o código mergeado e o push
    do artefato não ser rejeitado por a `main` local estar para trás. Ver o
    cabeçalho do módulo para o que ela se recusa a fazer e para quando
    `pode_publicar` fica falso. `pausas` são os segundos entre tentativas de
    fetch; ``()`` tenta uma vez só.
    """
    resultado = ResultadoAtualizacao()
    try:
        _atualizar(raiz, resultado, pausas)
    except (OSError, subprocess.SubprocessError) as exc:
        # git ausente, ou que não respondeu no prazo. Sem resposta não há prova
        # de nada; e exceção aqui mataria a rotina antes de ela poder avisar.
        resultado.contem_origin = False
        resultado.erro = f"git não respondeu: {exc}"
    return resultado


def _atualizar(raiz: Path, resultado: ResultadoAtualizacao, pausas) -> None:
    em_curso = operacao_em_curso(raiz)
    if em_curso:
        resultado.erro = f"{em_curso} em andamento; main local não atualizada"
        return
    ramo = ramo_atual(raiz)

    # O fetch vem antes da regra do ramo: fora da `main` não se avança nada,
    # mas ainda é preciso saber se o HEAD tem tudo o que a origin tem.
    proc, tentativas = _buscar_origin(raiz, pausas)
    if proc.returncode != 0:
        resultado.erro = (
            f"git fetch falhou ({tentativas} tentativa(s)): {_ultima_linha(proc)}. "
            "Sem a origin/main não dá para saber se o código do disco está em dia.")
        return
    contagem = _contagem(raiz)
    if contagem is None:
        resultado.erro = "não deu para comparar o HEAD com a origin/main"
        return
    frente, atras = contagem
    resultado.frente, resultado.atras = frente, atras

    if ramo != RAMO_PUBLICADO:
        # Avançar aqui seria merge num ramo de trabalho alheio. Publica só se o
        # ramo já contém a origin; o artefato, de todo modo, não será commitado.
        resultado.contem_origin = not atras
        resultado.erro = (f"ramo atual é {ramo or 'desconhecido'}, não "
                          f"{RAMO_PUBLICADO}; main local não atualizada")
        if atras:
            resultado.erro += f", e faltam {atras} commit(s) da origin/main no HEAD"
        return
    if not atras:
        resultado.contem_origin = True
        resultado.motivo = ("main local já em dia" if not frente else
                            f"main local {frente} commit(s) à frente; o próximo "
                            "push os leva")
        return
    if frente:
        resultado.erro = (
            f"main local divergiu: {frente} commit(s) à frente e {atras} atrás "
            "da origin/main. Avanço só por fast-forward é impossível; resolva à "
            "mão (nada foi reescrito). Até lá a rotina não publica, porque "
            "rodaria o código antigo.")
        return

    proc = _git(raiz, "merge", "--ff-only", f"origin/{RAMO_PUBLICADO}")
    if proc.returncode != 0:
        resultado.erro = (
            f"fast-forward recusado ({atras} commit(s) atrás): "
            f"{_motivo_da_recusa(proc)}. Provável mudança na árvore de trabalho "
            "em arquivo que a origin também mudou; nada foi sobrescrito.")
        return
    resultado.avancou = atras
    resultado.atras = 0
    resultado.contem_origin = True


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
    if mudados is None:
        resultado.erros.append(
            "não deu para saber se o artefato mudou (git diff/ls-files falhou ou "
            "ficou sem resposta); nada foi commitado e o alvo segue pendente")
        return resultado
    if mudados:
        proc = _git(raiz, "add", "--", *mudados)
        if proc.returncode != 0:
            resultado.erros.append(f"git add falhou: {_ultima_linha(proc)}")
            return resultado

        # Pathspec explícito: commita SÓ estes caminhos, mesmo que haja outra
        # coisa no índice. A rotina não pode varrer para dentro do commit o que
        # a pessoa estava preparando.
        proc = _git(raiz, "commit", "-m", mensagem, "--", *mudados)
        if proc.returncode != 0:
            resultado.erros.append(f"git commit falhou: {_ultima_linha(proc)}")
            return resultado
        resultado.commitou = True
        resultado.arquivos = tuple(mudados)

    if not empurrar:
        if not mudados:
            resultado.motivo = "artefato idêntico ao commitado"
        return resultado

    # Idêntico à HEAD não quer dizer que está na origin: o commit de uma
    # execução cujo push falhou fica na main local, e a regeneração seguinte
    # sai igual a ele. Quem decide se há o que empurrar é a origin/main.
    fora = _fora_da_origin(raiz, declarados)
    if fora is None:
        resultado.erros.append(
            "não deu para comparar o artefato com a origin/main; sem isso não "
            "dá para saber se ele chegou")
        return resultado
    if not mudados and not fora:
        resultado.motivo = "artefato idêntico ao commitado e já na origin/main"
        return resultado
    if not mudados:
        resultado.arquivos = tuple(fora)

    proc = _git(raiz, "push", "origin", RAMO_PUBLICADO, timeout=TIMEOUT_PUSH)
    if proc.returncode == RC_TIMEOUT and _chegou_a_origin(raiz, declarados):
        # O envio completou e o git pendurou depois. O que vale é a origin.
        resultado.empurrou = True
        resultado.motivo = (f"push {_ultima_linha(proc)}, mas a origin/main "
                            "já tem o artefato")
        return resultado
    if proc.returncode != 0:
        # Sem force e sem rebase automático: ver o cabeçalho do módulo.
        quando = ("commitado localmente" if resultado.commitou
                  else "commit anterior ainda só na main local")
        resultado.erros.append(
            f"{quando}, mas o push falhou: {_ultima_linha(proc)}. "
            "O commit está na main local; nada foi forçado, e a próxima "
            "execução tenta de novo.")
        return resultado
    resultado.empurrou = True
    return resultado


def _fora_da_origin(raiz: Path, declarados: list[str]) -> list[str] | None:
    """Quais artefatos da HEAD diferem da `origin/main` local.

    ``None`` quando o git não respondeu (sem a ref da origin, por exemplo): aí
    ninguém afirma que o artefato chegou.
    """
    proc = _git(raiz, "diff", "--name-only", f"origin/{RAMO_PUBLICADO}", "HEAD",
                "--", *declarados)
    if proc.returncode != 0:
        return None
    return sorted(linha.strip() for linha in (proc.stdout or "").splitlines()
                  if linha.strip())


def _chegou_a_origin(raiz: Path, declarados: list[str]) -> bool:
    """Depois de um push sem resposta: a origin já tem o que a HEAD tem?"""
    proc = _git(raiz, "fetch", "origin", RAMO_PUBLICADO)
    return proc.returncode == 0 and _fora_da_origin(raiz, declarados) == []
