"""Quanto pregão o mercado teve para reagir, entre dois instantes.

Por que este módulo existe
--------------------------
O índice de relevância mede a idade de uma notícia em **horas corridas**, e
horas corridas não são oportunidade de reação. Uma intervenção do Banco Central
publicada às 03:00 de um sábado tem 54 horas de idade quando a segunda-feira
abre -- e teve **zero** pregões para ser precificada. Ela é tão acionável quanto
no instante em que saiu, e o motor a tratava como notícia velha.

O erro tem sinal: ele **rebaixa sistematicamente notícia de fim de semana e de
madrugada**, que é justamente quando banco central, regulador e conselho de
administração publicam o que não querem no meio do pregão.

A ponta oposta é o mesmo eixo. Uma queda de lucro de doze dias, já replicada,
não é "velha por doze dias" -- é velha por **oito pregões** de oportunidade de
precificação. Contar pregões dá nome à mesma grandeza nas duas pontas.

O que este módulo não faz, e por que
------------------------------------
**Não afirma que algo já está precificado.** Isso exige observar o preço depois
do evento, contra a memória de mercado -- e desde 06/09/2026 essa safra existe
(``core/memoria_mercado``). O que continua não existindo é o julgamento aqui: o
calendário não ganha acesso à safra por ela ter passado a existir, e derivar
"já precificado" do relógio seria inventar a conclusão do mesmo jeito. O que o
calendário sustenta é a frase menor e verdadeira: *o mercado teve N pregões
para reagir*.

**Não afirma feriado fora da janela observada.** Até 06/09/2026 a contagem era
dia útil puro, e a lacuna era declarada com a alternativa nomeada:
tabela escrita à mão envelhece em silêncio e passa a mentir com a mesma cara de
quem acerta (``memoria: aviso-que-envelhece-invertido``).

O que fechou a lacuna não foi uma tabela, foi o **complemento observado** da
série de preços do armazém local: dia útil em que a bolsa inteira não negociou
nenhum papel não é opinião sobre o calendário, é o calendário. Ele chega aqui
como artefato publicado por ``scripts/publicar_calendario_pregao.py`` -- 213
feriados da B3 e 156 da NYSE desde 2010 -- e não por conexão, porque a produção
não alcança o armazém e **duas implementações da mesma contagem dariam duas
idades para a mesma notícia conforme quem perguntou**.

A lacuna que sobra é a ponta do futuro, e ela é a mesma de antes: **além da
última data observada**, a contagem volta a ser dia útil puro. Isso é
inevitável -- feriado que ainda não aconteceu não pode ter sido observado -- e a
direção do erro continua única e conhecida: sem feriados, a contagem só pode
**superestimar** o número de pregões, nunca subestimar; ela pode fazer uma
notícia parecer mais velha do que é, e nunca fazer notícia velha parecer fresca.

Por isso :func:`cobertura` existe e é pública: a frase que descreve a limitação
tem de ser **derivada da medição**, e não escrita à mão em algum lugar onde ela
possa continuar dizendo "nenhum feriado é modelado" um ano depois de deixarem
de ser verdade.

Ausência do artefato **não é erro**. Sem ele o módulo roda como rodava, e
:func:`cobertura` diz que roda assim. Um ``FileNotFoundError` aqui derrubaria a
coleta inteira por causa de uma melhoria de precisão de meia dúzia de dias por
ano, que é a troca errada.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

__all__ = ["Praca", "B3", "NYSE", "esta_aberto", "pregoes_encerrados_entre",
           "proximo_fechamento", "cobertura", "ARTEFATO_CALENDARIO"]

logger = logging.getLogger(__name__)

#: Onde mora o calendário observado. Fica em ``data/public/`` junto do snapshot
#: de FII porque é a mesma natureza de coisa: artefato gerado do armazém local,
#: versionado, lido pela produção que não alcança o armazém.
ARTEFATO_CALENDARIO = (Path(__file__).resolve().parents[1]
                       / "data" / "public" / "calendario_pregao.json")


@dataclass(frozen=True)
class Praca:
    """Uma bolsa, no mínimo necessário para contar sessões.

    ``abertura`` e ``fechamento`` são horários locais da praça. O fuso entra
    por nome (``ZoneInfo``) e não por deslocamento fixo, porque horário de
    verão muda o deslocamento e não muda o pregão: Nova York abre às 9:30 da
    manhã dela o ano inteiro.
    """

    nome: str
    fuso: str
    abertura: time
    fechamento: time

    def local(self, quando: datetime) -> datetime:
        """Converte para o horário da praça, assumindo UTC quando ingênuo.

        Assumir UTC é a convenção do resto do módulo de notícias, e é a única
        defensável aqui: um carimbo sem fuso vindo de provedor estrangeiro
        interpretado como horário local produziria contagem errada de até um
        pregão inteiro, silenciosamente.
        """
        if quando.tzinfo is None:
            quando = quando.replace(tzinfo=timezone.utc)
        return quando.astimezone(ZoneInfo(self.fuso))


#: Sessão regular da B3, sem leilão de fechamento e sem after-market. O
#: after-market move volume pequeno e não é onde a notícia é precificada.
B3 = Praca("B3", "America/Sao_Paulo", time(10, 0), time(17, 0))

#: Sessão regular de Nova York. Pré e pós-mercado ficam de fora pelo mesmo
#: motivo, e aqui a omissão é mais visível: notícia de balanço americano sai
#: quase sempre fora da sessão regular. É por isso que a contagem é de
#: *oportunidade completa*, e não de "o preço já se mexeu".
NYSE = Praca("NYSE", "America/New_York", time(9, 30), time(16, 0))


@lru_cache(maxsize=1)
def _calendario() -> dict[str, tuple[frozenset[date], date | None, date | None]]:
    """Feriados observados por praça, com a janela em que foram observados.

    Cacheado porque é arquivo imutável em disco lido a cada notícia avaliada, e
    ``lru_cache`` sem argumento é o cache mais barato que existe. Quem publicar
    um artefato novo reinicia o processo -- como já acontece com o snapshot.
    """
    try:
        bruto = json.loads(ARTEFATO_CALENDARIO.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.info("calendario de pregao observado ausente em %s: a contagem "
                    "usa dia util puro", ARTEFATO_CALENDARIO)
        return {}
    except (OSError, ValueError):
        # Artefato ilegível é ausência, não exceção: ver o docstring do módulo.
        logger.warning("calendario de pregao ilegivel em %s: a contagem usa "
                       "dia util puro", ARTEFATO_CALENDARIO)
        return {}

    saida = {}
    for nome, dados in (bruto.get("pracas") or {}).items():
        try:
            feriados = frozenset(date.fromisoformat(d)
                                 for d in dados.get("feriados") or ())
            inicio = date.fromisoformat(dados["inicio"])
            fim = date.fromisoformat(dados["fim"])
        except (KeyError, TypeError, ValueError):
            logger.warning("praca %s no calendario esta malformada: ignorada",
                           nome)
            continue
        saida[nome] = (feriados, inicio, fim)
    return saida


def cobertura(praca: Praca = B3) -> dict:
    """O que o calendário desta praça **de fato** cobre, para quem declara.

    Devolve ``{"observado": bool, "inicio": date|None, "fim": date|None,
    "feriados": int, "limitacao": str}``.

    Existe porque a frase que descreve a limitação tem de sair da medição. Um
    texto fixo dizendo "feriado não é modelado" continuaria soando como rigor
    depois de deixar de ser verdade -- é ``memoria:
    aviso-que-envelhece-invertido``, e este projeto já pagou por ele.
    """
    feriados, inicio, fim = _calendario().get(praca.nome, (frozenset(), None, None))
    if not fim:
        return {"observado": False, "inicio": None, "fim": None, "feriados": 0,
                "limitacao": (f"calendario de pregao da {praca.nome} nao "
                              "observado: a contagem usa dia util puro e so "
                              "pode superestimar pregoes, nunca subestimar")}
    return {"observado": True, "inicio": inicio, "fim": fim,
            "feriados": len(feriados),
            "limitacao": (f"feriados da {praca.nome} observados de {inicio} a "
                          f"{fim} ({len(feriados)} datas); depois de {fim} a "
                          "contagem volta a dia util puro e so pode "
                          "superestimar pregoes, nunca subestimar")}


def _e_dia_util(dia: date, praca: Praca = B3) -> bool:
    """Segunda a sexta, menos feriado observado da praça.

    Fora da janela observada o feriado não é *assumido ausente*: ele é
    **desconhecido**, e a contagem devolve ao comportamento anterior. A
    distinção importa porque o erro tem direção única só nesse recuo -- dentro
    da janela não há erro a compensar, há calendário.
    """
    if dia.weekday() >= 5:
        return False
    feriados, inicio, fim = _calendario().get(praca.nome,
                                              (frozenset(), None, None))
    if fim is None or not (inicio <= dia <= fim):
        return True
    return dia not in feriados


def esta_aberto(quando: datetime, praca: Praca = B3) -> bool:
    """Se o pregão da praça estava aberto naquele instante."""
    local = praca.local(quando)
    if not _e_dia_util(local.date(), praca):
        return False
    return praca.abertura <= local.time() < praca.fechamento


def proximo_fechamento(quando: datetime, praca: Praca = B3) -> datetime:
    """O primeiro fechamento de pregão em ou após ``quando``, em UTC.

    É o instante a partir do qual existe uma sessão inteira de oportunidade de
    reação. Serve para responder "quando esta notícia deixa de ser inédita para
    o mercado", que é uma pergunta diferente de "quando ela envelhece".
    """
    local = praca.local(quando)
    dia = local.date()
    for _ in range(15):  # cobre recesso longo emendado com fim de semana
        if _e_dia_util(dia, praca):
            fecha = datetime.combine(dia, praca.fechamento,
                                     tzinfo=ZoneInfo(praca.fuso))
            if fecha >= local:
                return fecha.astimezone(timezone.utc)
        dia += timedelta(days=1)
    raise ValueError(f"nenhum pregao em 15 dias a partir de {quando}")


def pregoes_encerrados_entre(inicio: datetime, fim: datetime,
                             praca: Praca = B3) -> int:
    """Sessões que **fecharam** no intervalo ``(inicio, fim]``.

    Sessão encerrada, e não sessão iniciada, porque o que a contagem mede é
    oportunidade *completa* de precificação. Notícia publicada às 11:00 com o
    pregão em curso ainda não teve o dia inteiro para ser digerida, e contar
    esse dia como um pregão inteiro seria arredondar a favor da conclusão de
    que ela já é velha.

    Devolve ``0`` quando ``fim <= inicio`` -- inclusive para carimbos fora de
    ordem, que acontecem: provedor com relógio adiantado publica no futuro. Zero
    é a resposta certa e conservadora ali, porque "o mercado ainda não teve
    chance nenhuma" é exatamente o que se sabe.
    """
    # A conversão vem antes da comparação, e não depois: comparar carimbos
    # crus estoura com "offset-naive and offset-aware" quando um dos dois vem
    # ingênuo do provedor. ``Praca.local`` é quem sabe que ingênuo significa
    # UTC neste projeto, e é ele que precisa decidir isso primeiro.
    ini_local = praca.local(inicio)
    fim_local = praca.local(fim)
    if fim_local <= ini_local:
        return 0

    n = 0
    dia = ini_local.date()
    while dia <= fim_local.date():
        if _e_dia_util(dia, praca):
            fecha = datetime.combine(dia, praca.fechamento,
                                     tzinfo=ZoneInfo(praca.fuso))
            if ini_local < fecha <= fim_local:
                n += 1
        dia += timedelta(days=1)
    return n
