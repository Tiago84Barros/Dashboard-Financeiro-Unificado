"""Contrato dos provedores de notícia.

O motor conversa com este contrato, nunca com uma API concreta. Trocar Alpha
Vantage por Marketaux, ou acrescentar um quarto provedor, é escrever uma classe
nova aqui dentro -- nada em ``relevancia``, ``impacto``, ``coleta`` ou nas views
menciona o nome de uma API.

``ProvedorBase`` concentra tudo o que é igual entre provedores e fácil de errar
uma vez por adaptador: consulta ao cache, freio de cota ANTES da chamada,
retentativa só do que é retentável, tradução de status HTTP em erro tipado e
redação da chave em qualquer mensagem. Um adaptador novo implementa apenas
``_requisicao`` e ``_extrair``.

A chave de API nunca aparece em log. Ela viaja em query string nos três
provedores implementados, então o que é registrado é o nome do provedor e o
status -- jamais a URL montada.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from core.noticias.cache import Cache, chave_de
from core.noticias.rate_limit import Orcamento
from core.noticias.transporte import (
    TIMEOUT_PADRAO,
    ErroTransporte,
    Redator,
    Resposta,
    Transporte,
    com_backoff,
)

logger = logging.getLogger(__name__)

ORIGEM_REDE = "rede"
ORIGEM_CACHE = "cache"
ORIGEM_CACHE_VENCIDO = "cache_vencido"


class ProvedorIndisponivel(ErroTransporte):
    """Provedor não pode ser usado: sem chave, sem configuração, ou recusado.

    Separado de falha de rede de propósito. Rede cai e volta; chave ausente não
    volta sozinha, e insistir contra um 401 só queima cota do plano gratuito
    para chegar ao mesmo 401.
    """

    def __init__(self, provedor: str, motivo: str, status: int | None = None):
        super().__init__(f"{provedor}: {motivo}", status=status,
                         retentavel=False)
        self.provedor = provedor
        self.motivo = motivo


class RespostaInvalida(ErroTransporte):
    """A API respondeu 200 com algo que não é o que ela documenta."""

    def __init__(self, provedor: str, motivo: str):
        super().__init__(f"{provedor}: resposta invalida ({motivo})",
                         retentavel=False)
        self.provedor = provedor


@dataclass(frozen=True)
class Consulta:
    """O que se quer buscar, em termos que qualquer provedor entenda.

    Cada adaptador traduz para os parâmetros da API dele e ignora o que ela não
    suporta -- e o que foi ignorado sai em ``ProvedorBase.limitacoes`` para a
    tela poder dizer que o filtro de país, por exemplo, não foi aplicado.
    """

    tickers: tuple[str, ...] = ()
    temas: tuple[str, ...] = ()
    desde: datetime | None = None
    limite: int = 50
    idiomas: tuple[str, ...] = ()
    paises: tuple[str, ...] = ()

    def como_parametros(self) -> dict[str, str]:
        """Forma canônica, para chave de cache. Ordenada e sem ``None``."""
        return {
            "tickers": ",".join(sorted(self.tickers)),
            "temas": ",".join(sorted(self.temas)),
            "desde": self.desde.isoformat() if self.desde else "",
            "limite": str(self.limite),
            "idiomas": ",".join(sorted(self.idiomas)),
            "paises": ",".join(sorted(self.paises)),
        }


@dataclass(frozen=True)
class ItemBruto:
    """Um item como o provedor entregou, antes de qualquer juízo do APP4.

    Datas continuam como texto: converter é trabalho de ``normalizacao``, e
    manter o valor original permite auditar um parse suspeito depois.
    """

    titulo: str
    url: str
    resumo: str | None = None
    veiculo: str | None = None
    autor: str | None = None
    publicado_em: object = None          # texto/epoch como veio
    idioma: str | None = None
    pais: str | None = None
    tickers: tuple[str, ...] = ()
    empresas: tuple[str, ...] = ()
    categorias: tuple[str, ...] = ()     # temas/tópicos do provedor
    sentimento_api: float | None = None
    rotulo_sentimento: str | None = None
    relevancia_api: float | None = None
    bruto: dict = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class RespostaProvedor:
    """Resultado de uma busca, com a procedência explícita.

    ``origem`` importa tanto quanto os itens: exibir conteúdo de cache vencido
    sem dizer que é de cache vencido é o modo de falha que o requisito de
    frescor proíbe nominalmente.
    """

    provedor: str
    itens: tuple[ItemBruto, ...]
    origem: str
    consultado_em: datetime
    dados_de: datetime | None = None      # quando o payload foi obtido da rede
    limitacoes: tuple[str, ...] = ()

    @property
    def do_cache(self) -> bool:
        return self.origem in (ORIGEM_CACHE, ORIGEM_CACHE_VENCIDO)

    @property
    def degradado(self) -> bool:
        return self.origem == ORIGEM_CACHE_VENCIDO


@runtime_checkable
class Provedor(Protocol):
    """O que o motor exige de qualquer fonte de notícia."""

    nome: str

    def disponivel(self) -> bool:
        ...

    def buscar(self, consulta: Consulta) -> RespostaProvedor:
        ...


def _agora_utc() -> datetime:
    return datetime.now(timezone.utc)


class ProvedorBase:
    """Esqueleto com cache, cota, retentativa e redação de segredo."""

    nome = "base"
    #: filtros do contrato que este provedor não sabe aplicar
    nao_suporta: tuple[str, ...] = ()

    def __init__(self, transporte: Transporte, *,
                 orcamento: Orcamento | None = None,
                 cache: Cache | None = None,
                 chave: str | None = None,
                 ttl_s: float | None = None,
                 tentativas: int = 3,
                 timeout: float = TIMEOUT_PADRAO,
                 agora=_agora_utc,
                 dormir=None):
        self._transporte = transporte
        self._orcamento = orcamento
        self._cache = cache
        self._chave = chave or None
        self._ttl_s = ttl_s
        self._tentativas = tentativas
        self._timeout = timeout
        self._agora = agora
        self._dormir = dormir
        self._redator = Redator([chave])

    # -- a implementar pelo adaptador -------------------------------------
    def _requisicao(self, consulta: Consulta) -> tuple[str, dict[str, object]]:
        raise NotImplementedError

    def _extrair(self, carga: object) -> list[ItemBruto]:
        raise NotImplementedError

    # -- contrato ----------------------------------------------------------
    @property
    def familia(self) -> str:
        """Nome sob o qual a cota é contada.

        Um provedor pode ter várias instâncias -- é o caso do RSS, uma por
        feed, com nomes ``rss:infomoney``, ``rss:valor``. A cota é do serviço,
        não da instância, então o orçamento olha o prefixo. Contar por
        instância daria a cada feed um orçamento próprio e o freio deixaria de
        frear.
        """
        return self.nome.split(":", 1)[0]

    def disponivel(self) -> bool:
        """Provedor que exige chave sobrescreve para checar a chave."""
        return True

    def limitacoes(self, consulta: Consulta) -> tuple[str, ...]:
        """Filtros pedidos que este provedor não aplica.

        Sai junto com a resposta em vez de sumir: um filtro de país silenciosamente
        ignorado faz a tela prometer um recorte que os dados não têm.
        """
        pedidos = {
            "paises": bool(consulta.paises),
            "idiomas": bool(consulta.idiomas),
            "desde": consulta.desde is not None,
            "tickers": bool(consulta.tickers),
            "temas": bool(consulta.temas),
        }
        return tuple(f"filtro {campo} nao suportado por {self.nome}"
                     for campo in self.nao_suporta if pedidos.get(campo))

    def buscar(self, consulta: Consulta) -> RespostaProvedor:
        if not self.disponivel():
            raise ProvedorIndisponivel(self.nome, "nao configurado")

        chave_cache = chave_de(self.nome, consulta.como_parametros())
        if self._cache is not None:
            entrada = self._cache.obter(chave_cache, self._ttl_s)
            if entrada is not None:
                return RespostaProvedor(
                    provedor=self.nome,
                    itens=tuple(self._extrair(entrada.carga)),
                    origem=ORIGEM_CACHE,
                    consultado_em=self._agora(),
                    dados_de=entrada.gravado_em,
                    limitacoes=self.limitacoes(consulta),
                )

        url, params = self._requisicao(consulta)
        carga = self._buscar_na_rede(url, params)
        if self._cache is not None:
            self._cache.guardar(chave_cache, carga)

        agora = self._agora()
        return RespostaProvedor(
            provedor=self.nome,
            itens=tuple(self._extrair(carga)),
            origem=ORIGEM_REDE,
            consultado_em=agora,
            dados_de=agora,
            limitacoes=self.limitacoes(consulta),
        )

    def do_cache_vencido(self, consulta: Consulta) -> RespostaProvedor | None:
        """Última coleta guardada, mesmo fora do prazo, sempre rotulada.

        Só o orquestrador chama, e só quando o provedor falhou. Devolve
        ``None`` quando não há nada guardado -- e aí a tela diz "fonte
        indisponível", que é honesto, em vez de mostrar uma lista vazia que
        parece "nenhuma notícia".
        """
        if self._cache is None:
            return None
        entrada = self._cache.obter_vencida(chave_de(self.nome,
                                                     consulta.como_parametros()))
        if entrada is None:
            return None
        try:
            itens = tuple(self._extrair(entrada.carga))
        except ErroTransporte:
            return None
        return RespostaProvedor(
            provedor=self.nome,
            itens=itens,
            origem=ORIGEM_CACHE_VENCIDO if entrada.vencida else ORIGEM_CACHE,
            consultado_em=self._agora(),
            dados_de=entrada.gravado_em,
            limitacoes=self.limitacoes(consulta),
        )

    # -- rede ---------------------------------------------------------------
    def _buscar_na_rede(self, url: str, params: Mapping[str, object]) -> object:
        if self._orcamento is not None:
            self._orcamento.exigir(self.familia)   # levanta LimiteExcedido

        def uma_tentativa() -> Resposta:
            # Registra ANTES de sair: a chamada que estoura o timeout consumiu
            # cota do mesmo jeito, e nao contá-la faria o freio local mentir.
            if self._orcamento is not None:
                self._orcamento.registrar(self.familia)
            resposta = self._transporte.obter(url, params=params,
                                              timeout=self._timeout)
            self._conferir_status(resposta)
            return resposta

        extras = {}
        if self._dormir is not None:
            extras["dormir"] = self._dormir
        resposta = com_backoff(uma_tentativa, tentativas=self._tentativas,
                               redator=self._redator, rotulo=self.nome,
                               **extras)
        return self._carregar_json(resposta)

    def _conferir_status(self, resposta: Resposta) -> None:
        status = resposta.status
        if 200 <= status < 300:
            return
        if status in (401, 403):
            raise ProvedorIndisponivel(self.nome, "credencial recusada", status)
        if status == 429:
            raise ErroTransporte(f"{self.nome}: rate limit do servidor (429)",
                                 status=429, retentavel=True)
        if status >= 500:
            raise ErroTransporte(f"{self.nome}: erro do servidor ({status})",
                                 status=status, retentavel=True)
        raise ErroTransporte(f"{self.nome}: HTTP {status}", status=status,
                             retentavel=False)

    def _carregar_json(self, resposta: Resposta) -> object:
        try:
            return json.loads(resposta.texto or "")
        except ValueError as exc:
            raise RespostaInvalida(self.nome, f"JSON malformado: {exc}") from exc


def _texto(valor: object) -> str | None:
    """Normaliza campo textual opcional vindo de JSON de terceiro."""
    if valor is None:
        return None
    texto = str(valor).strip()
    return texto or None


def _tupla(valores: object) -> tuple[str, ...]:
    """Lista de strings, sem vazio e sem repetição, preservando a ordem."""
    if valores is None:
        return ()
    if isinstance(valores, (str, bytes)):
        valores = [valores]
    if not isinstance(valores, Sequence):
        return ()
    vistos: list[str] = []
    for item in valores:
        texto = _texto(item)
        if texto and texto not in vistos:
            vistos.append(texto)
    return tuple(vistos)


def _decimal(valor: object) -> float | None:
    """Número opcional. Texto não numérico vira ``None``, nunca ``0.0``.

    A diferença importa: ``0.0`` é sentimento neutro observado, ``None`` é
    ausência de medição -- e o índice de relevância trata os dois de forma
    diferente de propósito.
    """
    if valor is None or isinstance(valor, bool):
        return None
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None
