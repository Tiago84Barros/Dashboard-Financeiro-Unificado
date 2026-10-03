from datetime import date

from core.investimentos import ganho_total
from core.ir_renda_variavel import resultado_realizado


def test_provento_entra_uma_vez_so():
    # Números da tela de 03/10/2026: o card antigo somava mercado + proventos
    # (R$ 411.312,77), contando de novo o provento que virou cota.
    g = ganho_total({"total_mercado": 395569.42, "total_investido": 387841.29,
                     "total_dividendos": 15743.35})
    assert round(g["valorizacao"], 2) == 7728.13
    assert round(g["ganho"], 2) == 23471.48
    assert g["realizado"] is None


def test_lucro_de_vendas_entra_no_ganho():
    g = ganho_total({"total_mercado": 1200.0, "total_investido": 1000.0,
                     "total_dividendos": 50.0}, {"ganho": -300.0})
    assert g["realizado"] == -300.0
    assert g["ganho"] == 200.0 - 300.0 + 50.0


def test_lucro_de_vendas_indisponivel_nao_vira_zero_silencioso():
    g = ganho_total({"total_mercado": 1200.0, "total_investido": 1000.0},
                    {"ganho": None, "motivo": "sem extrato"})
    assert g["realizado"] is None
    assert g["ganho"] == 200.0


def test_sem_custo_nao_inventa_ganho():
    assert ganho_total({"total_mercado": 100.0, "total_investido": 0.0}) is None
    assert ganho_total({}) is None


def _tx(d, ticker, lado, qtd, preco):
    return {"transaction_date": d, "ticker": ticker, "classe": "stock", "type": lado,
            "quantity": qtd, "unit_price": preco, "fees": 0}


def test_resultado_realizado_a_preco_medio():
    r = resultado_realizado([
        _tx(date(2021, 1, 4), "ABCD3", "buy", 100, 10.0),
        _tx(date(2021, 2, 1), "ABCD3", "buy", 100, 20.0),   # PM 15
        _tx(date(2022, 3, 1), "ABCD3", "sell", 150, 12.0),  # (12 − 15) × 150
    ])
    assert round(r["ganho"], 2) == -450.0
    assert round(r["valor_vendido"], 2) == 1800.0
    assert r["valor_sem_custo"] == 0.0


def test_venda_sem_compra_no_extrato_fica_de_fora_e_e_declarada():
    r = resultado_realizado([_tx(date(2020, 5, 4), "WXYZ3", "sell", 10, 30.0)])
    assert r["ganho"] == 0.0
    assert round(r["valor_sem_custo"], 2) == 300.0
    assert r["sem_custo_por_ticker"] == {
        "WXYZ3": {"qtd": 10.0, "valor": 300.0, "primeira_venda": "2020-05-04"}}


def test_posicao_anterior_declarada_da_custo_a_venda():
    from core.posicao_anterior import como_compras

    trades = [_tx(date(2019, 12, 2), "WXYZ3", "buy", 10, 40.0),
              _tx(date(2020, 5, 4), "WXYZ3F", "sell", 20, 30.0)]
    abertura = {"WXYZ3": {"quantidade": 10, "custo_total": 200.0},
                "NADA3": {"quantidade": 5, "custo_total": 50.0}}   # sem negociação
    compras = como_compras(abertura, trades)
    assert [c["ticker"] for c in compras] == ["WXYZ3"]
    r = resultado_realizado(compras + trades)
    # PM (200 + 400) / 20 = 30 → venda a 30 empata.
    assert round(r["ganho"], 2) == 0.0
    assert r["valor_sem_custo"] == 0.0
    assert r["sem_custo_por_ticker"] == {}


def test_posicao_anterior_menor_que_a_venda_deixa_o_resto_sem_custo():
    from core.posicao_anterior import como_compras

    trades = [_tx(date(2020, 5, 4), "WXYZ3", "sell", 10, 30.0)]
    compras = como_compras({"WXYZ3": {"quantidade": 4, "custo_total": 80.0}}, trades)
    r = resultado_realizado(compras + trades)
    assert round(r["ganho"], 2) == 4 * (30.0 - 20.0)
    assert round(r["valor_sem_custo"], 2) == 180.0
    assert r["sem_custo_por_ticker"]["WXYZ3"]["qtd"] == 6.0


def test_ddl_da_posicao_anterior_roda_sem_parametros():
    # garantir_tabela usa exec_driver_sql: '%' viraria placeholder no psycopg2.
    from core.posicao_anterior import _DDL

    sql = _DDL.read_text(encoding="utf-8")
    assert "%" not in sql
    assert "CREATE TABLE IF NOT EXISTS investment_opening_positions" in sql
