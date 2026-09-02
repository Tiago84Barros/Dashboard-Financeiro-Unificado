"""Camada de provedores: contrato, falha, cota, cache e segredo.

Cobre os cenários exigidos "API indisponível" e "rate limit". Nenhum teste
aqui toca a rede: o transporte é injetado, o que é a razão de o protocolo
``Transporte`` existir. A suíte bloqueia socket para endereço não-loopback
(``tests/conftest.py``), então um teste que vazasse para a rede falharia com
``RuntimeError`` em vez de passar em silêncio.
"""
from __future__ import annotations

import json
import logging

import pytest

from core.noticias.cache import CacheMemoria
from core.noticias.provedores import registro
from core.noticias.provedores.alphavantage import AlphaVantage
from core.noticias.provedores.base import (
    ORIGEM_CACHE,
    ORIGEM_CACHE_VENCIDO,
    ORIGEM_REDE,
    Consulta,
    ProvedorIndisponivel,
    RespostaInvalida,
)
from core.noticias.provedores.marketaux import Marketaux
from core.noticias.rate_limit import Limite, LimiteExcedido, Orcamento
from core.noticias.transporte import (
    ErroTransporte,
    Redator,
    Resposta,
    TransporteFalso,
)
from tests.apoio_noticias import AGORA

CHAVE = "CHAVE-DE-TESTE-NAO-E-CREDENCIAL-REAL"

CARGA_AV = {
    "items": "2",
    "feed": [
        {
            "title": "Alfa reporta lucro acima do esperado no trimestre",
            "url": "https://www.reuters.com/markets/alfa-lucro/",
            "time_published": "20260901T1030",
            "authors": ["Fulana de Tal", "Beltrano de Tal"],
            "summary": "A companhia divulgou receita e margem em alta.",
            "source": "Reuters",
            "overall_sentiment_score": "0.31",
            "overall_sentiment_label": "Somewhat-Bullish",
            "topics": [
                {"topic": "Earnings", "relevance_score": "0.9"},
                {"topic": "Technology", "relevance_score": "0.3"},
            ],
            "ticker_sentiment": [
                {"ticker": "alfa", "ticker_sentiment_score": "0.44"},
            ],
        },
        # Sem URL: descartado de propósito, porque sem URL não há dedup.
        {"title": "Item incompleto", "url": ""},
    ],
}

CARGA_MX = {
    "data": [
        {
            "uuid": "0000-teste",
            "title": "Alfa anuncia acordo de fusao com a Beta",
            "description": "As companhias comunicaram o acordo ao mercado.",
            "url": "https://valor.globo.com/empresas/alfa-beta/",
            "source": "valor.globo.com",
            "published_at": "2026-09-01T10:00:00.000000Z",
            "language": "pt",
            "relevance_score": 0.8,
            "entities": [
                {"symbol": "ALFA3", "name": "Alfa S.A.", "country": "br",
                 "industry": "Industrials", "sentiment_score": 0.4},
                {"symbol": "BETA4", "name": "Beta S.A.", "country": "br",
                 "industry": "Industrials"},
            ],
        }
    ]
}


def _ok(carga) -> Resposta:
    return Resposta(status=200, texto=json.dumps(carga))


def _av(transporte, **kw) -> AlphaVantage:
    kw.setdefault("chave", CHAVE)
    kw.setdefault("dormir", lambda _s: None)
    return AlphaVantage(transporte, **kw)


# ── contrato e extração ──────────────────────────────────────────────────────

def test_alphavantage_extrai_os_campos_que_a_api_entrega():
    transporte = TransporteFalso([_ok(CARGA_AV)])
    resposta = _av(transporte).buscar(Consulta(tickers=("ALFA",)))

    assert resposta.origem == ORIGEM_REDE
    assert len(resposta.itens) == 1, "item sem URL tem de ser descartado"

    item = resposta.itens[0]
    assert item.titulo.startswith("Alfa reporta lucro")
    assert item.veiculo == "Reuters"
    assert item.autor == "Fulana de Tal, Beltrano de Tal"
    assert item.publicado_em == "20260901T1030"
    assert item.tickers == ("ALFA",)
    assert item.sentimento_api == pytest.approx(0.31)
    assert item.rotulo_sentimento == "Somewhat-Bullish"
    # Aderência é o máximo dos tópicos, não a média: matéria muito aderente a
    # um tema não perde ponto por tocar de leve em outro.
    assert item.relevancia_api == pytest.approx(0.9)
    assert item.bruto["ticker_sentiment"] == {"ALFA": 0.44}


def test_marketaux_cumpre_o_mesmo_contrato_com_outro_formato():
    """O contrato só se prova com o segundo implementador."""
    transporte = TransporteFalso([_ok(CARGA_MX)])
    resposta = Marketaux(transporte, chave=CHAVE).buscar(Consulta())

    item = resposta.itens[0]
    assert item.tickers == ("ALFA3", "BETA4")
    assert item.empresas == ("Alfa S.A.", "Beta S.A.")
    assert item.idioma == "pt"
    assert item.pais == "BR"
    # Média só dos escores presentes. Entidade sem escore não entra como zero.
    assert item.sentimento_api == pytest.approx(0.4)


def test_a_chave_vai_nos_parametros_e_nao_no_caminho_da_url():
    transporte = TransporteFalso([_ok(CARGA_AV)])
    _av(transporte).buscar(Consulta(tickers=("ALFA",), temas=("earnings",)))

    url, params = transporte.chamadas[0]
    assert CHAVE not in url
    assert params["apikey"] == CHAVE
    assert params["tickers"] == "ALFA"
    assert params["topics"] == "earnings"


def test_filtro_nao_suportado_vira_limitacao_declarada():
    """Filtro ignorado em silêncio faz a tela prometer um recorte inexistente."""
    transporte = TransporteFalso([_ok(CARGA_AV)])
    consulta = Consulta(paises=("br",), idiomas=("pt",))
    resposta = _av(transporte).buscar(consulta)

    assert len(resposta.limitacoes) == 2
    assert any("paises" in lim for lim in resposta.limitacoes)
    assert any("idiomas" in lim for lim in resposta.limitacoes)

    # Marketaux aplica os dois filtros: nada a declarar.
    outro = TransporteFalso([_ok(CARGA_MX)])
    assert Marketaux(outro, chave=CHAVE).buscar(consulta).limitacoes == ()


# ── API indisponível ─────────────────────────────────────────────────────────

def test_sem_chave_o_provedor_se_declara_indisponivel_sem_gastar_chamada():
    transporte = TransporteFalso([])
    provedor = AlphaVantage(transporte, chave=None)

    assert provedor.disponivel() is False
    with pytest.raises(ProvedorIndisponivel):
        provedor.buscar(Consulta())
    assert transporte.chamadas == []


def test_credencial_recusada_nao_e_retentada():
    """Insistir contra um 401 gasta cota para chegar ao mesmo 401."""
    transporte = TransporteFalso([Resposta(status=401, texto="unauthorized")])

    with pytest.raises(ProvedorIndisponivel) as erro:
        _av(transporte).buscar(Consulta())

    assert erro.value.retentavel is False
    assert erro.value.status == 401
    assert len(transporte.chamadas) == 1


def test_erro_do_servidor_e_retentado_ate_o_limite_de_tentativas():
    esperas: list[float] = []
    transporte = TransporteFalso([Resposta(status=500, texto="")] * 3)
    provedor = _av(transporte, tentativas=3, dormir=esperas.append)

    with pytest.raises(ErroTransporte) as erro:
        provedor.buscar(Consulta())

    assert erro.value.retentavel is True
    assert len(transporte.chamadas) == 3
    assert len(esperas) == 2, "espera entre tentativas, nao depois da ultima"
    # Espera exponencial com jitter em [0,5; 1,0]: o ciclo 2 parte de 2s e o
    # ciclo 1 de 1s, entao a janela do segundo comeca onde a do primeiro acaba.
    assert esperas[0] > 0 and esperas[1] >= esperas[0]


def test_erro_da_api_com_http_200_vira_excecao_e_nao_coleta_vazia():
    """O modo de falha mais perigoso desta API: erro embrulhado em 200."""
    transporte = TransporteFalso([_ok({"Error Message": "invalid apikey"})])
    with pytest.raises(ProvedorIndisponivel):
        _av(transporte).buscar(Consulta())


def test_resposta_sem_a_chave_esperada_e_invalida():
    transporte = TransporteFalso([_ok({"algo": "inesperado"})])
    with pytest.raises(RespostaInvalida):
        _av(transporte).buscar(Consulta())


def test_json_malformado_e_invalido_e_nao_retentavel():
    transporte = TransporteFalso([Resposta(status=200, texto="{nao e json")])
    with pytest.raises(RespostaInvalida) as erro:
        _av(transporte).buscar(Consulta())
    assert erro.value.retentavel is False


# ── rate limit ───────────────────────────────────────────────────────────────

def test_estouro_de_cota_com_http_200_vira_limite_excedido():
    aviso = ("Thank you for using Alpha Vantage! Our standard API rate limit "
             "is 25 requests per day.")
    transporte = TransporteFalso([_ok({"Information": aviso})])
    with pytest.raises(LimiteExcedido):
        _av(transporte).buscar(Consulta())


def test_marketaux_traduz_o_codigo_de_cota_da_propria_api():
    carga = {"error": {"code": "usage_limit_reached", "message": "limite"}}
    transporte = TransporteFalso([_ok(carga)])
    with pytest.raises(LimiteExcedido):
        Marketaux(transporte, chave=CHAVE).buscar(Consulta())


def test_o_freio_local_bloqueia_antes_de_gastar_a_requisicao():
    """Descobrir o limite pelo 429 do servidor custa a cota que já foi gasta."""
    orcamento = Orcamento(
        limites={"alphavantage": Limite(por_minuto=None, por_dia=2)},
        caminho=None, agora=lambda: AGORA, persistir=False)
    transporte = TransporteFalso([_ok(CARGA_AV), _ok(CARGA_AV), _ok(CARGA_AV)])
    provedor = _av(transporte, orcamento=orcamento)

    provedor.buscar(Consulta(tickers=("A",)))
    provedor.buscar(Consulta(tickers=("B",)))
    with pytest.raises(LimiteExcedido) as erro:
        provedor.buscar(Consulta(tickers=("C",)))

    assert len(transporte.chamadas) == 2, "a terceira nao pode chegar a rede"
    assert erro.value.retentavel is False, "esperar aqui seria esperar horas"
    assert erro.value.liberado_em is not None


def test_o_orcamento_conta_a_familia_e_nao_a_instancia():
    """Vários feeds RSS dividem a cota do serviço, não têm uma cada."""
    orcamento = Orcamento(limites={"rss": Limite(por_dia=1)}, caminho=None,
                          agora=lambda: AGORA, persistir=False)
    orcamento.registrar("rss")
    assert orcamento.permite("rss") is False


# ── cache ────────────────────────────────────────────────────────────────────

def test_cache_dentro_do_prazo_evita_a_segunda_requisicao():
    cache = CacheMemoria(ttl_s=900.0, agora=lambda: AGORA)
    transporte = TransporteFalso([_ok(CARGA_AV)])
    provedor = _av(transporte, cache=cache)
    consulta = Consulta(tickers=("ALFA",))

    primeira = provedor.buscar(consulta)
    segunda = provedor.buscar(consulta)

    assert primeira.origem == ORIGEM_REDE
    assert segunda.origem == ORIGEM_CACHE
    assert len(transporte.chamadas) == 1
    assert segunda.itens == primeira.itens


def test_consultas_diferentes_nao_compartilham_entrada_de_cache():
    cache = CacheMemoria(ttl_s=900.0, agora=lambda: AGORA)
    transporte = TransporteFalso([_ok(CARGA_AV), _ok(CARGA_AV)])
    provedor = _av(transporte, cache=cache)

    provedor.buscar(Consulta(tickers=("ALFA",)))
    provedor.buscar(Consulta(tickers=("BETA",)))
    assert len(transporte.chamadas) == 2


def test_cache_vencido_so_sai_pela_porta_que_o_rotula_como_vencido():
    relogio = {"t": AGORA}
    cache = CacheMemoria(ttl_s=900.0, agora=lambda: relogio["t"])
    transporte = TransporteFalso([
        _ok(CARGA_AV),
        ErroTransporte("rede caiu", retentavel=False),
    ])
    provedor = _av(transporte, cache=cache)
    consulta = Consulta(tickers=("ALFA",))

    provedor.buscar(consulta)
    relogio["t"] = AGORA.replace(hour=14)          # duas horas depois

    # A porta normal NÃO devolve o vencido: ela vai à rede e a rede falhou.
    with pytest.raises(ErroTransporte):
        provedor.buscar(consulta)

    degradada = provedor.do_cache_vencido(consulta)
    assert degradada is not None
    assert degradada.origem == ORIGEM_CACHE_VENCIDO
    assert degradada.degradado is True
    assert degradada.dados_de == AGORA


def test_sem_nada_guardado_o_cache_vencido_devolve_nada():
    """``None`` vira "fonte indisponível"; lista vazia viraria "sem noticia"."""
    provedor = _av(TransporteFalso([]),
                   cache=CacheMemoria(ttl_s=900.0, agora=lambda: AGORA))
    assert provedor.do_cache_vencido(Consulta()) is None


# ── segredo nunca em log ─────────────────────────────────────────────────────

def test_a_chave_nunca_aparece_no_log_nem_quando_vem_dentro_do_erro(caplog):
    vazamento = ErroTransporte(
        f"falha ao conectar em https://api/x?apikey={CHAVE}", retentavel=True)
    transporte = TransporteFalso([vazamento, _ok(CARGA_AV)])

    with caplog.at_level(logging.DEBUG):
        resposta = _av(transporte, tentativas=3).buscar(Consulta())

    assert resposta.origem == ORIGEM_REDE
    assert CHAVE not in caplog.text
    assert "***" in caplog.text


def test_redator_ignora_segredo_curto_demais_para_mascarar():
    """Mascarar 'abc' apagaria trechos legítimos e tornaria o log inútil."""
    assert Redator(["abc"])("abc definido") == "abc definido"
    assert Redator(["segredo-longo-o-bastante"])("x segredo-longo-o-bastante") \
        == "x ***"


# ── registro: quem é construído e por quê ────────────────────────────────────

class _ConfigFalsa:
    """Config sintética: os testes não podem depender do .env do usuário."""

    def __init__(self, provedores, chaves):
        self.provedores_noticias = tuple(provedores)
        self._chaves = dict(chaves)

    def chave_noticias(self, provedor):
        return self._chaves.get(provedor)


def test_provedor_sem_chave_nao_e_instanciado():
    cfg = _ConfigFalsa(("alphavantage", "marketaux"),
                       {"alphavantage": CHAVE})
    construidos = registro.construir(
        transporte=TransporteFalso([]), config=cfg)

    assert [p.nome for p in construidos] == ["alphavantage"]


def test_a_ordem_configurada_e_a_ordem_de_tentativa():
    cfg = _ConfigFalsa(("marketaux", "alphavantage"),
                       {"alphavantage": CHAVE, "marketaux": CHAVE})
    construidos = registro.construir(
        transporte=TransporteFalso([]), config=cfg)
    assert [p.nome for p in construidos] == ["marketaux", "alphavantage"]


def test_provedor_desconhecido_e_ignorado_sem_derrubar_a_montagem():
    cfg = _ConfigFalsa(("nao_existe", "alphavantage"), {"alphavantage": CHAVE})
    construidos = registro.construir(
        transporte=TransporteFalso([]), config=cfg)
    assert [p.nome for p in construidos] == ["alphavantage"]


def test_descrever_diz_o_que_falta_sem_revelar_a_chave():
    cfg = _ConfigFalsa(("alphavantage", "marketaux", "rss"),
                       {"alphavantage": CHAVE})
    situacoes = {s.nome: s for s in registro.descrever(config=cfg)}

    assert situacoes["alphavantage"].disponivel is True
    assert situacoes["marketaux"].disponivel is False
    assert situacoes["marketaux"].motivo == "chave nao configurada"
    assert situacoes["rss"].exige_chave is False
    assert CHAVE not in repr(situacoes)
