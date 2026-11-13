"""P/VP spot = valor de mercado do título ÷ PL (normalize.pvp_do_titulo).

O ``priceToBook`` da brapi herda a contagem de ações dela, que às vezes vem
errada (29/10/2026: TFCO4 15,8, MILS3 0,0, JBSS32 3,55). O P/VP spot passa a
ser a mesma conta do ttm (``metrics.compute_snapshot``: market_cap/equity),
com as ações conferidas do #729. Os quotes de TFCO4 e MILS3 são os campos
reais de 29/10/2026; o balanço é sintético.
"""
import pytest

from data_pipeline.market import acoes as ac
from data_pipeline.market import metrics as mx
from data_pipeline.market import normalize as nz


def _balanco(pl, *, minoritarios=0.0, data="2026-06-30"):
    return {"endDate": data, "shareholdersEquity": pl + minoritarios,
            "minorityInterest": minoritarios, "totalAssets": 3 * pl}


def _quote(base, pl, ptb, **kw):
    q = {**base, "balanceSheetHistoryQuarterly": [_balanco(pl, **kw)]}
    q["defaultKeyStatistics"] = {**base["defaultKeyStatistics"], "priceToBook": ptb}
    return q


_TFCO4 = {
    "symbol": "TFCO4", "regularMarketPrice": 10.09, "earningsPerShare": 0.9684276,
    "marketCap": 1_544_110_416,
    "defaultKeyStatistics": {"trailingEps": 0.1559320, "sharesOutstanding": 942_744_260,
                             "netIncomeToCommon": 147_004_000},
}
_MILS3 = {
    "symbol": "MILS3", "regularMarketPrice": 15.15, "earningsPerShare": 1.9778641,
    "marketCap": 3_584_443_662,
    "defaultKeyStatistics": {"trailingEps": 9321.7014033, "sharesOutstanding": 48172,
                             "netIncomeToCommon": 449_045_000},
}
# Contagem certa: o priceToBook da brapi e a conta própria coincidem.
_BOA = {
    "symbol": "XPTO3", "regularMarketPrice": 20.0, "earningsPerShare": 2.0,
    "marketCap": 2.0e9,
    "defaultKeyStatistics": {"trailingEps": 2.0, "sharesOutstanding": 1.0e8,
                             "netIncomeToCommon": 2.0e8},
}


def _spot(q, composicao=None):
    return {r["metric_name"]: r for r in nz.metric_rows(q, composicao)}


@pytest.mark.parametrize("base,pl,ptb_brapi,esperado", [
    (_TFCO4, 638e6, 15.8, 2.40),
    (_MILS3, 1.737e9, 0.0, 1.98),
])
def test_contagem_errada_nao_contamina_o_pvp(base, pl, ptb_brapi, esperado):
    q = _quote(base, pl, ptb_brapi)
    linha = _spot(q)["P/VP"]
    assert linha["metric_value"] == pytest.approx(esperado, rel=0.02)
    assert linha["calculation_method"] == nz.METODO_PVP_DO_TITULO


def test_contagem_certa_coincide_com_o_price_to_book():
    q = _quote(_BOA, 1.0e9, 2.0)
    assert _spot(q)["P/VP"]["metric_value"] == pytest.approx(2.0)


def test_equivalencia_com_o_ttm():
    """Mesmo preço e mesmo balanço: P/VP spot = market_cap/equity do ttm."""
    q = _quote(_TFCO4, 638e6, 15.8, minoritarios=50e6)
    eq = next(r["equity"] for r in nz.balance_rows(q))
    assert eq == pytest.approx(638e6)          # PL dos controladores, sem minoritários
    mc = ac.valor_de_mercado_do_quote(q, unit=False)
    ttm, _ = mx.compute_snapshot({"market_cap": mc, "equity": eq})["P/VP"]
    assert _spot(q)["P/VP"]["metric_value"] == pytest.approx(ttm, rel=1e-6)


def test_balanco_mais_novo_vence_e_trimestral_desempata():
    q = {**_BOA,
         "balanceSheetHistory": [_balanco(5.0e8, data="2025-12-31"),
                                 _balanco(9.0e8, data="2026-06-30")],
         "balanceSheetHistoryQuarterly": [_balanco(1.0e9, data="2026-06-30"),
                                          _balanco(7.0e8, data="2026-03-31")]}
    assert nz.pl_mais_recente(q) == 1.0e9


def test_sem_balanco_cai_no_price_to_book_quando_a_contagem_confere():
    q = {**_BOA, "defaultKeyStatistics": {**_BOA["defaultKeyStatistics"], "priceToBook": 1.9}}
    linha = _spot(q)["P/VP"]
    assert linha["metric_value"] == 1.9
    assert linha["calculation_method"] == nz.METODO_PVP_DA_BRAPI


@pytest.mark.parametrize("base", [_TFCO4, _MILS3])
def test_sem_balanco_e_contagem_rejeitada_fica_sem_pvp(base):
    q = {**base, "defaultKeyStatistics": {**base["defaultKeyStatistics"], "priceToBook": 15.8}}
    assert "P/VP" not in _spot(q)


def test_pl_zero_nao_divide():
    q = _quote(_BOA, 0.0, None)
    assert "P/VP" not in _spot(q)


def test_unit_usa_o_valor_de_mercado_da_companhia():
    """Unit com k = 3: valor de mercado = preço da unit × ações ÷ 3."""
    q = {"symbol": "XPTO11", "regularMarketPrice": 30.0,
         "defaultKeyStatistics": {"sharesOutstanding": 3.0e8, "trailingEps": 6.0,
                                  "netIncomeToCommon": 6.0e8, "priceToBook": 99.0},
         "balanceSheetHistoryQuarterly": [_balanco(1.5e9)]}
    pvp, metodo = nz.pvp_do_titulo(q, unit=True)
    assert pvp == pytest.approx(30.0 * 3.0e8 / 3 / 1.5e9)
    assert metodo == nz.METODO_PVP_DO_TITULO

