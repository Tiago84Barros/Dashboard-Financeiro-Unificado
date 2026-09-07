"""Limites de uso: quanto se pode gastar antes de parar de gastar.

O item do requisito é "limites de uso", e ele protege três coisas diferentes
que costumam ser confundidas numa única palavra "rate limit":

1. **Custo.** Chamada de LLM é paga. Um laço acidental na coleta de notícias
   não pode virar fatura.
2. **A fonte.** Bater sem freio num provedor público é o caminho mais curto
   para ser bloqueado -- e aí o dado some para todo mundo, não só para quem
   errou.
3. **A superfície de ataque.** Conteúdo externo hostil que consegue provocar
   uma rodada de chamadas por notícia coletada vira amplificador. Limite por
   janela é o que impede a amplificação de crescer com o volume da coleta.

Janela deslizante, e não balde por hora cheia
----------------------------------------------
Contador que zera na virada da hora deixa passar o dobro do limite na fronteira:
gasta tudo às 10h59 e tudo de novo às 11h00. O erro é o mesmo de
``memoria: cadencia-em-horas-pula-dia`` visto do outro lado -- alinhar a régua a
um relógio de parede em vez de ao intervalo que se quer proteger.

Aqui a janela é deslizante: cada consumo carrega o seu instante, e o que saiu da
janela some do cálculo.

Sem relógio implícito
---------------------
Todo método aceita ``agora``. Teste que depende da hora em que roda é teste que
falha sozinho de madrugada -- foi exatamente o defeito corrigido em
``core/frescor.py`` (commit ``eee5a7a``).

Puro: sem rede, sem banco. O estado vive em memória do processo, e é isso que
se quer -- limite de uso não é dado de negócio, e persistir isso no Supabase
custaria espaço num banco que já está em 425 MB de 500 MB.
"""
from __future__ import annotations

import datetime as dt
import threading
from collections import deque
from dataclasses import dataclass, field

# ── Os limites nomeados ──────────────────────────────────────────────────────
LLM_EXPLICACAO = "llm_explicacao"
COLETA_NOTICIAS = "coleta_noticias"
CONSULTA_PRECO = "consulta_preco"
NOTIFICACAO_EXTERNA = "notificacao_externa"


@dataclass(frozen=True)
class Regra:
    """``maximo`` eventos a cada ``janela_s`` segundos.

    ``motivo`` não é enfeite: quando o limite estoura, alguém vê a mensagem e
    precisa decidir se o teto está errado ou se o chamador está. Sem o motivo
    escrito, a decisão default é subir o teto.
    """

    nome: str
    maximo: int
    janela_s: float
    motivo: str = ""

    def __post_init__(self) -> None:
        if self.maximo < 1 or self.janela_s <= 0:
            raise ValueError(
                f"limite {self.nome!r} inalcançável: maximo={self.maximo}, "
                f"janela_s={self.janela_s}. Limite que nunca deixa passar não "
                "é proteção, é desligamento silencioso.")


#: Tetos iniciais. São palpites informados, não medições -- e o requisito do
#: Prompt 3 vale aqui também: peso sugerido não é verdade definitiva. O que os
#: torna revisáveis é :meth:`Contador.pressao`, que publica o quanto de cada
#: teto está sendo usado de fato.
PADRAO: dict[str, Regra] = {
    LLM_EXPLICACAO: Regra(
        LLM_EXPLICACAO, maximo=30, janela_s=3600,
        motivo="a explicação é opcional; o painel funciona sem ela"),
    COLETA_NOTICIAS: Regra(
        COLETA_NOTICIAS, maximo=240, janela_s=3600,
        motivo="proteger a fonte pública de bloqueio por volume"),
    CONSULTA_PRECO: Regra(
        CONSULTA_PRECO, maximo=600, janela_s=3600,
        motivo="preço tem janela de validade; repetir dentro dela é desperdício"),
    NOTIFICACAO_EXTERNA: Regra(
        NOTIFICACAO_EXTERNA, maximo=6, janela_s=3600,
        motivo="alerta repetido sem mudança material treina o usuário a ignorar"),
}


@dataclass(frozen=True)
class Veredito:
    """Resposta de :meth:`Contador.permitir`."""

    nome: str
    permitido: bool
    usados: int
    maximo: int
    espera_s: float = 0.0
    motivo: str = ""

    def descrever(self) -> str:
        if self.permitido:
            return f"{self.nome}: {self.usados}/{self.maximo} na janela"
        base = (f"{self.nome}: limite de {self.maximo} atingido; "
                f"liberação em {self.espera_s:.0f}s")
        return f"{base} — {self.motivo}" if self.motivo else base


def _agora(agora: dt.datetime | None) -> dt.datetime:
    return agora or dt.datetime.now(dt.timezone.utc)


@dataclass
class Contador:
    """Janela deslizante por nome de limite.

    Thread-safe porque o Streamlit atende sessões em threads e a coleta roda
    fora da interface (Prompt 2). Duas threads chegando juntas no último slot
    é o caso comum, não o exótico.
    """

    regras: dict[str, Regra] = field(default_factory=lambda: dict(PADRAO))
    _eventos: dict[str, deque] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _negados: dict[str, int] = field(default_factory=dict, repr=False)

    def _limpar(self, nome: str, agora: dt.datetime) -> deque:
        regra = self.regras[nome]
        fila = self._eventos.setdefault(nome, deque())
        corte = agora - dt.timedelta(seconds=regra.janela_s)
        while fila and fila[0] <= corte:
            fila.popleft()
        return fila

    def permitir(self, nome: str, *, agora: dt.datetime | None = None,
                 consumir: bool = True) -> Veredito:
        """Consulta e -- por omissão -- consome um slot.

        ``consumir=False`` existe para a tela poder mostrar a pressão sem
        gastar o limite ao desenhar.
        """
        if nome not in self.regras:
            raise KeyError(
                f"limite {nome!r} não declarado. Criar limite implícito no "
                "ponto de uso esconde o teto de quem precisa revisá-lo.")
        instante = _agora(agora)
        regra = self.regras[nome]
        with self._lock:
            fila = self._limpar(nome, instante)
            if len(fila) >= regra.maximo:
                espera = regra.janela_s - (instante - fila[0]).total_seconds()
                if consumir:
                    self._negados[nome] = self._negados.get(nome, 0) + 1
                return Veredito(nome, False, len(fila), regra.maximo,
                                max(0.0, espera), regra.motivo)
            if consumir:
                fila.append(instante)
            return Veredito(nome, True, len(fila), regra.maximo)

    def pressao(self, nome: str, *, agora: dt.datetime | None = None) -> float:
        """Fração do teto em uso, de 0 a 1.

        É o número que diz se o teto está calibrado. Pressão cravada em 0,0
        durante semanas quer dizer que o limite nunca chega perto de disparar
        -- é decoração. Pressão cravada em 1,0 quer dizer que ele está no
        caminho do uso normal, e aí ele está errado, não o chamador.
        """
        with self._lock:
            fila = self._limpar(nome, _agora(agora))
            return len(fila) / self.regras[nome].maximo

    def negados(self, nome: str) -> int:
        return self._negados.get(nome, 0)

    def resumo_auditoria(self, *, agora: dt.datetime | None = None) -> dict:
        instante = _agora(agora)
        return {
            nome: {
                "pressao": round(self.pressao(nome, agora=instante), 3),
                "maximo": regra.maximo,
                "janela_s": regra.janela_s,
                "negados": self.negados(nome),
            }
            for nome, regra in self.regras.items()
        }


#: Contador do processo. Um só, porque limite por instância não limita nada.
GLOBAL = Contador()


def permitir(nome: str, *, agora: dt.datetime | None = None,
             consumir: bool = True) -> Veredito:
    return GLOBAL.permitir(nome, agora=agora, consumir=consumir)
