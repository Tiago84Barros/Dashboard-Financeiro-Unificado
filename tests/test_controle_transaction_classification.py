from core.controle import canonical_transaction_type, _filtrar_transacoes


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
