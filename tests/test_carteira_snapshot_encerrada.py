"""Testes de `_montar_carteira_snapshot`: o que entra e o que sai da carteira.

O parser da B3 grava a posição vendida como `quantity = 0` / `market_value =
0` justamente para a foto de HOJE existir e ganhar, no DENSE_RANK de
`_SQL_POSICOES_SNAPSHOT`, da foto antiga de outra fonte. Essa metade só serve
se a outra metade cortar o zero — é o que este arquivo prende.

Prende também um vazamento de escopo que mora no mesmo caminho: `is_rf` era
decidido pelo tipo da ÚLTIMA linha que o SQL devolveu, e valia para a
carteira inteira.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from core.investimentos import _montar_carteira_snapshot


def _row(ticker, *, qty, vm, asset_type="stock", asset_name=None,
         live_price=None, invested=None):
    return SimpleNamespace(
        ticker=ticker,
        quantity=qty,
        market_value=vm,
        invested_value=invested,
        asset_type=asset_type,
        asset_name=asset_name or ticker,
        sector="other",
        currency="BRL",
        country="BR",
        report_date=date(2026, 9, 21),
        live_price=live_price,
        live_timestamp=None,
        usd_brl_rate=None,
        pp_quantity=0,
        pp_total_invested=0,
        pp_average_price=0,
    )


def test_posicao_encerrada_sai_da_carteira():
    """CSMG3 com valor zero não pode aparecer na carteira.

    É o pedido inteiro: o extrato diz que o papel foi vendido, e a tela tem
    que parar de mostrá-lo. Se este corte cair, a venda volta a ser gravada e
    exibida como um ativo de R$ 0,00 — que parece dado faltando, não venda.
    """
    carteira = _montar_carteira_snapshot([
        _row("BBAS3", qty=1479, vm=34017.0),
        _row("CSMG3", qty=0, vm=0.0),
        _row("MBRF3", qty=0, vm=0.0),
    ])
    tickers = {p["ticker"] for p in carteira["posicoes"]}
    assert tickers == {"BBAS3"}


def test_tesouro_com_qtd_zero_e_valor_positivo_continua_na_carteira():
    """Zero por ARREDONDAMENTO não é zero por venda.

    A XP reporta título do Tesouro com qty=0 e saldo cheio. O corte é pelo
    VALOR (`vm <= 0`), não pela quantidade — trocar um pelo outro apagaria
    R$ 5.483 de TSELIC2028.
    """
    carteira = _montar_carteira_snapshot([
        _row("TSELIC2028", qty=0, vm=5483.0, asset_type="tesouro"),
    ])
    assert [p["ticker"] for p in carteira["posicoes"]] == ["TSELIC2028"]


def test_tesouro_no_fim_da_lista_nao_congela_a_cotacao_das_acoes():
    """`is_rf` é do ativo do grupo, não da última linha que o SQL devolveu.

    Renda fixa não tem cotação diária, então `is_rf` manda usar o preço do
    snapshot. Lido da última row, esse "não tem cotação" contaminava a
    carteira inteira: BBAS3 ficaria em R$ 23,00 (snapshot) em vez dos R$ 25,00
    da cotação viva, sem erro nenhum aparecer.
    """
    carteira = _montar_carteira_snapshot([
        _row("BBAS3", qty=100, vm=2300.0, live_price=25.0),
        _row("TSELIC2031", qty=2.83, vm=56154.59, asset_type="tesouro"),
    ])
    bbas = next(p for p in carteira["posicoes"] if p["ticker"] == "BBAS3")
    assert bbas["preco_atual"] == 25.0
    assert bbas["valor_mercado"] == 2500.0


def test_nome_do_tesouro_sai_amigavel_mesmo_gravado_em_codigo():
    """As linhas já no Supabase guardam "LFT mar/2031"; a tela mostra o nome.

    `get_or_create_asset` não reescreve `assets.name` de um ticker que já
    existe, então corrigir só a importação deixaria o histórico em código
    para sempre.
    """
    carteira = _montar_carteira_snapshot([
        _row("TSELIC2031", qty=2.83, vm=56154.59, asset_type="tesouro",
             asset_name="LFT mar/2031"),
    ])
    assert carteira["posicoes"][0]["nome"] == "Tesouro Selic 2031"
