from datetime import date

from core.controle import (
    canonical_transaction_type,
    parse_fatura_cartao_csv,
    _filtrar_transacoes,
    _SQL_DIVIDAS_CC,
    _SQL_HISTORICO_CC_MENSAL,
    _SQL_TRANSACOES_CARTAO,
)
from views.controle_financeiro import (
    _CAT_SAIDA,
    _FORMAS_PGTO_SAIDA,
    _card_rows_dataframe,
    _is_credit_card_invoice_source,
    _is_manual_card_related_text,
    _normalize_merchant_name,
    _prepare_category_analysis,
    _prepare_category_limit_analysis,
    _prepare_future_invoice_projection,
    _prepare_installment_analysis,
    _prepare_merchant_analysis,
    _prepare_recurring_analysis,
    _summary_credit_card,
)


def _cc_tx(
    idx: int,
    descricao: str,
    valor: float,
    vencimento: date,
    compra: date,
    categoria: str = "Compras",
    parcela_atual: int = 1,
    total_parcelas: int = 1,
    source: str = "csv",
) -> dict:
    return {
        "id": str(idx),
        "descricao": descricao,
        "valor": -abs(valor),
        "data": vencimento,
        "tipo": "expense",
        "tipo_fluxo": "expense",
        "status": "settled",
        "source": source,
        "categoria": categoria,
        "conta": "Cartao Teste",
        "account_type": "credit_card",
        "payment_date": compra,
        "data_compra": compra,
        "installment_current": parcela_atual,
        "installment_total": total_parcelas,
        "installment_group": f"grupo-{idx}" if total_parcelas > 1 else None,
    }


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


def test_manual_sidebar_allows_only_account_payment_flow():
    assert _FORMAS_PGTO_SAIDA == ["Conta"]
    assert "Pagamento de Cartão" not in _CAT_SAIDA
    assert not any("cart" in item.lower() for item in _FORMAS_PGTO_SAIDA)
    assert not any("pix" in item.lower() for item in _FORMAS_PGTO_SAIDA)
    assert not any("dinheiro" in item.lower() for item in _FORMAS_PGTO_SAIDA)


def test_manual_sidebar_blocks_card_related_free_text():
    assert _is_manual_card_related_text("Pagamento de Cartão")
    assert _is_manual_card_related_text("credito da fatura")
    assert _is_manual_card_related_text("compras do cartão")
    assert not _is_manual_card_related_text("Mercado")
    assert not _is_manual_card_related_text("Renda Fixa")


def test_credit_card_tab_accepts_only_csv_invoice_source():
    assert _is_credit_card_invoice_source("csv")
    assert _is_credit_card_invoice_source(" CSV ")
    assert not _is_credit_card_invoice_source("manual")
    assert not _is_credit_card_invoice_source("csv_migration")
    assert not _is_credit_card_invoice_source("import")
    assert not _is_credit_card_invoice_source("mock")


def test_credit_card_sql_queries_are_limited_to_csv_imports():
    expected = "COALESCE(t.source, '') = 'csv'"
    assert expected in _SQL_TRANSACOES_CARTAO
    assert expected in _SQL_HISTORICO_CC_MENSAL
    assert expected in _SQL_DIVIDAS_CC


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


def test_credit_card_merchant_normalization_groups_noisy_names():
    assert _normalize_merchant_name("EC *FORMOSA SUPERM E MAGAZ 123") == _normalize_merchant_name("FORMOSA SUPERM E MAGAZ")

    df = _card_rows_dataframe([
        _cc_tx(1, "EC *FORMOSA SUPERM E MAGAZ 123 | Compra 05/05/2026 | Cartao 3083 | Parcela Unica", 120, date(2026, 5, 10), date(2026, 5, 5), "Mercado"),
        _cc_tx(2, "FORMOSA SUPERM E MAGAZ | Compra 07/05/2026 | Cartao 3083 | Parcela Unica", 80, date(2026, 5, 10), date(2026, 5, 7), "Mercado"),
    ])

    merchants = _prepare_merchant_analysis(df)

    assert len(merchants) == 1
    assert merchants.iloc[0]["Transacoes"] == 2
    assert merchants.iloc[0]["Total (R$)"] == 200


def test_credit_card_recurrence_requires_more_than_same_month_duplicates():
    same_month = _card_rows_dataframe([
        _cc_tx(1, "FORMOSA SUPERM E MAGAZ | Compra 05/05/2026 | Cartao 3083 | Parcela Unica", 120, date(2026, 5, 10), date(2026, 5, 5), "Mercado"),
        _cc_tx(2, "FORMOSA SUPERM E MAGAZ | Compra 07/05/2026 | Cartao 3083 | Parcela Unica", 80, date(2026, 5, 10), date(2026, 5, 7), "Mercado"),
    ])
    assert _prepare_recurring_analysis(same_month).empty

    recurring = _card_rows_dataframe([
        _cc_tx(1, "STREAMING PREMIUM | Compra 05/03/2026 | Cartao 3083 | Parcela Unica", 59.9, date(2026, 3, 10), date(2026, 3, 5), "Assinaturas"),
        _cc_tx(2, "STREAMING PREMIUM | Compra 05/04/2026 | Cartao 3083 | Parcela Unica", 59.9, date(2026, 4, 10), date(2026, 4, 5), "Assinaturas"),
        _cc_tx(3, "STREAMING PREMIUM | Compra 05/05/2026 | Cartao 3083 | Parcela Unica", 59.9, date(2026, 5, 10), date(2026, 5, 5), "Assinaturas"),
    ])

    rec = _prepare_recurring_analysis(recurring)

    assert len(rec) == 1
    assert rec.iloc[0]["Recorrencia"] == "recorrente"
    assert rec.iloc[0]["Meses"] == 3


def test_credit_card_category_limits_and_future_projection():
    df = _card_rows_dataframe([
        _cc_tx(1, "NOTEBOOK | Compra 15/05/2026 | Cartao 3083 | Parcela 3/5", 500, date(2026, 5, 10), date(2026, 5, 15), "Compras", 3, 5),
        _cc_tx(2, "MERCADO LOCAL | Compra 16/05/2026 | Cartao 3083 | Parcela Unica", 300, date(2026, 5, 10), date(2026, 5, 16), "Mercado"),
    ])
    cat_df = _prepare_category_analysis(df)
    limits = _prepare_category_limit_analysis(cat_df, {"compras": 400, "mercado": 500})
    projection = _prepare_future_invoice_projection(df)

    compras = limits[limits["Categoria"] == "Compras"].iloc[0]
    assert compras["Status"] == "excedido"
    assert compras["Folga/Excesso"] == -100
    assert list(projection["Mes"]) == ["Jun/2026", "Jul/2026"]
    assert list(projection["Valor projetado"]) == [500, 500]


def test_credit_card_installment_analysis_hides_internal_group_id():
    df = _card_rows_dataframe([
        _cc_tx(1, "NOTEBOOK | Compra 15/05/2026 | Cartao 3083 | Parcela 3/5", 500, date(2026, 5, 10), date(2026, 5, 15), "Compras", 3, 5),
    ])

    installment_df = _prepare_installment_analysis(df)

    assert "installment_group" not in installment_df.columns
    assert list(installment_df.columns) == [
        "Estabelecimento",
        "Categoria",
        "Final",
        "Parcela atual",
        "Total parcelas",
        "Valor no mes",
        "Restantes",
        "Pendente estimado",
    ]


def test_credit_card_dataframe_discards_manual_migration_and_mock_rows():
    df = _card_rows_dataframe([
        _cc_tx(1, "SMILES CLUB SMILES | Compra 05/02/2026 | Cartao 3083 | Parcela 3/12", 37.8, date(2026, 5, 10), date(2026, 2, 5), "Marketing Direto", 3, 12),
        _cc_tx(2, "Material do Toldo para substituicao na area Gourmet (5x)", 5600, date(2026, 5, 14), date(2026, 5, 14), "Outros", source="csv_migration"),
        _cc_tx(3, "Compra manual antiga", 90, date(2026, 5, 10), date(2026, 5, 5), "Compras", source="manual"),
        _cc_tx(4, "Exemplo mock", 50, date(2026, 5, 10), date(2026, 5, 5), "Compras", source="mock"),
    ])

    assert list(df["estabelecimento"]) == ["SMILES CLUB SMILES"]
    assert "Toldo" not in " ".join(df["descricao"].tolist())
