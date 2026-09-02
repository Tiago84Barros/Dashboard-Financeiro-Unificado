"""Adaptador do Alpha Vantage NEWS_SENTIMENT.

Escolhido como primeiro provedor com chave por um motivo verificável: é o único
dos três gratuitos que entrega, na mesma resposta, quase toda a lista de campos
exigida -- autor, resumo, domínio da fonte, tópicos com pontuação de aderência,
sentimento geral e sentimento *por ticker*. Os outros obrigariam a inventar
metade dos campos, e campo inventado é exatamente o que o ``AGENTS.md`` proíbe.

Duas armadilhas desta API, ambas tratadas aqui:

1. **Erro com HTTP 200.** Estouro de cota, chave inválida e parâmetro errado
   voltam como ``200`` com uma chave ``Note``, ``Information`` ou
   ``Error Message`` no corpo. Quem confia no status conclui que a coleta deu
   certo e grava zero notícias como se o dia não tivesse notícia nenhuma.
2. **``time_published`` sem fuso.** Vem como ``20260901T1230``, sem
   deslocamento. Tratamos como UTC e isso está registrado nas limitações -- é
   suposição, não fato documentado pelo provedor.
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
    _tupla,
)
from core.noticias.rate_limit import LimiteExcedido
from core.noticias.transporte import Resposta

URL = "https://www.alphavantage.co/query"

# Sinais de estouro de cota no corpo de uma resposta 200.
_MARCAS_COTA = ("call frequency", "rate limit", "premium", "requests per day",
                "higher api call")


class AlphaVantage(ProvedorBase):
    """Notícias com sentimento do Alpha Vantage."""

    nome = "alphavantage"
    # A API filtra por ticker, tópico e data, mas não por país nem por idioma:
    # o feed é essencialmente em inglês e voltado ao mercado americano.
    nao_suporta = ("paises", "idiomas")

    def disponivel(self) -> bool:
        return bool(self._chave)

    def _requisicao(self, consulta: Consulta) -> tuple[str, dict[str, object]]:
        if not self._chave:
            raise ProvedorIndisponivel(self.nome, "ALPHAVANTAGE_API_KEY ausente")
        params: dict[str, object] = {
            "function": "NEWS_SENTIMENT",
            "sort": "LATEST",
            # A API aceita até 1000; pedir mais do que se vai usar não custa
            # cota extra (é a mesma chamada) mas infla o payload cacheado.
            "limit": max(1, min(int(consulta.limite or 50), 1000)),
            "apikey": self._chave,
        }
        if consulta.tickers:
            params["tickers"] = ",".join(consulta.tickers)
        if consulta.temas:
            params["topics"] = ",".join(consulta.temas)
        if consulta.desde is not None:
            params["time_from"] = consulta.desde.strftime("%Y%m%dT%H%M")
        return URL, params

    def _carregar_json(self, resposta: Resposta) -> object:
        carga = super()._carregar_json(resposta)
        if not isinstance(carga, dict):
            raise RespostaInvalida(self.nome, "objeto esperado no topo")

        # Erro embrulhado em 200: precisa virar exceção tipada, senão o motor
        # registra uma coleta bem-sucedida com zero itens.
        aviso = _texto(carga.get("Note")) or _texto(carga.get("Information"))
        erro = _texto(carga.get("Error Message"))
        if erro:
            raise ProvedorIndisponivel(self.nome, f"recusado pela API: {erro}")
        if aviso:
            if any(m in aviso.lower() for m in _MARCAS_COTA):
                raise LimiteExcedido(self.nome)
            raise RespostaInvalida(self.nome, aviso)
        if "feed" not in carga:
            raise RespostaInvalida(self.nome, "resposta sem a chave feed")
        return carga

    def _extrair(self, carga: object) -> list[ItemBruto]:
        if not isinstance(carga, dict):
            raise RespostaInvalida(self.nome, "objeto esperado no topo")
        feed = carga.get("feed")
        if not isinstance(feed, list):
            raise RespostaInvalida(self.nome, "feed nao e lista")

        itens: list[ItemBruto] = []
        for cru in feed:
            if not isinstance(cru, dict):
                continue
            url = _texto(cru.get("url"))
            titulo = _texto(cru.get("title"))
            if not url or not titulo:
                # Sem URL não há dedup nem verificação; sem título não há
                # notícia. Descartar aqui é melhor do que propagar um registro
                # que nenhuma camada adiante consegue avaliar.
                continue

            tickers, sentimentos = self._por_ticker(cru.get("ticker_sentiment"))
            topicos, aderencia = self._topicos(cru.get("topics"))

            itens.append(ItemBruto(
                titulo=titulo,
                url=url,
                resumo=_texto(cru.get("summary")),
                veiculo=_texto(cru.get("source")),
                autor=", ".join(_tupla(cru.get("authors"))) or None,
                publicado_em=_texto(cru.get("time_published")),
                idioma=None,       # a API não declara; detectamos do texto
                pais=None,
                tickers=tickers,
                categorias=topicos,
                sentimento_api=_decimal(cru.get("overall_sentiment_score")),
                rotulo_sentimento=_texto(cru.get("overall_sentiment_label")),
                relevancia_api=aderencia,
                bruto={"ticker_sentiment": sentimentos},
            ))
        return itens

    @staticmethod
    def _por_ticker(valor: object) -> tuple[tuple[str, ...], dict[str, float]]:
        """Tickers citados e o sentimento de cada um.

        O sentimento por ticker é mais informativo que o geral: uma matéria
        sobre a compra de A por B costuma ser positiva para um lado e negativa
        para o outro, e o escore geral achata isso em algo próximo de zero.
        """
        if not isinstance(valor, list):
            return (), {}
        tickers: list[str] = []
        escores: dict[str, float] = {}
        for item in valor:
            if not isinstance(item, dict):
                continue
            simbolo = _texto(item.get("ticker"))
            if not simbolo:
                continue
            simbolo = simbolo.upper()
            if simbolo not in tickers:
                tickers.append(simbolo)
            escore = _decimal(item.get("ticker_sentiment_score"))
            if escore is not None:
                escores[simbolo] = escore
        return tuple(tickers), escores

    @staticmethod
    def _topicos(valor: object) -> tuple[tuple[str, ...], float | None]:
        """Tópicos e a maior aderência declarada.

        Guardamos o máximo e não a média: a média cai quando a API cita muitos
        tópicos secundários, e uma matéria muito aderente a um tema relevante
        não deve perder pontos por também tocar de leve em outros.
        """
        if not isinstance(valor, list):
            return (), None
        topicos: list[str] = []
        melhor: float | None = None
        for item in valor:
            if isinstance(item, dict):
                nome = _texto(item.get("topic"))
                escore = _decimal(item.get("relevance_score"))
            else:
                nome, escore = _texto(item), None
            if nome and nome not in topicos:
                topicos.append(nome)
            if escore is not None and (melhor is None or escore > melhor):
                melhor = escore
        return tuple(topicos), melhor
