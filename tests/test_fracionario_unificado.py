"""Fracionário (BBAS3F) tratado como o mesmo ativo do lote padrão (BBAS3)
fora da tela de proventos: carteira de reserva, somas de proventos em SQL,
séries de preço e a análise por ativo."""
import pandas as pd

from core.investimentos import (
    _SQL_EVOLUCAO_DIV,
    _SQL_RENTAB_PROVENTOS_B3,
    _SQL_TICKER_BASE,
    _juntar_fracionario_posicoes,
)


def _pos(ticker, qtd, ti, vm, moeda="BRL", nome=None):
    return {"ticker": ticker, "nome": nome or ticker, "moeda": moeda,
            "quantidade": qtd, "total_investido": ti, "valor_mercado": vm,
            "preco_medio": ti / qtd, "rentab_pct": None}


def test_carteira_reserva_soma_fracionario_no_lote_padrao():
    posicoes = [
        _pos("BBAS3F", 10, 200.0, 250.0, nome="BBAS3F"),
        _pos("BBAS3", 100, 2000.0, 2500.0, nome="Banco do Brasil"),
        _pos("TAEE11", 5, 150.0, 160.0),
    ]
    saida = _juntar_fracionario_posicoes(posicoes)
    assert [p["ticker"] for p in saida] == ["BBAS3", "TAEE11"]
    bb = saida[0]
    assert bb["quantidade"] == 110
    assert bb["total_investido"] == 2200.0
    assert bb["valor_mercado"] == 2750.0
    assert bb["preco_medio"] == 20.0
    assert bb["rentab_pct"] == 25.0
    assert bb["nome"] == "Banco do Brasil"  # o nome vem do lote padrão


def test_carteira_reserva_so_fracionario_vira_ticker_base():
    saida = _juntar_fracionario_posicoes([_pos("MXRF11F", 3, 30.0, 31.0)])
    assert saida[0]["ticker"] == "MXRF11"
    assert saida[0]["quantidade"] == 3


def test_carteira_reserva_nao_toca_exterior():
    saida = _juntar_fracionario_posicoes([
        _pos("BYDDF", 1, 10.0, 11.0, moeda="USD"),
        _pos("BYDD", 1, 10.0, 11.0, moeda="USD"),
    ])
    assert [p["ticker"] for p in saida] == ["BYDDF", "BYDD"]


def test_sql_de_proventos_deduplica_pelo_ticker_base():
    # Particionar por asset_id deixava BBAS3 e BBAS3F contarem o mesmo
    # pagamento duas vezes na evolução e na rentabilidade vs CDI.
    for sql in (_SQL_EVOLUCAO_DIV, _SQL_RENTAB_PROVENTOS_B3):
        assert "PARTITION BY " + _SQL_TICKER_BASE in sql
        assert "PARTITION BY d.asset_id" not in sql
        assert "JOIN assets a ON a.id = d.asset_id" in sql


def test_series_de_preco_juntam_fracionario_e_preferem_lote_padrao():
    from views.investimentos import _com_fracionario, _preco_por_ticker_base

    mapa = _com_fracionario(["BBAS3", "MXRF11", "AAPL"])
    assert mapa == {"BBAS3": "BBAS3", "MXRF11": "MXRF11", "AAPL": "AAPL",
                    "BBAS3F": "BBAS3", "MXRF11F": "MXRF11"}

    d1, d2 = pd.Timestamp("2026-01-02"), pd.Timestamp("2026-01-03")
    df = pd.DataFrame([
        {"data": d1, "ticker": "BBAS3", "preco": 20.0},
        {"data": d1, "ticker": "BBAS3F", "preco": 20.5},
        {"data": d2, "ticker": "BBAS3F", "preco": 21.0},
        {"data": d1, "ticker": "MXRF11F", "preco": 10.0},
    ])
    saida = _preco_por_ticker_base(df, mapa)
    pivot = saida.pivot(index="data", columns="ticker", values="preco")
    assert sorted(pivot.columns) == ["BBAS3", "MXRF11"]
    assert pivot.loc[d1, "BBAS3"] == 20.0  # lote padrão vence no mesmo dia
    assert pivot.loc[d2, "BBAS3"] == 21.0  # só o F no dia: usa o F
    assert pivot.loc[d1, "MXRF11"] == 10.0


def test_analise_por_ativo_aceita_ticker_fracionario():
    from core.inteligencia_ativos import _posicao

    carteira = {"posicoes": [{"ticker": "BBAS3"}, {"ticker": "BYDDF"}]}
    assert _posicao(carteira, "bbas3f")["ticker"] == "BBAS3"
    assert _posicao(carteira, "BBAS3")["ticker"] == "BBAS3"
    assert _posicao(carteira, "BYDDF")["ticker"] == "BYDDF"  # exato vence
    assert _posicao(carteira, "PETR4F") is None


# ── Recálculo de portfolio_positions: lote e fracionário são UMA posição ─────
# `positions._compute` guardava o estado por asset_id. Ação comprada no
# fracionário e vendida no lote padrão (o caminho normal de quem junta 100)
# deixava a venda descoberta em BBAS3 -- zerada com alerta -- e as cotas do
# BBAS3F vivas, com o custo antigo entrando na média da compra seguinte.

def _tx(ticker, tipo, qty, price, dia):
    from decimal import Decimal

    return {"user_id": "u1", "asset_id": f"a-{ticker}", "ticker": ticker,
            "type": tipo, "quantity": Decimal(str(qty)),
            "unit_price": Decimal(str(price)), "fees": Decimal("0"),
            "transaction_date": dia}


def test_venda_no_lote_consome_cotas_compradas_no_fracionario():
    from decimal import Decimal

    from data_pipeline.importers.investments.positions import _compute

    positions, alerts = _compute([
        _tx("BBAS3", "buy", 100, 20, "2024-01-02"),
        _tx("BBAS3F", "buy", 50, 20, "2024-01-03"),
        _tx("BBAS3", "sell", 150, 30, "2024-02-01"),
        _tx("BBAS3F", "buy", 10, 40, "2024-03-01"),
    ])
    assert [(p["ticker"], p["asset_id"]) for p in positions] == [("BBAS3", "a-BBAS3")]
    assert positions[0]["quantity"] == Decimal("10")
    assert float(positions[0]["average_price"]) == 40.0
    assert float(positions[0]["total_invested"]) == 400.0
    assert not [a for a in alerts if a["type"] == "quantidade_negativa"]


def test_fracionario_vendido_em_parte_divide_o_custo_medio_com_o_lote():
    from decimal import Decimal

    from data_pipeline.importers.investments.positions import _compute

    positions, alerts = _compute([
        _tx("PETR3", "buy", 100, 30, "2024-01-02"),
        _tx("PETR3F", "buy", 50, 45, "2024-01-03"),   # PM conjunto: 35
        _tx("PETR3F", "sell", 30, 50, "2024-02-01"),  # vender não muda o PM
        _tx("PETR3", "buy", 80, 40, "2024-03-01"),    # (120*35 + 3200) / 200
    ])
    assert len(positions) == 1
    p = positions[0]
    assert (p["ticker"], p["asset_id"]) == ("PETR3", "a-PETR3")
    assert p["quantity"] == Decimal("200")
    assert float(p["average_price"]) == 37.0
    assert not alerts


def test_so_fracionario_grava_no_proprio_ativo():
    """Sem negociação no lote padrão não há asset_id da base a usar."""
    from decimal import Decimal

    from data_pipeline.importers.investments.positions import _compute

    positions, _ = _compute([
        _tx("MXRF11F", "buy", 7, 10, "2024-01-02"),
        _tx("MXRF11F", "sell", 2, 11, "2024-02-01"),
    ])
    assert [(p["ticker"], p["asset_id"]) for p in positions] == [("MXRF11F", "a-MXRF11F")]
    assert positions[0]["quantity"] == Decimal("5")
