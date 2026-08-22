"""Regressão: a aba Tabelas não deve misturar fatura de cartão (fluxo futuro)
com lançamentos já efetivados (manual = fluxo do mês).

A fatura de cartão importada via CSV (account_type='credit_card' AND
source='csv') ainda não saiu da conta — é fluxo futuro. A aba Dashboard já
excluía esses lançamentos dos totais; a aba Tabelas (get_transacoes_filtradas /
_filtrar_transacoes) não excluía, inflando "Saídas" com dinheiro que ainda não
saiu do bolso.
"""
from core.controle import _filtrar_transacoes


def _txs_mes_misto():
    return [
        {
            "descricao": "Supermercado (manual)",
            "valor": 300.0,
            "tipo_fluxo": "expense",
            "categoria": "Mercado",
            "eh_fatura_cartao": False,
            "ano": 2026,
            "mes": 8,
            "dia": 5,
        },
        {
            "descricao": "Fatura cartão agosto (CSV)",
            "valor": 1200.0,
            "tipo_fluxo": "expense",
            "categoria": "Cartão",
            "eh_fatura_cartao": True,
            "ano": 2026,
            "mes": 8,
            "dia": 10,
        },
    ]


def test_fatura_cartao_excluida_por_padrao():
    out = _filtrar_transacoes(_txs_mes_misto(), "Todos", "Todas", None, None, None, "")
    assert [t["descricao"] for t in out] == ["Supermercado (manual)"]
    assert sum(t["valor"] for t in out) == 300.0


def test_fatura_cartao_incluida_quando_solicitado():
    out = _filtrar_transacoes(
        _txs_mes_misto(), "Todos", "Todas", None, None, None, "",
        incluir_fatura_cartao=True,
    )
    assert len(out) == 2
    assert sum(t["valor"] for t in out) == 1500.0
