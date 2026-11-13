"""Ações emitidas conferidas contra o marketCap da brapi (acoes.acoes_emitidas).

A contagem errada da brapi (MILS3, BNBR3, TFCO4 em 29/10/2026) contaminava o
valor de mercado e, por ele, o P/L, o P/VP, o EV/EBIT e o P/FCO do ttm. A
conferência é a mesma regra do LPA do título (#727,
``core.brapi.escolher_pela_referencia``), e os testes cobrem a equivalência
entre os caminhos: P/L spot (preço ÷ LPA) = valor de mercado ÷ lucro.

MILS3 e TFCO4 são payloads reais de 29/10/2026; o resto é sintético.
"""
import pytest

from core import brapi
from data_pipeline.market import acoes as ac
from data_pipeline.market import ingest as ig
from data_pipeline.market import metrics as mx
from data_pipeline.market import normalize as nz

_MILS3 = {
    "symbol": "MILS3", "regularMarketPrice": 15.15, "priceEarnings": 7.9661373,
    "earningsPerShare": 1.9778641, "marketCap": 3_584_443_662,
    "defaultKeyStatistics": {"trailingEps": 9321.7014033, "sharesOutstanding": 48172,
                             "netIncomeToCommon": 449_045_000},
}
_TFCO4 = {
    "symbol": "TFCO4", "regularMarketPrice": 10.09, "priceEarnings": 10.4192482,
    "earningsPerShare": 0.9684276, "marketCap": 1_544_110_416,
    "defaultKeyStatistics": {"trailingEps": 0.1559320, "sharesOutstanding": 942_744_260,
                             "netIncomeToCommon": 147_004_000},
}
# PN com marketCap da companhia: a referência destoa, mas os dois campos da
# brapi concordam -- a contagem fica.
_PN = {
    "symbol": "XPTO4", "regularMarketPrice": 100.0, "earningsPerShare": 10.072,
    "marketCap": 1.0e9,
    "defaultKeyStatistics": {"trailingEps": 10.08, "sharesOutstanding": 23_294_643,
                             "netIncomeToCommon": 2.3481e8},
}


def _by(rows):
    return {r["metric_name"]: r for r in rows if r.get("period") == "spot"}


def _lucro(q):
    return q["defaultKeyStatistics"]["netIncomeToCommon"]


@pytest.mark.parametrize("q", [_MILS3, _TFCO4])
def test_contagem_errada_vira_lucro_sobre_lpa_do_topo(q):
    assert ac.acoes_emitidas(q) == pytest.approx(_lucro(q) / q["earningsPerShare"])
    # a base de k continua a contagem bruta: o trailingEps foi feito com ela
    assert ac._acoes_brutas(q) == q["defaultKeyStatistics"]["sharesOutstanding"]


@pytest.mark.parametrize("q", [_MILS3, _TFCO4, _PN])
def test_equivalencia_entre_a_regra_do_lpa_e_a_das_acoes(q):
    """Ação (k = 1): lucro ÷ LPA do título = ações emitidas conferidas."""
    assert _lucro(q) / brapi.lpa_do_titulo(q) == pytest.approx(ac.acoes_emitidas(q))


def test_campos_da_brapi_concordando_ficam_mesmo_longe_da_referencia():
    assert ac.acoes_emitidas(_PN) == 23_294_643


def test_contagem_perto_da_referencia_fica():
    q = {"regularMarketPrice": 10.0, "marketCap": 7_000.0, "earningsPerShare": 99.0,
         "defaultKeyStatistics": {"sharesOutstanding": 1_000.0}}
    assert ac.acoes_emitidas(q) == 1_000.0


def test_nenhuma_contagem_plausivel_cai_no_marketcap_da_brapi():
    q = {"symbol": "XPTO3", "regularMarketPrice": 10.0, "marketCap": 1.0e9,
         "earningsPerShare": 50.0,
         "defaultKeyStatistics": {"sharesOutstanding": 20_000.0, "trailingEps": 5_000.0,
                                  "netIncomeToCommon": 1.0e8}}
    assert ac.acoes_emitidas(q) is None
    assert ac.valor_de_mercado_do_quote(q, unit=False) == 1.0e9
    by = _by(nz.metric_rows(q))
    assert "Acoes_Emitidas" not in by
    assert by["marketCap"]["metric_value"] == 1.0e9


def test_prejuizo_tambem_e_conferido():
    # lucro e LPA do topo negativos dão ações positivas
    q = {"regularMarketPrice": 10.0, "marketCap": 10_000_000.0, "earningsPerShare": -0.5,
         "defaultKeyStatistics": {"sharesOutstanding": 1_000.0, "trailingEps": -500.0,
                                  "netIncomeToCommon": -500_000.0}}
    assert ac.acoes_emitidas(q) == pytest.approx(1_000_000.0)


def test_unit_confere_em_titulos_e_devolve_acoes():
    # k = 3; contagem 10x baixa; o topo é o LPA da unit; marketCap da companhia
    q = {"symbol": "XPTO11", "regularMarketPrice": 30.0, "marketCap": 30_000.0,
         "earningsPerShare": 3.0, "longName": "Companhia Sintetica SA Units",
         "defaultKeyStatistics": {"sharesOutstanding": 300.0, "trailingEps": 30.0,
                                  "netIncomeToCommon": 3_000.0}}
    assert ac.acoes_por_titulo(q, unit=True) == 3
    assert ac.acoes_emitidas(q) == pytest.approx(3_000.0)
    assert ac.valor_de_mercado_do_quote(q, unit=True) == pytest.approx(30_000.0)


def test_unit_sem_marketcap_fica_como_veio():
    q = {"regularMarketPrice": 30.0,
         "defaultKeyStatistics": {"sharesOutstanding": 300.0, "trailingEps": 30.0,
                                  "netIncomeToCommon": 3_000.0}}
    assert ac.acoes_emitidas(q) == 300.0


def test_quote_diario_confere_a_contagem_emprestada_da_composicao():
    diario = {"symbol": "MILS3", "regularMarketPrice": 15.15,
              "earningsPerShare": 1.9778641, "marketCap": 3_584_443_662}
    q = ac.quote_com_composicao(diario, _MILS3["defaultKeyStatistics"])
    assert ac.acoes_emitidas(q) == pytest.approx(_lucro(_MILS3) / 1.9778641)


# ── os caminhos: spot, ttm do reprocess, histórico publicado, escala do LPA ──

@pytest.mark.parametrize("q", [_MILS3, _TFCO4])
def test_spot_marketcap_e_acoes_saem_conferidos(q):
    by = _by(nz.metric_rows(q))
    assert by["Acoes_Emitidas"]["metric_value"] == pytest.approx(ac.acoes_emitidas(q))
    # P/L spot = valor de mercado spot ÷ lucro
    assert (by["marketCap"]["metric_value"] / _lucro(q)
            == pytest.approx(by["P/L"]["metric_value"]))


@pytest.mark.parametrize("q", [_MILS3, _TFCO4])
def test_pl_ttm_do_reprocess_bate_com_o_spot(q):
    """O snapshot do reprocess: valor de mercado das linhas spot ÷ lucro."""
    by = _by(nz.metric_rows(q))
    mc = ig._valor_de_mercado_atual(
        preco=q["regularMarketPrice"], acoes=by["Acoes_Emitidas"]["metric_value"],
        por_titulo=by["Acoes_por_Titulo"]["metric_value"],
        mc_spot=by["marketCap"]["metric_value"], unit=False,
        lucro_anual=None, lpa_anual=None)
    snap = mx.compute_snapshot({"market_cap": mc, "net_income": _lucro(q),
                                "price": q["regularMarketPrice"]})
    assert snap["P/L"][0] == pytest.approx(by["P/L"]["metric_value"])
    assert snap["P/L"][0] == pytest.approx(q["priceEarnings"], rel=0.05)


def test_porte_do_historico_publicado_usa_as_acoes_conferidas():
    from scripts.publish_valuation_historico import linhas_de_valor_de_mercado
    linhas, _ = linhas_de_valor_de_mercado([
        {"ticker": "MILS3", "fetched_at": "2026-10-29", "payload": _MILS3}])
    by = _by(nz.metric_rows(_MILS3))
    assert linhas[0]["origem"] == "acoes"
    assert linhas[0]["market_cap"] == pytest.approx(by["marketCap"]["metric_value"])
    assert linhas[0]["titulos"] == pytest.approx(ac.acoes_emitidas(_MILS3))


def test_ancora_da_escala_do_lpa_usa_as_acoes_conferidas():
    assert nz._acoes(_MILS3) == pytest.approx(ac.acoes_emitidas(_MILS3))
