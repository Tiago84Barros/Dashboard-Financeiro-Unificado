"""Vigia das automações: decide, sem rede nem relógio, o que merece aviso.

Núcleo puro. Recebe listas de execuções do GitHub Actions, o estado da rotina
de publicação e a memória dos avisos já dados, e devolve o que avisar. Quem
consulta o `gh`, lê arquivo e chama o Telegram é `scripts/vigia_automacoes.py`.

Existe por incidente, de novo. De 01/10 a 03/10/2026 o `data_pipeline.yml` e o
`market-refresh.yml` falharam todo dia (``No module named 'psycopg'``) e
ninguém foi avisado; a vitrine de FIIs ficou parada desde 28/09, com o erro
registrado em `local_staging/estado_publicacao.json`, também sem aviso. A
rotina de publicação avisa quando UM alvo falha numa execução, mas não olha o
GitHub e não diz "isto está parado há seis dias". O vigia cobre essas duas
lacunas.

Regras (as mesmas de `scripts/atualizar_vitrines.py`):

**Falhar avisa; ficar em dia não avisa.** E o problema que se resolve avisa
uma vez -- sem isso, quem recebeu o alarme não sabe se ainda precisa agir.

**Sem spam.** O mesmo problema só é reavisado se a assinatura dele mudar ou
depois de 24 h do último aviso.

Regra das execuções agendadas (``workflow``):

- só contam execuções com ``event=schedule`` no ramo principal; disparo manual
  é teste de alguém, não a automação;
- execução em andamento (``status`` diferente de ``completed``) é ignorada --
  ainda não tem desfecho;
- ``success`` zera a sequência;
- ``failure``, ``timed_out`` e ``startup_failure`` são falha;
- ``cancelled``, ``skipped``, ``neutral``, ``action_required`` e ``stale`` são
  ignorados: não dizem nada sobre a automação funcionar. É o caso do
  `noticias.yml`, que nasce desligado por variável de repositório -- o cron
  dispara e o job sai ``skipped`` em todas as execuções. Pular não é falhar;
- avisa com **2 ou mais falhas consecutivas mais recentes**. Uma falha isolada
  é ruído de runner/rede; duas seguidas já é a automação parada.

Regra das publicações (``vitrine``): idade da última publicação BEM-SUCEDIDA,
lida do estado local da rotina (zero egress do Supabase), contra
`core.frescor.limite_do_alvo` -- cadência da agenda + tolerância, a mesma régua
com que a tela declara a vitrine vencida. Nada de limite novo aqui.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from core.frescor import TOLERANCIA_DIAS, idade_em_dias, limite_do_alvo
from core.publicacao_agenda import ALVOS

CONCLUSOES_FALHA = frozenset({"failure", "timed_out", "startup_failure"})
CONCLUSOES_SUCESSO = frozenset({"success"})
MIN_FALHAS_CONSECUTIVAS = 2
REAVISO = timedelta(hours=24)
# O vigia também pode ficar cego: sem `gh` (deslogado, rede caída) ele não vê
# workflow nenhum, e um vigia cego que não reclama é o defeito original. A
# folga de 48 h existe porque rede caída ao ligar o computador é rotina.
CEGUEIRA_MAXIMA = timedelta(hours=48)


@dataclass(frozen=True)
class Problema:
    """Algo que merece aviso.

    ``assinatura`` é o que define "o problema mudou": se muda, avisa de novo
    mesmo dentro das 24 h. Não pode carregar nada que mude sozinho a cada
    execução (contagem, idade), senão vira aviso diário disfarçado.
    """

    chave: str
    assinatura: str
    texto: str


def _instante(valor) -> datetime | None:
    if not valor:
        return None
    if isinstance(valor, datetime):
        dt = valor
    else:
        try:
            dt = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _dia(valor) -> str:
    dt = _instante(valor)
    return dt.astimezone().strftime("%d/%m %H:%M") if dt else "?"


# --------------------------------------------------------------------------
# Workflows agendados
# --------------------------------------------------------------------------

_SCHEDULE = re.compile(r"^\s+schedule:\s*(#.*)?$")


def tem_agendamento(conteudo_yaml: str) -> bool:
    """O workflow tem gatilho ``schedule:`` ativo (fora de comentário)?

    Texto, e não YAML: o `bootstrap-brapi.yml` cita ``schedule:`` num
    comentário ("recoloque ...") e um parser de YAML acertaria, mas não é
    dependência do projeto -- e a linha comentada começa com ``#``.
    """
    return any(_SCHEDULE.match(linha) for linha in conteudo_yaml.splitlines()
               if not linha.lstrip().startswith("#"))


def falhas_consecutivas(runs: list[dict], ramo: str | None = "main"
                        ) -> tuple[list[dict], bool]:
    """Falhas mais recentes até o primeiro sucesso, da mais nova para a mais velha.

    Devolve também se a sequência é **completa** -- se achou o sucesso que a
    abre. Sem ele, a janela consultada inteira foi falha e a sequência real
    pode ser maior.
    """
    ordenados = sorted(runs, key=lambda r: _instante(r.get("createdAt"))
                       or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    falhas: list[dict] = []
    for run in ordenados:
        if ramo and run.get("headBranch") not in (None, "", ramo):
            continue
        if (run.get("status") or "completed") != "completed":
            continue
        conclusao = (run.get("conclusion") or "").lower()
        if conclusao in CONCLUSOES_SUCESSO:
            return falhas, True
        if conclusao in CONCLUSOES_FALHA:
            falhas.append(run)
    return falhas, False


def problema_workflow(nome: str, runs: list[dict], ramo: str | None = "main"
                      ) -> Problema | None:
    falhas, completa = falhas_consecutivas(runs, ramo)
    if len(falhas) < MIN_FALHAS_CONSECUTIVAS:
        return None
    ultima, primeira = falhas[0], falhas[-1]
    quantas = f"{len(falhas)}" if completa else f"pelo menos {len(falhas)}"
    desde = _dia(primeira.get("createdAt")) + ("" if completa else " ou antes")
    texto = (f"Workflow {nome}: {quantas} execuções agendadas seguidas com falha "
             f"(desde {desde}; última "
             f"{_dia(ultima.get('createdAt'))}, {ultima.get('conclusion')}). "
             f"{ultima.get('url') or ''}").strip()
    # Assinatura = o tipo da última falha. A URL ou a contagem mudariam a cada
    # execução e o "mudou" viraria aviso a cada cron.
    return Problema(f"workflow:{nome}", str(ultima.get("conclusion")), texto)


# --------------------------------------------------------------------------
# Publicações (vitrines e ingestões da rotina local)
# --------------------------------------------------------------------------

def problemas_vitrine(estado: dict, hoje: date, alvos=ALVOS
                      ) -> tuple[list[Problema], set[str]]:
    """Alvos cuja última publicação bem-sucedida passou do limite.

    Devolve os problemas e as chaves efetivamente verificadas: alvo sem
    registro não tem idade conhecida e fica fora das duas listas -- não se
    afirma "parado" nem "resolvido" sobre o que não se mediu. Estado vazio é
    um problema próprio: o vigia não consegue dizer nada das publicações.
    """
    if not estado:
        return ([Problema("vitrines:estado", "ausente",
                          "Estado da rotina de publicação ausente ou ilegível "
                          "(local_staging/estado_publicacao.json): não dá para "
                          "medir a idade das vitrines.")],
                {"vitrines:estado"})
    problemas: list[Problema] = []
    verificados = {"vitrines:estado"}
    for alvo in alvos:
        limite = limite_do_alvo(alvo.chave)
        registro = estado.get(alvo.chave)
        if limite is None or not isinstance(registro, dict):
            continue
        ultima = registro.get("ultima_publicacao")
        idade = idade_em_dias(_instante(ultima), hoje=hoje)
        if idade is None:
            continue
        chave = f"vitrine:{alvo.chave}"
        verificados.add(chave)
        if idade <= limite:
            continue
        texto = (f"{alvo.titulo} ({alvo.chave}): última publicação em "
                 f"{_dia(ultima)}, há {idade} dias -- limite {limite} d "
                 f"(cadência {alvo.cadencia_dias} d + tolerância {TOLERANCIA_DIAS} d).")
        if registro.get("ultimo_status") not in (None, "ok"):
            texto += (f" A última tentativa ({_dia(registro.get('ultima_tentativa'))}) "
                      "falhou; veja local_staging/logs/atualizacao_vitrines.log.")
        # A data da última publicação só muda quando publica -- e publicar
        # zera a idade. Na prática, reaviso só pelas 24 h.
        problemas.append(Problema(chave, str(ultima), texto))
    return problemas, verificados


# --------------------------------------------------------------------------
# Cegueira do próprio vigia
# --------------------------------------------------------------------------

def atualizar_cegueira(meta: dict | None, gh_ok: bool, agora: datetime
                       ) -> tuple[dict, Problema | None]:
    """Acompanha há quanto tempo o vigia não consegue ler o GitHub."""
    meta = dict(meta or {})
    if gh_ok:
        return {"ultimo_ok": agora.isoformat(), "falhando_desde": None}, None
    desde = _instante(meta.get("falhando_desde")) or agora
    meta["falhando_desde"] = desde.isoformat()
    if agora - desde < CEGUEIRA_MAXIMA:
        return meta, None
    horas = int((agora - desde).total_seconds() // 3600)
    return meta, Problema(
        "vigia:github", "cego",
        f"O vigia não consegue consultar o GitHub Actions há {horas} h "
        f"(desde {_dia(desde)}): `gh` ausente, deslogado ou sem rede. Os "
        "workflows agendados estão sem vigilância.")


# --------------------------------------------------------------------------
# Antispam e resolução
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Decisao:
    avisar: list[Problema]
    resolvidos: list[tuple[str, str]]  # (chave, texto do último aviso)
    memoria: dict  # memória nova, válida se os avisos saírem


def decidir(problemas: list[Problema], memoria: dict, agora: datetime,
            verificados: set[str]) -> Decisao:
    """Filtra o que avisar agora e o que se resolveu desde o último aviso.

    ``memoria`` é ``{chave: {assinatura, texto, primeiro_aviso, ultimo_aviso}}``.
    Só se declara resolvido o que foi verificado nesta rodada: uma consulta ao
    `gh` que falhou não pode virar "workflow normalizado".
    """
    nova = {k: dict(v) for k, v in (memoria or {}).items()}
    avisar: list[Problema] = []
    atuais = set()
    for problema in problemas:
        atuais.add(problema.chave)
        anterior = nova.get(problema.chave)
        ultimo = _instante((anterior or {}).get("ultimo_aviso"))
        mudou = anterior is None or anterior.get("assinatura") != problema.assinatura
        if mudou or ultimo is None or agora - ultimo >= REAVISO:
            avisar.append(problema)
            nova[problema.chave] = {
                "assinatura": problema.assinatura,
                "texto": problema.texto,
                "primeiro_aviso": (anterior or {}).get("primeiro_aviso")
                or agora.isoformat(),
                "ultimo_aviso": agora.isoformat(),
            }
    resolvidos = []
    for chave in sorted(set(nova) - atuais):
        if chave in verificados:
            resolvidos.append((chave, nova[chave].get("texto", "")))
            del nova[chave]
    return Decisao(avisar, resolvidos, nova)


def montar_mensagem(decisao: Decisao, memoria_anterior: dict
                    ) -> tuple[str, str] | None:
    """(assunto, texto) da notificação, ou ``None`` se não há o que dizer."""
    if not decisao.avisar and not decisao.resolvidos:
        return None
    partes = []
    if decisao.avisar:
        linhas = []
        for p in decisao.avisar:
            anterior = (memoria_anterior or {}).get(p.chave)
            marca = ""
            if anterior and anterior.get("assinatura") == p.assinatura:
                marca = f" [segue desde {_dia(anterior.get('primeiro_aviso'))}]"
            linhas.append(f"- {p.texto}{marca}")
        partes.append(f"Automação com problema ({len(decisao.avisar)}):\n"
                      + "\n".join(linhas))
    if decisao.resolvidos:
        partes.append("Resolvido:\n" + "\n".join(
            f"- {chave.split(':', 1)[-1]} voltou ao normal" for chave, _ in decisao.resolvidos))
    assunto = ("Dashboard: automação com problema" if decisao.avisar
               else "Dashboard: automação normalizada")
    return assunto, "\n\n".join(partes)
