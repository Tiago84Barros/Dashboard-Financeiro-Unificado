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
# Atualidade trimestral das demonstrações da B3 no armazém (B3-02)
# --------------------------------------------------------------------------

CHAVE_ATUALIDADE_B3 = "b3:trimestre_armazem"


def problema_atualidade_b3(avaliacao: dict | None) -> Problema | None:
    """Armazém com trimestre vigente anterior ao que o calendário CVM exige.

    A idade de publicação não pega isto: o alvo ``b3_metrics`` publicou em dia
    com as demonstrações do armazém paradas desde 23/07/2026. O trimestre
    vigente sai da cobertura do universo, não do ``MAX(data)`` (ver
    `core.b3_atualidade_trimestral`). A assinatura é o par vigente -> esperado,
    que só muda com o trimestre, e por isso não vira aviso diário.
    """
    if not avaliacao or not avaliacao.get("atrasada"):
        return None
    from core.b3_atualidade_trimestral import rotulo
    assinatura = f"{rotulo(avaliacao.get('vigente'))}->{rotulo(avaliacao.get('esperado'))}"
    return Problema(
        CHAVE_ATUALIDADE_B3, assinatura,
        "Demonstrações da B3 no armazém local atrasadas. "
        f"{avaliacao.get('texto', '')} As métricas que o alvo b3_metrics publica "
        "saem dessa base. Atualize com `python run_market_ingest.py annual "
        "--warehouse` e `python run_market_ingest.py reprocess --warehouse`.")


# --------------------------------------------------------------------------
# Coleta de notícias (acervo local)
# --------------------------------------------------------------------------
#
# Incidente de 07/10/2026: o Alpha Vantage, que trazia ~75% das notícias
# americanas (70 a 175 por dia), caiu para 0 a 6 por dia de um dia para o
# outro, e de 14 a 22/10 o coletor inteiro não rodou. Ninguém soube por um
# mês: o coletor grava o estado de cada provedor em `noticias_coleta_estado`,
# mas nada lia isso para avisar. Três perguntas, cada uma com seu limiar:
#
# 1. a coleta roda? -- última tentativa global há mais de COLETA_PARADA;
# 2. o provedor responde? -- sem sucesso há DIAS_SEM_SUCESSO dias ou mais;
# 3. o provedor rende? -- responde, mas os últimos DIAS_QUEDA dias completos
#    ficaram abaixo de FRACAO_QUEDA do normal dele. É o caso de 07/10: a cota
#    diária esgota cedo, as chamadas seguintes voltam vazias e contam como
#    sucesso, e a pergunta 2 nunca dispararia sozinha.

CHAVE_COLETA_NOTICIAS = "noticias:coleta"
#: O ciclo mais lento da cadência é de horas; 26 h cobre um dia inteiro mais a
#: folga de o vigia rodar no fim da tarde.
COLETA_PARADA = timedelta(hours=26)
DIAS_SEM_SUCESSO = 2
DIAS_QUEDA = 2
FRACAO_QUEDA = 0.2
#: Provedor que rende menos que isto por dia no normal não é julgado pelo
#: rendimento: com 3 notícias/dia, um dia com zero é acaso, não defeito.
MIN_NORMAL_DIA = 10
#: O "normal" é o percentil 75 dos dias de BASE_DIAS. Mediana curta faria a
#: queda virar o novo normal em duas semanas e o vigia declararia "resolvido"
#: um provedor que continua morto; com o p75 de 60 dias, isso leva ~45 dias.
BASE_DIAS = 60


def _como_data(valor) -> date:
    """Chave do ``por_dia``: ``date`` do banco ou texto ISO de quem serializou."""
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    return date.fromisoformat(str(valor)[:10])


def _p75(valores: list[int]) -> float:
    ordem = sorted(valores)
    if not ordem:
        return 0.0
    return float(ordem[min(len(ordem) - 1, int(round(0.75 * (len(ordem) - 1))))])


def problemas_noticias(estados: dict[str, dict], por_dia: dict[str, dict],
                       agora: datetime) -> tuple[list[Problema], set[str]]:
    """Coleta parada, provedor falhando e provedor rendendo quase nada. Puro.

    ``estados`` é ``noticias_coleta_estado`` por chave (``global`` e
    ``provedor:<nome>``); ``por_dia`` é ``{provedor: {date: notícias novas}}``
    contado por ``coletado_em`` em ``noticias_itens``. Só entra em
    ``verificados`` o que se mediu: provedor sem linha de estado não é
    declarado nem parado nem resolvido.
    """
    problemas: list[Problema] = []
    verificados: set[str] = set()
    glob = estados.get("global")
    parada = False
    if glob:
        verificados.add(CHAVE_COLETA_NOTICIAS)
        ultima = _instante(glob.get("ultima_tentativa"))
        if ultima is None or agora - ultima > COLETA_PARADA:
            parada = True
            horas = "?" if ultima is None else int((agora - ultima).total_seconds() // 3600)
            problemas.append(Problema(
                CHAVE_COLETA_NOTICIAS, f"parada:{ultima.isoformat() if ultima else '-'}",
                f"Coleta de notícias não roda desde {_dia(ultima)} ({horas} h). A "
                "tarefa \"DFU - Coleta de noticias\" está parada, desativada ou o "
                "PC ficou desligado; sem ela o acervo e a vitrine de notícias "
                "envelhecem."))

    em_rotacao: set[str] = set()
    for chave, est in sorted(estados.items()):
        if not chave.startswith("provedor:"):
            continue
        nome = chave.split(":", 1)[1]
        tentativa = _instante(est.get("ultima_tentativa"))
        # Provedor que saiu da rotação (sem chave, desligado) não é falha.
        if tentativa is None or agora - tentativa > timedelta(days=DIAS_SEM_SUCESSO + 1):
            continue
        em_rotacao.add(nome)
        k = f"noticias:provedor:{nome}"
        verificados.add(k)
        sucesso = _instante(est.get("ultimo_sucesso"))
        if sucesso is not None and agora - sucesso < timedelta(days=DIAS_SEM_SUCESSO):
            continue
        erro = str(est.get("ultimo_erro") or "erro não detalhado")[:200]
        problemas.append(Problema(
            k, f"sem_sucesso:{sucesso.isoformat() if sucesso else '-'}",
            f"Notícias: o provedor {nome} não tem coleta bem-sucedida desde "
            f"{_dia(sucesso) if sucesso else 'nunca'}. Último erro: {erro}. "
            "Cota esgotada, chave inválida ou plano alterado são as causas comuns."))

    if parada:
        # Com a coleta inteira parada, todo provedor "rende zero": o aviso
        # global já diz o que há para fazer.
        return problemas, verificados
    hoje = agora.astimezone(timezone.utc).date()
    recentes = [hoje - timedelta(days=i) for i in range(1, DIAS_QUEDA + 1)]
    base = [hoje - timedelta(days=i)
            for i in range(DIAS_QUEDA + 1, DIAS_QUEDA + 1 + BASE_DIAS)]
    for nome, contagem in sorted(por_dia.items()):
        # Mesma elegibilidade da pergunta 2: provedor desligado de propósito, ou
        # sem linha de estado, tem histórico de rendimento mas não está em
        # rotação -- julgá-lo pelo rendimento seria aviso diário de algo que
        # ninguém espera que funcione.
        if nome not in em_rotacao:
            continue
        contagem = {_como_data(d): int(n or 0) for d, n in (contagem or {}).items()}
        normal = _p75([contagem.get(d, 0) for d in base])
        if normal < MIN_NORMAL_DIA:
            continue
        k = f"noticias:rendimento:{nome}"
        verificados.add(k)
        ultimos = [contagem.get(d, 0) for d in recentes]
        if all(n < FRACAO_QUEDA * normal for n in ultimos):
            problemas.append(Problema(
                k, "queda",
                f"Notícias: o provedor {nome} trouxe {' e '.join(map(str, ultimos))} "
                f"notícia(s) nova(s) nos {DIAS_QUEDA} últimos dias, contra "
                f"{normal:.0f}/dia no normal dele. Ele responde, mas quase vazio: "
                "costuma ser cota diária esgotando cedo, consulta que deixou de "
                "casar ou mudança de plano do provedor."))
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
