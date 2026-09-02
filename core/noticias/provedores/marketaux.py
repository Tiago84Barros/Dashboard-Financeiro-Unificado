"""Adaptador do Marketaux.

Segundo provedor com chave. Existe por duas razões, e nenhuma delas é
redundância: ele cobre bolsas fora dos Estados Unidos (inclusive a B3, com
sufixo ``.SA``), e é o teste vivo de que a camada de abstração não ficou colada
no formato do primeiro provedor. Um contrato só se prova com o segundo
implementador.

Entrega país e setor por entidade, que o Alpha Vantage não dá; em troca, o
plano gratuito limita a três artigos por resposta. Isso está nas limitações,
não escondido: um teto de três muda o que a contagem de fontes independentes
consegue observar.
"""
from __future__ import annotations

from core.noticias.provedores.base import (
    Consulta,
    ItemBruto,
    ProvedorBase,
    ProvedorIndisponivel,
    RespostaInvalida,
    _decimal,
    _texto,
)
from core.noticias.rate_limit import LimiteExcedido
from core.noticias.transporte import Resposta

URL = "https://api.marketaux.com/v1/news/all"

_CODIGOS_COTA = frozenset({"usage_limit_reached", "rate_limit_reached"})
_CODIGOS_CREDENCIAL = frozenset({"invalid_api_token", "token_missing",
                                 "unauthorized"})


class Marketaux(ProvedorBase):
    """Notícias com entidades resolvidas do Marketaux."""

    nome = "marketaux"
    nao_suporta = ()

    def disponivel(self) -> bool:
        return bool(self._chave)

    def _requisicao(self, consulta: Consulta) -> tuple[str, dict[str, object]]:
        if not self._chave:
            raise ProvedorIndisponivel(self.nome, "MARKETAUX_API_KEY ausente")
        params: dict[str, object] = {
            "api_token": self._chave,
            "limit": max(1, min(int(consulta.limite or 3), 100)),
            # Só interessa matéria em que a entidade aparece de fato, não
            # citada de passagem no rodapé de mercado.
            "filter_entities": "true",
            "must_have_entities": "true",
        }
        if consulta.tickers:
            params["symbols"] = ",".join(consulta.tickers)
        if consulta.temas:
            params["search"] = " | ".join(consulta.temas)
        if consulta.idiomas:
            params["language"] = ",".join(consulta.idiomas)
        if consulta.paises:
            params["countries"] = ",".join(c.lower() for c in consulta.paises)
        if consulta.desde is not None:
            params["published_after"] = consulta.desde.strftime("%Y-%m-%dT%H:%M")
        return URL, params

    def _carregar_json(self, resposta: Resposta) -> object:
        carga = super()._carregar_json(resposta)
        if not isinstance(carga, dict):
            raise RespostaInvalida(self.nome, "objeto esperado no topo")
        erro = carga.get("error")
        if isinstance(erro, dict):
            codigo = (_texto(erro.get("code")) or "").lower()
            mensagem = _texto(erro.get("message")) or codigo or "sem detalhe"
            if codigo in _CODIGOS_COTA:
                raise LimiteExcedido(self.nome)
            if codigo in _CODIGOS_CREDENCIAL:
                raise ProvedorIndisponivel(self.nome, mensagem)
            raise RespostaInvalida(self.nome, mensagem)
        if "data" not in carga:
            raise RespostaInvalida(self.nome, "resposta sem a chave data")
        return carga

    def _extrair(self, carga: object) -> list[ItemBruto]:
        if not isinstance(carga, dict):
            raise RespostaInvalida(self.nome, "objeto esperado no topo")
        dados = carga.get("data")
        if not isinstance(dados, list):
            raise RespostaInvalida(self.nome, "data nao e lista")

        itens: list[ItemBruto] = []
        for cru in dados:
            if not isinstance(cru, dict):
                continue
            url = _texto(cru.get("url"))
            titulo = _texto(cru.get("title"))
            if not url or not titulo:
                continue

            entidades = self._entidades(cru.get("entities"))
            itens.append(ItemBruto(
                titulo=titulo,
                url=url,
                resumo=_texto(cru.get("description")) or _texto(cru.get("snippet")),
                veiculo=_texto(cru.get("source")),
                autor=None,     # o plano gratuito não devolve autoria
                publicado_em=_texto(cru.get("published_at")),
                idioma=_texto(cru.get("language")),
                pais=entidades["pais"],
                tickers=entidades["tickers"],
                empresas=entidades["empresas"],
                categorias=entidades["setores"],
                sentimento_api=entidades["sentimento"],
                rotulo_sentimento=None,
                relevancia_api=_decimal(cru.get("relevance_score")),
                bruto={"uuid": _texto(cru.get("uuid")),
                       "setores": list(entidades["setores"]),
                       "paises": list(entidades["paises"])},
            ))
        return itens

    @staticmethod
    def _entidades(valor: object) -> dict:
        """Consolida a lista de entidades da matéria.

        O sentimento agregado é a média dos escores presentes, e entidade sem
        escore simplesmente não entra na média -- não entra como zero. Contar
        ausência como neutro puxaria toda matéria com muitas entidades para
        perto de zero e faria parecer que o mercado nunca reage a nada.
        """
        vazio = {"tickers": (), "empresas": (), "setores": (), "paises": (),
                 "pais": None, "sentimento": None}
        if not isinstance(valor, list):
            return vazio

        tickers: list[str] = []
        empresas: list[str] = []
        setores: list[str] = []
        paises: list[str] = []
        escores: list[float] = []
        for item in valor:
            if not isinstance(item, dict):
                continue
            simbolo = _texto(item.get("symbol"))
            if simbolo and simbolo.upper() not in tickers:
                tickers.append(simbolo.upper())
            nome = _texto(item.get("name"))
            if nome and nome not in empresas:
                empresas.append(nome)
            setor = _texto(item.get("industry"))
            if setor and setor not in setores:
                setores.append(setor)
            pais = _texto(item.get("country"))
            if pais and pais.upper() not in paises:
                paises.append(pais.upper())
            escore = _decimal(item.get("sentiment_score"))
            if escore is not None:
                escores.append(escore)

        return {
            "tickers": tuple(tickers),
            "empresas": tuple(empresas),
            "setores": tuple(setores),
            "paises": tuple(paises),
            "pais": paises[0] if paises else None,
            "sentimento": (sum(escores) / len(escores)) if escores else None,
        }
