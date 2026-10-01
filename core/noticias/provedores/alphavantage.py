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

3. **``tickers`` cruza com E, não com OU.** A documentação não diz, e a
   intuição diz o contrário. Medido contra a API em 06/09/2026:

   ===============================  =======
   consulta                         itens
   ===============================  =======
   sem ticker                          50
   ``ADBE``                            50
   ``AAPL``                            50
   ``AAPL,MSFT``                       50
   ``AAPL,PETR4``                    **0**
   ``PETR4``                         **0**
   universo de 20 da carteira        **0**
   só os 9 americanos da carteira    **0**
   ===============================  =======

   Ou seja: a API devolve a **interseção**, e um único símbolo que ela não
   cobre zera a resposta inteira. Foi por isso que este provedor entregou
   **zero itens** ao acervo desde que existe, com chave válida e sem levantar
   erro nenhum -- ele respondia ``200`` com ``feed`` vazio, que é a resposta
   correta para a pergunta errada. Ausência que não parece falha é o modo de
   falha mais caro deste projeto.

4. **``ticker_sentiment`` lista quem é citado, não quem é o sujeito.** Uma
   matéria sobre a Palo Alto traz MSFT com ``relevance_score`` 0,60; outra
   sobre a Intel traz BLK, TSLA, AMD e MSFT, todos entre 0,57 e 0,65. Aceitar a
   lista inteira dava à MSFT, em 26/09/2026, 50 notícias em 7 dias, das quais
   32 não citavam a Microsoft -- e a nota conjuntural +33 saía de notícia de
   outra empresa. O corte está em :data:`RELEVANCIA_MINIMA_TICKER`, medido;
   ver :meth:`AlphaVantage._por_ticker`.
"""
from __future__ import annotations

import re

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

#: Relevância mínima para um ticker de ``ticker_sentiment`` ser atribuído à
#: notícia, quando ela cita mais de um. Medido em 26/09/2026 sobre 6.320 pares
#: (item, ticker) de 4.471 itens crus da API (18 a 26/09), contra um critério
#: independente -- o título ou o resumo citam o nome, a marca ou o símbolo:
#:
#: ============  ==========  ============
#: faixa          citam       não citam
#: ============  ==========  ============
#: < 0,40 (*)       391          336
#: 0,40 a 0,70      144        1.289
#: 0,70 a 0,95      331          184
#: >= 0,95        3.111          534
#: ============  ==========  ============
#:
#: A faixa de 0,55 a 0,65 é onde mora o ticker tangencial: MSFT em matéria da
#: Palo Alto, WFC em matéria do Bank of Montreal, e os que citam são o banco
#: que aparece como acionista ou o índice. A precisão dela é de 10%. O vale da
#: distribuição fica entre 0,65 e 0,70 (30 pares); 0,65 e 0,70 dão o mesmo
#: resultado, então o corte não depende de casa decimal.
#:
#: (*) A faixa baixa é quase toda de item com **um único** ticker -- aviso de
#: Form 4 da Fortinet, rating da UBS para a Autodesk, com relevância 0,30 --, e
#: ali o ticker é o sujeito. Por isso o ticker único passa sem corte: a API não
#: teria outro candidato, e o score baixo mede a extensão do texto, não o
#: sujeito. Com as duas regras, ficam 95,8% dos pares que citam a empresa e
#: saem 58% dos que não citam.
RELEVANCIA_MINIMA_TICKER = 0.70

#: A API resume o conteúdo da página, e quando a página veio vazia ou com erro
#: o resumo diz isso: "This article from Yahoo Finance is largely empty,
#: displaying an error message". O item não tem fato nenhum e, antes do corte
#: de relevância, levava NVDA, TSLA, AAPL e MSFT. Três em 7.083 no acervo em
#: 26/09/2026 -- raro, mas é ruído puro. As expressões são estreitas de
#: propósito: "placeholder" sozinho casa com aviso de Form 4, que é notícia.
_PAGINA_VAZIA = re.compile(
    r"\berror (?:page|message)\b"
    r"|\b(?:largely|mostly|essentially|entirely) (?:empty|blank)\b"
    r"|\bplaceholder or (?:an? )?(?:stub|error)\b"
    r"|\b(?:is|appears to be|seems to be) (?:an? )?(?:stub|blank page|empty page)\b"
    r"|\bcontent (?:is|was) (?:missing|unavailable|not available)\b"
    r"|\bno (?:actual|substantive) (?:article )?content\b",
    re.IGNORECASE,
)


def pagina_vazia(resumo: str | None) -> bool:
    """O resumo da API declara que a página não tinha conteúdo."""
    return bool(resumo and _PAGINA_VAZIA.search(resumo))


class AlphaVantage(ProvedorBase):
    """Notícias com sentimento do Alpha Vantage."""

    nome = "alphavantage"
    # A API filtra por ticker, tópico e data, mas não por país nem por idioma:
    # o feed é essencialmente em inglês e voltado ao mercado americano.
    nao_suporta = ("paises", "idiomas")

    def disponivel(self) -> bool:
        return bool(self._chave)

    def limitacoes(self, consulta: Consulta) -> tuple[str, ...]:
        """As do contrato, mais a interseção de tickers desta API."""
        saida = list(super().limitacoes(consulta))
        if len(consulta.tickers) > 1:
            saida.append(
                f"filtro tickers não aplicado por {self.nome}: a API cruza os "
                f"símbolos com E (interseção), e os {len(consulta.tickers)} "
                f"pedidos juntos devolvem zero; a consulta saiu ampla e a "
                f"atribuição ficou com o resolvedor de entidades")
        return tuple(saida)

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
        # Um só ticker é pergunta legítima; dois já são interseção, e a
        # carteira inteira é interseção vazia garantida. Com mais de um, a
        # consulta sai **ampla** e a atribuição fica com o resolvedor de
        # entidades, que já roda em ``coleta`` com o universo carregado. Feed
        # amplo com atribuição a jusante rende notícia; interseção de 20 nomes
        # rende zero. A troca vai declarada em ``limitacoes``, nunca calada.
        #
        # A alternativa -- uma requisição por ticker -- foi medida e recusada:
        # a cota gratuita é de 25 chamadas por dia (``rate_limit.LIMITES_PADRAO``)
        # e o ciclo roda a cada 30 a 60 min. Vinte chamadas por ciclo esgotariam
        # o dia na primeira hora e deixariam o provedor fora do ar no resto.
        if len(consulta.tickers) == 1:
            params["tickers"] = consulta.tickers[0]
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
            resumo = _texto(cru.get("summary"))
            if pagina_vazia(resumo):
                continue

            tickers, sentimentos, descartados = self._por_ticker(
                cru.get("ticker_sentiment"))
            topicos, aderencia = self._topicos(cru.get("topics"))

            itens.append(ItemBruto(
                titulo=titulo,
                url=url,
                resumo=resumo,
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
                # Os descartados ficam registrados: sumir com eles sem rastro
                # impediria medir o corte de novo quando a API mudar.
                bruto={"ticker_sentiment": sentimentos,
                       "tickers_tangenciais": descartados},
            ))
        return itens

    @staticmethod
    def _por_ticker(valor: object) -> tuple[
            tuple[str, ...], dict[str, float], dict[str, float | None]]:
        """Tickers que são sujeito da notícia, o sentimento de cada um e os
        descartados como tangenciais (com a relevância que tinham).

        O sentimento por ticker é mais informativo que o geral: uma matéria
        sobre a compra de A por B costuma ser positiva para um lado e negativa
        para o outro, e o escore geral achata isso em algo próximo de zero.

        Ticker único passa sempre; com mais de um, só os de relevância
        >= :data:`RELEVANCIA_MINIMA_TICKER`. Relevância ausente não passa no
        corte: ausência não é evidência de que o ticker seja o sujeito, e
        aceitá-la reabriria o defeito no dia em que a API parar de mandar o
        campo.
        """
        if not isinstance(valor, list):
            return (), {}, {}
        ordem: list[str] = []
        escores: dict[str, float] = {}
        relevancias: dict[str, float | None] = {}
        for item in valor:
            if not isinstance(item, dict):
                continue
            simbolo = _texto(item.get("ticker"))
            if not simbolo:
                continue
            simbolo = simbolo.upper()
            relevancia = _decimal(item.get("relevance_score"))
            if simbolo not in ordem:
                ordem.append(simbolo)
                relevancias[simbolo] = relevancia
            elif relevancia is not None:
                anterior = relevancias[simbolo]
                relevancias[simbolo] = (relevancia if anterior is None
                                        else max(anterior, relevancia))
            escore = _decimal(item.get("ticker_sentiment_score"))
            if escore is not None:
                escores[simbolo] = escore

        # ``CRYPTO:BTC`` e ``FOREX:USD`` não são empresa e não disputam o posto
        # de sujeito: a medição contou só os símbolos de ativo.
        if len([s for s in ordem if ":" not in s]) <= 1:
            return tuple(ordem), escores, {}
        tickers = tuple(
            s for s in ordem
            if (relevancias[s] or 0.0) >= RELEVANCIA_MINIMA_TICKER)
        descartados = {s: relevancias[s] for s in ordem if s not in tickers}
        return (tickers, {s: e for s, e in escores.items() if s in tickers},
                descartados)

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
