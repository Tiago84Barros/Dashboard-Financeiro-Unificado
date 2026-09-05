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
**Não afirma que algo já está precificado.** Para isso seria preciso observar o
preço depois do evento, contra a memória de mercado -- que ainda não tem safra
construída. Dizer "já precificado" a partir do calendário seria inventar a
conclusão a partir do relógio. O que o calendário sustenta é a frase menor e
verdadeira: *o mercado teve N pregões para reagir*.

**Não modela feriado.** Isto é uma lacuna declarada, não um esquecimento, e a
alternativa era pior: tabela de feriados embutida no código envelhece em
silêncio e passa a mentir com a mesma cara de quem acerta -- o projeto já viveu
um aviso que envelheceu invertido e seguiu soando como rigor.

O que salva a lacuna é a **direção do erro**, que é única e conhecida: sem
feriados, a contagem só pode **superestimar** o número de pregões, nunca
subestimar. Superestimar oportunidade de reação envelhece a notícia mais rápido,
nunca mais devagar. Ou seja: o módulo pode fazer uma notícia parecer mais velha
do que é, e **nunca** pode fazer notícia velha parecer fresca. O erro cabe em
até uma dúzia de dias por ano e cai sempre para o lado conservador.

Quem quiser fechar a lacuna fecha com dado observado -- as datas distintas da
série de preços no armazém local são o calendário de pregão de verdade, sem
tabela para manter. Não está aqui porque a produção não alcança o armazém, e
duas implementações da mesma contagem dariam duas idades para a mesma notícia
conforme quem perguntou.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

__all__ = ["Praca", "B3", "NYSE", "esta_aberto", "pregoes_encerrados_entre",
           "proximo_fechamento"]


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


def _e_dia_util(dia: date) -> bool:
    """Segunda a sexta. Feriado não entra -- ver a lacuna declarada no topo."""
    return dia.weekday() < 5


def esta_aberto(quando: datetime, praca: Praca = B3) -> bool:
    """Se o pregão da praça estava aberto naquele instante."""
    local = praca.local(quando)
    if not _e_dia_util(local.date()):
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
        if _e_dia_util(dia):
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
        if _e_dia_util(dia):
            fecha = datetime.combine(dia, praca.fechamento,
                                     tzinfo=ZoneInfo(praca.fuso))
            if ini_local < fecha <= fim_local:
                n += 1
        dia += timedelta(days=1)
    return n
