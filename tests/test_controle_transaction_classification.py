from datetime import date

from core.controle import (
    canonical_transaction_type,
    parse_fatura_cartao_csv,
    _filtrar_transacoes,
)
from views.controle_financeiro import _card_rows_dataframe, _summary_credit_card


def test_investment_categories_override_positive_flow_type():
    assert canonical_transaction_type("transfer", "Exterior", 7000) == "investment"
    assert canonical_transaction_type("income", "Investimentos", 1000) == "investment"
    assert canonical_transaction_type("entrada", "Renda Fixa", 500) == "investment"


def test_regular_income_expense_and_transfer_are_preserved():
    assert canonical_transaction_type("income", "Salario", 5000) == "income"
    assert canonical_transaction_type("expense", "Mercado", -200) == "expense"
    assert canonical_transaction_type("transfer", "Conta Corrente", 300) == "transfer"


def test_table_filters_use_canonical_type_not_amount_sign():
    txs = [
        {"descricao": "Salario", "valor": 5000, "categoria": "Salario", "tipo_fluxo": "income"},
        {"descricao": "Nomad", "valor": 7000, "categoria": "Exterior", "tipo_fluxo": "investment"},
        {"descricao": "Mercado", "valor": -350, "categoria": "Mercado", "tipo_fluxo": "expense"},
        {"descricao": "Transferencia", "valor": 300, "categoria": "Conta", "tipo_fluxo": "transfer"},
    ]

    receitas = _filtrar_transacoes(txs, "Receitas", "Todas", None, None, None, "")
    despesas = _filtrar_transacoes(txs, "Despesas", "Todas", None, None, None, "")
    investimentos = _filtrar_transacoes(txs, "Investimentos", "Todas", None, None, None, "")

    assert [t["descricao"] for t in receitas] == ["Salario"]
    assert [t["descricao"] for t in despesas] == ["Mercado"]
    assert [t["descricao"] for t in investimentos] == ["Nomad"]


def test_parse_credit_card_invoice_csv_model():
    csv_text = """Data de Compra;Nome no Cartão;Final do Cartão;Categoria;Descrição;Parcela;Valor (em US$);Cotação (em R$);Valor (em R$)
05/02/2026;TIAGO BARROS;3083;Marketing Direto;SMILES CLUB SMILES;3/12;0;0;37.80
06/04/2026;TIAGO BARROS;3083;-;Pag Fatura Boleto;Única;0;0;-145.86
28/04/2026;TIAGO BARROS;3083;-;Estorno Tarifa;Única;0;0;-98.00
"""
    parsed = parse_fatura_cartao_csv(csv_text.encode("utf-8"), date(2026, 5, 10))

    assert parsed["ok"] is True
    assert parsed["summary"]["rows"] == 3
    assert parsed["summary"]["total_purchases"] == 37.80
    assert parsed["summary"]["total_credits"] == 243.86
    assert parsed["summary"]["net_total"] == -206.06

    purchase, payment, refund = parsed["rows"]
    assert purchase["due_date"] == date(2026, 5, 10)
    assert purchase["purchase_date"] == date(2026, 2, 5)
    assert purchase["type"] == "expense"
    assert purchase["amount"] == -37.80
    assert purchase["installment_current"] == 3
    assert purchase["installment_total"] == 12
    assert purchase["installment_group"]

    assert payment["type"] == "transfer"
    assert payment["category"] == "Pagamento de Cartão"
    assert payment["amount"] == 145.86

    assert refund["type"] == "transfer"
    assert refund["category"] == "Créditos e Estornos"


def test_credit_card_invoice_analytics_separates_consumption_adjustments_and_fees():
    csv_text = """Data de Compra;Nome no Cartão;Final do Cartão;Categoria;Descrição;Parcela;Valor (em US$);Cotação (em R$);Valor (em R$)
05/02/2026;TIAGO BARROS;3083;Marketing Direto;SMILES CLUB SMILES;3/12;0;0;37.80
06/04/2026;TIAGO BARROS;3083;-;Pag Fatura Boleto;Única;0;0;-145.86
28/04/2026;TIAGO BARROS;3083;-;Anuidade Diferenciada;12/12;0;0;98.00
28/04/2026;TIAGO BARROS;3083;-;Estorno Tarifa;Única;0;0;-98.00
"""
    parsed = parse_fatura_cartao_csv(csv_text.encode("utf-8"), date(2026, 5, 10))

    txs = []
    for idx, row in enumerate(parsed["rows"], start=1):
        txs.append({
            "id": str(idx),
            "descricao": row["description"],
            "valor": row["amount"],
            "data": row["due_date"],
            "tipo": row["type"],
            "tipo_fluxo": "expense" if row["type"] == "expense" else "transfer",
            "status": "settled",
            "source": "csv",
            "categoria": row["category"],
            "conta": "Cartao Teste",
            "account_type": "credit_card",
            "payment_date": row["purchase_date"],
            "data_compra": row["purchase_date"],
            "installment_current": row["installment_current"],
            "installment_total": row["installment_total"],
            "installment_group": row["installment_group"],
        })

    summary = _summary_credit_card(_card_rows_dataframe(txs))

    assert summary["total_compras"] == 37.80
    assert summary["tarifas"] == 98.00
    assert summary["pagamentos"] == 145.86
    assert summary["estornos"] == 98.00
    assert summary["total_liquido"] == -108.06
