"""Ritmo da coleta: em que modo o APP4 está e quando ele deve tentar de novo.

Três modos, e o modo **não é escolhido aqui**
---------------------------------------------
``normal`` / ``vigilancia`` / ``crise`` saem do nível do Motor de Eventos
Extremos por uma tabela fixa. Isso é deliberado: se este módulo tivesse critério
próprio para "entrar em vigilância", passaria a existir um segundo juiz de
crise, com regras que ninguém auditou, capaz de discordar do primeiro.

A consequência boa é que o **encerramento automático da vigilância vem de
graça**. A descida de nível já é governada por ``eventos_extremos.transicao``:
mínimo de 12 h no nível e um degrau por avaliação. O modo herda essa histerese
e, por construção, não pode oscilar mais rápido que ela.

Universo por modo: frequência maior, universo menor
---------------------------------------------------
Cota de provedor gratuito é finita (Alpha Vantage: 25 chamadas/dia). Subir a
frequência sem encolher o universo não aumenta o frescor -- esgota a cota antes
do meio-dia e o resto do dia fica sem nenhuma coleta. Por isso cada modo declara
suas ``prioridades``:

``normal``      carteira, depois candidatos, depois mercado amplo.
``vigilancia``  carteira e candidatos. O mercado amplo sai.
``crise``       só a carteira. É o que precisa de resposta em minutos.

Tolerância no gatilho
---------------------
``deve_coletar`` não compara idade com o intervalo cru. Um agendador que dispara
em horário fixo, medido contra um portão de tempo decorrido, pula um ciclo
inteiro sempre que a execução chega alguns segundos adiantada -- e pular fica
"certo" pela regra. A folga de ``TOLERANCIA_FRACAO`` existe só para isso.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

MODO_NORMAL = "normal"
MODO_VIGILANCIA = "vigilancia"
MODO_CRISE = "crise"

MODOS = (MODO_NORMAL, MODO_VIGILANCIA, MODO_CRISE)

#: Nível do motor de eventos extremos -> modo de coleta.
MODO_POR_NIVEL = {0: MODO_NORMAL, 1: MODO_VIGILANCIA, 2: MODO_VIGILANCIA,
                  3: MODO_CRISE, 4: MODO_CRISE}

ALVO_CARTEIRA = "carteira"
ALVO_CANDIDATOS = "candidatos"
ALVO_MERCADO = "mercado_amplo"

PRIORIDADES = {
    MODO_NORMAL: (ALVO_CARTEIRA, ALVO_CANDIDATOS, ALVO_MERCADO),
    MODO_VIGILANCIA: (ALVO_CARTEIRA, ALVO_CANDIDATOS),
    MODO_CRISE: (ALVO_CARTEIRA,),
}

ROTULO_MODO = {
    MODO_NORMAL: "Normal",
    MODO_VIGILANCIA: "Vigilância",
    MODO_CRISE: "Crise",
}

#: Multiplicador sobre o intervalo antes de declarar o dado atrasado. Uma coleta
#: que atrasou meio ciclo não é dado velho; dois ciclos sem sucesso, sim. Mesmo
#: número de ``frescor_noticias.FOLGA_CADENCIA``, e de propósito: dois limiares
#: diferentes para "vencido" fariam a tela e o job discordarem sobre o mesmo
#: carimbo.
FOLGA_SLA = 2.0

#: Folga no gatilho, como fração do intervalo. Ver o docstring do módulo.
TOLERANCIA_FRACAO = 0.15

STATUS_ATUALIZADO = "atualizado"
STATUS_ATRASADO = "atrasado"
STATUS_DEGRADADO = "degradado"
STATUS_INDISPONIVEL = "indisponivel"

ROTULO_STATUS = {
    STATUS_ATUALIZADO: "Atualizado",
    STATUS_ATRASADO: "Atrasado",
    STATUS_DEGRADADO: "Degradado",
    STATUS_INDISPONIVEL: "Indisponível",
}

#: Status em que o dado não sustenta recomendação de emergência. ``degradado``
#: entra na lista: uma coleta parcial apresentada como completa é justamente o
#: modo de falha que o requisito nomeia.
STATUS_SEM_RECOMENDACAO_EMERGENCIAL = (
    STATUS_ATRASADO, STATUS_DEGRADADO, STATUS_INDISPONIVEL)


def _utc(valor: datetime | None) -> datetime | None:
    if valor is None:
        return None
    return (valor.replace(tzinfo=timezone.utc) if valor.tzinfo is None
            else valor.astimezone(timezone.utc))


def modo_para_nivel(nivel: int | None) -> str:
    """Modo de coleta correspondente ao nível de crise.

    ``None`` -- nível não avaliado -- devolve ``normal``, e essa é a escolha
    conservadora **para a cota**, não para o risco: sem avaliação de crise não
    há evidência de crise, e gastar cota de emergência por falta de informação
    esvaziaria o orçamento justamente antes de uma crise real. Quem precisa
    saber que o nível não foi avaliado lê isso do painel, que não chama de
    "Normal" o que nunca foi medido.
    """
    if nivel is None:
        return MODO_NORMAL
    return MODO_POR_NIVEL.get(int(nivel), MODO_CRISE)


@dataclass(frozen=True)
class Cadencia:
    """O ritmo de um modo: intervalo, SLA de frescor e universo a cobrir."""

    modo: str
    intervalo_min: float
    prioridades: tuple[str, ...]
    sla_min: float

    @property
    def rotulo(self) -> str:
        return ROTULO_MODO.get(self.modo, self.modo)

    @property
    def tolerancia_min(self) -> float:
        return self.intervalo_min * TOLERANCIA_FRACAO

    def descrever(self) -> str:
        alvos = ", ".join(self.prioridades)
        return (f"modo {self.rotulo}: a cada {self.intervalo_min:.0f} min, "
                f"atrasado após {self.sla_min:.0f} min, universo: {alvos}")


def cadencia(modo: str, *, config=None, sla_max_min: float | None = None
             ) -> Cadencia:
    """Monta a cadência do modo a partir da configuração.

    ``config`` é injetável para teste; por omissão vem de ``core.config``.
    """
    if config is None:
        from core.config import settings as config

    modo = modo if modo in MODOS else MODO_NORMAL
    intervalo = {
        MODO_NORMAL: config.noticias_freq_normal_min,
        MODO_VIGILANCIA: config.noticias_freq_vigilancia_min,
        MODO_CRISE: config.noticias_freq_crise_min,
    }[modo]

    # Teto absoluto de tempo sem atualização, se configurado. Ele não pode
    # *afrouxar* o SLA do modo -- só apertá-lo. Um teto global de 24 h não
    # deveria transformar a crise, que vence em 30 min, em algo que só fica
    # atrasado no dia seguinte.
    limite = intervalo * FOLGA_SLA
    teto = (sla_max_min if sla_max_min is not None
            else getattr(config, "noticias_max_sem_atualizacao_min", 0.0))
    if teto and teto > 0:
        limite = min(limite, float(teto))

    return Cadencia(modo=modo, intervalo_min=float(intervalo),
                    prioridades=PRIORIDADES[modo], sla_min=float(limite))


def proximo_ciclo(ultima_tentativa: datetime | None, cad: Cadencia, *,
                  agora: datetime | None = None) -> datetime:
    """Quando a próxima coleta é esperada.

    Conta da última **tentativa**, não do último sucesso: um provedor fora do ar
    não deve fazer o job tentar em rajada, e contar do sucesso faria exatamente
    isso enquanto a falha durasse.
    """
    agora = _utc(agora) or datetime.now(timezone.utc)
    base = _utc(ultima_tentativa) or agora
    previsto = base + timedelta(minutes=cad.intervalo_min)
    return previsto if previsto > agora else agora


def deve_coletar(ultima_tentativa: datetime | None, cad: Cadencia, *,
                 agora: datetime | None = None, forcar: bool = False
                 ) -> tuple[bool, str]:
    """Se está na hora de tentar, e por quê. Nunca decide sozinho por sucesso."""
    if forcar:
        return True, "coleta forçada"
    if ultima_tentativa is None:
        return True, "nenhuma tentativa registrada"

    agora = _utc(agora) or datetime.now(timezone.utc)
    decorrido = (agora - _utc(ultima_tentativa)).total_seconds() / 60.0
    limite = cad.intervalo_min - cad.tolerancia_min
    if decorrido >= limite:
        return True, (f"{decorrido:.0f} min desde a última tentativa "
                      f"(intervalo {cad.intervalo_min:.0f} min)")
    return False, (f"apenas {decorrido:.0f} min desde a última tentativa; "
                   f"o modo {cad.rotulo} coleta a cada "
                   f"{cad.intervalo_min:.0f} min")


def status(ultimo_sucesso: datetime | None, cad: Cadencia, *,
           agora: datetime | None = None, provedores_ok: int = 0,
           provedores_previstos: int = 0, parcial: bool = False,
           usou_cache_vencido: bool = False) -> str:
    """Classifica o estado da coleta em uma das quatro palavras do requisito.

    Precedência, do pior para o melhor -- e ela importa, porque um dado velho
    coletado por um provedor degradado é **atrasado**, não degradado: a idade é
    o defeito maior e é o que bloqueia recomendação de emergência.

    ``indisponivel``  nunca houve sucesso, ou nenhum provedor respondeu agora.
    ``atrasado``      o último sucesso é mais velho que o SLA do modo.
    ``degradado``     dado dentro do prazo, mas obtido parcialmente.
    ``atualizado``    dentro do prazo e completo.
    """
    agora = _utc(agora) or datetime.now(timezone.utc)
    sucesso = _utc(ultimo_sucesso)

    if sucesso is None:
        return STATUS_INDISPONIVEL
    if provedores_previstos and provedores_ok <= 0:
        return STATUS_INDISPONIVEL

    idade = (agora - sucesso).total_seconds() / 60.0
    if idade > cad.sla_min:
        return STATUS_ATRASADO

    degradou = (parcial or usou_cache_vencido
                or (provedores_previstos and provedores_ok < provedores_previstos))
    return STATUS_DEGRADADO if degradou else STATUS_ATUALIZADO


def permite_recomendacao_emergencial(status_atual: str) -> bool:
    """Só dado atualizado sustenta recomendação de emergência.

    Não esconde nem apaga o resto: o painel continua exibindo o que foi
    coletado, com o carimbo de atraso. O que fica bloqueado é a recomendação
    que se apresentaria como urgente apoiada em dado vencido.
    """
    return status_atual not in STATUS_SEM_RECOMENDACAO_EMERGENCIAL
