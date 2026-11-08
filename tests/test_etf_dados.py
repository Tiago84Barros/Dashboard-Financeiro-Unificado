"""Dados de ETF (taxa, composição, setores, liquidez) para a Inteligência dos
Ativos. Antes, `fontes_fundamentos.dados_etf` devolvia `{}` e os oito
indicadores de ETF saíam "Dado não disponível.". Sem rede: o yfinance é dublê.
"""
import gzip
import json
from datetime import datetime, timezone

import pytest

from core.inteligencia_ativos import etf_dados as ed
from core.inteligencia_ativos import fontes_fundamentos as ff
from core.inteligencia_ativos import fundamentos as f
from core.inteligencia_ativos import informacoes as inf
from core.inteligencia_ativos import secoes
from core.inteligencia_ativos.modelos import InfoBasica
from scripts import publish_etf_dados as pub

AGORA = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)

INFO_SPY = {"longName": "SPDR S&P 500 ETF Trust", "category": "Large Blend",
            "totalAssets": 6.3e11, "averageVolume": 7.1e7, "currency": "USD",
            "yield": 0.0118, "netExpenseRatio": 0.0945,
            "longBusinessSummary": "The Trust seeks to provide investment results "
            "that, before expenses, correspond generally to the price and yield "
            "performance of the S&P 500 Index."}
FUNDOS_SPY = {
    "descricao": None, "visao": {"categoryName": "Large Blend",
                                 "family": "SPDR State Street"},
    "operacoes": {"Annual Report Expense Ratio": 0.000945},
    "top": [{"simbolo": "NVDA", "nome": "NVIDIA", "peso": 0.075},
            {"simbolo": "AAPL", "nome": "Apple", "peso": 0.068},
            {"simbolo": "MSFT", "nome": "Microsoft", "peso": 0.062}],
    "setores": {"technology": 0.33, "financial_services": 0.13, "energy": 0.0},
    "classes": {"stockPosition": 0.997, "cashPosition": 0.003},
}


# ── normalização ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("texto, indice", [
    (INFO_SPY["longBusinessSummary"], "S&P 500 Index"),
    ("The fund seeks to track the investment results of the MSCI EAFE Index, "
     "which measures...", "MSCI EAFE Index"),
    ("The fund invests in short-term Treasury bills.", None),
])
def test_indice_lido_da_descricao(texto, indice):
    assert ed.indice_da_descricao(texto) == indice


def test_taxa_converte_fracao_e_percentual_para_a_mesma_escala():
    # Relatório anual em fração, info em percentual: as duas viram 0,0945%.
    assert ed.expense_ratio_pct({"Annual Report Expense Ratio": 0.000945}, {}) == 0.0945
    assert ed.expense_ratio_pct({}, {"netExpenseRatio": 0.0945}) == 0.0945
    assert ed.expense_ratio_pct({}, {}) is None


def test_registro_ordena_posicoes_e_descarta_setor_zerado():
    reg = ed.extrair("spy", INFO_SPY, FUNDOS_SPY, gerado_em="2026-11-08")
    assert reg["simbolo"] == "SPY" and reg["indice"] == "S&P 500 Index"
    assert [t["simbolo"] for t in reg["top"]] == ["NVDA", "AAPL", "MSFT"]
    assert "energy" not in reg["setores"]
    assert reg["dividend_yield_pct"] == pytest.approx(1.18)


def test_indicadores_da_tela_saem_preenchidos_e_tracking_error_fica_ausente():
    reg = ed.extrair("SPY", INFO_SPY, FUNDOS_SPY, gerado_em="2026-11-08")
    dados = ed.dados_de(reg)
    assert dados["indice"].valor == "S&P 500 Index"
    assert dados["expense_ratio"].valor == 0.0945
    assert dados["composicao"].valor.startswith("Maiores posições: NVDA 7,5%")
    assert dados["concentracao"].valor == "3 maiores posições = 20,5% do fundo"
    assert "patrimônio US$ 630,0 bi" in dados["liquidez"].valor
    assert dados["exposicao_setorial"].valor.startswith("Technology 33,0%")
    # Categoria não é composição por país: vai com nota dizendo isso.
    assert "não composição por país" in dados["exposicao_geografica"].nota
    # Sem a série do índice, tracking error não é inventado.
    assert "tracking_error" not in dados
    montado = f.montar(f.ETF, dados, moeda="USD")
    assert montado is not None


def test_sem_registro_nada_e_inventado():
    assert ed.dados_de(None) == {}
    assert ed.dados_de({"simbolo": "X"}) == {}


# ── publicador ────────────────────────────────────────────────────────────────
def test_ticker_brasileiro_ganha_sufixo_do_yahoo_e_a_chave_fica_a_da_carteira():
    assert pub.simbolo_yahoo("IVVB11", "BRL") == "IVVB11.SA"
    assert pub.simbolo_yahoo("SPY", "USD") == "SPY"
    pedidos = []

    def baixador(s):
        pedidos.append(s)
        if s == "QUEBRA":
            raise RuntimeError("HTTP 404")
        return INFO_SPY, FUNDOS_SPY

    payload = pub.montar({"IVVB11": "BRL", "SPY": "USD", "QUEBRA": "USD"},
                         baixador=baixador, agora=AGORA)
    assert sorted(pedidos) == ["IVVB11.SA", "QUEBRA", "SPY"]
    assert set(payload["por_ticker"]) == {"IVVB11", "SPY"}
    assert "QUEBRA" in payload["falhas"]
    assert payload["gerado_em"] == "2026-11-08T12:00:00+00:00"


# ── leitura pela tela ─────────────────────────────────────────────────────────
@pytest.fixture()
def vitrine(tmp_path, monkeypatch):
    payload = pub.montar({"SPY": "USD"}, baixador=lambda s: (INFO_SPY, FUNDOS_SPY),
                         agora=AGORA)
    caminho = tmp_path / "etf_dados.json.gz"
    pub.serializar(payload, caminho)
    monkeypatch.setattr(ff, "ARQUIVO_ETF", caminho)
    return payload


def test_fundamentos_do_etf_leem_a_vitrine(vitrine):
    dados = ff.dados_etf({"ticker": "spy"})
    assert dados["expense_ratio"].valor == 0.0945
    assert ff.dados_etf({"ticker": "IEFA"}) == {}


def test_semente_versionada_e_legivel_e_vazia():
    with gzip.open("data/public/etf_dados.json.gz") as fh:
        semente = json.loads(fh.read())
    assert semente["por_ticker"] == {} or all(
        "simbolo" in v for v in semente["por_ticker"].values())


# ── notícias das maiores posições ─────────────────────────────────────────────
def _info(classe="ETF Internacional", ticker="SPY"):
    return InfoBasica(ticker=ticker, nome="SPDR", classe=classe,
                      classe_politica="exterior", subclasse=None, setor=None,
                      moeda="USD", valor_investido=None, valor_mercado=None,
                      peso_atual=5.0)


def _secao_sem_noticia():
    n = inf.Noticias(motivo="Dado não disponível. Nenhuma notícia vinculada a SPY.")
    return secoes.Secao(chave="noticias", titulo="Notícias", estado=secoes.SEM_DADOS,
                        resumo=n.motivo, dados=n.como_dict())


def test_etf_sem_noticia_traz_as_das_maiores_posicoes():
    noticia = inf.Noticia(headline="Nvidia beats estimates", date="2026-11-07",
                          source="acervo", url=None, impact_level=inf.MEDIUM,
                          affected_dimension=(), summary=None,
                          categoria="resultado", motivo="Resultado", ticker="NVDA")
    pedidos = []

    def leitor(tickers):
        pedidos.append(list(tickers))
        return (noticia,)

    reg = ed.extrair("SPY", INFO_SPY, FUNDOS_SPY, gerado_em="2026-11-08")
    out = secoes.noticias_do_etf(_secao_sem_noticia(), _info(), leitor=leitor,
                                 composicao=lambda t: reg)
    n = inf.Noticias.de_dict(out.dados)
    assert pedidos == [["NVDA", "AAPL", "MSFT"]]
    assert not n.itens and n.setor[0].headline == "Nvidia beats estimates"
    assert n.setor_rotulo == secoes.ROTULO_POSICOES_ETF + "SPY"


def test_acao_nao_ganha_noticias_de_posicoes():
    secao = _secao_sem_noticia()
    out = secoes.noticias_do_etf(secao, _info(classe="Ações BR", ticker="PETR4"),
                                 leitor=lambda t: pytest.fail("não devia ler"),
                                 composicao=lambda t: pytest.fail("não devia ler"))
    assert out is secao
