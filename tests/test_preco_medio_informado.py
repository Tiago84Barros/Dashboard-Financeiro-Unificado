"""
tests/test_preco_medio_informado.py
===================================
O preço médio que o PRÓPRIO investidor declara.

O caso que criou isto: o BBAS3 aparecia com PM de R$ 23,92 e procedência
`b3_posicao_detalhada` -- ou seja, com a autoridade da custódia central. O
número tinha sido DIGITADO À MÃO pelo usuário na planilha de posição que ele
subiu, porque nem o extrato nem as notas sabiam o preço médio real (o
relatório de Negociação da B3 começa em nov/2019 e as compras anteriores não
existem em fonte nenhuma).

A saída não foi esconder o palpite, foi rotulá-lo: ele entra por uma porta
própria, com procedência própria, e a tela diz "informado por você".
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from core.investimentos import _montar_carteira_snapshot
from core.precos_medios_manuais import normalizar_ticker


def _row(ticker, *, qty, vm, invested=None, pp_qty=0, pp_ti=0, pp_avg=0,
         b3_avg=0, b3_qty=0):
    return SimpleNamespace(
        ticker=ticker, quantity=qty, market_value=vm, invested_value=invested,
        asset_type="stock", asset_name=ticker, sector="other",
        currency="BRL", country="BR", report_date=date(2026, 9, 21),
        live_price=None, live_timestamp=None, usd_brl_rate=None,
        pp_quantity=pp_qty, pp_total_invested=pp_ti, pp_average_price=pp_avg,
        b3_avg_price=b3_avg, b3_quantity=b3_qty,
        b3_report_date=date(2026, 9, 21),
    )


_BBAS3 = _row("BBAS3", qty=1479, vm=34017.00, invested=35377.68,
              pp_qty=1086, pp_ti=11294.64, pp_avg=10.4003,
              b3_avg=23.9200, b3_qty=1479)


def _pos(rows, ticker, manuais=None):
    carteira = _montar_carteira_snapshot(rows, None, manuais)
    return next(p for p in carteira["posicoes"] if p["ticker"] == ticker)


def test_declaracao_do_usuario_ganha_de_todas_as_fontes():
    pos = _pos([_BBAS3], "BBAS3", {"BBAS3": {"preco_medio": 19.50}})
    assert round(pos["preco_medio"], 2) == 19.50
    assert pos["custo_fonte"] == "informado_pelo_usuario"
    assert round(pos["total_investido"], 2) == round(19.50 * 1479, 2)


def test_declaracao_do_usuario_nao_se_passa_por_numero_da_b3():
    """A procedência viaja com o número -- é o ponto inteiro da feature."""
    pos = _pos([_BBAS3], "BBAS3", {"BBAS3": {"preco_medio": 19.50}})
    assert pos["custo_fonte"] != "b3_posicao_detalhada"
    assert pos["custo_estimado"] is True, (
        "número digitado pela pessoa não pode ser marcado como custo "
        "autoritativo; é exatamente assim que R$ 23,92 passou meses "
        "publicado como declarado pela B3"
    )


def test_sem_declaracao_a_cadeia_normal_continua_valendo():
    """Apagar a declaração devolve o ativo à fonte anterior, sem resíduo."""
    pos = _pos([_BBAS3], "BBAS3", {})
    assert pos["custo_fonte"] == "b3_posicao_detalhada"
    assert round(pos["preco_medio"], 2) == 23.92


def test_declaracao_de_outro_ticker_nao_vaza():
    pos = _pos([_BBAS3], "BBAS3", {"PETR3": {"preco_medio": 1.00}})
    assert pos["custo_fonte"] == "b3_posicao_detalhada"


def test_valor_invalido_e_ignorado():
    """Zero ou negativo não é declaração; é campo em branco."""
    for ruim in (0, 0.0, -5.0, None):
        pos = _pos([_BBAS3], "BBAS3", {"BBAS3": {"preco_medio": ruim}})
        assert pos["custo_fonte"] == "b3_posicao_detalhada", ruim


def test_chave_e_a_mesma_pela_qual_a_carteira_agrupa():
    """BBAS3F e BBAS3 são a mesma posição -- e têm de ser a mesma chave.

    Se a normalização daqui divergir de `_base_ticker`, a declaração some em
    silêncio: nada quebra, a tela só volta a exibir a fonte anterior.
    """
    from core.investimentos import _base_ticker

    for t in ("bbas3f", " BBAS3 ", "BBAS3F", "HGLG11F", "SPY", "IEFA"):
        assert normalizar_ticker(t) == _base_ticker(t.strip().upper())


def test_declaracao_alcanca_a_posicao_do_fracionario():
    """Declarar em BBAS3 tem de valer para o grupo BBAS3 + BBAS3F."""
    rows = [
        _row("BBAS3",  qty=1000, vm=23000.00, b3_avg=23.92, b3_qty=1479),
        _row("BBAS3F", qty=479,  vm=11017.00, b3_avg=23.92, b3_qty=1479),
    ]
    pos = _pos(rows, "BBAS3", {"BBAS3": {"preco_medio": 19.50}})
    assert pos["quantidade"] == 1479
    assert round(pos["preco_medio"], 2) == 19.50


def test_o_card_diz_informado_por_voce():
    """O rótulo é a feature. Sem ele, o palpite volta a parecer fato."""
    from views.investimentos import _card_ativo

    pos = dict(
        ticker="BBAS3", cor="#000", rentab_pct=-3.0, valor_mercado=34017.0,
        total_investido=28840.5, preco_atual=23.0, preco_medio=19.5,
        quantidade=1479, pct_carteira=10.0, moeda="BRL",
        custo_fonte="informado_pelo_usuario", cotacao_fonte="live",
        cotacao_timestamp=None, diferenca_reais=5176.5, nome="Banco do Brasil",
        classe="Ações", setor="Financeiro",
    )
    html = _card_ativo(pos, 0.0)
    assert "informado por você" in html
    assert "Posição Detalhada" not in html
