"""Transporte HTTP injetavel, retentativa com espera e redacao de segredos.

Por que nao reaproveitar ``data_pipeline.quality.scheduler.with_backoff``: ele
chama ``time.sleep`` de verdade, sorteia jitter de ``random`` global e nao tem
onde encaixar um relogio. Um teste de retentativa com ele custa segundos de
parede e nao consegue afirmar *quanto* se esperou. Aqui o adiamento e injetado,
entao o teste observa a sequencia de esperas sem dormir um milissegundo.

A suite bloqueia socket para endereco nao-loopback (`tests/conftest.py`), o que
so funciona se todo acesso a rede passar por uma dependencia substituivel. Esse
e o segundo motivo do protocolo `Transporte`.

**Nenhuma funcao daqui registra a URL crua.** Chave de API viaja em query string
nos tres provedores implementados; logar a URL vazaria a chave no arquivo de
log, que e exatamente o que o requisito 10 proibe. Toda mensagem passa por
`Redator`.
"""
from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)

TIMEOUT_PADRAO = 15.0
TENTATIVAS_PADRAO = 3
ESPERA_BASE = 1.0
ESPERA_TETO = 30.0

MASCARA = "***"


class ErroTransporte(Exception):
    """Falha ao falar com o provedor. Carrega se vale a pena tentar de novo."""

    def __init__(self, mensagem: str, *, status: int | None = None,
                 retentavel: bool = False):
        super().__init__(mensagem)
        self.status = status
        self.retentavel = retentavel


@dataclass(frozen=True)
class Resposta:
    """Resposta HTTP reduzida ao que os adaptadores usam."""

    status: int
    texto: str
    cabecalhos: Mapping[str, str] = field(default_factory=dict)
    url: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


@runtime_checkable
class Transporte(Protocol):
    """Contrato minimo de rede. Um GET, sem estado observavel."""

    def obter(self, url: str, *, params: Mapping[str, object] | None = None,
              cabecalhos: Mapping[str, str] | None = None,
              timeout: float = TIMEOUT_PADRAO) -> Resposta:
        ...


class Redator:
    """Substitui segredos conhecidos por ``***`` antes de qualquer log.

    Recebe os valores das chaves, nao os nomes: e o valor que aparece na URL.
    Segredo com menos de 8 caracteres e ignorado -- mascarar uma cadeia curta
    demais apagaria trechos legitimos da mensagem e ainda tornaria o log
    inutil para diagnostico.
    """

    def __init__(self, segredos: Iterable[str | None] = ()):
        self._segredos = sorted(
            {str(s) for s in segredos if s and len(str(s)) >= 8},
            key=len,
            reverse=True,
        )

    def __call__(self, texto: object) -> str:
        saida = str(texto)
        for segredo in self._segredos:
            saida = saida.replace(segredo, MASCARA)
        return saida


class TransporteRequests:
    """Implementacao real, sobre ``requests``.

    Importa ``requests`` no construtor e nao no topo do modulo para que o
    pacote continue importavel (e testavel) num ambiente sem a biblioteca.
    """

    def __init__(self, user_agent: str = "APP4-Dashboard-Financeiro/1.0",
                 sessao=None):
        if sessao is None:
            import requests  # noqa: PLC0415 - dependencia opcional no import
            sessao = requests.Session()
        self._sessao = sessao
        self._user_agent = user_agent

    def obter(self, url: str, *, params: Mapping[str, object] | None = None,
              cabecalhos: Mapping[str, str] | None = None,
              timeout: float = TIMEOUT_PADRAO) -> Resposta:
        cabs = {"User-Agent": self._user_agent}
        if cabecalhos:
            cabs.update(cabecalhos)
        try:
            resp = self._sessao.get(url, params=dict(params or {}),
                                    headers=cabs, timeout=timeout)
        except Exception as exc:  # rede, DNS, TLS, timeout
            raise ErroTransporte(
                f"falha de rede ({type(exc).__name__})", retentavel=True
            ) from exc
        return Resposta(
            status=int(resp.status_code),
            texto=resp.text or "",
            cabecalhos=dict(resp.headers or {}),
            url=url,
        )


class TransporteFalso:
    """Transporte de teste: responde de uma fila e registra as chamadas.

    Guarda ``url`` e ``params`` de cada chamada para o teste afirmar o que foi
    pedido -- inclusive que a chave *nao* apareceu em lugar nenhum de um log.
    """

    def __init__(self, respostas: Iterable[Resposta | Exception]):
        self._fila = list(respostas)
        self.chamadas: list[tuple[str, dict]] = []

    def obter(self, url: str, *, params: Mapping[str, object] | None = None,
              cabecalhos: Mapping[str, str] | None = None,
              timeout: float = TIMEOUT_PADRAO) -> Resposta:
        self.chamadas.append((url, dict(params or {})))
        if not self._fila:
            raise ErroTransporte("fila de respostas esgotada", retentavel=False)
        item = self._fila.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def espera_do_ciclo(tentativa: int, base: float = ESPERA_BASE,
                    teto: float = ESPERA_TETO,
                    jitter: Callable[[], float] | None = None) -> float:
    """Espera exponencial com jitter, limitada por ``teto``.

    ``tentativa`` comeca em 1. O jitter e injetavel para o teste ser
    determinístico; o padrao usa uma instancia propria de ``Random`` em vez do
    ``random`` global, para nao depender de -- nem perturbar -- a semente que
    outro modulo tenha fixado.
    """
    bruto = min(base * (2 ** max(0, tentativa - 1)), teto)
    fator = jitter() if jitter is not None else _RNG.uniform(0.5, 1.0)
    return max(0.0, bruto * fator)


_RNG = random.Random(20260901)


def com_backoff(
    fn: Callable[[], object],
    *,
    tentativas: int = TENTATIVAS_PADRAO,
    base: float = ESPERA_BASE,
    teto: float = ESPERA_TETO,
    dormir: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] | None = None,
    redator: Redator | None = None,
    rotulo: str = "",
):
    """Executa ``fn`` repetindo apenas o que e retentavel.

    Falha nao-retentavel (400, 401, 404, payload invalido) sobe na primeira
    ocorrencia: insistir contra um 401 gasta cota do plano gratuito para chegar
    ao mesmo 401. So 429, 5xx e falha de rede sao repetidos.
    """
    red = redator or Redator()
    ultima: Exception | None = None
    for tentativa in range(1, max(1, tentativas) + 1):
        try:
            return fn()
        except ErroTransporte as exc:
            ultima = exc
            if not exc.retentavel or tentativa >= tentativas:
                raise
            pausa = espera_do_ciclo(tentativa, base, teto, jitter)
            logger.warning(
                "%s: tentativa %d/%d falhou (%s); aguardando %.1fs",
                rotulo or "transporte", tentativa, tentativas,
                red(exc), pausa,
            )
            dormir(pausa)
    if ultima is not None:  # pragma: no cover - inalcancavel
        raise ultima
    raise ErroTransporte("nenhuma tentativa executada")
