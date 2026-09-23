"""
tests/test_recompute_posicoes.py
================================
Regressao do recalculo de `portfolio_positions` a partir das notas.

Dois defeitos reais, medidos em producao sobre a carteira do usuario:

1. A media ponderada da compra usava `s["qty"]` mesmo quando ela estava
   NEGATIVA. O historico da B3 comeca em nov/2019 (e onde o relatorio de
   Negociacao comeca), entao ativos comprados antes disso aparecem so com a
   venda: a quantidade fica negativa e, na compra seguinte, o termo
   `qty * avg` entra como CREDITO e puxa a media para perto de zero. Foi
   assim que 747 cotas de BBAS3 ficaram gravadas com R$ 0,000245 de preco
   medio. O guarda `if avg <= 0: continue` nao pega, porque 0,000245 e
   positivo -- o numero errado passa como posicao legitima.

2. `recompute_for_user` so fazia UPSERT. Posicao que deixava de qualificar
   (media invalida, quantidade zerada) nunca era apagada: continuava na
   tabela com o valor da ultima vez em que qualificou, e o app seguia
   publicando esse fossil.
"""
from __future__ import annotations

from decimal import Decimal

from data_pipeline.importers.investments.positions import _compute


def _tx(ticker, tipo, qty, price, dia):
    return {
        "user_id":          "u1",
        "asset_id":         f"a-{ticker}",
        "ticker":           ticker,
        "type":             tipo,
        "quantity":         Decimal(str(qty)),
        "unit_price":       Decimal(str(price)),
        "fees":             Decimal("0"),
        "transaction_date": dia,
    }


def _por_ticker(positions):
    return {p["ticker"]: p for p in positions}


def test_venda_sem_cobertura_nao_vira_credito_na_media():
    """A assinatura do BBAS3: vendas do pedaco pre-2019 e uma compra depois."""
    txs = [
        _tx("BBAS3", "buy",  451, 30.00, "2019-11-04"),
        _tx("BBAS3", "sell", 1012, 28.00, "2021-03-10"),
        _tx("BBAS3", "buy",  100, 26.49, "2025-08-19"),
    ]
    pos = _por_ticker(_compute(txs)[0])
    assert "BBAS3" in pos, (
        "a compra posterior a venda sem cobertura e uma posicao real: "
        "100 cotas a 26,49"
    )
    assert pos["BBAS3"]["quantity"] == Decimal("100.00000000")
    assert round(float(pos["BBAS3"]["average_price"]), 2) == 26.49, (
        "media contaminada pelo saldo negativo -- o termo qty*avg entrou "
        "como credito"
    )


def test_media_irrisoria_nao_e_gravada_como_posicao():
    """747 cotas a R$ 0,000245 nao sao uma posicao; sao um defeito."""
    txs = [
        _tx("XPTO3", "buy",  200, 20.00, "2019-11-04"),
        _tx("XPTO3", "sell", 5000, 25.00, "2020-01-10"),
        _tx("XPTO3", "buy",  747, 24.00, "2021-05-10"),
    ]
    pos = _por_ticker(_compute(txs)[0])
    assert round(float(pos["XPTO3"]["average_price"]), 2) == 24.00


def test_venda_coberta_nao_muda_o_preco_medio():
    """Guarda do caso oposto: sob custo medio, vender nao mexe no PM."""
    txs = [
        _tx("PETR3", "buy",  100, 30.00, "2020-01-02"),
        _tx("PETR3", "buy",  100, 40.00, "2020-02-02"),
        _tx("PETR3", "sell",  50, 99.00, "2020-03-02"),
    ]
    pos = _por_ticker(_compute(txs)[0])
    assert pos["PETR3"]["quantity"] == Decimal("150.00000000")
    assert round(float(pos["PETR3"]["average_price"]), 2) == 35.00


def test_zerar_a_posicao_reinicia_a_base_de_custo():
    """Vendeu tudo e recomprou: o PM e o da recompra, nao a media das duas."""
    txs = [
        _tx("MGLU3", "buy",  100, 80.00, "2020-01-02"),
        _tx("MGLU3", "sell", 100, 20.00, "2022-01-02"),
        _tx("MGLU3", "buy",  100, 10.00, "2024-01-02"),
    ]
    pos = _por_ticker(_compute(txs)[0])
    assert round(float(pos["MGLU3"]["average_price"]), 2) == 10.00


def test_venda_sem_cobertura_continua_alertando():
    """Corrigir a aritmetica nao pode calar a evidencia de historico truncado."""
    txs = [
        _tx("ABCB4", "buy",  320, 15.00, "2019-11-04"),
        _tx("ABCB4", "sell", 2597, 18.00, "2020-06-10"),
    ]
    _, alerts = _compute(txs)
    tipos = {a["type"] for a in alerts}
    assert "quantidade_negativa" in tipos


def test_recompute_apaga_posicao_que_deixou_de_qualificar():
    """O UPSERT sozinho deixa fossil: o 747 @ R$ 0,18 sobreviveu a tudo."""
    import data_pipeline.importers.investments.positions as mod

    executados: list[tuple[str, object]] = []

    class _Res:
        rowcount = 1

        def fetchone(self):
            return ("pid-1",)

        def fetchall(self):
            return []

    class _Conn:
        def execute(self, stmt, params=None):
            executados.append((str(stmt), params))
            return _Res()

        def begin(self):
            class _Tx:
                def __enter__(self_inner):
                    return None

                def __exit__(self_inner, *a):
                    return False

            return _Tx()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Engine:
        def connect(self):
            return _Conn()

    monkey_txs = [_tx("PETR3", "buy", 100, 30.00, "2020-01-02")]
    original = mod._load_transactions
    mod._load_transactions = lambda conn, uid: monkey_txs
    try:
        out = mod.recompute_for_user(_Engine(), "u1")
    finally:
        mod._load_transactions = original

    assert out["ok"] is True
    deletes = [
        s for s, _ in executados
        if "DELETE FROM portfolio_positions" in s
    ]
    assert deletes, (
        "posicao que deixou de qualificar precisa sair da tabela; so o "
        "UPSERT deixa o valor antigo publicado para sempre"
    )
    assert "portfolio_id = :pid" in deletes[0], (
        "o DELETE tem de ser escopado pela carteira, nunca varrer a tabela"
    )
    assert out.get("positions_deleted") is not None
