"""Eventos corporativos da Movimentação da B3 no preço médio.

Sem eles, um desdobro deixava a quantidade recalculada na metade da posição
real e o PM no dobro: a conciliação em ``core/investimentos.py`` reprovava o
ativo e o preço médio caía para outra fonte. Aqui o evento entra na mesma
sequência das negociações, preservando custo onde não houve dinheiro.
"""
from __future__ import annotations

from decimal import Decimal

import data_pipeline.importers.investments.positions as mod
from data_pipeline.importers.investments.positions import _compute


def _tx(ticker, tipo, qty, price, dia, aid=None):
    return {
        "user_id": "u1", "asset_id": aid or f"a-{ticker}", "ticker": ticker,
        "type": tipo, "quantity": Decimal(str(qty)), "unit_price": Decimal(str(price)),
        "fees": Decimal("0"), "transaction_date": dia,
    }


def _ev(ticker, mov, sentido, qty, dia, preco=None, valor=None):
    return {
        "event_date": dia, "movement": mov, "direction": sentido, "ticker": ticker,
        "quantity": Decimal(str(qty)),
        "unit_price": None if preco is None else Decimal(str(preco)),
        "total_value": None if valor is None else Decimal(str(valor)),
    }


def _pos(txs, evs):
    positions, alerts = _compute(txs, evs)
    return {p["ticker"]: p for p in positions}, alerts


def test_desdobro_dobra_a_quantidade_e_preserva_o_custo():
    pos, _ = _pos(
        [_tx("WEGE3", "buy", 100, 40, "2020-01-10")],
        [_ev("WEGE3", "desdobro", "Credito", 100, "2021-04-01")],
    )
    assert pos["WEGE3"]["quantity"] == Decimal("200")
    assert float(pos["WEGE3"]["average_price"]) == 20.0
    assert float(pos["WEGE3"]["total_invested"]) == 4000.0


def test_venda_no_dia_do_credito_ja_e_em_cotas_novas():
    """Evento antes da negociação do mesmo dia: senão a venda fica descoberta."""
    pos, alerts = _pos(
        [_tx("WEGE3", "buy", 100, 40, "2020-01-10"),
         _tx("WEGE3", "sell", 150, 25, "2021-04-01")],
        [_ev("WEGE3", "desdobro", "Credito", 100, "2021-04-01")],
    )
    assert pos["WEGE3"]["quantity"] == Decimal("50")
    assert float(pos["WEGE3"]["average_price"]) == 20.0
    assert not [a for a in alerts if a["type"] == "quantidade_negativa"]


def test_grupamento_reduz_quantidade_e_preserva_o_custo():
    pos, _ = _pos(
        [_tx("MGLU3", "buy", 1000, 2, "2022-01-10")],
        [_ev("MGLU3", "grupamento", "Debito", 900, "2023-05-02")],
    )
    assert pos["MGLU3"]["quantity"] == Decimal("100")
    assert float(pos["MGLU3"]["average_price"]) == 20.0


def test_grupamento_publicado_nas_duas_pontas_da_o_mesmo_resultado():
    """Débito das 1.000 antigas + crédito das 100 novas = débito líquido de 900."""
    pos, alerts = _pos(
        [_tx("MGLU3", "buy", 1000, 2, "2022-01-10")],
        [_ev("MGLU3", "grupamento", "Debito", 1000, "2023-05-02"),
         _ev("MGLU3", "grupamento", "Credito", 100, "2023-05-02")],
    )
    assert pos["MGLU3"]["quantity"] == Decimal("100")
    assert float(pos["MGLU3"]["average_price"]) == 20.0
    assert not alerts


def test_bonificacao_soma_o_custo_atribuido_publicado():
    pos, _ = _pos(
        [_tx("ITSA4", "buy", 100, 10, "2020-01-10")],
        [_ev("ITSA4", "bonificação em ativos", "Credito", 10, "2021-12-20", preco=5)],
    )
    assert pos["ITSA4"]["quantity"] == Decimal("110")
    # (100*10 + 10*5) / 110
    assert round(float(pos["ITSA4"]["average_price"]), 4) == round(1050 / 110, 4)


def test_bonificacao_sem_preco_dilui_o_pm():
    pos, _ = _pos(
        [_tx("ITSA4", "buy", 100, 11, "2020-01-10")],
        [_ev("ITSA4", "bonificação em ativos", "Credito", 10, "2021-12-20")],
    )
    assert float(pos["ITSA4"]["average_price"]) == 10.0


def test_fracao_sai_como_venda_sem_mexer_no_pm():
    pos, _ = _pos(
        [_tx("ITSA4", "buy", 100, 11, "2020-01-10")],
        [_ev("ITSA4", "bonificação em ativos", "Credito", 10.5, "2021-12-20"),
         _ev("ITSA4", "fração em ativos", "Debito", 0.5, "2021-12-27")],
    )
    assert pos["ITSA4"]["quantity"] == Decimal("110")
    assert round(float(pos["ITSA4"]["average_price"]), 6) == round(1100 / 110.5, 6)


def test_subscricao_de_fii_compra_a_cota_pelo_valor_publicado():
    pos, _ = _pos(
        [_tx("HGLG11", "buy", 10, 160, "2021-01-10")],
        [_ev("HGLG12", "recibo de subscrição", "Credito", 5, "2021-06-01", valor=750)],
    )
    assert pos["HGLG11"]["quantity"] == Decimal("15")
    assert round(float(pos["HGLG11"]["average_price"]), 4) == round((1600 + 750) / 15, 4)


def test_subscricao_sem_valor_nao_entra_de_graca():
    pos, alerts = _pos(
        [_tx("HGLG11", "buy", 10, 160, "2021-01-10")],
        [_ev("HGLG12", "recibo de subscrição", "Credito", 5, "2021-06-01")],
    )
    assert pos["HGLG11"]["quantity"] == Decimal("10")
    assert float(pos["HGLG11"]["average_price"]) == 160.0
    assert any(a["type"] == "subscricao_sem_valor" for a in alerts)


def test_evento_sobre_posicao_zerada_nao_cria_cota_sem_custo():
    """Histórico truncado: comprado antes de nov/2019, desdobrado depois."""
    pos, alerts = _pos(
        [_tx("BBAS3", "buy", 10, 30, "2025-01-10")],
        [_ev("BBAS3", "desdobro", "Credito", 500, "2024-04-16")],
    )
    assert pos["BBAS3"]["quantity"] == Decimal("10")
    assert float(pos["BBAS3"]["average_price"]) == 30.0
    assert any(a["type"] == "evento_sem_posicao" for a in alerts)


def test_evento_de_ticker_sem_negociacao_vira_alerta():
    pos, alerts = _pos(
        [_tx("PETR4", "buy", 10, 30, "2020-01-10")],
        [_ev("VALE3", "desdobro", "Credito", 10, "2021-01-10")],
    )
    assert set(pos) == {"PETR4"}
    assert any(a["type"] == "evento_sem_ativo" for a in alerts)


def test_evento_do_fracionario_vai_para_o_ativo_com_cotas():
    """PETR4 e PETR4F somam no pp_base; o evento ajusta quem tem a base de custo."""
    pos, _ = _pos(
        [_tx("PETR4F", "buy", 7, 30, "2020-01-10", aid="a-frac"),
         _tx("PETR4", "buy", 100, 30, "2020-01-11", aid="a-lote"),
         _tx("PETR4F", "sell", 7, 31, "2020-02-10", aid="a-frac")],
        [_ev("PETR4", "desdobro", "Credito", 100, "2021-01-10")],
    )
    assert set(pos) == {"PETR4"}
    assert pos["PETR4"]["quantity"] == Decimal("200")
    assert float(pos["PETR4"]["average_price"]) == 15.0


def test_rotulos_ignorados_nao_mexem_na_posicao():
    pos, alerts = _pos(
        [_tx("TAEE11", "buy", 10, 30, "2020-01-10")],
        [_ev("TAEE11", "transferência", "Credito", 10, "2021-01-10"),
         _ev("TAEE11", "incorporação", "Credito", 10, "2021-01-10"),
         _ev("TAEE11", "atualização", "Credito", 10, "2021-01-10")],
    )
    assert pos["TAEE11"]["quantity"] == Decimal("10")
    assert not alerts


def test_sem_eventos_o_resultado_e_o_de_antes():
    txs = [_tx("PETR3", "buy", 100, 30, "2020-01-02"),
           _tx("PETR3", "sell", 40, 35, "2020-03-02")]
    assert _compute(txs) == _compute(txs, [])


class _Res:
    def __init__(self, scalar=None, rows=()):
        self._scalar, self._rows = scalar, list(rows)

    def scalar(self):
        return self._scalar

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, existe, rows=()):
        self.existe, self.rows, self.sqls = existe, rows, []

    def execute(self, stmt, params=None):
        self.sqls.append(str(stmt))
        if "to_regclass" in str(stmt):
            return _Res(scalar=self.existe)
        return _Res(rows=self.rows)


def test_tabela_ausente_nao_consulta_nada():
    """SELECT em tabela ausente abortaria a transação do recálculo inteiro."""
    conn = _Conn(existe=False)
    assert mod._load_events(conn, "u1") == []
    assert len(conn.sqls) == 1


def test_carrega_so_os_rotulos_de_posicao():
    conn = _Conn(existe=True, rows=[("2021-01-10", "desdobro", "Credito", "X3", 1, None, None)])
    evs = mod._load_events(conn, "u1")
    assert evs[0]["movement"] == "desdobro"
    assert "movement = ANY(:movs)" in conn.sqls[1]
