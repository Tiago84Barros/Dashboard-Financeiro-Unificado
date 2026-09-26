"""Quem comprou a ação não é o assunto da notícia sobre a ação.

O defeito, medido
-----------------
Em 26/09/2026, reprocessando o acervo local (22.785 itens) com o resolvedor, o
ramo por nome atribuía gestoras e bancos que só apareciam como **detentor** em
manchete de 13F: "Bank of America Corp DE Buys 177,853 Shares of Lemonade,
Inc. $LMND" saía com BAC; "ONEOK, Inc. $OKE Stake Raised by Envestnet Asset
Management" levava STT pelo resumo. Pelo detentor na manchete eram 234
atribuições (BAC 139, STT 81, BNY 7, BLK 1); pelo resumo desses itens, mais
202 (BLK 108, STT 29, BAC 12, BNY 8).

Mesma família do crédito da foto (``memoria: nome-citado-nao-e-sujeito``). O
que este arquivo cobra é a fronteira: o detentor sai, o emissor fica, e o
molde não engole manchete em que o "detentor" é o assunto.
"""
from __future__ import annotations

import pytest

from core.noticias import universo_entidades as ue
from core.noticias.entidades import Universo, relato_de_posicao, resolver

_UNIVERSO = Universo.de_pares({
    "BAC": "Bank of America",
    "BLK": "BlackRock",
    "STT": "State Street",
    "BNY": "Bank of New York Mellon",
    "LMND": "Lemonade",
    "OKE": "ONEOK",
    "RSG": "Republic Services",
    "NVDA": "Nvidia",
    "GS": "Goldman Sachs Group",
    "MDT": "Medtronic",
})


def _tickers(titulo: str, resumo: str | None = None) -> set[str]:
    return set(resolver(titulo, resumo, universo=_UNIVERSO).tickers)


# --------------------------------------------------------------------------
# O detentor não vira assunto


@pytest.mark.parametrize("titulo, emissor", [
    ("Bank of America Corp DE Buys 177,853 Shares of Lemonade, Inc. $LMND",
     "LMND"),
    ("ONEOK, Inc. $OKE Stake Raised by Envestnet Asset Management", "OKE"),
    ("320,779 Shares in Lemonade, Inc. $LMND Bought by Bank of New York "
     "Mellon Corp", "LMND"),
    ("State Street Corp Has $7.53 Billion Position in ONEOK, Inc. $OKE", "OKE"),
    ("BlackRock Inc. Takes $1.69 Million Position in Lemonade, Inc. $LMND",
     "LMND"),
    ("Cascade Investment, L.L.C. Purchases 311,304 Shares of Republic "
     "Services (NYSE:RSG) Stock", "RSG"),
])
def test_detentor_na_manchete_nao_e_atribuido(titulo, emissor):
    assert _tickers(titulo) == {emissor}


def test_resumo_de_13f_nao_atribui_os_outros_detentores():
    """O resumo do 13F é lista de detentores e de casas de análise.

    Foi daí que vieram 108 das 142 atribuições por nome a BLK no acervo.
    """
    titulo = ("QRG Capital Management Inc. Sells 6,359 Shares of Republic "
              "Services, Inc. $RSG")
    resumo = ("QRG Capital Management reduced its stake in Republic Services. "
              "Other institutional investors like BlackRock and State Street "
              "also adjusted their positions; Goldman Sachs Group rated it Buy.")
    assert _tickers(titulo, resumo) == {"RSG"}


def test_emissor_marcado_entra_mesmo_sem_casar_pelo_nome():
    """37 dos 972 relatos com emissor conhecido só casavam o nome pelo resumo.

    Cortar o resumo sem aceitar a marca ``$MDT`` trocaria o falso positivo do
    detentor por um falso negativo do assunto.
    """
    assert _tickers("Amundi Has $455.62 Million Stock Holdings in Medtronic "
                    "PLC $MDT") == {"MDT"}


def test_marca_fora_do_universo_nao_vira_ticker():
    assert _tickers("Bank of America Corp DE Sells 1,000 Shares of Foo Inc. "
                    "$ZZZZ") == set()


def test_declarado_pelo_provedor_nao_reintroduz_o_detentor():
    """A Alpha Vantage declara o que lê no mesmo resumo.

    Caso real do cache: "QRG ... Shares of Republic Services, Inc. $RSG"
    chegou com ``ticker_sentiment`` = RSG, BLK, WBA.
    """
    entidades = resolver(
        "QRG Capital Management Inc. Sells 6,359 Shares of Republic "
        "Services, Inc. $RSG",
        tickers_declarados=("RSG", "BLK", "STT"), universo=_UNIVERSO)
    assert entidades.tickers == ("RSG",)


def test_declarado_fora_do_molde_continua_valendo():
    entidades = resolver("Markets rally as banks report earnings",
                         tickers_declarados=("BAC", "STT"),
                         universo=_UNIVERSO)
    assert entidades.tickers == ("BAC", "STT")


# --------------------------------------------------------------------------
# A fronteira: o molde não engole o assunto


@pytest.mark.parametrize("titulo, esperado", [
    # Insider: a empresa é o assunto e vem antes do verbo.
    ("Nvidia (NVDA) Board Member Sells $410 Million of Company Stock",
     {"NVDA"}),
    # Investimento de verdade, sem marca de emissor: o investidor é assunto.
    ("Goldman Sachs Group Invests $400 Million in Cyera", {"GS"}),
    # Aquisição: o comprador é assunto, e não há substantivo de posição.
    ("Lemonade, Inc. $LMND Acquired by BlackRock", {"LMND", "BLK"}),
    # O banco como emissor do 13F continua sendo o banco.
    ("Kintra Wealth LLC Increases Holdings in Bank of America Corporation $BAC",
     {"BAC"}),
    ("BlackRock reduces stake in Lemonade", {"BLK", "LMND"}),
])
def test_fora_do_molde_nada_muda(titulo, esperado):
    assert _tickers(titulo) == esperado


def test_relato_separa_detentor_e_emissor():
    relato = relato_de_posicao(
        "ONEOK, Inc. $OKE Stake Raised by Envestnet Asset Management")
    assert relato is not None
    assert relato.detentor == "Envestnet Asset Management"
    assert relato.emissor == "ONEOK, Inc. $OKE"
    assert relato.marcados == ("OKE",)


# --------------------------------------------------------------------------
# "news" é substantivo comum, não a News Corp


class _Result:
    def __init__(self, linhas):
        self._linhas = linhas

    def mappings(self):
        return iter(self._linhas)

    def __iter__(self):
        return iter(())


class _Engine:
    def __init__(self, linhas):
        self._linhas = linhas

    def connect(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, *a, **k):
        return _Result(self._linhas if "market_us" in str(sql) else [])


@pytest.fixture
def universo_news():
    ue.limpar_cache()
    u, _ = ue.carregar(engine=_Engine([
        {"ticker": "NWS", "nome": "NEWS CORP", "setor": "Media"},
        {"ticker": "MRCY", "nome": "MERCURY SYSTEMS INC", "setor": "Defense"},
    ]), usar_cache=False)
    yield u
    ue.limpar_cache()


def test_palavra_news_nao_atribui_news_corp(universo_news):
    """73 atribuições a NWS no acervo, 7 da News Corp."""
    titulo = ("Is Mercury Systems (MRCY) Undervalued On Its Governance "
              "Settlement News?")
    assert set(resolver(titulo, universo=universo_news).tickers) == {"MRCY"}
    assert resolver("Breakfast News: How Fools Are Investing Right Now",
                    universo=universo_news).tickers == ()


@pytest.mark.parametrize("titulo", [
    "News Corp stock slips 1.7 percent ahead of the open",
    "Is News Corporation Stock Underperforming the S&P 500?",
])
def test_news_corp_pelo_nome_inteiro_continua(universo_news, titulo):
    assert resolver(titulo, universo=universo_news).tickers == ("NWS",)
