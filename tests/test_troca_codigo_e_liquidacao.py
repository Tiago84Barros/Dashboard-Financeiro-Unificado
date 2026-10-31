"""Troca de código e negociação que faltou, no recálculo de posição.

Conciliação de 12/10/2026 contra a Área do Investidor da B3: TRPL3 -> ISAE3
e RBFF11 -> RBFM11 deixavam o código antigo vivo para sempre (posição
fantasma), e execuções idênticas no mesmo pregão entravam uma vez só (TAEE3
100 no app, 200 liquidadas). A liquidação completa o extrato só onde a falta
tem a assinatura exata desse defeito; o resto dela fica de fora.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import data_pipeline.importers.investments.positions as mod
from data_pipeline.importers.investments.b3_negociacao import _parse_row
from data_pipeline.importers.investments.positions import _compute


def _tx(ticker, tipo, qty, price, dia):
    return {
        "user_id": "u1", "asset_id": f"a-{ticker}", "ticker": ticker,
        "type": tipo, "quantity": Decimal(str(qty)), "unit_price": Decimal(str(price)),
        "fees": Decimal("0"), "transaction_date": dia,
    }


def _atu(ticker, qty, dia, nome, sentido="Credito"):
    return {"event_date": dia, "movement": "atualização", "direction": sentido,
            "ticker": ticker, "quantity": Decimal(str(qty)), "unit_price": None,
            "total_value": None, "product": f"{ticker} - {nome}", "asset_id": f"a-{ticker}"}


def _liq(ticker, sentido, qty, dia, valor):
    return {"event_date": dia, "movement": "transferência - liquidação", "direction": sentido,
            "ticker": ticker, "quantity": Decimal(str(qty)), "unit_price": None,
            "total_value": Decimal(str(valor)), "product": f"{ticker} - X",
            "asset_id": f"a-{ticker}"}


def _pos(txs, evs):
    positions, alerts = _compute(txs, evs)
    return {p["ticker"]: p for p in positions}, alerts


def _tipos(alerts):
    return [a["type"] for a in alerts]


# ── Troca de código ──────────────────────────────────────────────────────────

ISA = "ISA ENERGIA BRASIL S.A."


def test_troca_de_codigo_passa_o_custo_e_zera_o_antigo():
    pos, alerts = _pos(
        [_tx("TRPL3", "buy", 100, 10, "2020-01-10")],
        [_atu("TRPL3", 100, "2024-06-01", ISA), _atu("ISAE3", 100, "2024-11-19", ISA)],
    )
    assert "TRPL3" not in pos
    assert pos["ISAE3"]["quantity"] == Decimal("100")
    assert float(pos["ISAE3"]["average_price"]) == 10.0
    assert "troca_de_codigo" in _tipos(alerts)


def test_venda_pelo_codigo_novo_nao_fica_descoberta():
    pos, alerts = _pos(
        [_tx("TRPL3", "buy", 100, 10, "2020-01-10"),
         _tx("ISAE3", "sell", 100, 25, "2025-03-01")],
        [_atu("TRPL3", 100, "2024-06-01", ISA), _atu("ISAE3", 100, "2024-11-19", ISA)],
    )
    assert "TRPL3" not in pos and "ISAE3" not in pos
    assert "quantidade_negativa" not in _tipos(alerts)


def test_quantidade_diferente_nao_e_troca_de_codigo():
    """CVCB3 43 -> recibo CVCB9 5: subscrição, não troca."""
    pos, alerts = _pos(
        [_tx("CVCB3", "buy", 43, 20, "2020-01-10")],
        [_atu("CVCB3", 43, "2020-06-01", "CVC BRASIL"), _atu("CVCB9", 5, "2020-08-17", "CVC BRASIL")],
    )
    assert pos["CVCB3"]["quantity"] == Decimal("43")
    assert "troca_de_codigo_quantidade" in _tipos(alerts)


def test_dois_antigos_do_mesmo_emissor_nao_escolhe():
    pos, alerts = _pos(
        [_tx("TAEE3", "buy", 10, 10, "2020-01-10"), _tx("TAEE4", "buy", 10, 10, "2020-01-10")],
        [_atu("TAEE3", 10, "2021-01-01", "TAESA"), _atu("TAEE4", 10, "2021-01-01", "TAESA"),
         _atu("TAEE11", 10, "2022-01-01", "TAESA")],
    )
    assert pos["TAEE3"]["quantity"] == Decimal("10") and pos["TAEE4"]["quantity"] == Decimal("10")
    assert "TAEE11" not in pos
    assert "troca_de_codigo_ambigua" in _tipos(alerts)


def test_emissor_diferente_nao_sucede():
    pos, alerts = _pos(
        [_tx("TRPL3", "buy", 100, 10, "2020-01-10")],
        [_atu("TRPL3", 100, "2024-06-01", ISA), _atu("PETR4", 100, "2024-11-19", "PETROBRAS")],
    )
    assert pos["TRPL3"]["quantity"] == Decimal("100")
    assert "PETR4" not in pos
    assert not alerts


# ── Liquidação ───────────────────────────────────────────────────────────────

def test_liquidacao_que_fecha_com_o_pregao_nao_cria_nada():
    pos, alerts = _pos(
        [_tx("TAEE3", "buy", 100, 11.13, "2020-01-08"),
         _tx("TAEE3", "buy", 200, 11.13, "2020-12-16")],
        [_liq("TAEE3", "Credito", 200, "2020-12-18", 2226)],
    )
    assert pos["TAEE3"]["quantity"] == Decimal("300")
    assert "negociacao_da_liquidacao" not in _tipos(alerts)


def test_execucao_identica_fundida_volta_pela_liquidacao():
    pos, alerts = _pos(
        [_tx("TAEE3", "buy", 50, 10, "2020-01-08"),
         _tx("TAEE3", "buy", 100, 11.13, "2020-12-16")],
        [_liq("TAEE3", "Credito", 200, "2020-12-18", 2226)],
    )
    assert pos["TAEE3"]["quantity"] == Decimal("250")
    assert round(float(pos["TAEE3"]["average_price"]), 4) == round((500 + 2226) / 250, 4)
    assert _tipos(alerts).count("negociacao_da_liquidacao") == 1


def test_falta_de_outro_tamanho_nao_e_reconstruida():
    """BBAS3 do outro usuário: 15 no extrato e 1.464 liquidadas (exercício)."""
    pos, alerts = _pos(
        [_tx("BBAS3", "buy", 100, 20, "2025-01-08"),
         _tx("BBAS3", "buy", 15, 19.69, "2025-08-19")],
        [_liq("BBAS3", "Credito", 1464, "2025-08-22", 1464 * 19.69)],
    )
    assert pos["BBAS3"]["quantity"] == Decimal("115")
    assert "negociacao_da_liquidacao" not in _tipos(alerts)


def test_falta_multipla_a_outro_preco_nao_e_reconstruida():
    pos, alerts = _pos(
        [_tx("TAEE3", "buy", 50, 10, "2020-01-08"),
         _tx("TAEE3", "buy", 100, 11.13, "2020-12-16")],
        [_liq("TAEE3", "Credito", 200, "2020-12-18", 200 * 12.50)],
    )
    assert pos["TAEE3"]["quantity"] == Decimal("150")
    assert "negociacao_da_liquidacao" not in _tipos(alerts)


def test_pregao_anterior_ao_extrato_vira_compra():
    pos, alerts = _pos(
        [_tx("TRPL3", "buy", 1, 27, "2020-01-02")],
        [_liq("TRPL3", "Credito", 31, "2020-01-03", 31 * 27.5)],
    )
    # O pregão de 02/01 liquida em 06/01: a liquidação de 03/01 é de 2019.
    assert pos["TRPL3"]["quantity"] == Decimal("32")
    assert "negociacao_da_liquidacao" in _tipos(alerts)


def test_liquidacao_sem_pregao_no_meio_do_historico_fica_de_fora():
    pos, alerts = _pos(
        [_tx("SBSP3", "buy", 100, 50, "2020-01-08")],
        [_liq("SBSP3", "Debito", 1757, "2023-08-17", 1757 * 55.57)],
    )
    assert pos["SBSP3"]["quantity"] == Decimal("100")
    assert "negociacao_da_liquidacao" not in _tipos(alerts)


def test_sem_liquidacao_o_resultado_e_o_de_antes():
    txs = [_tx("PETR3", "buy", 100, 30, "2020-01-02"), _tx("PETR3", "sell", 40, 35, "2020-03-02")]
    assert _compute(txs, []) == _compute(txs)


# ── Carga e importador ───────────────────────────────────────────────────────

class _Res:
    def __init__(self, scalar=None, rows=()):
        self._scalar, self._rows = scalar, list(rows)

    def scalar(self):
        return self._scalar

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, rows):
        self.rows, self.sqls, self.params = rows, [], []

    def execute(self, stmt, params=None):
        self.sqls.append(str(stmt))
        self.params.append(params)
        if "to_regclass" in str(stmt):
            return _Res(scalar=True)
        return _Res(rows=self.rows)


def test_carga_traz_produto_ativo_atualizacao_e_liquidacao():
    conn = _Conn([("2024-11-19", "atualização", "Credito", "ISAE3", 100, None, None,
                   "ISAE3 - ISA ENERGIA BRASIL S.A.", "aid-1")])
    ev = mod._load_events(conn, "u1")[0]
    assert ev["product"] == "ISAE3 - ISA ENERGIA BRASIL S.A."
    assert ev["asset_id"] == "aid-1"
    movs = conn.params[-1]["movs"]
    assert "atualização" in movs and "transferência - liquidação" in movs


def _linha(qtd=100, preco=11.13):
    return (date(2020, 12, 16), "Compra", "Mercado à Vista", "-", "XP",
            "TAEE3", qtd, preco, qtd * preco)


def test_execucoes_identicas_viram_duas_chaves_e_a_primeira_e_a_antiga():
    oc: dict = {}
    a = _parse_row(_linha(), account_label="B3", ocorrencias=oc)
    b = _parse_row(_linha(), account_label="B3", ocorrencias=oc)
    legado = _parse_row(_linha(), account_label="B3")
    assert a["external_id"] != b["external_id"]
    assert a["external_id"] == legado["external_id"]


def test_mesmo_arquivo_de_novo_gera_as_mesmas_chaves():
    def chaves():
        oc: dict = {}
        return [_parse_row(_linha(), account_label="B3", ocorrencias=oc)["external_id"]
                for _ in range(3)]
    assert chaves() == chaves()
