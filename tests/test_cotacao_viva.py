"""Cotação ao vivo da carteira, com o banco como reserva.

O caso que criou o módulo: em 26/10/2026 a carteira convertia os US$ 17.957 da
Nomad pelo R$ 4,9652 gravado no domingo, com o dólar a R$ 5,55 na abertura e o
card "USD / BRL" da mesma tela já mostrando R$ 5,56.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import core.investimentos as investimentos
from core import cotacao_viva as cv

AGORA = datetime(2026, 10, 26, 14, 0, tzinfo=timezone.utc)
DOMINGO = datetime(2026, 10, 25, tzinfo=timezone.utc)


def _fonte(monkeypatch, cotacoes: dict[str, float] | None):
    """Troca o yfinance; ``None`` simula a fonte fora do ar."""
    chamadas = []

    def _baixar(simbolos):
        chamadas.append(simbolos)
        if cotacoes is None:
            raise ConnectionError("429")
        return {s: cv.Cotacao(p, AGORA) for s, p in cotacoes.items() if s in simbolos}
    monkeypatch.setattr(cv, "_baixar", _baixar)
    return chamadas


@pytest.mark.parametrize("ticker,moeda,classe,esperado", [
    ("PETR4", "BRL", "stock", "PETR4.SA"),
    ("PETR4F", "BRL", "stock", "PETR4.SA"),
    ("HGLG11", "BRL", "fii", "HGLG11.SA"),
    ("SPY", "USD", "etf", "SPY"),
    ("BRK.B", "USD", "stock", "BRK-B"),
    ("TESOURO IPCA+ 2035", "BRL", "tesouro", None),
    ("TSELIC2028", "BRL", None, None),
    ("4606422UNA", "BRL", "fip", None),
    ("PETR4", "BRL", "renda_fixa", None),
    ("", "BRL", "stock", None),
])
def test_simbolo_yahoo(ticker, moeda, classe, esperado):
    assert cv.simbolo_yahoo(ticker, moeda, classe) == esperado


def test_simbolos_inclui_o_cambio_so_com_posicao_em_dolar():
    brl = [SimpleNamespace(ticker="PETR4", currency="BRL", asset_class="stock")]
    usd = [SimpleNamespace(ticker="SPY", currency="USD", asset_class="etf")]
    assert cv.simbolos(brl) == ("PETR4.SA",)
    assert cv.simbolos(brl, usd, None) == ("PETR4.SA", "SPY", cv.SIMBOLO_CAMBIO)


def test_busca_vale_15_minutos_numa_chamada_so(monkeypatch):
    chamadas = _fonte(monkeypatch, {"PETR4.SA": 38.0, "SPY": 760.0})
    a = cv.buscar(["SPY", "PETR4.SA"])
    b = cv.buscar(("PETR4.SA", "SPY"))
    assert a == b and a["SPY"].preco == 760.0
    assert len(chamadas) == 1
    assert cv.TTL_S == 900


def test_fonte_fora_do_ar_devolve_vazio_e_espera_antes_de_tentar_de_novo(monkeypatch):
    chamadas = _fonte(monkeypatch, None)
    assert cv.buscar(["SPY"]) == {}
    assert cv.buscar(["SPY"]) == {}
    assert len(chamadas) == 1  # a espera poupou a segunda tentativa
    # passada a espera, tenta de novo -- e o vazio não ficou no cache
    monkeypatch.setattr(cv, "_falha_ate", 0.0)
    _fonte(monkeypatch, {"SPY": 760.0})
    assert cv.buscar(["SPY"])["SPY"].preco == 760.0


def test_barra_de_hoje_vale_agora_e_a_de_antes_vale_o_seu_dia():
    assert cv._momento("PETR4.SA", date(2026, 10, 26), AGORA) == AGORA
    assert cv._momento("SPY", date(2026, 10, 23), AGORA) == datetime(
        2026, 10, 23, tzinfo=timezone.utc)


def _linha_usd(**kw):
    base = dict(ticker="SPY", quantity=10.0, average_price=700.0,
                total_invested=7000.0, current_price=759.97,
                current_price_timestamp=datetime(2026, 10, 23, tzinfo=timezone.utc),
                usd_brl_rate=4.9652, usd_brl_timestamp=DOMINGO,
                asset_name="SPDR S&P 500", asset_class="etf", sector=None,
                currency="USD")
    base.update(kw)
    return SimpleNamespace(**base)


def test_aplicar_troca_preco_e_cambio_e_guarda_o_resto():
    cot = {"SPY": cv.Cotacao(765.0, AGORA), cv.SIMBOLO_CAMBIO: cv.Cotacao(5.55, AGORA)}
    [r], n = cv.aplicar([_linha_usd()], "current_price", "current_price_timestamp", cot)
    assert n == 1
    assert (r.current_price, r.current_price_timestamp) == (765.0, AGORA)
    assert (r.usd_brl_rate, r.usd_brl_timestamp) == (5.55, AGORA)
    assert r.quantity == 10.0 and r.ticker == "SPY"


def test_preco_ao_vivo_implausivel_fica_com_o_do_banco():
    """Símbolo trocado no Yahoo não pode virar patrimônio."""
    cot = {"SPY": cv.Cotacao(76.0, AGORA)}
    [r], n = cv.aplicar([_linha_usd()], "current_price", "current_price_timestamp", cot)
    assert n == 0
    assert r.current_price == 759.97


def test_cambio_implausivel_ou_abaixo_do_piso_fica_com_o_do_banco():
    for taxa in (1.5, 11.0):
        cot = {cv.SIMBOLO_CAMBIO: cv.Cotacao(taxa, AGORA)}
        [r], _ = cv.aplicar([_linha_usd()], "current_price",
                            "current_price_timestamp", cot)
        assert r.usd_brl_rate == 4.9652


def test_sem_cotacao_ao_vivo_as_linhas_saem_intactas():
    linhas = [_linha_usd()]
    assert cv.aplicar(linhas, "current_price", "current_price_timestamp", {}) == (linhas, 0)


def test_extra_da_nomad_sai_pelo_cambio_e_preco_ao_vivo():
    """O caso de 26/10/2026: o mercado em BRL usa o câmbio de agora."""
    cot = {"SPY": cv.Cotacao(765.0, AGORA), cv.SIMBOLO_CAMBIO: cv.Cotacao(5.55, AGORA)}
    linhas, _ = cv.aplicar([_linha_usd()], "current_price",
                           "current_price_timestamp", cot)
    carteira = {"posicoes": [], "avisos_dados": []}
    investimentos._adicionar_extras_ao_snapshot(carteira, linhas)
    [p] = carteira["posicoes"]
    assert p["valor_mercado"] == round(10 * 765.0 * 5.55, 2)
    assert p["fx_rate_atual"] == 5.55
    assert p["cotacao_fonte"] == "live"


def test_extra_da_nomad_sem_fonte_ao_vivo_usa_o_fechamento_do_banco():
    carteira = {"posicoes": [], "avisos_dados": []}
    investimentos._adicionar_extras_ao_snapshot(carteira, [_linha_usd()])
    [p] = carteira["posicoes"]
    assert p["valor_mercado"] == round(10 * 759.97 * 4.9652, 2)


def _linha_snapshot(ticker, *, live, ts, tipo="stock", vm=1000.0, qty=10.0):
    return SimpleNamespace(
        ticker=ticker, quantity=qty, market_value=vm, invested_value=vm,
        asset_type=tipo, asset_name=ticker, sector="other", currency="BRL",
        country="BR", report_date=date(2026, 10, 20), live_price=live,
        live_timestamp=ts, usd_brl_rate=4.9652, pp_quantity=0,
        pp_total_invested=0, pp_average_price=0, b3_avg_price=0, b3_quantity=0,
        b3_report_date=None, is_loaned=False, market_price=None)


def test_snapshot_da_b3_usa_o_preco_ao_vivo_e_nao_toca_no_tesouro():
    rows = [
        _linha_snapshot("PETR4", live=36.0, ts=DOMINGO - timedelta(days=2)),
        _linha_snapshot("TESOURO SELIC 2029", live=None, ts=None, tipo="tesouro",
                        vm=5000.0, qty=0.3),
    ]
    cot = {"PETR4.SA": cv.Cotacao(38.0, AGORA), cv.SIMBOLO_CAMBIO: cv.Cotacao(5.55, AGORA)}
    rows, n = cv.aplicar(rows, "live_price", "live_timestamp", cot)
    assert n == 1
    carteira = investimentos._montar_carteira_snapshot(rows)
    por_ticker = {p["ticker"]: p for p in carteira["posicoes"]}
    assert por_ticker["PETR4"]["valor_mercado"] == 380.0
    assert por_ticker["PETR4"]["cotacao_timestamp"] == AGORA
    assert por_ticker["TESOURO SELIC 2029"]["valor_mercado"] == 5000.0


def test_resumo_diz_quantas_e_de_quando():
    cot = {"SPY": cv.Cotacao(765.0, AGORA), cv.SIMBOLO_CAMBIO: cv.Cotacao(5.55, AGORA)}
    assert cv.resumo(cot, 1) == {"n_ao_vivo": 1, "atualizado_em": AGORA, "cambio": 5.55}
    assert cv.resumo({}, 0) == {"n_ao_vivo": 0, "atualizado_em": None, "cambio": None}


def test_rotulo_da_tela_distingue_ao_vivo_de_fechamento():
    from views.investimentos import _rotulo_cotacao_viva

    agora = datetime.now(timezone.utc)
    assert _rotulo_cotacao_viva(None) == "Fechamento gravado"
    assert _rotulo_cotacao_viva({"n_ao_vivo": 0, "atualizado_em": agora}) == "Fechamento gravado"
    assert _rotulo_cotacao_viva(
        {"n_ao_vivo": 3, "atualizado_em": agora}).startswith("Ao vivo às ")
    assert _rotulo_cotacao_viva(
        {"n_ao_vivo": 3, "atualizado_em": DOMINGO - timedelta(days=2)}) == "Fechamento de 23/10"
